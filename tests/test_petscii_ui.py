"""Tests for the PETSCII login-screen branch (session.py's
login_screen()) and the top-level shell of anetbbs/features/petscii_ui.py's
run_petscii_menu() (the real Phase 1 main menu loop -- see
tests/test_petscii_ui_screens.py for the individual screens it dispatches
to: boards/echomail/PM/files/who's-online/profile). See the "PETSCII
Terminal Support (Phase 1)" plan for context.
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


def _queue_read_line(responses):
    """A read_line() stand-in that pops one scripted response per call
    and raises (fast, loud) once the queue runs dry -- NOT a default
    value like '', which doesn't match any menu's exit condition and
    would spin a `while True:` menu loop forever instead of failing.
    See RunPetsciiMenuTests' class docstring for the real incident this
    guards against."""
    queue = list(responses)

    async def _read_line(prompt=''):
        if not queue:
            raise AssertionError(
                f'_queue_read_line ran out of responses (prompt={prompt!r}) -- '
                'add another response or fix the menu flow')
        return queue.pop(0)
    return _read_line


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


class RunPetsciiMenuTests(unittest.TestCase):
    """run_petscii_menu() was originally a one-shot placeholder greeting
    (hence the old class name, *StubTests) -- it's now the real Phase 1
    main menu loop (see tests/test_petscii_ui_screens.py for the full
    screens it dispatches to). These tests only cover the loop's own
    shell: rendering, no raw ANSI, and a clean exit on 'Q'.

    IMPORTANT: a fake read_line() that always returns '' (as this file's
    OLDER tests did, back when run_petscii_menu() only asked one
    question and returned) now spins the real `while True:` main menu
    loop forever -- '' matches none of '1'-'6'/'Q'. This was caught
    live: the resulting infinite loop kept appending to the fake
    writer's output buffer every iteration with no bound, consuming
    steadily more memory over many minutes rather than failing fast.
    Every scripted read_line() queue in this file must end in 'Q'."""

    def test_writes_plain_text_menu_with_no_raw_ansi_then_exits_on_q(self):
        from anetbbs.features.petscii_ui import run_petscii_menu

        session, writer = _make_session(forced_term_mode='petscii', forced_width=40)
        session.user = {'id': 1, 'username': 'wanda'}
        session.read_line = _queue_read_line(['Q'])

        asyncio.run(run_petscii_menu(session))
        # Nothing raised; writer.written already went through the real
        # petscii encode path in write() (this isn't a mock write).
        self.assertGreater(len(writer.written), 0)
        self.assertNotIn(b'\x1b[', bytes(writer.written))

    def test_shows_main_menu_options_and_logoff_message(self):
        from anetbbs.features.petscii_ui import run_petscii_menu
        from anetbbs.features.petscii_codec import decode

        session, writer = _make_session(forced_term_mode='petscii')
        session.user = {'id': 2, 'username': 'zeke'}
        session.read_line = _queue_read_line(['Q'])

        asyncio.run(run_petscii_menu(session))
        transcript = decode(bytes(writer.written))
        self.assertIn('Main Menu', transcript)
        self.assertIn('Message Boards', transcript)
        self.assertIn('Logoff', transcript)
        self.assertIn('Goodbye', transcript)


if __name__ == '__main__':
    unittest.main()
