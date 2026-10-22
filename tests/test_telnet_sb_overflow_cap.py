"""Regression tests for the _MAX_SB_PAYLOAD_BYTES cap added to
BBSSession.handle_telnet_command() (anetbbs/core/session.py).

Real gap found in a security audit: an IAC SB <option> subnegotiation
with no terminating IAC SE grew self.telnet_command_buffer by one byte
per call forever (every real caller reads one byte at a time) and
re-scanned the whole thing from byte zero on every call looking for
IAC SE -- O(n) memory and O(n^2) total CPU for n bytes streamed,
reachable pre-authentication from a single slow attacker sending even
one byte every few seconds, with no idle-timeout to stop it since the
connection is "active" the whole time. Because every session shares one
asyncio event loop, a bad enough backlog could start starving every
other logged-in user's reads too.

These tests call handle_telnet_command() directly (not through
read_line(), which would simply hang waiting for a terminator that
never arrives in the attack scenario) and assert the fix's actual
guarantee -- that memory/state stays bounded regardless of how long an
attacker streams -- structurally (buffer length, overflow flag), not
via wall-clock timing, which would be a flaky way to prove an O(1) vs.
O(n) claim. A structural assertion is strictly stronger here: it holds
regardless of machine speed or test-runner jitter.
"""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_read_line_telnet_iac_handling import _make_session, _NAWS_RESIZE_80x24

from anetbbs.core.session import _MAX_SB_PAYLOAD_BYTES

_IAC = 0xFF
_SB = 0xFA
_SE = 0xF0
_NAWS = 0x1F
_GMCP = 0xC9  # 201 -- an option this code never actually parses the
              # payload of, used here as a stand-in "unknown/unused
              # option" so NAWS-specific parsing doesn't mask the cap.


def _run(coro):
    return asyncio.run(coro)


class NormalSubnegotiationUnaffectedTests(unittest.TestCase):
    """The cap must never fire for any real, legitimate negotiation --
    confirms the fix doesn't regress ordinary NAWS/TTYPE handling."""

    def test_naws_well_under_cap_still_updates_window_size(self):
        session, _writer = _make_session([])
        for b in _NAWS_RESIZE_80x24:
            _run(session.handle_telnet_command(bytes([b])))
        self.assertEqual(session.window_size, (80, 24))
        self.assertFalse(session._telnet_sb_overflow)
        self.assertEqual(len(session.telnet_command_buffer), 0)

    def test_small_unknown_option_payload_under_cap_resolves_cleanly(self):
        """An option this code doesn't specifically act on (GMCP) but
        whose payload is small must still be consumed structurally
        (not leaked into typed input) without tripping the cap."""
        session, _writer = _make_session([])
        seq = bytes([_IAC, _SB, _GMCP]) + b'{"ok":true}' + bytes([_IAC, _SE])
        result = bytearray()
        for b in seq:
            result += _run(session.handle_telnet_command(bytes([b])))
        result += _run(session.handle_telnet_command(b'hello'))
        self.assertEqual(bytes(result), b'hello')
        self.assertFalse(session._telnet_sb_overflow)


class OversizedUnterminatedSubnegotiationTests(unittest.TestCase):
    """The actual vulnerability scenario: IAC SB <option> followed by
    garbage that never terminates."""

    def test_buffer_never_grows_past_a_small_bound_regardless_of_stream_length(self):
        session, _writer = _make_session([])
        _run(session.handle_telnet_command(bytes([_IAC])))
        _run(session.handle_telnet_command(bytes([_SB])))
        _run(session.handle_telnet_command(bytes([_GMCP])))

        # Phase 1: stream exactly up to and past the cap. The buffer is
        # EXPECTED to ramp up to (but never past) _MAX_SB_PAYLOAD_BYTES
        # during this phase -- that ramp, bounded by a fixed constant,
        # is the correct/unchanged "accumulate, then decide" design.
        # The actual vulnerability was what happens AFTER this point.
        for _ in range(_MAX_SB_PAYLOAD_BYTES + 5):
            _run(session.handle_telnet_command(bytes([0x41])))  # 'A'
            self.assertLessEqual(
                len(session.telnet_command_buffer), _MAX_SB_PAYLOAD_BYTES + 3,
                'the pre-overflow accumulation phase must itself stay '
                'bounded by the fixed cap, not grow past it')
        self.assertTrue(session._telnet_sb_overflow,
                        'an unterminated, over-cap subnegotiation must be '
                        'marked abandoned so later bytes are cheaply '
                        'discarded instead of re-scanned')

        # Phase 2: the actual fix under test. Keep streaming for MANY
        # more bytes than the cap -- before the fix, every single one
        # of these bytes would have grown telnet_command_buffer by one
        # more byte (and cost one more full re-scan of everything seen
        # so far). After the fix, none of them should touch the buffer
        # at all -- state lives only in the single _telnet_sb_overflow
        # bool, so memory use here must be exactly O(1), not merely
        # "bounded by a large constant."
        max_buffer_during_discard = 0
        for _ in range(_MAX_SB_PAYLOAD_BYTES * 5):
            _run(session.handle_telnet_command(bytes([0x41])))
            max_buffer_during_discard = max(
                max_buffer_during_discard, len(session.telnet_command_buffer))

        self.assertEqual(
            max_buffer_during_discard, 0,
            'once abandoned, telnet_command_buffer must never be written '
            'to again -- all further discarding must be tracked by the '
            'O(1) _telnet_sb_overflow/_telnet_sb_overflow_had_iac state '
            'only, regardless of how many more bytes the attacker sends')

    def test_ordinary_typed_input_before_the_overflow_is_not_lost(self):
        session, _writer = _make_session([])
        result = bytearray()
        for c in b'hi ':
            result += _run(session.handle_telnet_command(bytes([c])))
        result += _run(session.handle_telnet_command(bytes([_IAC])))
        result += _run(session.handle_telnet_command(bytes([_SB])))
        result += _run(session.handle_telnet_command(bytes([_GMCP])))
        for _ in range(_MAX_SB_PAYLOAD_BYTES + 10):
            result += _run(session.handle_telnet_command(bytes([0x41])))
        self.assertEqual(bytes(result), b'hi ')

    def test_terminator_after_overflow_resets_state_and_resumes_normal_parsing(self):
        """Even an abandoned sequence must not permanently wedge the
        session -- once its real IAC SE eventually shows up (combined
        in one chunk here), ordinary typed input right after it must
        come through untouched."""
        session, _writer = _make_session([])
        _run(session.handle_telnet_command(bytes([_IAC, _SB, _GMCP])))
        for _ in range(_MAX_SB_PAYLOAD_BYTES + 10):
            _run(session.handle_telnet_command(bytes([0x41])))
        self.assertTrue(session._telnet_sb_overflow)

        result = _run(session.handle_telnet_command(
            bytes([_IAC, _SE]) + b'ok'))
        self.assertEqual(result, b'ok')
        self.assertFalse(session._telnet_sb_overflow)
        self.assertEqual(len(session.telnet_command_buffer), 0)

    def test_terminator_split_exactly_across_the_overflow_call_boundary(self):
        """The one genuinely tricky case: IAC lands as the very last
        byte discarded in one call, SE as the very first byte of the
        next -- the dominant real-world read pattern for this module
        is one byte per call, so this must still be recognized as the
        terminator rather than silently discarding every byte of the
        session forever afterward."""
        session, _writer = _make_session([])
        _run(session.handle_telnet_command(bytes([_IAC, _SB, _GMCP])))
        for _ in range(_MAX_SB_PAYLOAD_BYTES + 10):
            _run(session.handle_telnet_command(bytes([0x41])))
        self.assertTrue(session._telnet_sb_overflow)

        # IAC alone first -- must not resolve yet, and must remember it.
        mid = _run(session.handle_telnet_command(bytes([_IAC])))
        self.assertEqual(mid, b'')
        self.assertTrue(session._telnet_sb_overflow)
        self.assertTrue(session._telnet_sb_overflow_had_iac)

        # SE alone next -- must now resolve, and subsequent ordinary
        # bytes must parse normally again.
        result = _run(session.handle_telnet_command(bytes([_SE])))
        self.assertEqual(result, b'')
        self.assertFalse(session._telnet_sb_overflow)

        followed = _run(session.handle_telnet_command(b'done'))
        self.assertEqual(followed, b'done')

    def test_a_stray_iac_during_overflow_discard_does_not_falsely_match(self):
        """A lone IAC byte that ISN'T followed by SE (e.g. it's itself
        garbage payload, or starts an unrelated command) must not be
        mistaken for the terminator -- only an actual IAC,SE pair
        (combined or split across exactly one call boundary) may clear
        the overflow state."""
        session, _writer = _make_session([])
        _run(session.handle_telnet_command(bytes([_IAC, _SB, _GMCP])))
        for _ in range(_MAX_SB_PAYLOAD_BYTES + 10):
            _run(session.handle_telnet_command(bytes([0x41])))
        self.assertTrue(session._telnet_sb_overflow)

        _run(session.handle_telnet_command(bytes([_IAC])))
        self.assertTrue(session._telnet_sb_overflow_had_iac)
        # Followed by something that is NOT SE.
        _run(session.handle_telnet_command(bytes([0x42])))  # 'B'
        self.assertTrue(session._telnet_sb_overflow,
                        'still discarding -- that was not the terminator')
        self.assertFalse(session._telnet_sb_overflow_had_iac)


class SingleChunkBoundedProcessingTests(unittest.TestCase):
    """handle_telnet_command()'s real caller always hands it one byte
    per call (confirmed in the module's own docstring/history), but the
    cap's cost guarantee must hold per CALL regardless of how large a
    single chunk is, not only "eventually, across many small calls" --
    confirms the fix doesn't just move the O(n) cost into a single big
    call instead of spreading it across many small ones."""

    def test_one_huge_unterminated_chunk_resolves_in_a_single_call(self):
        """A single call carrying far more than the cap, with no
        terminator anywhere in it, must still finish (not hang/spin)
        and must leave state exactly as the byte-by-byte case does --
        O(1) buffer, overflow flagged."""
        session, _writer = _make_session([])
        huge_payload = bytes([0x41]) * (_MAX_SB_PAYLOAD_BYTES * 50)
        result = _run(session.handle_telnet_command(
            bytes([_IAC, _SB, _GMCP]) + huge_payload))
        self.assertEqual(result, b'')
        self.assertTrue(session._telnet_sb_overflow)
        self.assertEqual(len(session.telnet_command_buffer), 0,
                         'a single oversized chunk must not get carried '
                         'forward wholesale either -- same O(1) guarantee '
                         'as the byte-by-byte case')

    def test_one_huge_chunk_containing_overflow_then_terminator_then_text(self):
        """Everything -- the SB start, far more garbage than the cap,
        the eventual IAC SE, and ordinary trailing text -- arriving in
        ONE single call must still be parsed correctly in one pass:
        the trailing text must come through, and nothing from the
        abandoned payload must leak into it."""
        session, _writer = _make_session([])
        huge_garbage = bytes([0x41]) * (_MAX_SB_PAYLOAD_BYTES * 10)
        chunk = (bytes([_IAC, _SB, _GMCP]) + huge_garbage
                + bytes([_IAC, _SE]) + b'hello')
        result = _run(session.handle_telnet_command(chunk))
        self.assertEqual(result, b'hello')
        self.assertFalse(session._telnet_sb_overflow)
        self.assertEqual(len(session.telnet_command_buffer), 0)


class MultipleOverflowsInOneSessionTests(unittest.TestCase):
    """The cap/discard mechanism must be fully reusable, not a one-shot
    latch -- a session that survives one abandoned subnegotiation (the
    normal case: most clients never do this even once) must handle a
    second one identically."""

    def test_second_overflow_after_recovering_from_the_first_behaves_identically(self):
        session, _writer = _make_session([])
        for _ in range(2):
            _run(session.handle_telnet_command(bytes([_IAC, _SB, _GMCP])))
            for _ in range(_MAX_SB_PAYLOAD_BYTES + 10):
                _run(session.handle_telnet_command(bytes([0x41])))
            self.assertTrue(session._telnet_sb_overflow)
            result = _run(session.handle_telnet_command(
                bytes([_IAC, _SE]) + b'ok'))
            self.assertEqual(result, b'ok')
            self.assertFalse(session._telnet_sb_overflow)


def _chunks_of_varied_sizes(data, sizes):
    """Splits data into consecutive pieces of the given sizes (cycled
    as needed), simulating realistic TCP fragmentation where a chunk
    boundary can land anywhere -- not just "1 byte" or "all of it"."""
    out = []
    i = 0
    size_idx = 0
    while i < len(data):
        n = sizes[size_idx % len(sizes)]
        out.append(data[i:i + n])
        i += n
        size_idx += 1
    return out


class FragmentedDeliveryVariationTests(unittest.TestCase):
    """Follow-up review asked specifically for "fragmented network
    input" coverage beyond the existing 1-byte-at-a-time and single-
    huge-chunk extremes already tested above -- chunk boundaries that
    don't align neatly with the cap, the option byte, or the
    terminator."""

    def test_oversized_unterminated_sequence_in_odd_sized_fragments(self):
        session, _writer = _make_session([])
        garbage = bytes([_IAC, _SB, _GMCP]) + bytes([0x41]) * (
            _MAX_SB_PAYLOAD_BYTES + 37)
        result = bytearray()
        for chunk in _chunks_of_varied_sizes(garbage, (3, 7, 5, 11, 1, 23)):
            result += _run(session.handle_telnet_command(chunk))
        self.assertEqual(bytes(result), b'')
        self.assertTrue(session._telnet_sb_overflow)
        self.assertEqual(len(session.telnet_command_buffer), 0)

    def test_terminator_and_trailing_text_in_odd_sized_fragments_after_overflow(self):
        session, _writer = _make_session([])
        garbage = bytes([_IAC, _SB, _GMCP]) + bytes([0x41]) * (
            _MAX_SB_PAYLOAD_BYTES + 37)
        for chunk in _chunks_of_varied_sizes(garbage, (3, 7, 5, 11, 1, 23)):
            _run(session.handle_telnet_command(chunk))
        self.assertTrue(session._telnet_sb_overflow)

        tail = bytes([_IAC, _SE]) + b'the rest of the session is fine'
        result = bytearray()
        for chunk in _chunks_of_varied_sizes(tail, (2, 9, 4, 17)):
            result += _run(session.handle_telnet_command(chunk))
        self.assertEqual(bytes(result), b'the rest of the session is fine')
        self.assertFalse(session._telnet_sb_overflow)

    def test_naws_resize_delivered_in_two_uneven_fragments(self):
        """A legitimate negotiation split at an arbitrary (non-byte-
        by-byte) boundary -- e.g. the OS delivers the first half of a
        NAWS sequence in one read and the rest in a second."""
        session, _writer = _make_session([])
        result = bytearray()
        result += _run(session.handle_telnet_command(
            _NAWS_RESIZE_80x24[:4]))
        result += _run(session.handle_telnet_command(
            _NAWS_RESIZE_80x24[4:]))
        self.assertEqual(bytes(result), b'')
        self.assertEqual(session.window_size, (80, 24))
        self.assertFalse(session._telnet_sb_overflow)


class EscapedIacByteCharacterizationTests(unittest.TestCase):
    """Characterizes (does not newly introduce or fix) a PRE-EXISTING,
    separately-flagged gap unrelated to this patch: RFC 854 requires a
    literal 0xFF DATA byte to be escaped as IAC IAC, with the receiver
    collapsing it back to one 0xFF. This code's generic IAC-command
    fallthrough (`i += 2; continue` for any IAC followed by something
    that isn't WILL/WONT/DO/DONT/SB) silently drops BOTH bytes instead
    -- true before this patch and still true after it, since this
    patch only touches the SB-subnegotiation branch. These tests pin
    that existing behavior so a future change to the SB cap can't
    silently alter it one way or the other without a test noticing,
    and so a future reader doesn't mistake an SB-cap-era regression
    for this separate, pre-existing, already-disclosed gap (or vice
    versa). Not a security issue on its own: no BBS login/menu/command
    flow ever needs a literal 0xFF in typed input."""

    def test_escaped_iac_outside_any_subnegotiation_is_dropped_not_collapsed(self):
        session, _writer = _make_session([])
        result = _run(session.handle_telnet_command(bytes([_IAC, _IAC])))
        self.assertEqual(result, b'',
                         'pre-existing behavior: IAC IAC currently drops '
                         'both bytes rather than collapsing to one literal '
                         '0xFF -- unaffected by the SB-cap fix either way')

    def test_escaped_iac_pair_does_not_trip_or_interact_with_the_sb_cap(self):
        """An IAC IAC pair appearing OUTSIDE a subnegotiation, right
        before an unrelated SB sequence, must not leave any residue
        that confuses the cap/overflow machinery for the SB sequence
        that follows."""
        session, _writer = _make_session([])
        result = bytearray()
        result += _run(session.handle_telnet_command(bytes([_IAC, _IAC])))
        for b in _NAWS_RESIZE_80x24:
            result += _run(session.handle_telnet_command(bytes([b])))
        self.assertEqual(bytes(result), b'')
        self.assertEqual(session.window_size, (80, 24))
        self.assertFalse(session._telnet_sb_overflow)


class MalformedSequenceEdgeCaseTests(unittest.TestCase):
    """Additional malformed/boundary shapes beyond the core overflow
    scenario, per a follow-up review's explicit request to cover
    "malformed sequences" and "recovery after rejected input" more
    broadly."""

    def test_sb_with_no_option_byte_before_data_ends_is_unaffected_by_the_cap(self):
        """IAC SB with absolutely nothing after it (not even an option
        byte) hits the EARLIER early-return path in the function, well
        before the cap/overflow logic is ever reached -- confirms that
        pre-existing path still works unmodified and isn't affected by
        the new state."""
        session, _writer = _make_session([])
        result = _run(session.handle_telnet_command(bytes([_IAC, _SB])))
        self.assertEqual(result, b'')
        self.assertFalse(session._telnet_sb_overflow)
        self.assertEqual(bytes(session.telnet_command_buffer),
                         bytes([_IAC, _SB]))
        # Completing it normally afterward must still work.
        result2 = _run(session.handle_telnet_command(
            bytes([_GMCP, _IAC, _SE]) + b'ok'))
        self.assertEqual(result2, b'ok')
        self.assertFalse(session._telnet_sb_overflow)

    def test_a_full_will_triplet_appearing_mid_discard_is_just_garbage(self):
        """A well-formed IAC WILL <option> triplet, if it happens to
        appear inside an already-abandoned subnegotiation's garbage
        stream, must be treated as inert discarded bytes -- not
        misinterpreted as a real option negotiation (which would
        incorrectly fire write()/_drain_protected() side effects from
        inside what should be a pure discard path)."""
        session, _writer = _make_session([])
        _run(session.handle_telnet_command(bytes([_IAC, _SB, _GMCP])))
        for _ in range(_MAX_SB_PAYLOAD_BYTES + 10):
            _run(session.handle_telnet_command(bytes([0x41])))
        self.assertTrue(session._telnet_sb_overflow)

        # A full WILL ECHO triplet, still inside the discard stream.
        WILL = 0xFB
        ECHO = 0x01
        written_before = bytes(_writer.written)
        _run(session.handle_telnet_command(bytes([_IAC, WILL, ECHO])))
        self.assertTrue(session._telnet_sb_overflow,
                        'a WILL triplet mid-discard must not be treated as '
                        'a real negotiation or clear the overflow state')
        self.assertEqual(bytes(_writer.written), written_before,
                         'must not have written any reply -- discard mode '
                         'only ever scans for the real terminator')

        result = _run(session.handle_telnet_command(bytes([_IAC, _SE]) + b'ok'))
        self.assertEqual(result, b'ok')
        self.assertFalse(session._telnet_sb_overflow)

    def test_rejected_oversized_password_like_input_recovers_for_the_next_read(self):
        """Broader "recovery after rejected input" check: after an
        abandoned subnegotiation is cleared, completely ordinary
        multi-character input (as read_line() would hand off a whole
        typed line) must be unaffected -- the overflow mechanism must
        leave no residue that corrupts subsequent ordinary reads."""
        session, _writer = _make_session([])
        _run(session.handle_telnet_command(bytes([_IAC, _SB, _GMCP])))
        for _ in range(_MAX_SB_PAYLOAD_BYTES + 10):
            _run(session.handle_telnet_command(bytes([0x41])))
        _run(session.handle_telnet_command(bytes([_IAC, _SE])))
        self.assertFalse(session._telnet_sb_overflow)

        result = bytearray()
        for c in b'perfectly ordinary input\r':
            result += _run(session.handle_telnet_command(bytes([c])))
        self.assertEqual(bytes(result), b'perfectly ordinary input\r')


if __name__ == '__main__':
    unittest.main()
