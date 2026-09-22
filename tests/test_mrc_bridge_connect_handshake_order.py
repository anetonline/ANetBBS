"""Regression test for a real gap found live (2026-09-22) chasing a
"have to /identify almost every time" report: MRCConnection.connect()
used to fire its "upstream_connected" notification -- which triggers
BridgeApp._rejoin_all_sessions(), sending a real IAMHERE+NEWROOM for
every handle that was previously in a room -- BEFORE sending our own
CAPABILITIES/BBSMETA/INFO* handshake packets, and before receive_loop()
even started listening for the hub's own reply.

Real evidence this was the actual mechanism, not genuine Trust expiry:
a real reference umrc-bridge on a separate BBS install survives its
own restarts without ever needing a fresh /identify (documented MRC
Trust genuinely lasts ~30 days there), while this bridge demanded
/identify on close to every reconnect -- and a live wire capture
showed the exact same NEWROOM packet (byte-for-byte) get rejected on
one join and accepted on another, with the only difference being
whether the handle had recently, successfully identified. The
rejoin's own NEWROOM was racing the hub's own per-connection setup of
"which BBS is this" -- sent before the hub had any CAPABILITIES/
BBSMETA/INFO* from us to resolve "Handle + BBS Name + BBS IP Address"
(the documented MRC Trust key) against.

Fixed by sending CAPABILITIES/BBSMETA/INFO* (and starting
receive_loop()) BEFORE firing the "upstream_connected" notification,
so the hub has full context on our BBS identity before any per-handle
rejoin traffic goes out.

These tests spy on the actual method calls inside connect()'s own
coroutine body rather than trying to observe wire-arrival order via a
separate reader task -- confirmed live while writing this that the
latter is racy even for a real bug fix: in a same-process test, a
tiny local drain() can return before the peer's own reader task ever
gets scheduled, so "packet recorded server-side" order doesn't
reliably reflect "packet sent" order. Everything spied on here
happens as one linear sequence of awaits within connect() itself,
which Python guarantees is ordered -- a reliable signal with no
cross-task race at all.
"""
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mrc.bridge.main import BridgeApp, MRCConnection  # noqa: E402
from mrc.bridge.db import BridgeDB  # noqa: E402


class _FakeHub:
    """A real local TCP listener standing in for the upstream MRC hub
    -- just accepts the connection and discards whatever it sends;
    only real sockets matter here (open_connection/drain need
    somewhere real to connect to), not what's received."""

    def __init__(self):
        self.server = None
        self.port = None

    async def start(self):
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self):
        self.server.close()
        try:
            await asyncio.wait_for(self.server.wait_closed(), timeout=2.0)
        except asyncio.TimeoutError:
            pass

    async def _handle(self, reader, writer):
        try:
            while True:
                chunk = await reader.read(4096)
                if not chunk:
                    break
        except (asyncio.CancelledError, ConnectionResetError):
            pass


class ConnectCallOrderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hub = _FakeHub()
        await self.hub.start()

    async def asyncTearDown(self):
        await self.hub.stop()

    async def _spy_order(self, conn, call_order, method_names):
        for name in method_names:
            original = getattr(conn, name)

            def make_wrapper(nm, orig):
                async def wrapper(*a, **kw):
                    call_order.append(nm)
                    return await orig(*a, **kw)
                return wrapper
            setattr(conn, name, make_wrapper(name, original))

    async def test_handshake_helpers_all_precede_status_callback(self):
        call_order = []

        async def status_callback(status):
            call_order.append("status_callback")

        conn = MRCConnection({
            "mrc_host": "127.0.0.1",
            "mrc_port": self.hub.port,
            "use_ssl": False,
            "bridge_bbs": "TestBBS",
            "capabilities": ["MCI", "CTCP"],
        }, status_callback=status_callback)

        await self._spy_order(
            conn, call_order,
            ["send_capabilities", "send_bbsmeta", "send_info_fields"])

        ok = await conn.connect()
        self.assertTrue(ok)

        self.assertEqual(
            call_order,
            ["send_capabilities", "send_bbsmeta", "send_info_fields", "status_callback"],
            "the hub must have every chance to learn who this BBS is "
            "(CAPABILITIES/BBSMETA/INFO*) before 'upstream_connected' "
            "is announced to the rest of the app (which triggers "
            "_rejoin_all_sessions' real per-handle NEWROOM traffic)")

        await conn.disconnect()

    async def test_receive_loop_started_before_status_callback(self):
        """Not just our own outgoing packets -- receive_loop() (which
        processes the hub's own replies, including any rejection) must
        also already be running before rejoin traffic goes out, so we
        don't send NEWROOM while blind to what the hub says back."""
        order = []

        async def status_callback(status):
            order.append("status_callback")

        conn = MRCConnection({
            "mrc_host": "127.0.0.1",
            "mrc_port": self.hub.port,
            "use_ssl": False,
            "bridge_bbs": "TestBBS",
            "capabilities": [],
        }, status_callback=status_callback)

        original_create_task = asyncio.create_task

        def spy_create_task(coro, *a, **kw):
            order.append("receive_loop_started")
            return original_create_task(coro, *a, **kw)

        with mock.patch("asyncio.create_task", spy_create_task):
            ok = await conn.connect()
        self.assertTrue(ok)

        self.assertEqual(order, ["receive_loop_started", "status_callback"])
        await conn.disconnect()


class RejoinAfterHandshakeEndToEndTests(unittest.IsolatedAsyncioTestCase):
    """The real end-to-end proof: drive an actual BridgeApp with a
    session that was in_room=True, connect for real, and confirm
    _rejoin_all_sessions' own send_packet(NEWROOM) call happens after
    the handshake helpers, not before."""

    async def asyncSetUp(self):
        self.hub = _FakeHub()
        await self.hub.start()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    async def asyncTearDown(self):
        await self.hub.stop()

    async def test_rejoin_newroom_sent_after_handshake_helpers(self):
        app = object.__new__(BridgeApp)
        app.config = {"bridge_bbs": "TestBBS"}
        app.db = BridgeDB(self._tmp.name)
        app.websockets = {111: object()}  # _live_sessions() only needs the key
        app.mrc_tcp_clients = {}
        app.join_packet_delay_ms = 0
        app.db.save_session("111", {
            "handle": "StingRay", "nick": "StingRay", "room": "lobby",
            "in_room": True,
        })

        async def sync_mystic_rooms():
            pass
        app._sync_mystic_rooms = sync_mystic_rooms

        call_order = []

        conn = MRCConnection({
            "mrc_host": "127.0.0.1",
            "mrc_port": self.hub.port,
            "use_ssl": False,
            "bridge_bbs": "TestBBS",
            "capabilities": [],
        }, status_callback=app._broadcast_bridge_status)
        app.mrc = conn

        for name in ("send_capabilities", "send_bbsmeta", "send_info_fields"):
            original = getattr(conn, name)

            def make_wrapper(nm, orig):
                async def wrapper(*a, **kw):
                    call_order.append(nm)
                    return await orig(*a, **kw)
                return wrapper
            setattr(conn, name, make_wrapper(name, original))

        original_send_packet = conn.send_packet

        async def spy_send_packet(packet):
            if "NEWROOM" in packet.upper():
                call_order.append("rejoin_newroom")
            return await original_send_packet(packet)
        conn.send_packet = spy_send_packet

        ok = await conn.connect()
        self.assertTrue(ok)

        self.assertIn("rejoin_newroom", call_order,
                      "the rejoin's own NEWROOM must actually have been sent")
        self.assertEqual(
            call_order,
            ["send_capabilities", "send_bbsmeta", "send_info_fields", "rejoin_newroom"])

        await conn.disconnect()


if __name__ == '__main__':
    unittest.main()
