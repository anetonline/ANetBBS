"""Regression test for a real bug found in a peer sysop's live poll log
(Firehawke, The SmallTime BBS): ANetBBS successfully received and
GOT-acknowledged 3 files during an outbound poll, but the peer's binkd
replied "ERR: M_GOT: cannot parse args" and hung up right after the
3rd GOT.

Root cause: _consume_inbound_file_frame() (used to receive files
interleaved into an outbound poll -- exactly what happened here, since
ANetBBS initiated this connection and the peer offered files back) sent
a bare "GOT: <filename>" with no size or timestamp. FTS-1026 defines
M_GOT as `filename size time`, mirroring M_FILE's own fields --
binkp_server.py (the inbound listener) already sends the correct
3-field form (`f'{name} {size} 0'`), but this file-receipt path, used
during a poll THIS side initiated, never got the same fix.

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
                         'FTS-1026, and the exact format binkp_server.py '
                         'already sends correctly -- a bare filename-only '
                         'GOT is what a real peer (binkd) rejected with '
                         '"ERR: M_GOT: cannot parse args" live')
        self.assertEqual(parts[0], '56bded08.tu0')
        self.assertEqual(parts[1], '4')

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


if __name__ == '__main__':
    unittest.main()
