"""Regression tests for two real gaps found live (2026-09-21) testing
a real umrc-client against the MRC bridge's raw-TCP listener:

1. BridgeApp._broadcast_latency() only ever pushed to WebSocket
   sessions (ws.send_json) -- a directly-connected umrc-client was
   never sent anything for it, so its in-chat status bar showed every
   other stat correctly except latency, which stayed at "--" forever.
   _send_mrc_tcp_payload()'s own docstring incorrectly claimed every
   payload type except "mrc_message" needed no translation for raw-TCP
   clients; latency is a real counterexample -- there's no equivalent
   wire traffic a TCP client would otherwise see. Fixed by
   synthesizing a real `LATENCY:<ms>` server-command packet (the exact
   wire shape umrc-client's own source, main.c's processPacket, parses
   into gLatency) and sending it to every raw-TCP session too.

2. While fixing that, found _send_mrc_tcp_payload()'s writer.drain()
   had no timeout -- same underlying mechanism as ANetBBS core's own
   v1.0.87 session write-hang fix and the upstream-hub drain fix in
   this same file's MRCConnection class, just on this one downstream,
   client-facing write path that had never been traced through before.
"""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mrc.bridge.main import BridgeApp  # noqa: E402
from mrc.bridge.mrc_protocol import MRCProtocol  # noqa: E402


class _FakeMRCConnection:
    def __init__(self):
        self.sent = []
        self.connected = True

    async def send_packet(self, packet: str):
        self.sent.append(packet)


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
        "join_packet_delay_ms": 0,
        "request_banners_on_join": False,
        "request_motd_on_join": False,
    }
    config.update(overrides)
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config))
    app = BridgeApp(str(config_path))
    app.mrc = _FakeMRCConnection()
    return app


class _TcpHarness(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmpdir.name)
        self.app = await _make_bridge(self.tmp_path)
        self.server = await asyncio.start_server(
            self.app.handle_mrc_tcp_connection, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        self.server.close()
        try:
            await asyncio.wait_for(self.server.wait_closed(), timeout=2.0)
        except asyncio.TimeoutError:
            pass
        self._tmpdir.cleanup()

    async def _connect_and_join(self, handle="StingRay", room="lobby"):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        writer.write(MRCProtocol.create_packet(
            handle, "site", room, "SERVER", "", "", "IAMHERE").encode())
        await writer.drain()
        # Give the bridge a moment to register the session.
        for _ in range(50):
            if self.app.mrc_tcp_clients:
                break
            await asyncio.sleep(0.02)
        return reader, writer


class LatencyReachesTcpClientTests(_TcpHarness):
    async def test_broadcast_latency_sends_real_latency_packet(self):
        reader, writer = await self._connect_and_join()

        await self.app._broadcast_latency(142.7)

        line = await asyncio.wait_for(reader.readline(), timeout=2.0)
        parsed = MRCProtocol.parse_packet(line.decode())
        self.assertEqual(parsed["message"], "LATENCY:143")  # round()
        self.assertEqual(parsed["to_user"], "CLIENT")

        writer.close()
        await writer.wait_closed()

    async def test_send_mrc_tcp_payload_ignores_unknown_types(self):
        """Defense in depth: a payload type with no real wire
        equivalent (e.g. bridge_status) must still be a silent no-op,
        not raise or send garbage."""
        reader, writer = await self._connect_and_join()

        await self.app._send_mrc_tcp_payload(
            writer, {"type": "bridge_status", "status": "upstream_connected"})
        await self.app._broadcast_latency(5)

        line = await asyncio.wait_for(reader.readline(), timeout=2.0)
        parsed = MRCProtocol.parse_packet(line.decode())
        self.assertEqual(parsed["message"], "LATENCY:5")

        writer.close()
        await writer.wait_closed()


class DrainTimeoutTests(_TcpHarness):
    async def test_stuck_drain_times_out_instead_of_hanging(self):
        reader, writer = await self._connect_and_join()
        # The bridge's OWN server-side writer for this connection --
        # a different StreamWriter object than the client-side one
        # above (open_connection() gives us the client end; the
        # bridge's handle_mrc_tcp_connection got a separate writer
        # for the server end of the same socket). _broadcast_latency
        # writes through THIS one, not the client-side `writer`.
        server_writer = next(iter(self.app.mrc_tcp_clients.values()))

        async def _stuck_drain():
            await asyncio.Event().wait()

        with mock.patch.object(server_writer, "drain", _stuck_drain):
            with mock.patch.object(
                    self.app, "_mrc_tcp_write_timeout_seconds", 0.05):
                # Must return promptly, not hang -- the real bug this
                # verifies-by-reverting confirms (removing the
                # wait_for wrapper reproduces a genuine unbounded hang
                # here).
                await asyncio.wait_for(
                    self.app._broadcast_latency(1), timeout=2.0)

        writer.close()
        await writer.wait_closed()


if __name__ == '__main__':
    unittest.main()
