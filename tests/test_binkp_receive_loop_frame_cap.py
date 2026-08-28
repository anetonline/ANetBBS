"""Regression test for a real live incident (2026-08-28): a real
FidoNet hub (`1:123/3003@fidonet`) had 1359 files / 66MB stuck in its
queue for 9 days, resending in full on every poll despite ANetBBS
logging every session as a clean success. Root-caused via a real Poll
Log transcript (Jerry watched a session "go through quite many of the
1000+ files" before ANetBBS's own code disconnected) plus the exact
frame math: 66MB alone needs roughly 16,000+ 4096-byte DATA frames,
before even counting the 1359 CMD_FILE headers -- more than 3x the
`for _ in range(5000)` cap `BinkPClient._receive_messages()` used to
hard-code its receive loop to. Once real transfers exceeded that count,
the loop gave up mid-session on EVERY attempt, leaving whatever was
still in-flight un-GOT-acked -- exactly why the hub's queue never
shrank.

This is the same class of bug already fixed once in this file for
_send_messages' ack-wait loop (`for _ in range(20)` -> a time-based
deadline, see that method's own docstring) -- a fixed frame-count
budget is inherently fragile against any peer/transfer bigger than the
count anticipated. Fixed here by removing the count cap entirely;
the loop is now bounded only by the per-frame 5.0s socket idle
timeout already set just above it (see test_binkp_short_idle_timeout.py
for that timeout's own regression coverage), which correctly detects a
real stall or hangup regardless of how many frames a healthy transfer
needs.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class ReceiveLoopHasNoFrameCountCapTests(unittest.TestCase):
    def test_receive_loop_survives_past_the_old_5000_frame_cap(self):
        from anetbbs.echomail.binkp import BinkPClient, CMD_NUL, CMD_EOB

        client = BinkPClient(host='x', port=1, our_address='1:114/30',
                             hub_address='1:114/0', password='secret')
        client._send_cmd = lambda cmd, text='': True

        class _FakeSocket:
            def settimeout(self, value):
                pass

        client._sock = _FakeSocket()

        # 6000 harmless info/NUL frames -- well past the old 5000-frame
        # cap -- then two CMD_EOB frames to end the session cleanly
        # (the got_eob >= 2 completion condition), standing in for a
        # real large transfer's worth of frames without needing to
        # construct real CMD_FILE/DATA sequences.
        FRAME_COUNT = 6000
        frames = [(True, bytes([CMD_NUL]))] * FRAME_COUNT
        frames += [(True, bytes([CMD_EOB]))] * 2
        call_count = {'n': 0}

        def _fake_recv():
            call_count['n'] += 1
            if call_count['n'] > len(frames):
                raise ConnectionError('test ran past all scripted frames')
            return frames[call_count['n'] - 1]

        client._recv_frame_logged = _fake_recv
        client._receive_messages(data_dir='/tmp')

        self.assertGreater(
            call_count['n'], 5000,
            'receive loop stopped at or before the old 5000-frame cap -- '
            'the fix removing that cap did not take effect')
        self.assertEqual(
            call_count['n'], FRAME_COUNT + 2,
            'expected the loop to consume every scripted frame and then '
            'stop cleanly on the second CMD_EOB, not before or after')


if __name__ == '__main__':
    unittest.main()
