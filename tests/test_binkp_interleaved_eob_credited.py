"""Regression test for a real, live-caught BinkP interop bug: a peer
(SouthEast Star, running binkd/1.1a-113/Linux) resent its ENTIRE inbound
netmail/echomail backlog on every single poll, forever, despite ANetBBS
correctly M_GOT-acknowledging every single file every time.

Root cause, found by comparing a real complete session transcript
against the FSP-1011 (binkp) spec: a spec-compliant peer sends its own
M_EOB the instant its own outbound queue empties (Table 5's Transmit
Routine, "No more files -> Send M_EOB") -- unconditionally, NOT gated on
waiting for our GOT, and NOT gated on us having sent our own EOB first.
ANetBBS's client session is phase-separated (BinkPClient.poll(): send
our own messages/hatch files and wait for GOT/SKIP/ERR via
_send_messages()/_wait_got(), THEN call _receive_messages(), which sends
OUR OWN M_EOB and only THEN starts waiting for the peer's).

Those two ack-wait loops (_send_messages, _wait_got) only special-cased
CMD_GOT/CMD_SKIP/CMD_ERR before falling through to the shared
_consume_inbound_file_frame() helper for anything else -- and that
helper only recognized CMD_FILE, silently discarding CMD_EOB (and any
other unrecognized command) with zero trace. So a peer's own M_EOB,
sent early and entirely per spec, arriving while we were still in our
own send phase, vanished. _receive_messages() then started its own
EOB-count loop from zero and waited (up to 5000 frames) for an EOB the
peer had already sent once and had no reason to ever repeat unprompted
-- from the PEER's own side, FSP-1011 6.3 requires it to have RECEIVED
our M_EOB before ITS session counts as successfully completed, and since
our side never progressed the handshake either, neither side's session
ever cleanly finished, so the peer requeued and resent its entire
backlog on the very next poll.

Fixed by having _consume_inbound_file_frame() record any CMD_EOB it
sees (self._interleaved_eob_count) instead of dropping it, and having
_receive_messages() seed its own got_eob counter from that count instead
of always starting at zero.
"""
import sys
import unittest
from pathlib import Path

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


class InterleavedEobCreditedTests(unittest.TestCase):
    def _build_client(self, reply_frames):
        from anetbbs.echomail.binkp import BinkPClient

        client = BinkPClient(host='x', port=1, our_address='1:114/30',
                             hub_address='1:114/0', password='secret')
        client._inbound_dir = '/tmp/binkp_interleaved_eob_test_inbound'
        client.sent_cmds = []

        def _capture_send_cmd(cmd, text=''):
            client.sent_cmds.append((cmd, text))
            return True
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

    def test_eob_seen_during_send_phase_is_not_dropped(self):
        """The exact real-world shape: the peer's own CMD_EOB arrives
        interleaved (via _consume_inbound_file_frame) while we're still
        waiting for GOT on our own outbound file -- it must be recorded,
        not silently discarded."""
        from anetbbs.echomail.binkp import CMD_EOB, CMD_GOT

        frames = [(True, _cmd_payload(CMD_EOB)), (True, _cmd_payload(CMD_GOT, 'x'))]
        client = self._build_client(frames)

        result = client._send_messages([_FakeMsg()], data_dir='/tmp')

        self.assertEqual(result, 1, 'our own GOT must still be recognized')
        self.assertEqual(
            client._interleaved_eob_count, 1,
            "the peer's early CMD_EOB must be recorded, not dropped")

    def test_receive_messages_credits_the_early_eob_instead_of_rewaiting(self):
        """End-to-end: _receive_messages() must treat an EOB already
        seen during the send phase as satisfying round 1 of the 2-round
        handshake, completing after the peer's single early EOB plus our
        own reply -- not hang waiting for a second peer EOB that a real
        peer (having already sent its one EOB before we even reached
        this method) has no reason to send again."""
        from anetbbs.echomail.binkp import CMD_EOB

        client = self._build_client([])  # peer sends nothing more, then closes
        client._interleaved_eob_count = 1  # as if seen during send phase

        result = client._receive_messages(data_dir='/tmp')

        self.assertEqual(result, [])  # clean, no hang, no exception
        eob_sends = [t for c, t in client.sent_cmds if c == CMD_EOB]
        # Our unconditional first EOB, plus our second-round reply since
        # got_eob (seeded to 1) satisfied "peer sent at least one".
        self.assertEqual(len(eob_sends), 2)

    def test_two_early_eobs_skip_the_wait_loop_entirely(self):
        """If the peer somehow got both EOB rounds in before we even
        reached _receive_messages (e.g. a very fast, very chatty peer),
        there must be nothing left to wait for -- no blocking recv call
        at all once got_eob is already >= 2."""
        client = self._build_client([])
        client._interleaved_eob_count = 2

        recv_called = []
        orig_recv = client._recv_frame_logged
        def _tracking_recv(*a, **kw):
            recv_called.append(True)
            return orig_recv(*a, **kw)
        client._recv_frame_logged = _tracking_recv

        result = client._receive_messages(data_dir='/tmp')

        self.assertEqual(result, [])
        self.assertEqual(recv_called, [],
                         'no frames should be read when both EOB rounds '
                         'were already satisfied before this method ran')

    def test_wait_got_also_credits_interleaved_eob(self):
        """_wait_got() (used by the hatch/TIC file-echo distribution
        path) shares the same _consume_inbound_file_frame() helper and
        must exhibit the identical fix."""
        from anetbbs.echomail.binkp import CMD_EOB, CMD_GOT

        frames = [(True, _cmd_payload(CMD_EOB)), (True, _cmd_payload(CMD_GOT, 'x'))]
        client = self._build_client(frames)

        self.assertTrue(client._wait_got())
        self.assertEqual(client._interleaved_eob_count, 1)

    def test_no_interleaved_eob_behaves_exactly_as_before(self):
        """Baseline: when the peer never sends an early EOB, behavior is
        unchanged from the existing two-round-handshake tests -- one EOB
        from the peer still provokes exactly one reply from us."""
        from anetbbs.echomail.binkp import CMD_EOB

        client = self._build_client([(True, _cmd_payload(CMD_EOB))])

        result = client._receive_messages(data_dir='/tmp')

        self.assertEqual(result, [])
        eob_sends = [t for c, t in client.sent_cmds if c == CMD_EOB]
        self.assertEqual(len(eob_sends), 2)


if __name__ == '__main__':
    unittest.main()
