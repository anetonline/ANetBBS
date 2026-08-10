"""Tests for anetbbs/features/mrc_chat_petscii.py -- the PETSCII
(C64/128) MRC chat client. Confirms the overridden methods force the
plain-scroll mode that anetbbs/features/mrc_chat.py's MRCChat already
has built in and self-guarded throughout (see that module's
docstring), instead of the ANSI split-screen/cursor-addressed mode
that a real C64 can't render -- plus three real bugs found live on
the Pi against this class's FIRST version (which just delegated
_read_chat_line() to session.read_line()):

  1. No password masking for /identify (etc.) -- read_line() has none.
  2. The AFK warning/screensaver could interrupt an MRC session --
     read_line() always passes allow_afk=True internally; MRC is
     supposed to never go through that path at all.
  3. An incoming message arriving mid-keystroke got spliced into the
     line being typed -- plain-mode _emit() and the input-echo loop
     both write directly to the same output stream with nothing
     serializing them, unlike ANSI split-screen mode where they're
     confined to separate cursor-addressed regions.

This file's _FakeSession deliberately has NO read_line/read_key/
read_password methods -- any accidental fall-through to one of those
(re-introducing bug #2) fails loudly with AttributeError instead of
silently passing.
"""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.core  # noqa: F401  (resolves a circular import if mrc_chat is imported first)
from anetbbs.features.mrc_chat_petscii import PetsciiMRCChat


class _FakeReader:
    """Feeds scripted single-byte reads, then EOF (b'')."""
    def __init__(self, data: bytes):
        self._chunks = [data[i:i + 1] for i in range(len(data))]

    async def read(self, n=1):
        if self._chunks:
            return self._chunks.pop(0)
        return b''


class _FakeSession:
    def __init__(self, petscii_width=40, input_bytes=b''):
        self.user = {'username': 'tester'}
        self.written = []
        self.petscii_width = petscii_width
        self.window_size = (80, 24)  # deliberately NOT petscii-sized --
        # proves _chat_width/_term_columns come from petscii_width, not window_size
        self.cleared = 0
        self.reader = _FakeReader(input_bytes)

    async def write(self, text):
        self.written.append(text)

    async def clear_screen(self):
        self.cleared += 1


def _run(coro):
    return asyncio.run(coro)


class PetsciiMRCChatSplitScreenTests(unittest.TestCase):
    def test_enter_split_screen_forces_plain_mode_and_petscii_width(self):
        session = _FakeSession(petscii_width=40)
        chat = PetsciiMRCChat(session)

        _run(chat._enter_split_screen())

        self.assertFalse(chat._split_screen,
                         '_split_screen must be False so _emit() and every '
                         'other self-guarded draw method takes the plain '
                         'fallback path')
        self.assertEqual(chat._chat_width, 40,
                         '_chat_width must come from session.petscii_width '
                         '(word-wrap uses it regardless of split-screen state)')
        self.assertEqual(chat._term_columns, 40,
                         '_term_columns must also be set -- a couple of width '
                         'calculations fall back to it when the sidebar is '
                         'disabled (always true here), and it otherwise sits '
                         'at its stale __init__ default (80)')
        self.assertEqual(session.cleared, 1)
        joined = ''.join(session.written)
        self.assertNotIn('\x1b[', joined,
                         'must not write any ANSI CSI sequence -- a real C64 '
                         "can't parse DECSTBM/CPR-probe/cursor-addressing")

    def test_exit_split_screen_just_clears(self):
        session = _FakeSession()
        chat = PetsciiMRCChat(session)
        _run(chat._exit_split_screen())
        self.assertEqual(session.cleared, 1)
        self.assertEqual(session.written, [])

    def test_emit_uses_plain_scroll_path(self):
        session = _FakeSession()
        chat = PetsciiMRCChat(session)
        _run(chat._enter_split_screen())
        session.written.clear()

        _run(chat._emit('\x1b[1;96mtester\x1b[0m: hello'))

        self.assertEqual(len(session.written), 1,
                         'plain mode must be a single session.write() call, '
                         'not a redraw-the-whole-scroll-region sequence')
        self.assertIn('hello', session.written[0])
        self.assertTrue(session.written[0].endswith('\r\n'))


class PetsciiMRCChatWordWrapTests(unittest.TestCase):
    """Regression tests for a real bug found live on the Pi: long
    messages wrapped badly, worse at 40 columns than 80 -- _emit() used
    to just write the raw string and let the TERMINAL'S hardware
    auto-wrap break it wherever the physical column landed, with no
    word-boundary awareness. Fixed by reusing MRCChat's own
    _word_wrap() helper (already used by the ANSI split-screen path)
    instead of relying on hardware wrap.
    """
    def test_long_message_wraps_at_word_boundaries_not_mid_word(self):
        session = _FakeSession(petscii_width=40)
        chat = PetsciiMRCChat(session)
        _run(chat._enter_split_screen())
        session.written.clear()

        _run(chat._emit(
            "Welcome to MRC StingRay! I'm here to serve. "
            "Get help by sending me a private message with the word help."))

        self.assertEqual(len(session.written), 1,
                         'must still be a single atomic write (all wrapped '
                         'lines joined), not one write per physical line -- '
                         'otherwise a keystroke could interleave BETWEEN '
                         'two lines of the same message')
        out = session.written[0]
        for line in out.split('\r\n'):
            self.assertLessEqual(len(line), 40,
                                 f'line exceeds the 40-column width: {line!r}')
        # No word split across a line boundary -- every real word from
        # the original message must appear intact somewhere in the output.
        for word in ('Welcome', 'StingRay!', "I'm", 'here', 'serve.',
                     'sending', 'message', 'help.'):
            self.assertIn(word, out, f'{word!r} must not be split mid-word')

    def test_short_message_on_narrow_width_is_unaffected(self):
        session = _FakeSession(petscii_width=40)
        chat = PetsciiMRCChat(session)
        _run(chat._enter_split_screen())
        session.written.clear()

        _run(chat._emit('hi Sting!'))

        self.assertEqual(session.written, ['hi Sting!\r\n'])

    def test_wraps_wider_on_an_80_column_session(self):
        session40 = _FakeSession(petscii_width=40)
        chat40 = PetsciiMRCChat(session40)
        _run(chat40._enter_split_screen())
        session40.written.clear()

        session80 = _FakeSession(petscii_width=80)
        chat80 = PetsciiMRCChat(session80)
        _run(chat80._enter_split_screen())
        session80.written.clear()

        msg = "Welcome to MRC StingRay! I'm here to serve. Get help anytime."
        _run(chat40._emit(msg))
        _run(chat80._emit(msg))

        lines_40 = session40.written[0].split('\r\n')
        lines_80 = session80.written[0].split('\r\n')
        self.assertGreater(len(lines_40), len(lines_80),
                          'the same message must wrap into MORE lines at '
                          '40 columns than at 80')


class PetsciiMRCChatInputTests(unittest.TestCase):
    def test_reads_raw_bytes_and_echoes_each_character(self):
        # PETSCII's letter-case byte assignment is inverted from ASCII's
        # (see petscii_codec.py's module docstring) -- decode_char()
        # swaps it back, so feeding the ASCII-UPPERCASE byte for a key
        # is how a real C64 keyboard sends what displays/decodes as
        # lowercase. Same convention session.py's own read_line()/
        # read_password() tests already use.
        session = _FakeSession(input_bytes=b'HI\r')
        chat = PetsciiMRCChat(session)

        result = _run(chat._read_chat_line())

        self.assertEqual(result, 'hi')
        self.assertEqual(''.join(session.written), 'hi\r\n')

    def test_identify_password_is_masked_character_by_character(self):
        session = _FakeSession(input_bytes=b'/IDENTIFY SECRET\r')
        chat = PetsciiMRCChat(session)

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
        session = _FakeSession(input_bytes=b'HELLO THERE\r')
        chat = PetsciiMRCChat(session)
        _run(chat._read_chat_line())
        self.assertEqual(''.join(session.written), 'hello there\r\n')

    def test_backspace_uses_petscii_del_byte_and_cursor_left_echo(self):
        from anetbbs.features.petscii_codec import CURSOR_LEFT
        session = _FakeSession(input_bytes=b'AB\x14\x14C\r')  # a, b, DEL, DEL, c, Enter
        chat = PetsciiMRCChat(session)

        result = _run(chat._read_chat_line())

        self.assertEqual(result, 'c',
                         "both 'a' and 'b' must be erased by the two DEL bytes")
        echoed = ''.join(session.written)
        self.assertEqual(echoed.count(CURSOR_LEFT), 4,
                         'each backspace echoes cursor-left twice (erase + '
                         're-position), matching read_password()\'s own '
                         'PETSCII backspace convention -- not ASCII \\b')
        self.assertNotIn('\x08', echoed,
                         'must never use ASCII backspace -- on real PETSCII '
                         'hardware that means something else entirely')

    def test_never_touches_read_line_or_read_key(self):
        """_FakeSession has no read_line/read_key/read_password methods at
        all -- if _read_chat_line ever fell through to one (the exact
        bug that let the AFK screensaver interrupt a chat session),
        this raises AttributeError instead of silently passing."""
        session = _FakeSession(input_bytes=b'/QUIT\r')
        chat = PetsciiMRCChat(session)
        result = _run(chat._read_chat_line())
        self.assertEqual(result, '/quit')


class PetsciiMRCChatConcurrencyTests(unittest.TestCase):
    """Regression tests for the real bug found live: an incoming message
    arriving mid-keystroke spliced into the line being typed. Both
    _emit() (incoming messages) and the input-echo loop must serialize
    on the same self._input_lock, matching how the ANSI split-screen
    path's _redraw_chat_area() already does against _draw_input_line()."""

    def test_emit_blocks_while_input_echo_holds_the_lock(self):
        async def _drive():
            session = _FakeSession()
            chat = PetsciiMRCChat(session)
            order = []

            async def _hold_lock():
                async with chat._input_lock:
                    order.append('holder-acquired')
                    await asyncio.sleep(0.05)
                    order.append('holder-releasing')

            async def _emit_concurrently():
                await asyncio.sleep(0.01)  # still inside the holder's window
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
            chat = PetsciiMRCChat(session)
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
