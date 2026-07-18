"""Regression test for the actual root cause of a real hub (binkd/1.1a-113,
SouthEast Star) resending its entire backlog every poll for months,
despite every file being individually M_GOT-acknowledged and multiple
prior fixes to session timing and EOB handshaking.

binkp_server.py's _consume_inbound_file_frame() parsed the M_FILE
header's mtime field but never stored it, so its M_GOT reply hard-coded
the timestamp to a literal "0" instead of echoing the real value.
Direct inspection of binkd's own source (prothlp.c's tfile_cmp())
confirmed live: binkd requires an EXACT match on name, size, AND mtime
before recognizing our M_GOT as acknowledging the file it sent --
remove_from_spool() (protocol.c) is only ever reached if that
comparison returns 0. Since a real mtime (e.g. 1784314217) never equals
0, binkd's match ALWAYS failed silently -- no protocol-visible error,
unlike the sibling bug in binkp.py this mirrors -- and it never removed
the file from its outbound spool, regardless of anything else in the
session. binkp.py (the outbound poller) had the identical bug; see
test_binkp_got_ack_includes_size.py for its regression test.
"""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class ServerGotAckMtimeTests(unittest.TestCase):

    class _FakeWriter:
        def __init__(self):
            self.sent = []
        def write(self, data):
            self.sent.append(data)
        async def drain(self):
            pass

    def _decode_got(self, writer):
        import struct
        from anetbbs.echomail.binkp_server import CMD_GOT, CMD_NAMES
        for frame in writer.sent:
            word = struct.unpack('>H', frame[0:2])[0]
            length = word & 0x7FFF
            payload = frame[2:2 + length]
            if payload and payload[0] == CMD_GOT:
                return payload[1:].decode('latin-1')
        return None

    def test_got_ack_echoes_real_mtime_not_zero(self):
        from anetbbs.echomail.binkp_server import (
            _consume_inbound_file_frame, CMD_FILE,
        )
        writer = self._FakeWriter()
        state = {'name': None, 'size': 0, 'buf': bytearray()}
        peer = ('1.2.3.4', 1)

        asyncio.run(_consume_inbound_file_frame(
            True, bytes([CMD_FILE]) + b'6a5a797f.pkt 1641 1784314217 0',
            peer, writer, state, []))
        asyncio.run(_consume_inbound_file_frame(
            False, b'\x00' * 1641, peer, writer, state, []))

        got = self._decode_got(writer)
        self.assertIsNotNone(got, 'expected an M_GOT to have been sent')
        parts = got.split()
        self.assertEqual(len(parts), 3)
        self.assertEqual(parts[0], '6a5a797f.pkt')
        self.assertEqual(parts[1], '1641')
        self.assertEqual(parts[2], '1784314217',
            "the timestamp must echo the M_FILE header's real mtime, not "
            "a hard-coded 0 -- binkd's tfile_cmp() requires an exact "
            "match on this field before it will remove the file from its "
            "own outbound spool, confirmed live against binkd's own "
            "source (prothlp.c)")

    def test_got_ack_mtime_varies_per_file(self):
        """Guard against a shared/stale mtime leaking across files in the
        same batch (e.g. reusing the previous file's state entry)."""
        from anetbbs.echomail.binkp_server import (
            _consume_inbound_file_frame, CMD_FILE,
        )
        writer = self._FakeWriter()
        state = {'name': None, 'size': 0, 'buf': bytearray()}
        peer = ('1.2.3.4', 1)

        asyncio.run(_consume_inbound_file_frame(
            True, bytes([CMD_FILE]) + b'first.pkt 4 1111111111 0',
            peer, writer, state, []))
        asyncio.run(_consume_inbound_file_frame(
            False, b'\x00\x00\x00\x00', peer, writer, state, []))

        asyncio.run(_consume_inbound_file_frame(
            True, bytes([CMD_FILE]) + b'second.pkt 4 2222222222 0',
            peer, writer, state, []))
        asyncio.run(_consume_inbound_file_frame(
            False, b'\x00\x00\x00\x00', peer, writer, state, []))

        import struct
        from anetbbs.echomail.binkp_server import CMD_GOT
        got_lines = []
        for frame in writer.sent:
            word = struct.unpack('>H', frame[0:2])[0]
            length = word & 0x7FFF
            payload = frame[2:2 + length]
            if payload and payload[0] == CMD_GOT:
                got_lines.append(payload[1:].decode('latin-1'))

        self.assertEqual(len(got_lines), 2)
        self.assertEqual(got_lines[0].split()[2], '1111111111')
        self.assertEqual(got_lines[1].split()[2], '2222222222')


if __name__ == '__main__':
    unittest.main()
