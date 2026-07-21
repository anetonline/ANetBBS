"""Tests for the PETSCII login-screen branch (session.py's
login_screen()) and the Phase-1 vertical-slice placeholder
(anetbbs/features/petscii_ui.py's run_petscii_menu). See the "PETSCII
Terminal Support (Phase 1)" plan for context -- this is the build-order
checkpoint 3 the sysop tests against real hardware/VICE before the real
menu screens get built.
"""
import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.core.session import BBSSession


class _FakeWriter:
    def __init__(self):
        self.written = bytearray()

    def write(self, data):
        self.written += data

    async def drain(self):
        pass

    def close(self):
        pass


def _make_session(**kwargs):
    writer = _FakeWriter()
    session = BBSSession(object(), writer, config={}, **kwargs)
    return session, writer


class LoginScreenPetsciiBranchTests(unittest.TestCase):
    def test_petscii_login_box_is_plain_text_at_the_right_width(self):
        session, writer = _make_session(forced_term_mode='petscii', forced_width=40)
        captured = {}

        async def _fake_read_line(prompt=''):
            captured['menu'] = prompt
            return '3'  # Exit -- avoids needing a real DB-backed login/registration flow

        session.read_line = _fake_read_line
        result = asyncio.run(session.login_screen())

        self.assertFalse(result)
        menu = captured['menu']
        self.assertNotIn('\x1b[', menu, 'petscii login box must not embed raw ANSI escapes')
        self.assertIn('Login', menu)
        self.assertIn('New User Registration', menu)
        self.assertIn('Exit', menu)
        bar_lines = [line for line in menu.split('\r\n') if line.startswith('+')]
        self.assertTrue(bar_lines)
        for line in bar_lines:
            self.assertEqual(len(line), 40, f'bar line {line!r} must be exactly 40 cols wide')

    def test_petscii_login_box_honors_80_column_width(self):
        session, writer = _make_session(forced_term_mode='petscii', forced_width=80)
        captured = {}

        async def _fake_read_line(prompt=''):
            captured['menu'] = prompt
            return '3'

        session.read_line = _fake_read_line
        asyncio.run(session.login_screen())

        bar_lines = [line for line in captured['menu'].split('\r\n') if line.startswith('+')]
        for line in bar_lines:
            self.assertEqual(len(line), 80)

    def test_sysop_welcome_ansi_art_skipped_for_petscii(self):
        # _show_ansi_screen('welcome') is raw sysop-uploaded ANSI art with
        # no degrade path -- must not even be attempted for petscii.
        session, writer = _make_session(forced_term_mode='petscii')
        session._show_ansi_screen = AsyncMock()

        async def _fake_read_line(prompt=''):
            return '3'
        session.read_line = _fake_read_line

        asyncio.run(session.login_screen())
        session._show_ansi_screen.assert_not_called()

    def test_ansi_session_still_gets_welcome_screen(self):
        # Regression guard: the petscii skip must not affect the normal path.
        session, writer = _make_session()  # default term_mode == 'ansi'
        session._show_ansi_screen = AsyncMock()

        async def _fake_read_line(prompt=''):
            return '3'
        session.read_line = _fake_read_line

        asyncio.run(session.login_screen())
        session._show_ansi_screen.assert_called_once_with('welcome')


class RunPetsciiMenuStubTests(unittest.TestCase):
    def test_writes_plain_text_greeting_with_no_raw_ansi(self):
        from anetbbs.features.petscii_ui import run_petscii_menu

        session, writer = _make_session(forced_term_mode='petscii', forced_width=40)
        session.user = {'id': 1, 'username': 'wanda'}

        async def _fake_read_line(prompt=''):
            return ''
        session.read_line = _fake_read_line

        asyncio.run(run_petscii_menu(session))
        # Nothing raised; writer.written already went through the real
        # petscii encode path in write() (this isn't a mock write).
        self.assertGreater(len(writer.written), 0)
        self.assertNotIn(b'\x1b[', bytes(writer.written))

    def test_greets_by_username(self):
        from anetbbs.features.petscii_ui import run_petscii_menu

        session, writer = _make_session(forced_term_mode='petscii')
        session.user = {'id': 2, 'username': 'zeke'}

        async def _fake_read_line(prompt=''):
            return ''
        session.read_line = _fake_read_line

        asyncio.run(run_petscii_menu(session))
        # PETSCII's charset inverts letter case on the wire (see
        # petscii_codec.py) -- decode it back before checking content.
        from anetbbs.features.petscii_codec import decode
        self.assertIn('zeke', decode(bytes(writer.written)))


if __name__ == '__main__':
    unittest.main()
