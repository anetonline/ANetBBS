"""Regression tests for a real compatibility-review finding
(2026-10-22): BBSSession.read_key()/read_key_arrow() called
_read_byte_maybe_spinning() directly instead of read_raw(), skipping
handle_telnet_command()'s telnet IAC parsing entirely -- unlike
read_line()/read_password(), which both already went through read_raw()
and got this for free. A NAWS resize (or any other IAC negotiation)
arriving while a single-key "Choice:" menu or lightbar menu was active
got read one raw byte at a time with no IAC/SB/SE stripping: most bytes
got silently dropped by the pre-existing control-char checks, but
whichever byte happened to be printable ASCII (true for most real
terminal widths/heights, e.g. width 80's low byte is 0x50='P') was
treated as a real keystroke -- confirmed live to cause an unintended
menu jump (into PM Inbox) from a real NAWS(80,24) sequence sent at the
main menu, never a crash/hang, just a silent misnavigation.

Fixed by switching both functions to read_raw() -- the same primitive
read_line()/read_password() already use. No new/duplicate parsing
logic. These tests call read_key()/read_key_arrow() directly against a
fake queued reader (same fixture pattern as
test_read_line_telnet_iac_handling.py), reused here rather than
duplicated.
"""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_read_line_telnet_iac_handling import _make_session

from anetbbs.core.session import _MAX_SB_PAYLOAD_BYTES

_IAC = 0xFF
_SB = 0xFA
_SE = 0xF0
_NAWS = 0x1F
_GMCP = 0xC9


def _naws(cols, rows):
    return bytes([_IAC, _SB, _NAWS,
                  (cols >> 8) & 0xFF, cols & 0xFF,
                  (rows >> 8) & 0xFF, rows & 0xFF,
                  _IAC, _SE])


def _run(coro):
    return asyncio.run(coro)


class ReadKeyNawsHandlingTests(unittest.TestCase):
    """read_key() -- the single-keystroke "Choice:" menu primitive."""

    def test_naws_before_a_real_keypress_is_not_treated_as_a_keystroke(self):
        chunks = [bytes([b]) for b in _naws(80, 24)] + [b'M']
        session, _writer = _make_session(chunks)
        result = _run(session.read_key())
        self.assertEqual(result, 'M',
                         'the NAWS sequence must be fully consumed as '
                         'negotiation, not surfaced as a keystroke')
        self.assertEqual(session.window_size, (80, 24))

    def test_naws_with_a_printable_ascii_dimension_byte_does_not_leak(self):
        """The exact live-reproduced bug: width 80's low byte is 0x50
        ('P') -- confirmed this used to get returned as a real 'P'
        keystroke instead of being consumed as part of the negotiation."""
        chunks = [bytes([b]) for b in _naws(80, 24)] + [b'Q']
        session, _writer = _make_session(chunks)
        result = _run(session.read_key())
        self.assertEqual(result, 'Q')
        self.assertNotEqual(result, 'P',
                            'the NAWS payload\'s own printable byte must '
                            'never be mistaken for a real keystroke')

    def test_repeated_resizes_are_all_consumed_cleanly(self):
        sizes = [(80, 24), (132, 43), (40, 24), (200, 60), (1, 1), (80, 24)]
        chunks = []
        for cols, rows in sizes:
            chunks += [bytes([b]) for b in _naws(cols, rows)]
        chunks.append(b'Z')
        session, _writer = _make_session(chunks)
        result = _run(session.read_key())
        self.assertEqual(result, 'Z')
        self.assertEqual(session.window_size, (80, 24))

    def test_fragmented_naws_delivered_in_uneven_chunks(self):
        payload = _naws(132, 43) + b'K'
        chunks = [payload[0:2], payload[2:5], payload[5:7], payload[7:]]
        session, _writer = _make_session(chunks)
        result = _run(session.read_key())
        self.assertEqual(result, 'K')
        self.assertEqual(session.window_size, (132, 43))

    def test_escaped_iac_does_not_become_a_keystroke(self):
        chunks = [bytes([_IAC]), bytes([_IAC]), b'X']
        session, _writer = _make_session(chunks)
        result = _run(session.read_key())
        self.assertEqual(result, 'X',
                         'an escaped IAC IAC pair must not surface as a '
                         'keystroke of its own (matches read_line()\'s '
                         'already-reviewed behavior for this same '
                         'pre-existing, separately-tracked gap)')

    def test_oversized_unterminated_sb_at_a_single_key_menu_recovers(self):
        chunks = [bytes([_IAC]), bytes([_SB]), bytes([_GMCP])]
        chunks += [bytes([0x41])] * (_MAX_SB_PAYLOAD_BYTES + 10)
        chunks += [bytes([_IAC]), bytes([_SE]), b'G']
        session, _writer = _make_session(chunks)
        result = _run(session.read_key())
        self.assertEqual(result, 'G')
        self.assertFalse(session._telnet_sb_overflow)
        self.assertEqual(len(session.telnet_command_buffer), 0)

    def test_normal_single_key_selection_is_unaffected(self):
        session, _writer = _make_session([b'g'])
        result = _run(session.read_key())
        self.assertEqual(result, 'G')

    def test_bare_enter_still_returns_empty_string(self):
        session, _writer = _make_session([b'\r'])
        result = _run(session.read_key())
        self.assertEqual(result, '')

    def test_real_disconnect_still_raises_carrierlost(self):
        from anetbbs.core.session import CarrierLost
        session, _writer = _make_session([])  # empty queue -> EOF
        with self.assertRaises(CarrierLost):
            _run(session.read_key())


class ReadKeyArrowNawsHandlingTests(unittest.TestCase):
    """read_key_arrow() -- drives lightbar menus (e.g. the login menu's
    Up/Dn navigation screen). Same underlying bug, same fix."""

    def test_naws_before_a_real_keypress_is_not_treated_as_input(self):
        chunks = [bytes([b]) for b in _naws(80, 24)] + [b'\r']
        session, _writer = _make_session(chunks)
        result = _run(session.read_key_arrow())
        self.assertEqual(result, 'ENTER')
        self.assertEqual(session.window_size, (80, 24))

    def test_escaped_iac_does_not_become_navigation_input(self):
        chunks = [bytes([_IAC]), bytes([_IAC]), b'\r']
        session, _writer = _make_session(chunks)
        result = _run(session.read_key_arrow())
        self.assertEqual(result, 'ENTER')

    def test_real_disconnect_still_raises_carrierlost(self):
        from anetbbs.core.session import CarrierLost
        session, _writer = _make_session([])
        with self.assertRaises(CarrierLost):
            _run(session.read_key_arrow())

    def test_arrow_keys_still_work_normally(self):
        # ESC [ A = up arrow
        session, _writer = _make_session([b'\x1b', b'[', b'A'])
        result = _run(session.read_key_arrow())
        self.assertEqual(result, 'UP')


if __name__ == '__main__':
    unittest.main()
