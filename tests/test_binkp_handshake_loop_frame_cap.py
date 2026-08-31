"""Regression test for a real Medium-severity finding from a security/
performance audit (2026-08-31): BinkPClient._handshake()'s outbound
password-negotiation loop was still hard-capped at `for _ in range(50)`
-- the exact same "fixed frame-count budget standing in for a proper
timeout" bug shape already fixed TWICE elsewhere in this exact file
(_send_messages' GOT-wait loop, and _receive_messages' 5000-frame cap
that caused the real 2026-08-30 hub-queue incident -- see
test_binkp_receive_loop_frame_cap.py for that one's own writeup). A hub
whose verbose banner/NUL lines legitimately run past 50 lines before
ADR/OK would fail the handshake outright even though nothing is
actually wrong. Fixed the same way as its siblings: the frame-count
cap is gone; the loop is now bounded only by the socket's existing
per-frame timeout (self.timeout, set once in _connect() via
socket.create_connection(..., timeout=self.timeout), which sticks for
every subsequent recv() on that socket).
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class HandshakeLoopHasNoFrameCountCapTests(unittest.TestCase):
    def test_handshake_survives_past_the_old_50_frame_cap(self):
        from anetbbs.echomail.binkp import BinkPClient, CMD_NUL, CMD_ADR, CMD_OK

        client = BinkPClient(host='x', port=1, our_address='1:114/30',
                             hub_address='1:114/0', password='secret')
        client._send_cmd = lambda cmd, text='': True
        client._verify_remote_address = lambda text: None

        class _FakeSocket:
            def settimeout(self, value):
                pass

        client._sock = _FakeSocket()

        # 80 harmless NUL banner lines -- well past the old 50-frame
        # cap -- then a real ADR + OK to complete the handshake
        # cleanly, standing in for a verbose real hub's banner without
        # needing a real socket.
        FRAME_COUNT = 80
        frames = [(True, bytes([CMD_NUL]) + b'banner line')] * FRAME_COUNT
        frames += [(True, bytes([CMD_ADR]) + b'1:114/0'),
                  (True, bytes([CMD_OK]) + b'secure')]
        call_count = {'n': 0}

        def _fake_recv():
            call_count['n'] += 1
            if call_count['n'] > len(frames):
                raise ConnectionError('test ran past all scripted frames')
            return frames[call_count['n'] - 1]

        client._recv_frame_logged = _fake_recv
        client._handshake()  # must not raise "handshake did not complete"

        self.assertGreater(
            call_count['n'], 50,
            'handshake loop stopped at or before the old 50-frame cap -- '
            'the fix removing that cap did not take effect')
        self.assertEqual(call_count['n'], FRAME_COUNT + 2,
                         'expected the loop to consume every scripted frame '
                         'and then stop cleanly on CMD_OK, not before or after')


if __name__ == '__main__':
    unittest.main()
