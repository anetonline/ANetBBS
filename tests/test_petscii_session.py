"""Unit tests for the PETSCII terminal-mode session plumbing added to
anetbbs.core.session.BBSSession (see the "PETSCII Terminal Support
(Phase 1)" plan): forced_term_mode/forced_width overrides, the
term_mode/petscii_width properties, and write()'s new PETSCII branch
(strip ANSI, encode via petscii_codec instead of cp437).

login_screen()'s and the post-login dispatch's PETSCII branches are
exercised in tests/test_petscii_ui.py against the lighter-weight
run_petscii_menu() stub instead of a full BBSSession, since driving
login_screen()/handle_login() end-to-end needs a real DB-backed
UserManager -- out of scope for this pure-plumbing test file.
"""
import asyncio
import sys
import unittest
from pathlib import Path

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
    reader = object()  # write() never touches reader
    writer = _FakeWriter()
    session = BBSSession(reader, writer, config={}, **kwargs)
    return session, writer


class ForcedTermModeTests(unittest.TestCase):
    def test_forced_term_mode_overrides_negotiation(self):
        session, _ = _make_session(forced_term_mode='petscii')
        self.assertEqual(session.term_mode, 'petscii')

    def test_no_override_falls_back_to_normal_detection(self):
        session, _ = _make_session()
        self.assertEqual(session.term_mode, 'ansi')  # default, no TTYPE negotiated

    def test_petscii_width_defaults_to_40(self):
        session, _ = _make_session(forced_term_mode='petscii')
        self.assertEqual(session.petscii_width, 40)

    def test_petscii_width_honors_forced_width(self):
        session, _ = _make_session(forced_term_mode='petscii', forced_width=80)
        self.assertEqual(session.petscii_width, 80)

    def test_petscii_width_meaningless_outside_petscii_mode_still_has_a_value(self):
        session, _ = _make_session()
        self.assertEqual(session.petscii_width, 40)  # never raises


class WritePetsciiBranchTests(unittest.TestCase):
    def test_plain_text_routes_through_petscii_codec(self):
        # PETSCII's upper/lowercase charset inverts ASCII's letter-case
        # byte assignment (confirmed on real hardware -- see
        # petscii_codec.py's docstring), so 'Hello, World!' comes out
        # with every letter's case swapped, not identity-mapped.
        session, writer = _make_session(forced_term_mode='petscii')
        asyncio.run(session.write('Hello, World!'))
        self.assertEqual(bytes(writer.written), b'hELLO, wORLD!')

    def test_ansi_escape_sequences_are_stripped_not_passed_through(self):
        session, writer = _make_session(forced_term_mode='petscii')
        asyncio.run(session.write('\x1b[1;33mHello\x1b[0m'))
        self.assertEqual(bytes(writer.written), b'hELLO')

    def test_control_code_characters_reach_the_wire_as_real_petscii_bytes(self):
        from anetbbs.features import petscii_codec as pc
        session, writer = _make_session(forced_term_mode='petscii')
        asyncio.run(session.write(f'{pc.CLR_HOME}Hi'))
        self.assertEqual(bytes(writer.written), bytes([0x93]) + b'hI')

    def test_non_petscii_session_still_uses_cp437_encode(self):
        session, writer = _make_session()  # default term_mode == 'ansi'
        asyncio.run(session.write('Hello'))
        self.assertEqual(bytes(writer.written), b'Hello')  # ASCII subset, same either way

    def test_ascii_mode_unaffected_by_petscii_branch(self):
        # Regression guard: adding the petscii branch must not change the
        # existing 'ascii' mode's own ANSI-stripping/cp437 behavior.
        session, writer = _make_session()
        session.terminal_type = 'dumb'
        self.assertEqual(session.term_mode, 'ascii')
        asyncio.run(session.write('\x1b[1;33mHello\x1b[0m'))
        self.assertEqual(bytes(writer.written), b'Hello')


if __name__ == '__main__':
    unittest.main()
