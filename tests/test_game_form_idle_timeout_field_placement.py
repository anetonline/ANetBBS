"""Regression test for a real live bug: the "Auto-abort on Inactivity"
checkbox (Game.idle_timeout_enabled) was added to
templates/games/admin/form.html right after the FOSSIL-driver checkbox
-- which is INSIDE the `section_door_dosemu`-specific block. Every
per-game-type section on this page (`section_door_dos`,
`section_door_native`, `section_door_synchronet`, etc.) is hidden
(display:none) AND has every one of its <input>/<select>/<textarea>
DISABLED by this page's own game_type-switching JavaScript, for every
game type except the one currently selected.

A sysop with a door_native game (confirmed live: uMRC) never saw the
checkbox at all, and even if they had, a disabled input never submits
with the form -- so the setting would have been silently unreachable
for every game type except door_dosemu.

The field-value round-trip tests already in
tests/test_game_idle_timeout_enabled_field.py POST form data directly
(bypassing the browser entirely) and passed the whole time -- they
never would have caught this, since the JS-driven hide/disable only
matters to a real browser rendering the page, not a raw HTTP POST.
This file specifically checks the field's PLACEMENT in the rendered
HTML relative to the per-game-type section markers, which is the only
thing that actually distinguishes "usable for every game type" from
"only usable for door_dosemu".
"""
import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class GameFormIdleTimeoutFieldPlacementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.game_form_placement_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User, Game
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        with cls.app.app_context():
            db.create_all()
            admin = User(username='formplacementadmin', email='fpa@example.com',
                        password_hash='x', is_admin=True, access_level=100)
            db.session.add(admin)
            native_game = Game(name='uMRC-like Native Door', slug='native-placement-test',
                               game_type='door_native')
            db.session.add(native_game)
            db.session.commit()
            cls.admin_id = admin.id
            cls.native_game_id = native_game.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _client_as_admin(self):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True
        return client

    def test_checkbox_is_not_inside_any_game_type_specific_section(self):
        """The real bug: idle_timeout_enabled must appear BEFORE the
        first per-game-type section div (i.e. in the common/General
        area every game type shares), not nested inside one of them."""
        client = self._client_as_admin()
        resp = client.get(f'/admin/games/{self.native_game_id}/edit')
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode('utf-8')

        checkbox_pos = html.find('id="idle_timeout_enabled"')
        self.assertGreater(checkbox_pos, -1,
                           'idle_timeout_enabled checkbox must be present '
                           'in the rendered form at all')

        first_section_pos = html.find('class="game-type-section"')
        self.assertGreater(first_section_pos, -1,
                           'sanity check: the page must actually have '
                           'per-game-type sections (if this fails, the '
                           'page structure itself changed)')

        self.assertLess(
            checkbox_pos, first_section_pos,
            "idle_timeout_enabled must render BEFORE the first "
            "game-type-section div -- if it's after, it's nested inside "
            "a per-game-type section and will be hidden/disabled by the "
            "page's own JS for every OTHER game type (the real bug: it "
            "was placed inside the door_dosemu-only section, so a "
            "door_native game like uMRC never showed or could submit it)")

    def test_checkbox_is_not_nested_inside_the_dosemu_section_specifically(self):
        """Direct regression guard for the exact bug found live:
        confirm the checkbox's markup doesn't fall between
        section_door_dosemu's opening div and the next top-level
        section's opening div."""
        client = self._client_as_admin()
        resp = client.get(f'/admin/games/{self.native_game_id}/edit')
        html = resp.data.decode('utf-8')

        dosemu_start = html.find('id="section_door_dosemu"')
        self.assertGreater(dosemu_start, -1)

        # Next `id="section_...` after dosemu's marks the end of its block.
        next_section = re.search(r'id="section_door_native"', html[dosemu_start:])
        self.assertIsNotNone(next_section)
        dosemu_end = dosemu_start + next_section.start()

        checkbox_pos = html.find('id="idle_timeout_enabled"')
        self.assertFalse(
            dosemu_start < checkbox_pos < dosemu_end,
            'idle_timeout_enabled must not be nested inside '
            'section_door_dosemu specifically')

    def test_needs_fossil_driver_is_still_correctly_dosemu_specific(self):
        """Sanity check the fix didn't accidentally move
        needs_fossil_driver OUT of its correct, genuinely dosemu2-only
        section -- that field really is dosemu2-specific and should
        stay right where it was."""
        client = self._client_as_admin()
        resp = client.get(f'/admin/games/{self.native_game_id}/edit')
        html = resp.data.decode('utf-8')

        dosemu_start = html.find('id="section_door_dosemu"')
        next_section = re.search(r'id="section_door_native"', html[dosemu_start:])
        dosemu_end = dosemu_start + next_section.start()

        fossil_pos = html.find('id="needs_fossil_driver"')
        self.assertTrue(
            dosemu_start < fossil_pos < dosemu_end,
            'needs_fossil_driver should still be inside section_door_dosemu')


if __name__ == '__main__':
    unittest.main()
