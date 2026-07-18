"""Regression test for a live-caught BinkP interop bug: a real binkd
FidoNet hub (binkp.pharcyde.org) reported every send from ANetBBS as a
FAILED transfer -- "connection closed by foreign host" immediately
after the file itself arrived byte-perfect and fully acknowledged
(M_GOT). Comparing against Synchronet's own mature BinkP
implementation (JSBinkP, exec/load/binkp.js) turned up the root cause:
binkp/1.1 (which ANetBBS's own VER line advertises) expects a
two-round M_EOB handshake -- once each side has both sent and received
at least one M_EOB, it sends a second M_EOB before the session is
considered cleanly finished. ANetBBS sent M_EOB exactly once on both
the client (binkp.py) and server (binkp_server.py) side, and closed
the socket the instant it saw the peer's *first* M_EOB. A strict,
spec-compliant peer like real binkd is left waiting for a second round
that never comes, and reads the subsequent abrupt socket close as an
unexpected mid-session disconnect -- even though the actual file
transfer completed successfully just before it.
"""
import asyncio
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _decode_sent_commands(raw_frames):
    out = []
    for frame in raw_frames:
        word = struct.unpack('>H', frame[0:2])[0]
        length = word & 0x7FFF
        payload = frame[2:2 + length]
        cmd = payload[0]
        text = payload[1:].decode('latin-1', errors='replace')
        out.append((cmd, text))
    return out


class ClientTwoRoundEobTests(unittest.TestCase):
    """binkp.py's BinkPClient._receive_messages()."""

    def _run(self, peer_frames):
        """peer_frames: list of (is_cmd, cmd_or_None, text_or_bytes) to
        hand back from successive _recv_frame_logged() calls; after the
        list is exhausted, raise ConnectionError (simulating the peer
        closing the socket, same as a real recv() returning 0 bytes)."""
        from anetbbs.echomail.binkp import BinkPClient, CMD_EOB

        client = BinkPClient(host='x', port=1, our_address='1:114/30',
                             hub_address='1:114/0', password='secret')
        sent = []
        client._send_cmd = lambda cmd, text='': sent.append((cmd, text)) or True

        it = iter(peer_frames)

        def _fake_recv(*a, **kw):
            try:
                return next(it)
            except StopIteration:
                raise ConnectionError("peer closed")

        client._recv_frame_logged = _fake_recv
        result = client._receive_messages(data_dir='/tmp')
        return sent, result

    def test_sends_second_eob_after_peer_sends_first(self):
        """Strict peer: sends exactly one M_EOB, then closes (simulating
        real binkd's behaviour once it's satisfied with the round-trip
        and has nothing more to send). We must reply with our own
        second M_EOB before the peer's close is observed."""
        from anetbbs.echomail.binkp import CMD_EOB
        sent, _ = self._run([(True, bytes([CMD_EOB]))])
        eob_sends = [t for c, t in sent if c == CMD_EOB]
        # One at the top of _receive_messages() (unconditional first
        # round), one in reply to the peer's EOB.
        self.assertEqual(len(eob_sends), 2)

    def test_breaks_cleanly_after_two_peer_eobs(self):
        """Peer does the full round-trip itself: sends EOB, then (after
        seeing ours) sends a second EOB. We must not send a third."""
        from anetbbs.echomail.binkp import CMD_EOB
        sent, _ = self._run([(True, bytes([CMD_EOB])), (True, bytes([CMD_EOB]))])
        eob_sends = [t for c, t in sent if c == CMD_EOB]
        self.assertEqual(len(eob_sends), 2)

    def test_lenient_peer_single_eob_then_close_still_returns_cleanly(self):
        """A peer that never does the second round (just closes after
        its first EOB, or after receiving ours) must not raise or hang
        -- the ConnectionError from the closed socket is caught and
        treated as a clean end, same as before this fix."""
        sent, result = self._run([])  # peer closes immediately
        self.assertEqual(result, [])  # no exception, empty parsed list

    def test_proactive_second_eob_sent_even_if_hub_never_replies_at_all(self):
        """The actual regression this fix targets on the dialing-out
        side: a real hub (binkd/1.1a-113, confirmed live) that never
        sends an explicit M_EOB back at all -- not even after a long
        silence -- must still get OUR OWN post-transfer confirmatory
        EOB, sent proactively, not only as a reaction to seeing one
        from the hub first. Before this fix, a hub like this only ever
        got our SINGLE, pre-transfer EOB (sent unconditionally at the
        top of this method, before any real file activity), which per
        binkp/1.1 gets voided the instant that activity follows it --
        leaving the hub with no confirmation the transfer was actually
        done, regardless of how many files were individually
        M_GOT-acknowledged. Mirrors the identical server-side test."""
        sent, _ = self._run([])  # hub sends nothing back at all, then closes
        from anetbbs.echomail.binkp import CMD_EOB
        eob_sends = [t for c, t in sent if c == CMD_EOB]
        self.assertGreaterEqual(len(eob_sends), 2,
            "_receive_messages() must proactively send a second EOB "
            "even when the hub never sends one back at all")

    def test_does_not_send_a_third_eob(self):
        """Guard against an infinite/runaway EOB ping-pong -- once we've
        sent two, a third peer EOB must not provoke a fourth send."""
        from anetbbs.echomail.binkp import CMD_EOB
        sent, _ = self._run([
            (True, bytes([CMD_EOB])),
            (True, bytes([CMD_EOB])),
        ])
        eob_sends = [t for c, t in sent if c == CMD_EOB]
        self.assertLessEqual(len(eob_sends), 2)


class ServerTwoRoundEobTests(unittest.TestCase):
    """binkp_server.py's _finish_session() (extracted end-of-batch step).

    The UNCONDITIONAL first M_EOB (round 1) is sent by the caller
    (_handle_connection) immediately after our own outbound-send phase
    -- covered by test_binkp_eob_sent_before_receive.py -- before this
    function ever runs.

    _finish_session() itself now ALWAYS sends its own second,
    POST-transfer M_EOB proactively, unconditionally, as its first
    action -- it no longer only reacts to the peer sending one back.
    Real live report: a real peer (binkd/1.1a-113) never sends an
    explicit M_EOB at all in these sessions, just goes silent for its
    own grace period and closes -- per binkp/1.1 (binkp11.txt), a
    session only counts as successfully finished once a round happens
    where NEITHER side sends nor receives any command between two
    consecutive EOB exchanges, and our round-1 EOB (sent before any
    real activity) can never by itself satisfy that; only sending a
    fresh, unconditional round-2 EOB after receiving does. Waiting
    passively to react to the peer's own second EOB left real,
    spec-compliant-but-untrusting peers waiting on a confirmation we
    never sent, so they never dequeued their outbound backlog despite
    every file being individually M_GOT-acknowledged."""

    class _FakeWriter:
        def __init__(self):
            self.sent = []
        def write(self, data):
            self.sent.append(data)
        async def drain(self):
            pass
        def close(self):
            pass
        async def wait_closed(self):
            pass

    class _FakeReader:
        """Backs both readexactly() (used by _recv_frame() to read the
        peer's second M_EOB) and read() (used by the post-EOB drain
        loop) from the same byte buffer, matching how a real
        asyncio.StreamReader behaves -- readexactly() raises
        IncompleteReadError at EOF, read() returns b''."""
        def __init__(self, frames):
            self._buf = b''.join(frames)
            self._pos = 0

        async def readexactly(self, n):
            remaining = len(self._buf) - self._pos
            if remaining < n:
                partial = self._buf[self._pos:]
                self._pos = len(self._buf)
                raise asyncio.IncompleteReadError(partial=partial, expected=n)
            data = self._buf[self._pos:self._pos + n]
            self._pos += n
            return data

        async def read(self, n):
            data = self._buf[self._pos:self._pos + n]
            self._pos += len(data)
            return data

    def _build_eob_frame(self):
        from anetbbs.echomail.binkp_server import CMD_EOB, _build_cmd
        return _build_cmd(CMD_EOB)

    def test_sends_second_eob_when_peer_sends_one_back(self):
        from anetbbs.echomail.binkp_server import _finish_session, CMD_EOB

        writer = self._FakeWriter()
        reader = self._FakeReader([self._build_eob_frame()])

        asyncio.run(_finish_session(reader, writer, ('1.2.3.4', 1), [], 0))

        commands = _decode_sent_commands(writer.sent)
        eob_sends = [t for c, t in commands if c == CMD_EOB]
        # One unconditional, proactive send at the top of this function,
        # plus one more in reply to the peer's own EOB arriving.
        self.assertEqual(len(eob_sends), 2)

    def test_lenient_peer_no_second_eob_still_closes_cleanly(self):
        from anetbbs.echomail.binkp_server import _finish_session, CMD_EOB

        writer = self._FakeWriter()
        reader = self._FakeReader([])  # peer sends nothing back, then EOF

        asyncio.run(_finish_session(reader, writer, ('1.2.3.4', 1), [], 0))

        commands = _decode_sent_commands(writer.sent)
        eob_sends = [t for c, t in commands if c == CMD_EOB]
        # The peer never sends anything back (or closes immediately) --
        # but we must still have sent our own unconditional, proactive
        # EOB as this function's first action, regardless. No crash, no
        # hang either way.
        self.assertEqual(len(eob_sends), 1)

    def test_proactive_eob_sent_even_if_peer_never_replies_at_all(self):
        """The actual regression this fix targets: a real peer
        (binkd/1.1a-113, confirmed live) that never sends an explicit
        M_EOB at all -- not even after a long silence -- must still
        get OUR OWN post-transfer EOB, sent unconditionally, not only
        as a reaction to seeing one from the peer first. Before this
        fix, a peer like this got ZERO EOBs from _finish_session() and
        was left to conclude the session was never properly confirmed,
        regardless of how many files were individually GOT-acknowledged."""
        from anetbbs.echomail.binkp_server import _finish_session, CMD_EOB

        writer = self._FakeWriter()
        # Peer sends nothing at all after its files -- EOF immediately,
        # simulating a peer that just goes silent then closes.
        reader = self._FakeReader([])

        asyncio.run(_finish_session(reader, writer, ('1.2.3.4', 1),
                                    [('some.pkt', b'x')], 0))

        commands = _decode_sent_commands(writer.sent)
        eob_sends = [t for c, t in commands if c == CMD_EOB]
        self.assertGreaterEqual(len(eob_sends), 1,
            "_finish_session() must proactively send its own EOB even "
            "when the peer never sends one back at all")


if __name__ == '__main__':
    unittest.main()
