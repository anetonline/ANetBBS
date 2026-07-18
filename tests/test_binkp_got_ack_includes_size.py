"""Regression test for two real bugs, both found in a peer sysop's live
poll log (Firehawke, The SmallTime BBS), in the same M_GOT-sending code:

1. ANetBBS successfully received and GOT-acknowledged 3 files during an
   outbound poll, but the peer's binkd replied "ERR: M_GOT: cannot
   parse args" and hung up right after the 3rd GOT. Root cause:
   _consume_inbound_file_frame() (used to receive files interleaved
   into an outbound poll -- exactly what happened here, since ANetBBS
   initiated this connection and the peer offered files back) sent a
   bare "GOT: <filename>" with no size or timestamp. FTS-1026 defines
   M_GOT as `filename size time`, mirroring M_FILE's own fields.

2. The fix for #1 added the missing fields, but hard-coded the
   timestamp to a literal "0" (matching binkp_server.py's own,
   identically-wrong format at the time) instead of echoing the real
   mtime the peer sent in its M_FILE header. Direct inspection of
   binkd's own source (prothlp.c's tfile_cmp()) confirmed this second
   bug live: binkd requires an EXACT match on name, size, AND mtime
   before recognizing our M_GOT as acknowledging the file it sent --
   remove_from_spool() is only ever reached if that comparison returns
   0. Since a real mtime (e.g. 1784138490) never equals 0, binkd's
   match ALWAYS failed silently (no protocol-visible error, unlike bug
   #1) and it never removed the file from its outbound spool -- the
   actual root cause of a real hub (binkd/1.1a-113) resending its
   entire backlog every poll for months, unrelated to session timing
   or EOB handshaking despite much investigation there first.

Uses the same lightweight monkeypatch-the-client-methods pattern as
test_binkp_send_ack_gating.py.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class GotAckIncludesSizeTests(unittest.TestCase):
    def _build_client(self, reply_frames):
        from anetbbs.echomail.binkp import BinkPClient

        client = BinkPClient(host='x', port=1, our_address='1:114/30',
                             hub_address='1:114/0', password='secret')
        client._inbound_dir = '/tmp/binkp_got_test_inbound'
        client._interleaved_received = []
        client.sent_cmds = []

        def _capture_send_cmd(cmd, text=''):
            client.sent_cmds.append((cmd, text))
        client._send_cmd = _capture_send_cmd
        client._send_data = lambda data: None

        it = iter(reply_frames)

        def _fake_recv(*a, **kw):
            try:
                return next(it)
            except StopIteration:
                raise ConnectionError("peer closed")

        client._recv_frame_logged = _fake_recv
        return client

    def test_got_ack_includes_filename_size_and_timestamp(self):
        from anetbbs.echomail.binkp import CMD_FILE, CMD_GOT
        client = self._build_client([])
        state = {'pending_file': None, 'pending_size': 0, 'pending_data': b''}

        client._consume_inbound_file_frame(
            True, bytes([CMD_FILE]) + b'56bded08.tu0 4 1784138490 0', state)
        client._consume_inbound_file_frame(False, b'\x00\x00\x00\x00', state)

        got_calls = [t for c, t in client.sent_cmds if c == CMD_GOT]
        self.assertEqual(len(got_calls), 1)
        parts = got_calls[0].split()
        self.assertEqual(len(parts), 3,
                         'M_GOT must send filename, size, and timestamp -- '
                         'FTS-1026 -- a bare filename-only GOT is what a '
                         'real peer (binkd) rejected with "ERR: M_GOT: '
                         'cannot parse args" live')
        self.assertEqual(parts[0], '56bded08.tu0')
        self.assertEqual(parts[1], '4')
        self.assertEqual(parts[2], '1784138490',
            "the timestamp must echo the M_FILE header's real mtime, not "
            "a hard-coded 0 -- binkd's tfile_cmp() requires an exact "
            "match on this field before it will remove the file from its "
            "own outbound spool, confirmed live against binkd's own "
            "source (prothlp.c)")

    def test_got_ack_size_matches_actual_file_size(self):
        from anetbbs.echomail.binkp import CMD_FILE, CMD_GOT
        client = self._build_client([])
        state = {'pending_file': None, 'pending_size': 0, 'pending_data': b''}

        client._consume_inbound_file_frame(
            True, bytes([CMD_FILE]) + b'bigfile.pkt 40648 1784220345 0', state)
        # Feed in exactly 40648 bytes across multiple frames, matching a
        # real multi-chunk receive.
        remaining = 40648
        while remaining > 0:
            chunk = min(4096, remaining)
            client._consume_inbound_file_frame(False, b'\x00' * chunk, state)
            remaining -= chunk

        got_calls = [t for c, t in client.sent_cmds if c == CMD_GOT]
        self.assertEqual(len(got_calls), 1)
        parts = got_calls[0].split()
        self.assertEqual(parts[1], '40648')
        self.assertEqual(parts[2], '1784220345')


if __name__ == '__main__':
    unittest.main()
