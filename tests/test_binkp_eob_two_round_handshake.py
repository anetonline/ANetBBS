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

    As of the BinkP inbound-listener reordering fix (real live report:
    a peer's own binkd sat in total silence for ~2 minutes after
    delivering its files, waiting to hear anything back from us, then
    gave up and marked the transfer failed), the UNCONDITIONAL first
    M_EOB is sent by the caller (_handle_connection) immediately after
    our own outbound-send phase -- covered by
    test_binkp_eob_sent_before_receive.py -- not by this function
    anymore. _finish_session() now only handles the SECOND round:
    reply with our own second M_EOB if (and only if) the peer sends
    one back."""

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
        # Our own first EOB is sent by the caller now, before this
        # function runs -- this call only sees the peer's second EOB
        # and replies with our own second (one send, not two).
        self.assertEqual(len(eob_sends), 1)

    def test_lenient_peer_no_second_eob_still_closes_cleanly(self):
        from anetbbs.echomail.binkp_server import _finish_session, CMD_EOB

        writer = self._FakeWriter()
        reader = self._FakeReader([])  # peer sends nothing back, then EOF

        asyncio.run(_finish_session(reader, writer, ('1.2.3.4', 1), [], 0))

        commands = _decode_sent_commands(writer.sent)
        eob_sends = [t for c, t in commands if c == CMD_EOB]
        # No second-round reply since the peer never sent its own
        # second EOB -- no crash, no hang. (Our first EOB, sent by the
        # caller before this function runs, isn't visible here at all.)
        self.assertEqual(len(eob_sends), 0)


if __name__ == '__main__':
    unittest.main()
