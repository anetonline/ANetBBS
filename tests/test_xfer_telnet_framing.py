"""Tests for the telnet IAC escape/unescape codec in anetbbs/features/xfer.py
(originally proposed in GitHub PR #6, andy5995/zmodem-8bit-clean) and for
the protocol gate added on top of it.

The gate matters as much as the codec itself: xfer.py's send_file()/
recv_file() are shared by telnet, SSH, and rlogin sessions (no protocol
check anywhere in bbs_ui.py before they're called). SSH/rlogin channels
have no IAC/telnet-framing concept at all -- applying the escape/unescape
codec unconditionally would "fix" telnet by corrupting SSH/rlogin
transfers instead (doubling literal 0xFF bytes in the file, writing raw
telnet negotiation bytes into the SSH channel's data stream, and
silently stripping byte sequences in uploaded binary files that happen
to resemble telnet commands). _is_telnet_session() must return False
for SSH/rlogin writer classes so those sessions pass bytes through
completely unmodified, exactly as they did before this codec existed.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anetbbs.features.xfer import (
    _telnet_escape, _TelnetUnescaper, _is_telnet_session,
    _IAC, _WILL, _WONT, _DO, _DONT, _SB, _SE, _OPT_BINARY, _OPT_SGA,
)


class TelnetEscapeTests(unittest.TestCase):
    def test_no_ff_unchanged(self):
        data = b'hello world, no special bytes here'
        self.assertEqual(_telnet_escape(data), data)

    def test_single_ff_doubled(self):
        self.assertEqual(_telnet_escape(b'\xff'), b'\xff\xff')

    def test_ff_inside_binary_data_doubled(self):
        data = b'\x01\x02\xff\x03\x04'
        self.assertEqual(_telnet_escape(data), b'\x01\x02\xff\xff\x03\x04')

    def test_consecutive_ff_bytes_all_doubled(self):
        self.assertEqual(_telnet_escape(b'\xff\xff'), b'\xff\xff\xff\xff')

    def test_empty_input(self):
        self.assertEqual(_telnet_escape(b''), b'')


class TelnetUnescaperTests(unittest.TestCase):
    def test_plain_data_passes_through(self):
        u = _TelnetUnescaper()
        self.assertEqual(u.feed(b'plain data, no telnet bytes'), b'plain data, no telnet bytes')

    def test_doubled_iac_collapses_to_single(self):
        u = _TelnetUnescaper()
        self.assertEqual(u.feed(bytes([_IAC, _IAC])), bytes([_IAC]))

    def test_doubled_iac_inside_binary_data_collapses(self):
        u = _TelnetUnescaper()
        data = bytes([0x01, 0x02, _IAC, _IAC, 0x03, 0x04])
        self.assertEqual(u.feed(data), bytes([0x01, 0x02, _IAC, 0x03, 0x04]))

    def test_will_option_command_dropped(self):
        u = _TelnetUnescaper()
        data = bytes([0x41]) + bytes([_IAC, _WILL, _OPT_BINARY]) + bytes([0x42])
        self.assertEqual(u.feed(data), bytes([0x41, 0x42]))

    def test_do_dont_wont_all_dropped(self):
        for cmd in (_WILL, _WONT, _DO, _DONT):
            u = _TelnetUnescaper()
            data = bytes([_IAC, cmd, _OPT_SGA])
            self.assertEqual(u.feed(data), b'', f'command {cmd:#x} not dropped')

    def test_subnegotiation_dropped(self):
        u = _TelnetUnescaper()
        # IAC SB <opt> <arbitrary payload> IAC SE
        data = (bytes([0x41]) + bytes([_IAC, _SB, 0x18]) + b'garbage-payload'
                + bytes([_IAC, _SE]) + bytes([0x42]))
        self.assertEqual(u.feed(data), bytes([0x41, 0x42]))

    def test_roundtrip_binary_content_with_ff_bytes(self):
        """escape() on the way out, feed() on the way back in must be a
        clean round trip for arbitrary binary content -- this is the
        actual guarantee a ZMODEM transfer depends on."""
        original = bytes(range(256)) * 4  # every byte value, several times
        escaped = _telnet_escape(original)
        u = _TelnetUnescaper()
        self.assertEqual(u.feed(escaped), original)

    def test_sequence_straddling_chunk_boundary_still_collapses(self):
        """A doubled IAC split across two feed() calls (simulating a
        4KiB read boundary landing mid-sequence) must still collapse
        correctly -- this is the whole reason _pending exists."""
        u = _TelnetUnescaper()
        first = u.feed(bytes([0x41, _IAC]))       # IAC alone, second half not here yet
        second = u.feed(bytes([_IAC, 0x42]))       # completes the doubled IAC
        self.assertEqual(first + second, bytes([0x41, _IAC, 0x42]))

    def test_will_command_straddling_chunk_boundary(self):
        u = _TelnetUnescaper()
        first = u.feed(bytes([0x41, _IAC, _WILL]))   # option byte not here yet
        second = u.feed(bytes([_OPT_BINARY, 0x42]))
        self.assertEqual(first + second, bytes([0x41, 0x42]))

    def test_subnegotiation_terminator_straddling_chunk_boundary(self):
        u = _TelnetUnescaper()
        first = u.feed(bytes([0x41, _IAC, _SB, 0x18]) + b'payload' + bytes([_IAC]))
        second = u.feed(bytes([_SE, 0x42]))
        self.assertEqual(first + second, bytes([0x41, 0x42]))

    def test_lone_trailing_iac_buffers_until_more_data(self):
        u = _TelnetUnescaper()
        out = u.feed(bytes([0x41, _IAC]))
        self.assertEqual(out, bytes([0x41]))
        # nothing lost -- the pending IAC completes once fed the rest
        out2 = u.feed(bytes([_IAC]))
        self.assertEqual(out2, bytes([_IAC]))


class IsTelnetSessionTests(unittest.TestCase):
    class _FakeWriter:
        pass

    def _session_with_writer_class(self, class_name):
        writer_cls = type(class_name, (self._FakeWriter,), {})
        session = type('FakeSession', (), {})()
        session.writer = writer_cls()
        return session

    def test_plain_telnet_writer_is_telnet(self):
        session = self._session_with_writer_class('StreamWriter')
        self.assertTrue(_is_telnet_session(session))

    def test_telnet_named_writer_is_telnet(self):
        session = self._session_with_writer_class('TelnetWriter')
        self.assertTrue(_is_telnet_session(session))

    def test_ssh_writer_is_not_telnet(self):
        session = self._session_with_writer_class('_SshStreamWriter')
        self.assertFalse(_is_telnet_session(session))

    def test_rlogin_writer_is_not_telnet(self):
        session = self._session_with_writer_class('RloginStreamWriter')
        self.assertFalse(_is_telnet_session(session))

    def test_case_insensitive(self):
        session = self._session_with_writer_class('SSHWriter')
        self.assertFalse(_is_telnet_session(session))


if __name__ == '__main__':
    unittest.main()
