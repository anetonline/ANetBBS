"""Tests for anetbbs/features/petscii_theme.py (the new PETSCII color
helper module) and its use in petscii_ui.py's menu rendering --
PETSCII menus were plain/monochrome even though ansi_to_petscii()
(added for message-body color) already fully wires session.write() to
translate ANSI color; menus just never embedded any color codes.
"""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class PetsciiThemeHelperTests(unittest.TestCase):
    def test_header_bar_wraps_title_in_color_and_reverse_video(self):
        from anetbbs.features import petscii_theme as theme
        bar = theme.header_bar('Main Menu', 20, theme.COLOR_LIGHT_BLUE)
        self.assertTrue(bar.startswith(theme.COLOR_LIGHT_BLUE))
        self.assertIn('\x12', bar)   # REVERSE_ON
        self.assertIn('\x92', bar)   # REVERSE_OFF
        self.assertIn('Main Menu', bar)

    def test_menu_line_colors_just_the_hotkey(self):
        from anetbbs.features import petscii_theme as theme
        line = theme.menu_line('1', 'Message Boards')
        self.assertIn(theme.COLOR_YELLOW + '1', line)
        self.assertIn('Message Boards', line)
        # Body color re-emitted after the hotkey, not left as yellow.
        self.assertIn(theme.DEFAULT_BODY_COLOR + '.', line)

    def test_resolve_color_known_name_case_insensitive(self):
        from anetbbs.features import petscii_theme as theme
        self.assertEqual(theme.resolve_color('light_blue'), theme.COLOR_LIGHT_BLUE)
        self.assertEqual(theme.resolve_color('RED'), theme.COLOR_RED)

    def test_resolve_color_unknown_or_none_falls_back_to_default(self):
        from anetbbs.features import petscii_theme as theme
        self.assertEqual(theme.resolve_color(None), theme.DEFAULT_HEADER_COLOR)
        self.assertEqual(theme.resolve_color(''), theme.DEFAULT_HEADER_COLOR)
        self.assertEqual(theme.resolve_color('NOT_A_REAL_COLOR'), theme.DEFAULT_HEADER_COLOR)
        self.assertEqual(theme.resolve_color('bogus', default=theme.COLOR_RED),
                         theme.COLOR_RED)


class _FakeSession:
    def __init__(self, user, responses):
        self.user = user
        self._responses = list(responses)
        self.written = []
        self._forced_width = 40

    async def write(self, text):
        self.written.append(text)

    async def read_line(self, prompt=''):
        if prompt:
            await self.write(prompt)
        if not self._responses:
            raise AssertionError('read_line() response queue exhausted')
        return self._responses.pop(0)

    async def clear_screen(self):
        self.written.append('[CLR]')

    @property
    def petscii_width(self):
        return self._forced_width

    def transcript(self):
        return ''.join(self.written)


class HeaderColorTests(unittest.TestCase):
    def test_header_defaults_to_the_module_default_color(self):
        from anetbbs.features import petscii_ui, petscii_theme

        async def _drive():
            session = _FakeSession({'id': 1}, [])
            await petscii_ui._header(session, 'Test')
            return session.transcript()

        out = asyncio.run(_drive())
        self.assertIn(petscii_theme.DEFAULT_HEADER_COLOR, out)

    def test_header_accepts_an_explicit_color_override(self):
        from anetbbs.features import petscii_ui, petscii_theme

        async def _drive():
            session = _FakeSession({'id': 1}, [])
            await petscii_ui._header(session, 'Test', petscii_theme.COLOR_RED)
            return session.transcript()

        out = asyncio.run(_drive())
        self.assertIn(petscii_theme.COLOR_RED, out)
        self.assertNotIn(petscii_theme.DEFAULT_HEADER_COLOR, out)

    def test_default_menu_hotkeys_are_colored(self):
        from anetbbs.features import petscii_ui, petscii_theme

        async def _drive():
            session = _FakeSession({'id': 1, 'access_level': 10}, ['Q'])
            await petscii_ui._run_default_petscii_menu(session)
            return session.transcript()

        out = asyncio.run(_drive())
        self.assertIn(petscii_theme.COLOR_YELLOW + '1', out)
        self.assertIn('MRC Chat', out)


class CustomMenuThemeColorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.petscii_color_menu_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, PetsciiMenu, PetsciiMenuItem
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()
            menu = PetsciiMenu(name='colortest', title='Color Test Menu',
                               theme_color='RED')
            db.session.add(menu)
            db.session.flush()
            db.session.add(PetsciiMenuItem(
                menu_id=menu.id, hotkey='Q', label='Logoff',
                action_type='logoff', sort_order=0))
            db.session.commit()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def test_custom_menu_applies_its_own_stored_theme_color(self):
        from anetbbs.features import petscii_ui, petscii_theme

        session = _FakeSession({'id': 1, 'access_level': 10}, ['Q'])
        with patch('anetbbs.features.bbs_ui._app', return_value=self.app):
            asyncio.run(petscii_ui._run_custom_petscii_menu(session, 'colortest'))

        out = session.transcript()
        self.assertIn(petscii_theme.COLOR_RED, out,
                      "a PetsciiMenu with theme_color='RED' must render its "
                      'header in real red, not the module default')


if __name__ == '__main__':
    unittest.main()
