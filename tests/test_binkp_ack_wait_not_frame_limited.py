"""Regression test for a real live bug: a sysop's AreaFix subscription
request to a real SBBSecho hub kept getting re-sent on every single
poll, forever, and the peer replied fresh each time ("Area Management
Request" + "List of Available Areas" netmail, once per poll).

Root cause: _send_messages()'s (and _wait_got()'s) wait-for-M_GOT loop
was bounded by a fixed FRAME COUNT (`for _ in range(20)`), not wall-
clock time. A peer that talks a lot before finally acking -- e.g.
SBBSecho replying with substantial content of its own, spread across
many small DATA frames -- could exhaust that 20-frame budget with our
own GOT never reached, even though the peer had already fully received
and processed what we sent. _send_messages then (correctly, by its own
logic) treated the packet as unacknowledged and left the netmail
queued for retry -- but since the peer really had processed it, the
very next poll sent the identical request again, and the peer replied
again, forever.

Fixed by bounding the wait to wall-clock time instead of frame count,
so ANY amount of interleaved peer chatter before the real GOT still
gets fully drained rather than silently timing out mid-stream.
"""
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _cmd_payload(cmd, text=''):
    return bytes([cmd]) + text.encode('latin-1', errors='replace')


class _FakeMsg:
    def __init__(self):
        self.area = None
        self.body = 'AreaFix request'
        self.from_name = 'Sysop'
        self.to_name = 'AreaFix'
        self.subject = 'Subscribe'
        self.tear_line = None
        self.origin_line = None
        self.kludges = None
        self.seenby = None
        self.path = None
        self.chrs = 'CP437 2'
        self.msg_id = None
        self.reply_id = None
        self.to_address = '1:1/0'
        self.from_address = '1:114/30'


class AckWaitNotFrameLimitedTests(unittest.TestCase):
    def _build_client(self, reply_frames):
        from anetbbs.echomail.binkp import BinkPClient, CMD_NUL

        client = BinkPClient(host='x', port=1, our_address='1:114/30',
                             hub_address='1:114/0', password='secret')
        client._inbound_dir = '/tmp/binkp_ack_wait_test_inbound'
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

    def test_got_arriving_after_25_frames_of_peer_chatter_is_still_recognized(self):
        """25 > the old hardcoded 20-frame limit -- this is the exact
        shape of the real bug: SBBSecho talking at length before
        finally acking our file."""
        from anetbbs.echomail.binkp import CMD_NUL, CMD_GOT

        chatter = [(True, _cmd_payload(CMD_NUL, 'noise')) for _ in range(25)]
        frames = chatter + [(True, _cmd_payload(CMD_GOT, 'x'))]
        client = self._build_client(frames)

        result = client._send_messages([_FakeMsg()], data_dir='/tmp')

        self.assertEqual(result, 1,
                         'a GOT arriving after more than 20 frames of '
                         'peer chatter must still be recognized as '
                         'acceptance, not silently timed out')

    def test_wait_got_also_survives_25_frames_of_chatter(self):
        """_wait_got() (used by the hatch/TIC file-echo distribution
        path) has the identical structural fix applied."""
        from anetbbs.echomail.binkp import CMD_NUL, CMD_GOT

        chatter = [(True, _cmd_payload(CMD_NUL, 'noise')) for _ in range(25)]
        frames = chatter + [(True, _cmd_payload(CMD_GOT, 'x'))]
        client = self._build_client(frames)

        self.assertTrue(client._wait_got())

    def test_still_gives_up_eventually_if_the_peer_never_acks(self):
        """Not an unbounded wait -- a genuinely silent/hung peer must
        still time out, just on wall-clock time, not frame count.
        Patches self.timeout down so this test doesn't take 60s+."""
        from anetbbs.echomail.binkp import CMD_NUL

        # Feed enough chatter to comfortably outlast a tiny timeout
        # without ever sending a GOT.
        chatter = [(True, _cmd_payload(CMD_NUL, 'noise')) for _ in range(500)]
        client = self._build_client(chatter)
        client.timeout = 0.05

        result = client._send_messages([_FakeMsg()], data_dir='/tmp')

        self.assertEqual(result, 0,
                         'a peer that truly never acks must still be '
                         'treated as unacknowledged eventually')


if __name__ == '__main__':
    unittest.main()
