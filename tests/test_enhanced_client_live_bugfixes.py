"""Regression tests for real bugs Jerry found testing the ANetBBS
Enhanced Client on his Pi3 (2026-10-02, first test pass): MRC chat
crashing with "Menu error: Concurrent call to receive() is not
allowed" is covered separately in test_enhanced_server_deadconn.py
(test_concurrent_receive_cannot_happen_even_under_repeated_cancellation).
This file covers the other three real, confirmed root causes:

1. Screens drawn via plain session.write() (RSS reader, who's-online,
   dial-out's own fallback banner, ...) lost their screen-clear
   entirely in 'enhanced' mode, because write()'s ANSI-stripping for
   that mode discarded \\x1b[2J along with everything else -- old
   content just kept stacking under new content on every redraw
   ("screen should clear" / duplicate RSS tables / login+IM
   overlapping on one screen, all the same root cause).

2. anetbbs/features/ansi_ui.py's write_menu_art() -- the stock-
   fallback-tier renderer for chat/game_center/dialout/etc custom
   ANSI art -- writes raw CP437 bytes directly to session.writer,
   bypassing session.write()'s mode-aware JSON-wrapping entirely. Not
   actually exercised live yet (no dialout.ans/rss.ans exists, and
   chat/game_center are DB-menu-driven on this already-migrated
   install), but the exact same bug class as _show_ansi_screen()
   (which already has this guard) -- closed at the same chokepoint
   before the next custom-art screen hits it.

3. dialout.py's _connect() proxies fully raw bytes in both directions
   (telnet IAC negotiation, arbitrary remote-BBS output) -- genuinely
   out of scope for a JSON-only protocol in this test version. Must
   fail with a clear message instead of silently doing nothing
   ("dial-out does not work" was the live symptom).
"""
import json
import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / 'tests'))

from _deadconn_fixtures import make_session  # noqa: E402

from anetbbs.features import ansi_ui  # noqa: E402
from anetbbs.features.dialout import DialoutMenu  # noqa: E402


class _NullReader:
    def exception(self):
        return None

    async def read(self, n=1):
        return b''


def _parse_json_stream(raw_bytes):
    """FakeWriter concatenates every self.writer.write() call's bytes
    with no delimiter (unlike the real _WSWriterAdapter, where each
    call is its own WebSocket frame) -- each 'enhanced'-mode write()
    now produces exactly one JSON object, so multiple calls land back
    to back as concatenated JSON with no separator. json.JSONDecoder's
    raw_decode() correctly parses one object at a time off the front
    of a string and reports where it stopped, which is the standard
    way to split a concatenated-JSON stream without guessing at
    delimiters. Returns a list of parsed messages, in order."""
    text = raw_bytes.decode('utf-8')
    decoder = json.JSONDecoder()
    messages = []
    i = 0
    text = text.strip()
    while i < len(text):
        obj, end = decoder.raw_decode(text, i)
        messages.append(obj)
        i = end
        while i < len(text) and text[i].isspace():
            i += 1
    return messages


class EnhancedWriteClearDetectionTests(unittest.IsolatedAsyncioTestCase):
    """Superseded design note: write()'s 'enhanced' branch originally
    detected \\x1b[2J and sent a separate {"type":"clear"} message
    before a flat, ANSI-stripped {"type":"text"} message per write()
    call. That one-shot-per-call model is what broke cursor-positioned
    PARTIAL redraws (lightbar menus, MRC chat's status bar) -- found
    live on the next Pi3 test pass. Replaced with a PERSISTENT per-
    session virtual-screen buffer (session._enhanced_feed(), built on
    ansi_html._run_vt()'s new stateful `state=` parameter) that every
    write() call continues rather than restarting -- \\x1b[2J now
    clears the buffer as a side effect of _run_vt()'s own state
    machine (matching real terminal behavior) instead of a bolted-on
    string search, and every write() produces a single {"type":
    "screen"} message, never "text"/"clear" at all anymore."""

    async def test_clear_sequence_actually_clears_prior_content(self):
        session, writer = make_session(_NullReader())
        session._forced_term_mode = 'enhanced'
        await session.write('OLD CONTENT')
        await session.write('\x1b[2J\x1b[HNEW CONTENT')
        # Check only the LATEST message -- the first (pre-clear)
        # message legitimately still contains "OLD CONTENT" in the
        # accumulated FakeWriter buffer; what matters is that the
        # clear actually reset the persistent cells dict so the
        # SECOND message doesn't still carry it forward.
        last_msg = _parse_json_stream(bytes(writer.written))[-1]
        all_text = ''.join(
            run.get('text', '') for row in last_msg['rows'] for run in row['runs'])
        self.assertNotIn('OLD CONTENT', all_text)
        self.assertIn('NEW CONTENT', all_text)

    async def test_plain_write_produces_a_screen_message_not_text_or_clear(self):
        session, writer = make_session(_NullReader())
        session._forced_term_mode = 'enhanced'
        await session.write('just some text')
        out = writer.written.decode('utf-8')
        self.assertIn('"screen"', out)
        self.assertNotIn('"type": "text"', out)
        self.assertNotIn('"type": "clear"', out)
        self.assertIn('just some text', out)

    async def test_bytes_write_also_goes_through_the_persistent_buffer(self):
        session, writer = make_session(_NullReader())
        session._forced_term_mode = 'enhanced'
        await session.write(b'\x1b[2J\x1b[HHi there')
        self.assertIn('Hi there', writer.written.decode('utf-8'))

    async def test_separate_write_calls_continue_the_same_cursor_position(self):
        """The actual point of the redesign: two write() calls with no
        clear/newline in between must behave like one continuous
        terminal stream, not two independent one-shot renders -- "AAA"
        then "BBB" (no \\r\\n) must land on the SAME row, concatenated,
        not "AAA" on row 0 and "BBB" starting over at row 0 too."""
        session, writer = make_session(_NullReader())
        session._forced_term_mode = 'enhanced'
        await session.write('AAA')
        await session.write('BBB')
        last_msg = _parse_json_stream(bytes(writer.written))[-1]
        all_text = ''.join(
            run.get('text', '') for row in last_msg['rows'] for run in row['runs'])
        self.assertIn('AAABBB', all_text)

    async def test_cursor_positioned_partial_redraw_updates_in_place(self):
        """The real lightbar-menu bug this whole redesign fixes: a
        cursor-position escape + one row's content must overwrite that
        row in place, leaving every OTHER already-drawn row intact --
        not get appended as a brand new, separately-positioned line."""
        session, writer = make_session(_NullReader())
        session._forced_term_mode = 'enhanced'
        await session.write('\x1b[2J\x1b[Hrow zero\r\nrow one\r\nrow two')
        # ANSI cursor addressing is 1-indexed: row 2 = 0-indexed row 1
        # ("row one", the middle line) -- row 1 would hit "row zero".
        await session.write('\x1b[2;1Hrow ONE UPDATED')
        last_msg = _parse_json_stream(bytes(writer.written))[-1]
        all_text = ' '.join(
            run.get('text', '') for row in last_msg['rows'] for run in row['runs'])
        self.assertIn('row ONE UPDATED', all_text)
        self.assertIn('row zero', all_text)
        self.assertIn('row two', all_text)


class WriteMenuArtEnhancedGuardTests(unittest.IsolatedAsyncioTestCase):
    class _FakeEnhancedSession:
        term_mode = 'enhanced'

        def __init__(self):
            self.writer = self

        def write(self, data):
            raise AssertionError(
                'write_menu_art() must never write raw bytes directly '
                'in enhanced mode -- it bypasses the JSON protocol')

    async def test_returns_false_without_touching_the_writer(self):
        session = self._FakeEnhancedSession()
        result = await ansi_ui.write_menu_art(session, 'chat')
        self.assertFalse(result)

    async def test_returns_false_even_for_a_slot_with_real_stock_art(self):
        # 'main' has a real shipped main.ans/main132.ans/main.asc --
        # confirms the guard fires before load_menu_ansi() even runs,
        # not just for slots with no art configured anyway.
        session = self._FakeEnhancedSession()
        result = await ansi_ui.write_menu_art(session, 'main')
        self.assertFalse(result)


class DialoutEnhancedGuardTests(unittest.IsolatedAsyncioTestCase):
    class _FakeSession:
        term_mode = 'enhanced'

        def __init__(self):
            self.written = []

        async def write(self, text):
            self.written.append(text)

        async def read_line(self, prompt=''):
            return ''

    async def test_connect_refuses_and_never_opens_a_socket(self):
        from unittest import mock

        session = self._FakeSession()
        menu = DialoutMenu(session)
        with mock.patch('asyncio.open_connection') as mock_open:
            await menu._connect('A-Net Online', 'bbs.a-net.online', 1337, 'telnet')
        mock_open.assert_not_called()
        self.assertTrue(
            any('Enhanced Client' in w for w in session.written), session.written)


if __name__ == '__main__':
    unittest.main()
