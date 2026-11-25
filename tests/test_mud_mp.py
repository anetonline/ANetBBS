"""Tests for anetbbs/features/mud/mp.py -- cross-process presence +
chat, adapted directly from anetcraft.py's own proven file-sync
pattern (see tests/test_anetcraft_mp_cross_process.py for the original
bug report this pattern was built to fix: every door session,
builtin_python games included, is spawned as its own separate OS
subprocess, so a plain module-level dict silently means "solo, with
extra steps").

Two layers, same split as the ANetCRAFT precedent:
  * MudMpFileProtocolTests -- fast, same-process unit tests.
  * MudMpRealCrossProcessTests -- the direct proof: two genuinely
    separate spawned OS processes seeing each other through SAVE_DIR
    alone.
"""
import multiprocessing
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.features.mud.mp as mp_mod


class MudMpFileProtocolTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig_save_dir = mp_mod.SAVE_DIR
        mp_mod.SAVE_DIR = Path(self._tmp)

    def tearDown(self):
        mp_mod.SAVE_DIR = self._orig_save_dir
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_safe_username_percent_encodes_not_strips(self):
        """Same injective-encoding fix as anetcraft.py's own
        _safe_username() -- confirms two usernames differing only by
        characters this BBS's own registration form allows (space,
        '.') never collapse onto the same presence file."""
        a = mp_mod.safe_username('bob smith')
        b = mp_mod.safe_username('bob.smith')
        c = mp_mod.safe_username("bob'smith")
        d = mp_mod.safe_username('bobsmith')
        self.assertEqual(len({a, b, c, d}), 4)
        self.assertNotIn('/', a)

    def test_publish_and_list_players(self):
        mp_mod.publish_presence('alice', 'village-square', time.time())
        players = mp_mod.list_players()
        self.assertIn('alice', players)
        self.assertEqual(players['alice']['room_slug'], 'village-square')

    def test_stale_presence_is_excluded(self):
        mp_mod.publish_presence('stale_bob', 'village-square',
                                time.time() - 1000)
        # Manually backdate the 'ts' field too (publish_presence always
        # stamps 'now', so simulate a stale write directly).
        path = mp_mod._player_path('stale_bob')
        import json
        data = json.loads(path.read_text())
        data['ts'] = time.time() - (mp_mod.MP_STALE_SECONDS + 5)
        path.write_text(json.dumps(data))
        players = mp_mod.list_players()
        self.assertNotIn('stale_bob', players)

    def test_remove_presence_makes_player_disappear_immediately(self):
        mp_mod.publish_presence('carol', 'village-square', time.time())
        self.assertIn('carol', mp_mod.list_players())
        mp_mod.remove_presence('carol')
        self.assertNotIn('carol', mp_mod.list_players())

    def test_remove_presence_on_unknown_player_does_not_raise(self):
        mp_mod.remove_presence('never-existed')  # must not raise

    def test_players_in_room_filters_by_room_and_excludes_self(self):
        mp_mod.publish_presence('dave', 'room-a', time.time())
        mp_mod.publish_presence('erin', 'room-a', time.time())
        mp_mod.publish_presence('frank', 'room-b', time.time())
        others = mp_mod.players_in_room('room-a', exclude_username='dave')
        self.assertEqual(others, ['erin'])

    def test_live_player_count(self):
        mp_mod.publish_presence('p1', 'room-a', time.time())
        mp_mod.publish_presence('p2', 'room-a', time.time())
        self.assertEqual(mp_mod.live_player_count(), 2)

    def test_chat_tail_sees_only_new_messages_since_last_poll(self):
        # A session starts at end-of-log: no history replay (fix F-1).
        tail1 = mp_mod.ChatTail()
        tail2 = mp_mod.ChatTail()
        tail1.send('alice', 'say', 'hello')
        msgs = tail2.poll()
        self.assertEqual(msgs, [('alice', 'say', 'hello', '', '')])
        self.assertEqual(tail2.poll(), [])  # nothing new on a second poll
        tail1.send('bob', 'yell', 'anyone there?')
        self.assertEqual(tail2.poll(), [('bob', 'yell', 'anyone there?', '', '')])

    def test_chat_tell_carries_a_target(self):
        tail1 = mp_mod.ChatTail()
        tail2 = mp_mod.ChatTail()
        tail1.send('alice', 'tell', 'psst', target='bob')
        msgs = tail2.poll()
        self.assertEqual(msgs, [('alice', 'tell', 'psst', 'bob', '')])

    def test_chat_tail_handles_log_truncation_gracefully(self):
        """Mirrors anetcraft.py's own _mp_tail_chat() truncation-
        detection: if the log shrank (rotated), reset to the start
        instead of crashing/hanging on a negative slice."""
        tail = mp_mod.ChatTail()
        tail.send('alice', 'say', 'first message that is somewhat long')
        tail.poll()
        mp_mod._chat_path().write_text('')  # simulate rotation
        tail.send('bob', 'say', 'short')
        msgs = tail.poll()
        self.assertEqual(msgs, [('bob', 'say', 'short', '', '')])


def _mp_worker(save_dir: str, username: str, other_username: str, out_q):
    """Runs in a genuinely separate, freshly-spawned interpreter --
    nothing from the parent process's memory is inherited, matching
    exactly how door_runner.py/builtin_runner.py actually launch each
    player's session in production."""
    import time as _time
    import anetbbs.features.mud.mp as _mod
    _mod.SAVE_DIR = __import__('pathlib').Path(save_dir)

    _mod.publish_presence(username, 'village-square', _time.time())
    for _ in range(50):
        seen = _mod.list_players()
        if other_username in seen:
            break
        _time.sleep(0.05)
    out_q.put(sorted(_mod.list_players().keys()))


class MudMpRealCrossProcessTests(unittest.TestCase):
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
                         "alice's own separate process never saw bob online")
        self.assertEqual(seen2, ['alice', 'bob'],
                         "bob's own separate process never saw alice online")


if __name__ == '__main__':
    unittest.main()
