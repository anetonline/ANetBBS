"""Regression test for a real Medium/High finding from a security/
performance audit (2026-09-10): anetbbs/features/anetcraft.py's
_safe_username() *stripped* every character outside [A-Za-z0-9_-]
instead of encoding it. The web registration form (web/auth.py's
RegisterForm.username Regexp) allows space, '.', and "'" in addition
to that set, and only rejects a NEW username if it matches an EXISTING
one exactly, case-insensitively -- punctuation variants of an already-
registered name are not blocked. So 'bob smith', 'bob.smith',
"bob'smith", and 'bobsmith' could all be registered as four distinct,
real accounts, and every one of them sanitized down to the identical
string 'bobsmith' -- meaning they all shared the exact same ANetCRAFT
single-player save file (and the same multiplayer per-user inventory
file). A player could deliberately register a punctuation-variant of
an existing username specifically to read or overwrite that other
player's save state, with no authorization check anywhere in the path.

Fixed by switching _safe_username() to percent-encode (urllib.parse.
quote with safe='') rather than strip -- an injective mapping over the
username character set this BBS allows, so distinct usernames can
never collide onto the same save file, while still guaranteeing no
literal '/' survives into the filename (see test_anetcraft_multiplayer_
save_path_traversal.py for the traversal-specific coverage).
"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.features.anetcraft as anetcraft_mod
from anetbbs.features.anetcraft import ANetCraft, _safe_username


class AnetcraftSafeUsernameCollisionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig_save_dir = anetcraft_mod.SAVE_DIR
        anetcraft_mod.SAVE_DIR = Path(self._tmp)
        # Multiplayer state lives entirely under SAVE_DIR/mp/ (see
        # anetcraft.py's cross-process MP redesign), so redirecting
        # SAVE_DIR per-test already isolates it.

    def tearDown(self):
        anetcraft_mod.SAVE_DIR = self._orig_save_dir
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_punctuation_variant_usernames_do_not_collide(self):
        """The exact four-way collision a real attacker could exploit:
        all four are valid, independently registerable usernames under
        web/auth.py's RegisterForm regex, and none of them are equal to
        each other case-insensitively (so registration's own uniqueness
        check would never catch the collision)."""
        variants = ['bob smith', 'bob.smith', "bob'smith", 'bobsmith']
        sanitized = [_safe_username(v) for v in variants]
        self.assertEqual(len(set(sanitized)), len(variants),
                         f'expected all distinct, got {sanitized}')

    def test_save_files_for_colliding_usernames_are_independent(self):
        """End-to-end: two of the colliding usernames each save a world,
        and neither one's save shows up under the other's _save_path()."""
        import json as _json

        victim = ANetCraft(session=None, username='bob smith')
        victim._new_world('survival')
        victim.player.hp = 7
        victim.save()

        attacker = ANetCraft(session=None, username='bob.smith')
        attacker._new_world('survival')
        attacker.player.hp = 19
        attacker.save()

        self.assertNotEqual(victim._save_path(), attacker._save_path())

        reloaded_victim = ANetCraft(session=None, username='bob smith')
        self.assertTrue(reloaded_victim.load())
        self.assertEqual(reloaded_victim.player.hp, 7,
                         "victim's save was overwritten by a "
                         "punctuation-variant username's save")

    def test_multiplayer_inventory_files_for_colliding_usernames_are_independent(self):
        from anetbbs.features.anetcraft import Player

        p1 = ANetCraft(session=None, username='bob smith')
        p1._mp_join()
        p1.player.hp = 3
        p1._mp_leave()

        p2 = ANetCraft(session=None, username="bob'smith")
        p2._mp_join()
        # A fresh join for a never-before-seen sanitized identity must
        # NOT pick up player 1's persisted inventory.
        self.assertNotEqual(p2.player.hp, 3)


if __name__ == '__main__':
    unittest.main()
