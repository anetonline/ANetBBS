"""Regression tests for a real bug found live (2026-09-25, a sysop
playtest report of "I am not seeing the multiplayer"): anetbbs/features/
anetcraft.py's multiplayer used to be a plain module-level dict
(`_MP = {}`), which only works if every player's session runs inside the
SAME Python process. They don't -- every door session, builtin_python
games included, is spawned as its own separate OS subprocess by
door_runner.py/builtin_runner.py (confirmed by reading both; true for
native SSH/telnet AND the web terminal, since both route through the same
subprocess launcher). Two real players each got their own private, empty
`_MP`, so choosing "Multiplayer" silently meant "solo, with extra steps" --
reproduced directly with two real concurrent subprocesses before this fix
(the second player's lobby never showed the first as online, and neither
ever saw the other's world changes).

Fixed by moving every piece of shared multiplayer state onto disk under a
new SAVE_DIR/mp/ subdirectory, polled/appended each tick -- the same
host-authoritative, file-polling design already proven out for the
standalone ANetCRAFT door's own C multiplayer (mp.c), adapted to this
game's Python/JSON idioms. See the big comment above _mp_root() in
anetcraft.py for the full design.

Two layers of coverage here:
  * AnetcraftMpFileProtocolTests -- fast, same-process unit tests of the
    actual sync logic (presence, block-delta propagation, mob authority
    election + handoff, attack/loot crediting, chat, snapshot rotation).
  * AnetcraftMpRealCrossProcessTests -- the direct proof for the reported
    bug shape: two genuinely separate OS processes (multiprocessing with
    the 'spawn' start method, not 'fork' -- spawn starts a fresh
    interpreter with nothing inherited from the parent's memory, the same
    way door_runner.py's subprocess.Popen(sys.executable, ...) does)
    confirming they actually see each other through SAVE_DIR alone.
"""
import json
import multiprocessing
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.features.anetcraft as anetcraft_mod
from anetbbs.features.anetcraft import ANetCraft, Mob


class AnetcraftMpFileProtocolTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig_save_dir = anetcraft_mod.SAVE_DIR
        anetcraft_mod.SAVE_DIR = Path(self._tmp)

    def tearDown(self):
        anetcraft_mod.SAVE_DIR = self._orig_save_dir
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_two_joined_players_see_each_other_in_presence(self):
        """This is the exact symptom from the bug report: the lobby's
        "(N online)" tag, and each player's own rendered view of the
        other, both come from _mp_list_players()/_mp_other_players."""
        alice = ANetCraft(session=None, username='alice')
        alice._mp_join()
        bob = ANetCraft(session=None, username='bob')
        bob._mp_join()

        seen_by_bob = anetcraft_mod._mp_list_players()
        self.assertIn('alice', seen_by_bob)
        self.assertIn('bob', seen_by_bob)

    def test_earliest_joiner_is_the_authority(self):
        alice = ANetCraft(session=None, username='alice')
        alice._mp_join()
        time.sleep(0.01)
        bob = ANetCraft(session=None, username='bob')
        bob._mp_join()

        self.assertTrue(alice._is_mp_host())
        self.assertFalse(bob._is_mp_host())

    def test_stale_authority_hands_off_automatically(self):
        """No push-based handoff message exists -- a departed/crashed
        authority is simply detected as stale and the next-earliest
        still-fresh player self-promotes."""
        alice = ANetCraft(session=None, username='alice')
        alice._mp_join()
        time.sleep(0.01)
        bob = ANetCraft(session=None, username='bob')
        bob._mp_join()
        self.assertTrue(alice._is_mp_host())

        # Simulate alice's process having vanished without a clean
        # _mp_leave() (e.g. a crash/kill -9) -- her presence file just
        # stops being refreshed and ages past AC_MP_STALE_SECONDS.
        stale_path = anetcraft_mod._mp_player_path('alice')
        data = json.loads(stale_path.read_text())
        data['ts'] = time.time() - (anetcraft_mod.AC_MP_STALE_SECONDS + 1)
        stale_path.write_text(json.dumps(data))

        self.assertTrue(bob._is_mp_host(),
                         'bob should self-promote once alice looks stale')

    def test_block_change_propagates_from_authority_to_guest(self):
        alice = ANetCraft(session=None, username='alice')
        alice._mp_join()
        time.sleep(0.01)
        bob = ANetCraft(session=None, username='bob')
        bob._mp_join()
        self.assertTrue(alice._is_mp_host())

        # Alice (the authority) mines out a block -- exactly what
        # _mine_step() does under the hood: self.world.set(...). The
        # World._on_change hook is what turns this into a shared delta.
        # (A known stone block, not whatever terrain gen happened to put
        # at some hardcoded coordinate -- avoids the test depending on
        # the random seed or cave placement.)
        alice.world.set(50, 20, 3)
        bob._mp_apply_new_deltas()
        self.assertEqual(bob.world.get(50, 20), 3)

        alice.world.set(50, 20, 0)

        # Bob hasn't polled yet -- his local copy is still stale.
        self.assertNotEqual(bob.world.get(50, 20), 0)
        bob._mp_apply_new_deltas()
        self.assertEqual(bob.world.get(50, 20), 0,
                         "guest's world never picked up the authority's "
                         'block change')

    def test_guest_block_change_does_not_echo_back_to_itself(self):
        """A guest's own _mp_apply_new_deltas() must never re-apply its
        own already-current world back onto itself in a way that would
        keep re-appending redundant deltas forever."""
        alice = ANetCraft(session=None, username='alice')
        alice._mp_join()
        time.sleep(0.01)
        bob = ANetCraft(session=None, username='bob')
        bob._mp_join()

        alice.world.set(60, 20, 0)
        bob._mp_apply_new_deltas()
        size_after_first_apply = anetcraft_mod._mp_deltas_path().stat().st_size

        bob._mp_apply_new_deltas()  # nothing new since last call
        size_after_second_apply = anetcraft_mod._mp_deltas_path().stat().st_size
        self.assertEqual(size_after_first_apply, size_after_second_apply)

    def test_mob_sync_and_non_authority_attack_credits_correctly(self):
        alice = ANetCraft(session=None, username='alice')
        alice._mp_join()
        time.sleep(0.01)
        bob = ANetCraft(session=None, username='bob')
        bob._mp_join()
        self.assertTrue(alice._is_mp_host())

        mob = Mob('zombie', 55.0, 20.0)
        mob.hp = 1   # one hit from bob's attack event will kill it
        alice._mobs = [mob]
        alice._mp_publish_mobs()

        bob._mobs = bob._mp_read_mobs()
        self.assertEqual(len(bob._mobs), 1)
        self.assertEqual(bob._mobs[0].id, mob.id)

        # Bob (non-authority) attacks -- must NOT mutate his own local
        # copy; it goes through an event for alice to resolve.
        bob._attack_mob(bob._mobs[0])
        self.assertFalse(bob._mobs[0].dead)

        alice._mp_process_events()
        self.assertTrue(alice._mobs[0].dead,
                         "authority never applied the guest's attack event")

        # Loot went to bob's grant inbox (he's not alice), not alice's
        # own inventory. Zombies drop nothing in this game's table --
        # switch to a cow (always drops meat+leather) to actually assert
        # a grant.
        cow = Mob('cow', 56.0, 20.0)
        cow.hp = 1
        alice._mobs = [cow]
        alice._mp_publish_mobs()
        bob._mobs = bob._mp_read_mobs()
        bob._attack_mob(bob._mobs[0])
        alice._mp_process_events()
        self.assertTrue(alice._mobs[0].dead)
        before = bob.player.count(60)  # meat
        bob._mp_apply_grants()
        after = bob.player.count(60)
        self.assertGreater(after, before,
                           "bob never received the loot from a kill the "
                           'authority resolved on his behalf')
        self.assertEqual(alice.player.count(60), 0,
                         "the authority must not keep the guest's kill "
                         'loot for itself')

    def test_chat_propagates_between_players(self):
        alice = ANetCraft(session=None, username='alice')
        alice._mp_join()
        bob = ANetCraft(session=None, username='bob')
        bob._mp_join()

        alice._mp_send_chat('hello bob')
        bob._mp_tail_chat()
        self.assertTrue(any(u == 'alice' and msg == 'hello bob'
                            for u, msg, _ in bob._mp_chat))

    def test_snapshot_rotation_resets_deltas_and_bumps_version(self):
        alice = ANetCraft(session=None, username='alice')
        alice._mp_join()   # already publishes an initial snapshot (version 1)
        version_after_join = json.loads(
            anetcraft_mod._shared_save_path().read_text())['version']

        alice.world.set(10, 20, 0)
        self.assertGreater(anetcraft_mod._mp_deltas_path().stat().st_size, 0)

        alice._mp_publish_snapshot()
        self.assertEqual(anetcraft_mod._mp_deltas_path().stat().st_size, 0)
        saved = json.loads(anetcraft_mod._shared_save_path().read_text())
        self.assertEqual(saved['version'], version_after_join + 1)


# ─── Real cross-process proof ───────────────────────────────────────────

def _mp_worker(save_dir: str, username: str, other_username: str, out_q):
    """Runs in a genuinely separate, freshly-spawned interpreter (see
    the 'spawn' start method below) -- nothing from the parent process's
    memory is inherited, matching exactly how door_runner.py/
    builtin_runner.py actually launch each player's session in production.
    """
    import time as _time
    import anetbbs.features.anetcraft as _mod
    _mod.SAVE_DIR = __import__('pathlib').Path(save_dir)

    player = _mod.ANetCraft(session=None, username=username)
    player._mp_join()
    # Give the other worker process a moment to have published its own
    # presence file too.
    for _ in range(50):
        seen = _mod._mp_list_players()
        if other_username in seen:
            break
        _time.sleep(0.05)
    out_q.put(sorted(_mod._mp_list_players().keys()))


class AnetcraftMpRealCrossProcessTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_two_real_separate_processes_see_each_other(self):
        ctx = multiprocessing.get_context('spawn')
        q1, q2 = ctx.Queue(), ctx.Queue()
        p1 = ctx.Process(target=_mp_worker, args=(self._tmp, 'alice', 'bob', q1))
        p2 = ctx.Process(target=_mp_worker, args=(self._tmp, 'bob', 'alice', q2))
        p1.start()
        p2.start()
        seen1 = q1.get(timeout=15)
        seen2 = q2.get(timeout=15)
        p1.join(timeout=5)
        p2.join(timeout=5)

        self.assertEqual(seen1, ['alice', 'bob'],
                         "alice's own separate process never saw bob online -- "
                         'this is the exact bug reported live')
        self.assertEqual(seen2, ['alice', 'bob'],
                         "bob's own separate process never saw alice online -- "
                         'this is the exact bug reported live')


if __name__ == '__main__':
    unittest.main()
