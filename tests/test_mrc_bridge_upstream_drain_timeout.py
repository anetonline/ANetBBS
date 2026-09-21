"""Regression test for a real gap found in the same audit pass that
produced ANetBBS core's _drain_protected() fix (v1.0.98): MRCConnection
(mrc/bridge/main.py) -- the bridge's single shared connection to the
real upstream MRC hub, multiplexing every locally connected user's
chat traffic -- called `await self.writer.drain()` with no timeout in
4 places: send_packet() (every outgoing chat message), _flush_queue()
(replaying the backlog after a reconnect), connect()'s handshake send,
and stop()'s best-effort SHUTDOWN notice.

Same underlying mechanism as ANetBBS's own v1.0.87 session-hang bug:
asyncio's StreamWriter.drain() has no built-in timeout. If the hub
stops reading without actually closing the socket (a network stall or
a hub-side bug, not a clean disconnect), drain() blocks forever. Since
every user's outgoing message funnels through this one connection,
this could silently swallow whichever user's send triggered it, with
no timeout, no error, and no reconnect -- the bridge just quietly
stops delivering that user's traffic (and anyone else's sent while the
hang persists) until the process is restarted.

Every affected call site already wraps its drain() in an
`except Exception:` that does the right thing (requeue + reconnect) --
so bounding the wait with asyncio.wait_for() is the entire fix, no new
control flow needed. These tests use a writer whose drain() never
completes to prove each call site now times out and recovers instead
of hanging -- verified by reverting first (removing the wait_for
wrapper reproduces a real, unbounded hang past this test's own bound).
"""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mrc.bridge.main import MRCConnection  # noqa: E402


class _StuckWriter:
    """A writer whose drain() never completes -- simulates a hub that
    stopped reading without closing the socket."""

    def __init__(self):
        self.written = bytearray()
        self._closed = False

    def write(self, data):
        self.written += data

    async def drain(self):
        await asyncio.Event().wait()

    def close(self):
        self._closed = True

    async def wait_closed(self):
        pass


class UpstreamDrainTimeoutTests(unittest.IsolatedAsyncioTestCase):

    async def test_send_packet_stuck_drain_times_out_and_requeues(self):
        conn = MRCConnection({"mrc_drain_timeout_seconds": 0.05})
        conn.writer = _StuckWriter()
        conn.reader = object()
        conn.connected = True

        await asyncio.wait_for(conn.send_packet("~PKT~"), timeout=5)

        self.assertFalse(conn.connected)
        self.assertIsNone(conn.writer)
        self.assertIn("~PKT~", conn._send_queue)

    async def test_flush_queue_stuck_drain_times_out_and_requeues(self):
        conn = MRCConnection({"mrc_drain_timeout_seconds": 0.05})
        conn.writer = _StuckWriter()
        conn.reader = object()
        conn.connected = True
        conn._send_queue.append("~PKT~")

        await asyncio.wait_for(conn._flush_queue(), timeout=5)

        self.assertFalse(conn.connected)
        self.assertIsNone(conn.writer)
        self.assertIn("~PKT~", conn._send_queue)

    async def test_stop_stuck_shutdown_drain_does_not_hang(self):
        conn = MRCConnection({"mrc_drain_timeout_seconds": 0.05,
                               "bridge_bbs": "TestBBS"})
        conn.writer = _StuckWriter()
        conn.reader = object()
        conn.connected = True

        # Best-effort: a stuck SHUTDOWN notice must not prevent stop()
        # from completing at all.
        await asyncio.wait_for(conn.stop(), timeout=5)

        self.assertFalse(conn.connected)


if __name__ == '__main__':
    unittest.main()
