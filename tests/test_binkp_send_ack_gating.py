"""Regression test for a real mail-loss bug found in a full-subsystem
BinkP audit: _send_messages() used to `return len(messages)`
unconditionally, no matter what the hub actually replied. poller.py's
caller then stamped every queued outbound message as sent based on
that count alone, with no further check.

A busy/unstable hub replying M_SKIP (a normal, spec-legal response --
e.g. "already have this bundle") or M_ERR, or simply never replying,
therefore silently and permanently discarded real outbound mail: no
retry, no visible error, and the poll log still read "success".

Fix: _send_messages() now only returns len(messages) when the hub
actually sends M_GOT for our packet; it returns 0 on M_SKIP, M_ERR, or
frame-exhaustion, explicitly logging that the message(s) were NOT
marked sent and will retry next poll. Matches Synchronet's own
binkp.js reference implementation, which only fires its tx_callback
(the hook that marks a file truly delivered) on M_GOT, and moves a
skipped file to a separate failed-files list instead of assuming
success.

Uses the same lightweight monkeypatch-the-client-methods pattern as
test_binkp_send_side_disconnect.py (patch _send_cmd/_send_data/
_recv_frame_logged directly rather than faking a raw socket).
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class _FakeMessage:
    """Minimal stand-in for an EchomailMessage/_NetmailAdapter ORM row --
    just enough attributes for _build_ftn_packet to run without error."""
    def __init__(self):
        self.area = None
        self.to_address = '1:114/30'
        self.from_address = '1:114/0'
        self.is_crash = False
        self.is_hold = False
        self.kludges = None
        self.chrs = 'CP437 2'
        self.msg_id = None
        self.reply_id = None
        self.seenby = None
        self.path = None
        self.body = 'test message body'
        self.tear_line = None
        self.origin_line = None
        self.from_name = 'Sysop'
        self.to_name = 'All'
        self.subject = 'Test'
        self.id = 1


class SendAckGatingTests(unittest.TestCase):
    def _build_client(self, reply_frames):
        from anetbbs.echomail.binkp import BinkPClient

        client = BinkPClient(host='x', port=1, our_address='1:114/30',
                             hub_address='1:114/0', password='secret')
        client._inbound_dir = '/tmp/binkp_test_inbound'
        client._interleaved_received = []
        client._send_cmd = lambda cmd, text='': None
        client._send_data = lambda data: None

        it = iter(reply_frames)

        def _fake_recv(*a, **kw):
            try:
                return next(it)
            except StopIteration:
                raise ConnectionError("peer closed")

        client._recv_frame_logged = _fake_recv
        return client

    def test_got_ack_marks_sent(self):
        from anetbbs.echomail.binkp import CMD_GOT
        client = self._build_client([(True, bytes([CMD_GOT]) + b'')])
        result = client._send_messages([_FakeMessage()], data_dir='/tmp')
        self.assertEqual(result, 1)

    def test_skip_does_not_mark_sent(self):
        from anetbbs.echomail.binkp import CMD_SKIP
        client = self._build_client(
            [(True, bytes([CMD_SKIP]) + b'already have it')])
        result = client._send_messages([_FakeMessage()], data_dir='/tmp')
        self.assertEqual(result, 0)

    def test_err_does_not_mark_sent(self):
        from anetbbs.echomail.binkp import CMD_ERR
        client = self._build_client(
            [(True, bytes([CMD_ERR]) + b'disk full')])
        result = client._send_messages([_FakeMessage()], data_dir='/tmp')
        self.assertEqual(result, 0)

    def test_frame_exhaustion_does_not_mark_sent(self):
        # No GOT/SKIP/ERR ever arrives -- peer just closes after some
        # unrelated frames, or the loop runs out of patience.
        client = self._build_client([])
        result = client._send_messages([_FakeMessage()], data_dir='/tmp')
        self.assertEqual(result, 0)

    def test_interleaved_file_before_got_still_marks_sent(self):
        # Peer offers us a small file WHILE we're waiting for our own
        # ack -- must be received+GOT'd inline, not treated as our ack
        # and not discarded (see _consume_inbound_file_frame).
        from anetbbs.echomail.binkp import CMD_FILE, CMD_GOT
        frames = [
            (True, bytes([CMD_FILE]) + b'peer.pkt 4 0 0'),
            (False, b'\x00\x00\x00\x00'),  # 4 junk bytes, not a real pkt
            (True, bytes([CMD_GOT]) + b''),
        ]
        client = self._build_client(frames)
        result = client._send_messages([_FakeMessage()], data_dir='/tmp')
        self.assertEqual(result, 1)

    def test_empty_messages_returns_zero_without_sending(self):
        client = self._build_client([])
        result = client._send_messages([], data_dir='/tmp')
        self.assertEqual(result, 0)


if __name__ == '__main__':
    unittest.main()
