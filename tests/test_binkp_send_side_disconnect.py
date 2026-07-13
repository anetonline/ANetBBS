"""Regression test for a live-reported BinkP bug: a sysop's poll to one
particular hub failed and repeated on every scheduled attempt, while two
other hubs on the same install worked fine. _receive_messages() already
treated the hub closing the connection during our *receive* calls as a
clean end of session (see test_binkp_eob_two_round_handshake.py), but the
two acknowledgement sends right next to it -- the second M_EOB and the
per-file M_GOT -- had no equivalent guard. If the hub closes its socket
in the narrow window before either send goes out, sendall() raises an
uncaught OSError that unwinds out of poll() and gets logged as a genuine
poll failure, even though the transfer itself completed successfully.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class SendSideDisconnectTests(unittest.TestCase):
    """binkp.py's BinkPClient._receive_messages()."""

    def _run(self, peer_frames, raise_on_call):
        """peer_frames: list of (is_cmd, data) to hand back from
        successive _recv_frame_logged() calls, then ConnectionError once
        exhausted. raise_on_call: 1-based _send_cmd() call number that
        should raise OSError (simulating a broken pipe) -- e.g. the
        client's own first, unconditional M_EOB (call 1) must always
        succeed; only a later ack matters for these tests."""
        from anetbbs.echomail.binkp import BinkPClient

        client = BinkPClient(host='x', port=1, our_address='1:114/30',
                             hub_address='1:114/0', password='secret')
        sent = []
        call_count = [0]

        def _fake_send(cmd, text=''):
            call_count[0] += 1
            if call_count[0] == raise_on_call:
                raise OSError("[Errno 32] Broken pipe")
            sent.append((cmd, text))
            return True

        client._send_cmd = _fake_send

        it = iter(peer_frames)

        def _fake_recv(*a, **kw):
            try:
                return next(it)
            except StopIteration:
                raise ConnectionError("peer closed")

        client._recv_frame_logged = _fake_recv
        result = client._receive_messages(data_dir='/tmp')
        return sent, result

    def test_broken_pipe_on_second_eob_does_not_raise(self):
        # Call 1 is our own unconditional first M_EOB (must succeed).
        # Peer then sends its first EOB; our reply (the second M_EOB,
        # call 2) fails because the hub has already closed the socket.
        sent, result = self._run(
            [(True, bytes([5]))], raise_on_call=2)  # 5 == CMD_EOB
        self.assertEqual(result, [])

    def test_broken_pipe_on_got_ack_does_not_raise(self):
        from anetbbs.echomail.binkp import CMD_FILE, CMD_GOT
        # Call 1 is our own unconditional first M_EOB. Peer then sends a
        # tiny complete file; our M_GOT ack (call 2) fails because the
        # hub has already closed the socket.
        frames = [
            (True, bytes([CMD_FILE]) + b'test.pkt 4 0 0'),
            (False, b'AAAA'),
        ]
        sent, result = self._run(frames, raise_on_call=2)
        self.assertEqual(result, [])
        got_sends = [c for c, t in sent if c == CMD_GOT]
        self.assertEqual(got_sends, [])


if __name__ == '__main__':
    unittest.main()
