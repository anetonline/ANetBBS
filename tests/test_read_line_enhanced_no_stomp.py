"""Regression tests for TWO real, sequentially-found live bugs in
BBSSession.read_line() (anetbbs/core/session.py), both on the ANetBBS
Enhanced Client (Pi3 test, 2026-10-02):

Bug 1 -- "I have no door menus... if I choose 1, there is nothing", zero
exceptions anywhere (clean server journal, clean browser console).
read_line() used to unconditionally write its prompt and echo every
typed character via self.write() -- in 'enhanced' mode, write() ALWAYS
feeds a persistent virtual-screen buffer and emits a 'screen' message
(see write()'s own docstring); there's no such thing as an invisible
write there. GameManager.show_door_menu()'s enhanced branch sends a
structured 'menu' message (clickable door buttons), then immediately
calls read_line("Pick a game (number or Q): ") to collect the click-
synthesized "<N>\\r" -- read_line()'s own prompt-write fired right
after, instantly emitting a 'screen' message that stomped the just-sent
menu with a near-blank one-line screen before the user could see, let
alone click, anything.

Bug 2 -- the FIRST fix for bug 1 (shipped and live-tested the same day)
over-corrected: it skipped read_line()'s prompt/echo for EVERY
'enhanced'-mode call, not just ones racing a still-visible menu overlay.
That silenced completely ordinary prompts that have nothing to do with
a menu at all -- "Press Enter to continue..." on the pre-login Matrix
splash, and handle_login()'s "Username: " right after its own
"=== User Login ===" header write -- both went invisible, leaving the
user on an apparently-blank screen with no cue to type anything.

The real fix: BBSSession._enhanced_menu_active (see its own docstring)
tracks whether a menu overlay is CURRENTLY showing -- set True the
moment encode_menu() is sent, cleared back to False the moment any real
write() legitimately replaces it with a 'screen' message (_enhanced_
feed()). read_line() only suppresses its prompt/echo when this flag is
true, so bug 1's stomping is still prevented (the menu is genuinely
still up) while bug 2's ordinary prompts show normally (no menu was
ever sent, or it was already replaced by real content).

Deliberately NOT tested through games.py's own fake _FakeSession
(tests/test_door_games_menu_enhanced_icons.py) -- that fixture's
read_line()/write() are simplified mocks that never reproduce real
write()'s enhanced-mode behavior, which is exactly why that whole
passing test suite caught neither bug. This drives the REAL
BBSSession.read_line()/write() against a REAL StreamReader-shaped fake
(one byte at a time, matching read_raw()'s real call pattern) and
inspects the real JSON frames written to the transport.
"""
import asyncio
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.core.session import BBSSession


class _FakeWriter:
    def __init__(self):
        self.written = []

    def write(self, data):
        self.written.append(data.decode('utf-8'))

    async def drain(self):
        pass

    def get_extra_info(self, key, default=None):
        return default

    def close(self):
        pass


class _FakeReader:
    """Stream-reader-shaped fake: hands back queued bytes one at a
    time, exactly like asyncio.StreamReader.read(1) behaves for a real
    telnet/SSH/websocket connection -- see handle_telnet_command()'s
    own docstring for why read_raw() always calls read(1) in practice."""
    def __init__(self, data: bytes):
        self._buf = bytearray(data)

    async def read(self, n=1):
        if not self._buf:
            return b''
        chunk = bytes(self._buf[:n])
        del self._buf[:n]
        return chunk

    def exception(self):
        return None


def _make_session(data: bytes):
    writer = _FakeWriter()
    reader = _FakeReader(data)
    session = BBSSession(reader, writer, config={}, forced_term_mode='enhanced')
    return session, writer


class ReadLineEnhancedMenuStompTests(unittest.TestCase):
    """Bug 1: a read_line() racing a still-visible menu overlay must
    not stomp it."""

    def test_prompt_write_emits_no_screen_message_while_a_menu_is_active(self):
        session, writer = _make_session(b'\r')
        session._enhanced_menu_active = True
        asyncio.run(session.read_line('Pick a game (number or Q): '))
        self.assertEqual(writer.written, [])

    def test_typing_and_enter_emit_no_screen_messages_either(self):
        """A synthesized click ("1\\r" on the wire, per
        encode_menu()'s 'send' override) must not produce any echo
        output either -- confirms the fix covers the per-character
        path, not just the initial prompt."""
        session, writer = _make_session(b'16\r')
        session._enhanced_menu_active = True
        line = asyncio.run(session.read_line('Pick a game (number or Q): '))
        self.assertEqual(line, '16')
        self.assertEqual(writer.written, [])

    def test_a_just_sent_menu_message_survives_the_read(self):
        """End-to-end shape of the real bug: send a 'menu' message (as
        show_door_menu()'s enhanced branch does, which also sets the
        _enhanced_menu_active flag -- not duplicated by hand here),
        then read_line() the click response -- the menu message must
        remain the ONLY structured frame on the wire; no competing
        'screen' message appears to have raced it."""
        session, writer = _make_session(b'1\r')
        from anetbbs.features.enhanced_protocol import encode_menu
        session._enhanced_vt_state = None
        session.writer.write(encode_menu(
            'Door Games', [('1', 'LORD', '', '', '1\r'), ('Q', 'Return', '', '', 'Q\r')]
        ).encode('utf-8'))
        asyncio.run(session._drain_protected())
        session._enhanced_menu_active = True

        line = asyncio.run(session.read_line('Pick a game (number or Q): '))
        self.assertEqual(line, '1')

        msgs = [json.loads(w) for w in writer.written]
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]['type'], 'menu')

    def test_a_real_write_after_the_menu_clears_the_active_flag(self):
        """The flag must not stay stuck true forever -- the moment the
        caller writes real content (e.g. launching the chosen door),
        the menu is legitimately gone and the flag must reflect that
        without any call site having to clear it by hand."""
        session, writer = _make_session(b'')
        session._enhanced_menu_active = True
        asyncio.run(session.write('Launching LORD...\r\n'))
        self.assertFalse(session._enhanced_menu_active)
        self.assertEqual(len(writer.written), 1)
        self.assertEqual(json.loads(writer.written[0])['type'], 'screen')


class ReadLineEnhancedOrdinaryPromptTests(unittest.TestCase):
    """Bug 2 (the over-correction): a read_line() prompt with NO menu
    overlay in play must behave exactly like every other mode -- prompt
    shows, typed characters echo."""

    def test_prompt_shows_normally_when_no_menu_was_ever_sent(self):
        """Matrix splash's "Press Enter to continue..." shape: nothing
        has ever called encode_menu() in this session, so the flag is
        still its default False -- the prompt must be visible."""
        session, writer = _make_session(b'\r')
        self.assertFalse(session._enhanced_menu_active)
        asyncio.run(session.read_line('Press Enter to continue...'))
        # One frame for the prompt itself, one for the Enter keystroke's
        # own echo ('\r\n') -- both legitimate, ordinary read_line()
        # output with no menu in play.
        self.assertEqual(len(writer.written), 2)
        msg = json.loads(writer.written[0])
        self.assertEqual(msg['type'], 'screen')
        text = ''.join(r.get('text', '') for row in msg['rows'] for r in row['runs'])
        self.assertIn('Press Enter to continue...', text)

    def test_prompt_shows_normally_after_a_menu_has_already_been_replaced(self):
        """handle_login()'s exact shape: write("=== User Login ===")
        right before read_line("Username: ") -- the write() call must
        have already cleared _enhanced_menu_active (left over True from
        an earlier menu, e.g. the login lightbar), so this prompt shows
        normally instead of vanishing."""
        session, writer = _make_session(b'StingRay\r')
        session._enhanced_menu_active = True  # left over from the login lightbar menu
        asyncio.run(session.write('\r\n=== User Login ===\r\n\r\n'))
        self.assertFalse(session._enhanced_menu_active)

        line = asyncio.run(session.read_line('Username: '))
        self.assertEqual(line, 'StingRay')

        msgs = [json.loads(w) for w in writer.written]
        all_text = ''.join(
            r.get('text', '') for m in msgs for row in m['rows'] for r in row['runs'])
        self.assertIn('User Login', all_text)
        self.assertIn('Username: ', all_text)
        self.assertIn('StingRay', all_text)

    def test_typed_characters_echo_normally_with_no_menu_active(self):
        session, writer = _make_session(b'hi\r')
        line = asyncio.run(session.read_line('Name: '))
        self.assertEqual(line, 'hi')
        msgs = [json.loads(w) for w in writer.written]
        self.assertGreater(len(msgs), 1, 'expected prompt + per-character echo frames')


class ReadLineNonEnhancedModeTests(unittest.TestCase):
    def test_non_enhanced_mode_is_completely_unaffected(self):
        """The fix must be scoped to 'enhanced' mode only -- every
        other terminal mode keeps writing its prompt and echo exactly
        as before, regardless of _enhanced_menu_active (which is never
        even set outside 'enhanced' mode)."""
        writer = _FakeWriter()
        reader = _FakeReader(b'hi\r')
        # No forced_term_mode -- term_mode is a read-only property that
        # defaults to 'ansi' absent a TTYPE/window-size signal, which
        # this fake connection doesn't provide.
        session = BBSSession(reader, writer, config={})
        self.assertEqual(session.term_mode, 'ansi')
        line = asyncio.run(session.read_line('Name: '))
        self.assertEqual(line, 'hi')
        transcript = ''.join(writer.written)
        self.assertIn('Name: ', transcript)
        self.assertIn('h', transcript)
        self.assertIn('i', transcript)


if __name__ == '__main__':
    unittest.main()
