"""Tests for anetbbs/features/mrc_chat_ascii.py -- the plain ASCII MRC
chat client. Sibling of tests/test_petscii_mrc_chat.py -- same shape,
simpler content: no PETSCII case-inversion, no color-byte translation,
no PETSCII DEL byte. Confirms the overridden methods force the
plain-scroll mode already built into mrc_chat.py's MRCChat (see that
module's docstring), instead of the ANSI split-screen/cursor-addressed
mode that a plain ascii terminal can't render (session.write() strips
every ANSI escape sequence outright for term_mode == 'ascii').

This file's _FakeSession deliberately has NO read_line/read_key
methods at all -- any accidental fall-through to one of those (the
same AFK-screensaver-interrupts-MRC bug found live for PETSCII) fails
loudly with AttributeError instead of silently passing.
"""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.core  # noqa: F401  (resolves a circular import if mrc_chat is imported first)
from anetbbs.features.mrc_chat_ascii import AsciiMRCChat


class _FakeReader:
    """Feeds scripted single-byte reads, then EOF (b'')."""
    def __init__(self, data: bytes):
        self._chunks = [data[i:i + 1] for i in range(len(data))]

    async def read(self, n=1):
        if self._chunks:
            return self._chunks.pop(0)
        return b''


class _FakeSession:
    def __init__(self, window_size=(80, 24), input_bytes=b''):
        self.user = {'username': 'tester'}
        self.written = []
        self.window_size = window_size
        self.reader = _FakeReader(input_bytes)

    async def write(self, text):
        self.written.append(text)


def _run(coro):
    return asyncio.run(coro)


class AsciiMRCChatSplitScreenTests(unittest.TestCase):
    def test_enter_split_screen_forces_plain_mode_and_window_width(self):
        session = _FakeSession(window_size=(80, 24))
        chat = AsciiMRCChat(session)

        _run(chat._enter_split_screen())

        self.assertFalse(chat._split_screen,
                         '_split_screen must be False so _emit() and every '
                         'other self-guarded draw method takes the plain '
                         'fallback path')
        self.assertEqual(chat._chat_width, 80,
                         '_chat_width must come from session.window_size')
        self.assertEqual(chat._term_columns, 80)
        joined = ''.join(session.written)
        self.assertNotIn('\x1b[', joined,
                         'must not write any ANSI CSI sequence -- write() '
                         'strips them for term_mode == ascii anyway, but a '
                         'CPR probe would also just hang waiting for a '
                         'reply a plain terminal never sends')

    def test_enter_split_screen_uses_narrow_window_size(self):
        session = _FakeSession(window_size=(40, 24))
        chat = AsciiMRCChat(session)
        _run(chat._enter_split_screen())
        self.assertEqual(chat._chat_width, 40)
        self.assertEqual(chat._term_columns, 40)

    def test_exit_split_screen_writes_no_escape_codes(self):
        session = _FakeSession()
        chat = AsciiMRCChat(session)
        _run(chat._exit_split_screen())
        joined = ''.join(session.written)
        self.assertNotIn('\x1b[', joined)

    def test_emit_uses_plain_scroll_path(self):
        session = _FakeSession()
        chat = AsciiMRCChat(session)
        _run(chat._enter_split_screen())
        session.written.clear()

        _run(chat._emit('\x1b[1;96mtester\x1b[0m: hello'))

        self.assertEqual(len(session.written), 1,
                         'plain mode must be a single session.write() call, '
                         'not a redraw-the-whole-scroll-region sequence')
        self.assertIn('hello', session.written[0])
        self.assertTrue(session.written[0].endswith('\r\n'))


class AsciiMRCChatWordWrapTests(unittest.TestCase):
    """Same real bug PETSCII had (see test_petscii_mrc_chat.py's
    PetsciiMRCChatWordWrapTests): raw terminal hardware auto-wrap has
    no word-boundary awareness. AsciiMRCChat reuses the same fix."""
    def test_long_message_wraps_at_word_boundaries_not_mid_word(self):
        session = _FakeSession(window_size=(40, 24))
        chat = AsciiMRCChat(session)
        _run(chat._enter_split_screen())
        session.written.clear()

        _run(chat._emit(
            "Welcome to MRC StingRay! I'm here to serve. "
            "Get help by sending me a private message with the word help."))

        self.assertEqual(len(session.written), 1,
                         'must still be a single atomic write (all wrapped '
                         'lines joined), not one write per physical line')
        out = session.written[0]
        for line in out.split('\r\n'):
            self.assertLessEqual(len(line), 40,
                                 f'line exceeds the 40-column width: {line!r}')
        for word in ('Welcome', 'StingRay!', "I'm", 'here', 'serve.',
                     'sending', 'message', 'help.'):
            self.assertIn(word, out, f'{word!r} must not be split mid-word')

    def test_short_message_is_unaffected(self):
        session = _FakeSession(window_size=(80, 24))
        chat = AsciiMRCChat(session)
        _run(chat._enter_split_screen())
        session.written.clear()

        _run(chat._emit('hi Sting!'))

        self.assertEqual(session.written, ['hi Sting!\r\n'])


class AsciiMRCChatInputTests(unittest.TestCase):
    def test_reads_raw_bytes_and_echoes_each_character(self):
        session = _FakeSession(input_bytes=b'hi\r')
        chat = AsciiMRCChat(session)

        result = _run(chat._read_chat_line())

        self.assertEqual(result, 'hi')
        self.assertEqual(''.join(session.written), 'hi\r\n')

    def test_identify_password_is_masked_character_by_character(self):
        session = _FakeSession(input_bytes=b'/identify secret\r')
        chat = AsciiMRCChat(session)

        result = _run(chat._read_chat_line())

        self.assertEqual(result, '/identify secret',
                         'the REAL line returned to the caller (and thus '
                         'sent to the bridge) must be unmasked')
        echoed = ''.join(session.written)
        self.assertTrue(echoed.startswith('/identify '),
                        f'the command prefix itself must echo in the clear: {echoed!r}')
        self.assertNotIn('secret', echoed,
                         f'the password must never appear in what was echoed '
                         f'back to the screen: {echoed!r}')
        self.assertEqual(echoed.count('*'), len('secret'),
                         'one * per masked password character')

    def test_plain_chat_message_is_not_masked(self):
        session = _FakeSession(input_bytes=b'hello there\r')
        chat = AsciiMRCChat(session)
        _run(chat._read_chat_line())
        self.assertEqual(''.join(session.written), 'hello there\r\n')

    def test_backspace_uses_ascii_bs_del_and_backspace_space_backspace_echo(self):
        for backspace_byte in (b'\x7f', b'\x08'):
            with self.subTest(backspace_byte=backspace_byte):
                session = _FakeSession(input_bytes=b'ab' + backspace_byte + backspace_byte + b'c\r')
                chat = AsciiMRCChat(session)

                result = _run(chat._read_chat_line())

                self.assertEqual(result, 'c',
                                 "both 'a' and 'b' must be erased by the two backspaces")
                echoed = ''.join(session.written)
                self.assertEqual(echoed.count('\b \b'), 2,
                                 'each backspace echoes the standard \\b \\b '
                                 'erase-in-place sequence, matching '
                                 "session.py's own read_line() convention "
                                 'for every non-petscii term_mode')

    def test_never_touches_read_line_or_read_key(self):
        """_FakeSession has no read_line/read_key methods at all -- if
        _read_chat_line ever fell through to one (the exact bug that let
        the AFK screensaver interrupt a PETSCII chat session), this
        raises AttributeError instead of silently passing."""
        session = _FakeSession(input_bytes=b'/quit\r')
        chat = AsciiMRCChat(session)
        result = _run(chat._read_chat_line())
        self.assertEqual(result, '/quit')


class AsciiMRCChatConcurrencyTests(unittest.TestCase):
    """Same message-splicing regression PETSCII had: an incoming
    message arriving mid-keystroke must not splice into the line being
    typed. Both _emit() and the input-echo loop serialize on the same
    self._input_lock."""

    def test_emit_blocks_while_input_echo_holds_the_lock(self):
        async def _drive():
            session = _FakeSession()
            chat = AsciiMRCChat(session)
            order = []

            async def _hold_lock():
                async with chat._input_lock:
                    order.append('holder-acquired')
                    await asyncio.sleep(0.05)
                    order.append('holder-releasing')

            async def _emit_concurrently():
                await asyncio.sleep(0.01)
                await chat._emit('incoming message')
                order.append('emit-wrote')

            await asyncio.gather(_hold_lock(), _emit_concurrently())
            return order

        order = _run(_drive())
        self.assertEqual(order, ['holder-acquired', 'holder-releasing', 'emit-wrote'],
                         '_emit() must block on _input_lock until the '
                         'in-progress keystroke write releases it, not '
                         'write concurrently and splice into it')

    def test_input_echo_blocks_while_emit_holds_the_lock(self):
        async def _drive():
            session = _FakeSession(input_bytes=b'x\r')
            chat = AsciiMRCChat(session)
            order = []

            async def _hold_lock():
                async with chat._input_lock:
                    order.append('holder-acquired')
                    await asyncio.sleep(0.05)
                    order.append('holder-releasing')

            async def _type_concurrently():
                await asyncio.sleep(0.01)
                await chat._read_chat_line()
                order.append('input-echoed')

            await asyncio.gather(_hold_lock(), _type_concurrently())
            return order

        order = _run(_drive())
        self.assertEqual(order, ['holder-acquired', 'holder-releasing', 'input-echoed'],
                         'keystroke echo writes must also block on the same '
                         'lock while an incoming _emit() write is in flight')


if __name__ == '__main__':
    unittest.main()
