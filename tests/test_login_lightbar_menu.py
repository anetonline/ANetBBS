"""Regression tests for the ANSI-terminal login screen's interactive
lightbar menu (session.py's _login_lightbar_menu()), which replaced
the old static numbered 1/2/3 box. Covers arrow-key navigation
(including wraparound), Enter-to-select, the direct 1/2/3 and L/N/E
hotkeys, and that login_screen() itself dispatches on whatever the
lightbar returns exactly the same way it already did for the
ascii/petscii branches' read_line()-based menus.
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


def _queue_keys(keys):
    queue = list(keys)

    async def _read_key_arrow():
        if not queue:
            raise AssertionError(
                '_queue_keys ran out of scripted keys -- add another or '
                'fix the expected navigation sequence')
        return queue.pop(0)
    return _read_key_arrow


class LoginLightbarNavigationTests(unittest.TestCase):
    def test_down_down_enter_selects_exit(self):
        session, writer = _make_session(forced_term_mode='ansi')
        session.read_key_arrow = _queue_keys(['DOWN', 'DOWN', 'ENTER'])
        result = asyncio.run(session._login_lightbar_menu('Test BBS'))
        self.assertEqual(result, '3')

    def test_up_from_first_item_wraps_to_last(self):
        session, writer = _make_session(forced_term_mode='ansi')
        session.read_key_arrow = _queue_keys(['UP', 'ENTER'])
        result = asyncio.run(session._login_lightbar_menu('Test BBS'))
        self.assertEqual(result, '3')

    def test_down_from_last_item_wraps_to_first(self):
        session, writer = _make_session(forced_term_mode='ansi')
        session.read_key_arrow = _queue_keys(['DOWN', 'DOWN', 'DOWN', 'ENTER'])
        result = asyncio.run(session._login_lightbar_menu('Test BBS'))
        self.assertEqual(result, '1')

    def test_right_and_left_also_move_the_highlight(self):
        session, writer = _make_session(forced_term_mode='ansi')
        session.read_key_arrow = _queue_keys(['RIGHT', 'LEFT', 'ENTER'])
        result = asyncio.run(session._login_lightbar_menu('Test BBS'))
        self.assertEqual(result, '1')

    def test_direct_digit_hotkey_short_circuits_navigation(self):
        session, writer = _make_session(forced_term_mode='ansi')
        session.read_key_arrow = _queue_keys(['2'])
        result = asyncio.run(session._login_lightbar_menu('Test BBS'))
        self.assertEqual(result, '2')

    def test_letter_hotkeys_map_to_the_right_choice(self):
        for key, expected in (('L', '1'), ('N', '2'), ('E', '3')):
            with self.subTest(key=key):
                session, writer = _make_session(forced_term_mode='ansi')
                session.read_key_arrow = _queue_keys([key])
                result = asyncio.run(session._login_lightbar_menu('Test BBS'))
                self.assertEqual(result, expected)

    def test_esc_and_ctrl_c_both_act_as_exit(self):
        for key in ('ESC', 'CTRL_C'):
            with self.subTest(key=key):
                session, writer = _make_session(forced_term_mode='ansi')
                session.read_key_arrow = _queue_keys([key])
                result = asyncio.run(session._login_lightbar_menu('Test BBS'))
                self.assertEqual(result, '3')

    def test_unrecognized_keys_are_ignored_not_fatal(self):
        session, writer = _make_session(forced_term_mode='ansi')
        session.read_key_arrow = _queue_keys(['PGUP', 'HOME', 'DOWN', 'ENTER'])
        result = asyncio.run(session._login_lightbar_menu('Test BBS'))
        self.assertEqual(result, '2')

    def test_initial_draw_renders_the_box_and_bbs_name(self):
        session, writer = _make_session(forced_term_mode='ansi')
        session.read_key_arrow = _queue_keys(['ENTER'])
        asyncio.run(session._login_lightbar_menu('My Cool BBS'))
        out = bytes(writer.written)
        self.assertIn(b'My Cool BBS', out)
        self.assertIn(b'Login', out)
        self.assertIn(b'New User Registration', out)
        self.assertIn(b'Exit', out)
        # cp437 box-drawing corners from the original design, preserved
        self.assertIn('╔'.encode('cp437'), out)
        self.assertIn('╚'.encode('cp437'), out)
        # reverse-video highlight escape must appear (Login starts selected)
        self.assertIn(b'\x1b[7m', out)
        # no raw Unicode arrow/cursor codepoints, AND no raw C0
        # control-picture bytes (0x10/0x18/0x19) ever hit the wire --
        # confirmed live on a real Pi test that a modern/web ANSI
        # client can render those as Unicode control-picture
        # placeholders (␐/␘/␙) instead of the intended glyphs, so the
        # cursor marker must be a real printable cp437 character.
        self.assertNotIn('↑'.encode('utf-8'), out)
        self.assertNotIn('›'.encode('utf-8'), out)
        for byte in (0x10, 0x18, 0x19):
            self.assertNotIn(bytes([byte]), out)
        self.assertIn('»'.encode('cp437'), out)

    def test_screen_is_cleared_before_returning_a_choice(self):
        # Regression guard for a real bug found live on the Pi: without
        # a clear before returning, handle_login()'s own "=== User
        # Login ===" header bled into the still-visible box (this
        # menu uses absolute cursor addressing, unlike the old
        # read_line()-based menu which always left the cursor on a
        # fresh line below the box by construction).
        session, writer = _make_session(forced_term_mode='ansi')
        session.read_key_arrow = _queue_keys(['ENTER'])
        asyncio.run(session._login_lightbar_menu('Test BBS'))
        out = bytes(writer.written)
        self.assertTrue(out.endswith(b'\x1b[2J\x1b[H'))


class LoginScreenLightbarDispatchTests(unittest.TestCase):
    """Confirms login_screen() itself calls the lightbar for ANSI mode
    and dispatches on its result exactly like the other term modes."""

    def test_ansi_mode_uses_lightbar_and_exit_choice_returns_false(self):
        session, writer = _make_session(forced_term_mode='ansi')
        session._login_lightbar_menu = AsyncMock(return_value='3')
        result = asyncio.run(session.login_screen())
        self.assertFalse(result)
        session._login_lightbar_menu.assert_awaited()

    def test_ansi_mode_login_choice_calls_handle_login(self):
        session, writer = _make_session(forced_term_mode='ansi')
        session._login_lightbar_menu = AsyncMock(return_value='1')
        session.handle_login = AsyncMock(return_value=True)
        result = asyncio.run(session.login_screen())
        self.assertTrue(result)
        session.handle_login.assert_awaited()

    def test_ascii_and_petscii_modes_do_not_use_the_lightbar(self):
        for mode in ('ascii', 'petscii'):
            with self.subTest(mode=mode):
                session, writer = _make_session(forced_term_mode=mode, forced_width=80)
                session._login_lightbar_menu = AsyncMock()

                async def _fake_read_line(prompt=''):
                    return '3'
                session.read_line = _fake_read_line

                result = asyncio.run(session.login_screen())
                self.assertFalse(result)
                session._login_lightbar_menu.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
