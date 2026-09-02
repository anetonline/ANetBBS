"""Regression test for a real Medium finding from a security/
performance audit (2026-09-02): anetbbs/features/anetcraft.py's
_mp_join() and _mp_leave() built the multiplayer inventory save path
as `SAVE_DIR / f'mp_{self.username}.json'`, using the raw username
directly -- bypassing the sanitizing helper a PRIOR audit already
added to close this exact class of bug for the single-player save path
(_save_path()). A username containing '/'/'..' could read/overwrite
another player's save or write outside SAVE_DIR entirely via these two
call sites specifically, even though the single-player path was
already fixed.

Fixed by extracting the sanitizing logic into a shared _safe_username()
helper and routing all three save-path call sites (_save_path,
_mp_join, _mp_leave) through it.
"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.features.anetcraft as anetcraft_mod
from anetbbs.features.anetcraft import ANetCraft, _safe_username


class AnetcraftSavePathTraversalTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig_save_dir = anetcraft_mod.SAVE_DIR
        anetcraft_mod.SAVE_DIR = Path(self._tmp)
        # Real CI-only failure found live (2026-09-02, GitHub Actions run
        # 33654440160): the shared multiplayer save path used to be a
        # separate module-level constant computed once at import time,
        # so patching SAVE_DIR alone didn't redirect it -- it's now a
        # _shared_save_path() function that always re-derives from
        # SAVE_DIR, closing that whole bug class rather than needing a
        # second thing to patch here.
        self._orig_mp = dict(anetcraft_mod._MP)
        anetcraft_mod._MP.clear()

    def tearDown(self):
        anetcraft_mod.SAVE_DIR = self._orig_save_dir
        anetcraft_mod._MP.clear()
        anetcraft_mod._MP.update(self._orig_mp)
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_safe_username_strips_traversal_characters(self):
        self.assertEqual(_safe_username('../../etc/passwd'), 'etcpasswd')
        self.assertEqual(_safe_username('normal_user-123'), 'normal_user-123')
        self.assertEqual(_safe_username(''), 'player')

    def test_multiplayer_join_reads_inventory_from_the_sanitized_path(self):
        """Pre-seed an inventory file at the SANITIZED location with a
        distinctive marker position, then confirm _mp_join() actually
        reads from there. Before the fix, _mp_join() computed its own
        (different, broken) path straight from the raw username --
        inv_path.exists() would be False for it, so the malicious
        username silently got a FRESH spawn-position player instead of
        the real saved one, rather than raising -- a quieter failure
        mode than _mp_leave()'s crash, but still proof the wrong path
        was being consulted."""
        import json as _json
        from anetbbs.features.anetcraft import Player
        malicious = '../../../../tmp/evil-join'
        safe_path = anetcraft_mod.SAVE_DIR / f'mp_{_safe_username(malicious)}.json'
        marker_player = Player(123.0, 45.0)
        safe_path.write_text(_json.dumps(marker_player.to_dict()))

        player = ANetCraft(session=None, username=malicious)
        player._mp_join()

        self.assertEqual(player.player.x, 123.0,
                         '_mp_join() must load from the sanitized path, '
                         'not silently spawn a fresh player because it '
                         'was looking in the wrong (unsanitized) place')
        self.assertEqual(player.player.y, 45.0)

    def test_multiplayer_leave_writes_inside_save_dir_not_escaped(self):
        malicious = '../../../../tmp/evil-leave'
        player = ANetCraft(session=None, username=malicious)
        player._mp_join()
        player._mp_leave()
        written = list(anetcraft_mod.SAVE_DIR.glob('mp_*.json'))
        self.assertTrue(written, 'expected an inventory file to be written')
        for f in written:
            self.assertTrue(
                str(f.resolve()).startswith(str(anetcraft_mod.SAVE_DIR.resolve())),
                f'inventory file escaped SAVE_DIR: {f.resolve()}')
        naive_escape = Path(self._tmp).parent / 'evil-leave.json'
        self.assertFalse(naive_escape.exists())


if __name__ == '__main__':
    unittest.main()
