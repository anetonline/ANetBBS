"""Regression tests for mrc/bridge/main.py's raw-TCP listener
(BridgeApp.handle_mrc_tcp_connection and friends).

Lets a real umrc-client (github.com/codefenix-dev/uMRC) connect
directly to ANetBBS's own MRC bridge in place of the separate
umrc-bridge daemon, with zero changes to uMRC itself -- see
docs/35-mods-directory.md-style context in the plan this was built
from. mrc/ is a deliberately independent package (its own systemd
unit, never imports from anetbbs), so this suite talks to BridgeApp
directly rather than through any Flask/anetbbs fixture -- it lives
under tests/ (not mrc/bridge/tests/) purely so the existing 15-batch
`ls tests/test_*.py` CI/local runner (see .github/workflows/
docker-build.yml) actually picks it up; no anetbbs import is needed by
the code under test.

Exercises real loopback TCP throughout (asyncio.start_server /
open_connection against 127.0.0.1, ephemeral port) rather than mocking
the socket layer -- matching this project's standing preference for
verifying real behavior over reasoning through a diff. The upstream
MRC hub connection (BridgeApp.mrc) is swapped for a small fake that
just records outbound packets, since no real hub is reachable in CI;
everything downstream of that (session routing, room/DM delivery,
wire-packet construction) is real, unmocked BridgeApp code.

The one case worth calling out: test_dm_and_broadcast_reach_mixed_
websocket_and_tcp_sessions is the scenario that made the earlier
"become a umrc-bridge client" approach architecturally unworkable
(umrc-bridge routes by one identity per TCP connection slot -- a
message to any user other than that slot's own identity would never
be delivered). Proving a real TCP session and a real WS session in the
same room both get exactly the messages meant for them is the load-
bearing assertion for the whole feature.
"""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mrc.bridge.main import BridgeApp, MRC_TCP_MAX_LINE_BYTES  # noqa: E402
from mrc.bridge.mrc_protocol import MRCProtocol  # noqa: E402


class _FakeMRCConnection:
    """Stands in for BridgeApp.mrc (a real MRCConnection normally
    holds the persistent upstream-hub socket) -- just records every
    outbound packet instead of doing real network I/O."""

    def __init__(self):
        self.sent = []
        self.connected = True

    async def send_packet(self, packet: str):
        self.sent.append(packet)

    def sent_messages(self):
        return [MRCProtocol.parse_packet(p) for p in self.sent]


class _FakeWebSocket:
    """Stands in for an aiohttp WebSocketResponse -- just records
    every JSON payload _safe_send() would have sent it."""

    def __init__(self):
        self.sent = []

    async def send_json(self, payload):
        self.sent.append(payload)


async def _make_bridge(tmp_path, **overrides) -> BridgeApp:
    config = {
        "mrc_host": "test.invalid",
        "mrc_port": 1,
        "bridge_bbs": "TestBBS",
        "platform_info": "TEST/1.0",
        "data_dir": str(tmp_path / "data"),
        "mrc_tcp_enabled": True,
        "mrc_tcp_listen_host": "127.0.0.1",
        "mrc_tcp_listen_port": 0,
        "ws_disconnect_grace_seconds": 0,
        "join_packet_delay_ms": 0,
        "request_banners_on_join": False,
        "request_motd_on_join": False,
        "announce_join_part": True,
    }
    config.update(overrides)
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config))
    app = BridgeApp(str(config_path))
    app.mrc = _FakeMRCConnection()
    return app


class _TcpBridgeHarness(unittest.IsolatedAsyncioTestCase):
    """Shared real-loopback-TCP server setup for every test below."""

    async def asyncSetUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmpdir.name)
        self.app = await _make_bridge(self.tmp_path)
        self.server = await asyncio.start_server(
            self.app.handle_mrc_tcp_connection, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        self.server.close()
        # Bounded, not a bare await: asyncio.Server.wait_closed() blocks
        # until every already-accepted connection's handler task exits
        # too, not just the listening socket. A test that fails/raises
        # before reaching its own writer.close() leaves
        # handle_mrc_tcp_connection still parked in `await reader.read()`
        # for that connection -- an unbounded wait_closed() here would
        # then hang the ENTIRE suite indefinitely on any single test
        # failure, rather than just failing that one test. Confirmed via
        # a direct repro against this exact Python version (3.12.3).
        try:
            await asyncio.wait_for(self.server.wait_closed(), timeout=2.0)
        except asyncio.TimeoutError:
            pass
        self._tmpdir.cleanup()

    async def _connect(self):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        return reader, writer

    async def _send(self, writer, f1, f2, f3, f4, f5, f6, f7):
        writer.write(MRCProtocol.create_packet(f1, f2, f3, f4, f5, f6, f7).encode())
        await writer.drain()

    async def _iamhere(self, writer, handle, room="lobby"):
        await self._send(writer, handle, "site", room, "SERVER", "", "", "IAMHERE")

    async def _read_packet(self, reader, timeout=2.0):
        line = await asyncio.wait_for(reader.readline(), timeout=timeout)
        return MRCProtocol.parse_packet(line.decode())

    async def _wait_until(self, predicate, timeout=2.0, interval=0.02):
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if predicate():
                return True
            await asyncio.sleep(interval)
        return predicate()

    async def _wait_for_single_session(self, timeout=2.0):
        # The bridge keys sessions by str(id(<server-side StreamWriter>)),
        # an object this test never sees directly (asyncio.start_server's
        # callback gets its own end of the socket, distinct from the
        # client-side writer open_connection() returns) -- so tests can't
        # predict the session id up front and must discover it this way.
        ok = await self._wait_until(lambda: len(self.app.db.list_sessions()) == 1, timeout=timeout)
        self.assertTrue(ok, "expected exactly one session to appear")
        session_id, sess = next(iter(self.app.db.list_sessions().items()))
        return session_id, sess


class MrcTcpJoinTests(_TcpBridgeHarness):

    async def test_iamhere_creates_session_and_joins_room(self):
        reader, writer = await self._connect()
        await self._iamhere(writer, "StingRay", "lobby")

        ok = await self._wait_until(lambda: len(self.app.db.list_sessions()) == 1)
        self.assertTrue(ok)

        sess = next(iter(self.app.db.list_sessions().values()))
        self.assertEqual(sess["handle"], "StingRay")
        self.assertEqual(sess["room"], "lobby")
        self.assertTrue(sess["in_room"])

        # _complete_join_after_identify's real wire traffic went out
        # over the fake hub connection.
        commands = [m["message"] for m in self.app.mrc.sent_messages()]
        self.assertTrue(any(c.startswith("NEWROOM:") for c in commands))

        writer.close()
        await writer.wait_closed()

    async def test_invalid_handle_does_not_create_a_session(self):
        reader, writer = await self._connect()
        # "SERVER" is a reserved handle (MRCProtocol.RESERVED_HANDLES)
        await self._iamhere(writer, "SERVER", "lobby")
        await asyncio.sleep(0.2)
        self.assertEqual(self.app.db.list_sessions(), {})
        writer.close()
        await writer.wait_closed()

    async def test_unparsable_packet_does_not_kill_the_connection(self):
        reader, writer = await self._connect()
        writer.write(b"not a valid mrc packet at all\n")
        await writer.drain()
        await asyncio.sleep(0.1)

        await self._iamhere(writer, "StingRay", "lobby")
        ok = await self._wait_until(lambda: len(self.app.db.list_sessions()) == 1)
        self.assertTrue(ok)
        writer.close()
        await writer.wait_closed()


class MrcTcpChatTests(_TcpBridgeHarness):

    async def _joined_client(self, handle, room="lobby"):
        reader, writer = await self._connect()
        await self._iamhere(writer, handle, room)
        session_id, _ = await self._wait_for_single_session()
        return reader, writer, session_id

    async def test_room_broadcast_is_forwarded_unmodified_not_rewrapped(self):
        reader, writer, _sid = await self._joined_client("StingRay")
        self.app.mrc.sent.clear()

        # umrc-client already embeds its own styled display name into
        # the message body itself before sending (main.c) -- the
        # bridge must NOT prepend its own _session_display_handle on
        # top of that, or every line would be double-prefixed.
        await self._send(writer, "StingRay", "site", "lobby", "", "", "lobby",
                          "|07[StingRay] hello there")

        ok = await self._wait_until(lambda: any(
            m["message"] == "|07[StingRay] hello there" for m in self.app.mrc.sent_messages()))
        self.assertTrue(ok)
        msg = next(m for m in self.app.mrc.sent_messages()
                   if m["message"] == "|07[StingRay] hello there")
        self.assertEqual(msg["to_user"], "")
        self.assertEqual(msg["to_room"], "lobby")

        writer.close()
        await writer.wait_closed()

    async def test_directed_message_has_empty_to_room(self):
        reader, writer, _sid = await self._joined_client("StingRay")
        self.app.mrc.sent.clear()

        await self._send(writer, "StingRay", "site", "lobby", "NightOwl", "", "",
                          "hey there, private note")

        ok = await self._wait_until(lambda: any(
            m["to_user"] == "NightOwl" for m in self.app.mrc.sent_messages()))
        self.assertTrue(ok)
        msg = next(m for m in self.app.mrc.sent_messages() if m["to_user"] == "NightOwl")
        self.assertEqual(msg["to_room"], "")
        self.assertEqual(msg["message"], "hey there, private note")

        writer.close()
        await writer.wait_closed()

    async def test_clients_own_notme_join_announcement_is_not_forwarded(self):
        reader, writer = await self._connect()
        await self._iamhere(writer, "StingRay", "lobby")
        await self._wait_for_single_session()

        # umrc-client sends its own NOTME join-announcement packet
        # (empty to_room) right after IAMHERE -- see sendMsgPacket's
        # call sites in uMRC's main.c. It must not be forwarded
        # verbatim, or every join would announce twice.
        await self._send(writer, "StingRay", "site", "lobby", "NOTME", "", "",
                          "*** StingRay's own client-side join text ***")
        await asyncio.sleep(0.3)

        texts = [m["message"] for m in self.app.mrc.sent_messages()]
        self.assertNotIn("*** StingRay's own client-side join text ***", texts)

        writer.close()
        await writer.wait_closed()

    async def test_logoff_removes_session_without_forwarding_logoff_to_hub(self):
        reader, writer, session_id = await self._joined_client("StingRay")
        self.app.mrc.sent.clear()

        await self._send(writer, "StingRay", "site", "lobby", "SERVER", "", "", "LOGOFF")

        ok = await self._wait_until(lambda: self.app.db.get_session(session_id) is None)
        self.assertTrue(ok)

        # Matches the WebSocket /quit path's own documented behavior:
        # LOGOFF is deliberately never sent to the real hub.
        texts = [m["message"].upper() for m in self.app.mrc.sent_messages()]
        self.assertNotIn("LOGOFF", texts)

        writer.close()
        await writer.wait_closed()

    async def test_room_change_forwards_newroom_and_updates_session(self):
        reader, writer, session_id = await self._joined_client("StingRay", room="lobby")
        self.app.mrc.sent.clear()

        await self._send(writer, "StingRay", "site", "lobby", "SERVER", "", "",
                          "NEWROOM:lobby:general")

        ok = await self._wait_until(
            lambda: (self.app.db.get_session(session_id) or {}).get("room") == "general")
        self.assertTrue(ok)

        commands = [m["message"] for m in self.app.mrc.sent_messages()]
        self.assertIn("NEWROOM:lobby:general", commands)

        writer.close()
        await writer.wait_closed()


class MrcTcpMixedTransportRoutingTests(_TcpBridgeHarness):
    """The scenario that made the earlier "become a umrc-bridge
    client" approach unworkable: a real TCP session and a real WS
    session sharing one bridge process must each receive exactly the
    messages meant for them, regardless of which transport they're on.
    """

    async def _register_fake_ws_session(self, handle, room="lobby"):
        ws = _FakeWebSocket()
        ws_id = id(ws)
        self.app.websockets[ws_id] = ws
        await self.app.db.save_session_async(str(ws_id), {
            "handle": handle, "nick": handle, "room": room,
            "in_room": True, "remote_ip": "",
            "style_prefix": "", "style_suffix": "", "style_color": "07",
            "style_prefix_color": "07", "style_handle_color": "07",
            "style_suffix_color": "07", "typing_color": "10",
        })
        return ws, ws_id

    async def test_dm_and_broadcast_reach_mixed_websocket_and_tcp_sessions(self):
        # Bob joins over raw TCP (umrc-client); Carol is a WebSocket
        # session in the same room (ANetBBS's own terminal/web client).
        reader, writer = await self._connect()
        await self._iamhere(writer, "Bob", "lobby")
        await self._wait_for_single_session()

        carol_ws, carol_id = await self._register_fake_ws_session("Carol", "lobby")

        # A DM from the hub addressed to Bob must reach Bob's TCP
        # session and must NOT reach Carol's WS session.
        await self.app._on_upstream_packet({
            "from_user": "Dave", "from_site": "site", "from_room": "lobby",
            "to_user": "Bob", "msg_ext": "", "to_room": "", "message": "psst, Bob",
        })

        pkt = await self._read_packet(reader)
        self.assertEqual(pkt["to_user"], "Bob")
        self.assertEqual(pkt["message"], "psst, Bob")
        self.assertEqual(carol_ws.sent, [])

        # A room broadcast from the hub must reach BOTH Bob (TCP) and
        # Carol (WS) -- the actual "one shared bridge, mixed clients"
        # requirement this whole feature exists for.
        await self.app._on_upstream_packet({
            "from_user": "Dave", "from_site": "site", "from_room": "lobby",
            "to_user": "", "msg_ext": "", "to_room": "lobby", "message": "hi room",
        })

        pkt2 = await self._read_packet(reader)
        self.assertEqual(pkt2["message"], "hi room")
        ok = await self._wait_until(lambda: len(carol_ws.sent) >= 1)
        self.assertTrue(ok)
        self.assertEqual(carol_ws.sent[-1]["message"], "hi room")

        writer.close()
        await writer.wait_closed()


class MrcTcpDisconnectTests(_TcpBridgeHarness):

    async def test_socket_close_removes_session(self):
        reader, writer = await self._connect()
        await self._iamhere(writer, "StingRay", "lobby")
        await self._wait_for_single_session()

        writer.close()
        await writer.wait_closed()

        ok = await self._wait_until(lambda: self.app.db.list_sessions() == {})
        self.assertTrue(ok)


class MrcTcpUnboundedBufferProtectionTests(_TcpBridgeHarness):
    """Real security review finding (2026-09-23): handle_mrc_tcp_
    connection's newline-delimited read loop had no cap on how large
    its buffer could grow while waiting for a line terminator -- the
    same "unbounded buffer / no size cap on a receive loop" bug class
    already fixed elsewhere in this codebase (BinkP, the MRC-IRC
    bridge, the web/terminal IRC clients, QWK), just missed here. A
    client that never sends '\\n' could grow memory without bound.
    Fixed with a cap (MRC_TCP_MAX_LINE_BYTES) checked after each
    read -- exceeding it closes the connection instead of growing
    further."""

    async def test_a_line_with_no_terminator_past_the_cap_closes_the_connection(self):
        reader, writer = await self._connect()
        # +1 to be unambiguously over the cap, not exactly at it.
        writer.write(b"A" * (MRC_TCP_MAX_LINE_BYTES + 1))
        await writer.drain()

        # The server must close its end -- reader.read() returns b''
        # on EOF once that happens, rather than hanging or the buffer
        # growing forever if more bytes kept arriving.
        data = await asyncio.wait_for(reader.read(1), timeout=2.0)
        self.assertEqual(data, b'', 'server must close the connection once '
                          'the no-terminator buffer exceeds the cap')

        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), timeout=2.0)
        except asyncio.TimeoutError:
            pass

    async def test_many_small_real_packets_in_one_burst_are_not_mistaken_for_overflow(self):
        """The cap is checked against the LEFTOVER partial line after
        every complete '\\n'-terminated packet in the current chunk is
        already split out and processed -- not against the raw total
        bytes ever seen. A legitimate client sending many small real
        packets back-to-back in one burst (well within a single
        4096-byte read()) must not get disconnected."""
        reader, writer = await self._connect()
        await self._iamhere(writer, "StingRay", "lobby")
        await self._wait_for_single_session()

        packets = b"".join(
            MRCProtocol.create_packet(
                "StingRay", "site", "lobby", "", "", "lobby", f"msg {i}"
            ).encode()
            for i in range(20)
        )
        self.assertLess(len(packets), MRC_TCP_MAX_LINE_BYTES)
        writer.write(packets)
        await writer.drain()

        # Session must still be alive and the connection still open --
        # confirmed by successfully sending one more packet and getting
        # a reply back over the fake hub, not by the socket closing.
        ok = await self._wait_until(
            lambda: len(self.app.mrc.sent_messages()) >= 20)
        self.assertTrue(ok, 'a legitimate multi-packet burst must not trip '
                         'the overflow protection')

        writer.close()
        await writer.wait_closed()


if __name__ == "__main__":
    unittest.main()
