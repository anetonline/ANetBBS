"""Regression test for a real Medium finding from a follow-up security
audit: handle_websocket()'s `web.WebSocketResponse(heartbeat=30)` (the
/ws and /mrcws routes -- every web MRC user and the terminal MRC door
go through this path) was built with no explicit `max_msg_size`, so it
fell back to aiohttp's own 4 MiB default. Every sibling ingestion
point in this exact service already caps much lower: this same file's
raw-TCP listener caps lines at MRC_TCP_MAX_LINE_BYTES (8192), and both
the MRC-IRC bridge's outbound leg to this bridge and the terminal MRC
client's own connection already pass max_msg_size=262144 when
connecting to it. Only the server side of this one WebSocketResponse
was missed -- fixed by passing the same 262144-byte cap
(MRC_WS_MAX_MSG_BYTES) explicitly.

Exercises a real aiohttp server/client (aiohttp.test_utils.TestServer/
TestClient) rather than mocking the WebSocket layer, matching this
project's standing preference for verifying real behavior over
reasoning through a diff.
"""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer, TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mrc.bridge.main import BridgeApp, MRC_WS_MAX_MSG_BYTES  # noqa: E402


class _FakeMRCConnection:
    def __init__(self):
        self.sent = []
        self.connected = True

    async def send_packet(self, packet: str):
        self.sent.append(packet)


async def _make_bridge(tmp_path) -> BridgeApp:
    config = {
        "mrc_host": "test.invalid",
        "mrc_port": 1,
        "bridge_bbs": "TestBBS",
        "platform_info": "TEST/1.0",
        "data_dir": str(tmp_path / "data"),
        "ws_disconnect_grace_seconds": 0,
        "join_packet_delay_ms": 0,
        "request_banners_on_join": False,
        "request_motd_on_join": False,
        "announce_join_part": True,
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config))
    app = BridgeApp(str(config_path))
    app.mrc = _FakeMRCConnection()
    return app


class WebSocketMaxMsgSizeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmpdir.name)
        self.bridge = await _make_bridge(self.tmp_path)
        web_app = web.Application()
        web_app.router.add_get("/ws", self.bridge.handle_websocket)
        self.server = TestServer(web_app)
        self.client = TestClient(self.server)
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self._tmpdir.cleanup()

    async def test_ordinary_small_message_still_works(self):
        """Sanity check: the cap must not false-positive on normal
        chat-sized traffic."""
        ws = await self.client.ws_connect("/ws", max_msg_size=0)
        try:
            await asyncio.wait_for(ws.receive_json(), timeout=2.0)  # welcome
            await asyncio.wait_for(ws.receive_json(), timeout=2.0)  # bridge_status
            await ws.send_str(json.dumps({"type": "ping", "t": 123}))
            reply = await asyncio.wait_for(ws.receive_json(), timeout=2.0)
            self.assertEqual(reply.get("type"), "pong")
            self.assertEqual(len(self.bridge.websockets), 1)
        finally:
            await ws.close()

    async def test_oversized_message_closes_the_connection(self):
        """A frame over MRC_WS_MAX_MSG_BYTES must be rejected -- the
        server-side aiohttp WebSocketResponse enforces this itself once
        max_msg_size is set, closing the connection rather than
        buffering/parsing an oversized payload."""
        self.assertLess(MRC_WS_MAX_MSG_BYTES, 1_000_000,
                         'sanity: the cap this test relies on must still '
                         'be well under the oversized payload below')
        # max_msg_size=0 on the client side disables ITS OWN receive-size
        # limit -- irrelevant here since we're testing what the SERVER
        # does with an oversized incoming frame, not what this client can
        # receive back, but set to avoid an unrelated client-side cap
        # ever masking the server-side assertion below.
        ws = await self.client.ws_connect("/ws", max_msg_size=0)
        try:
            await asyncio.wait_for(ws.receive_json(), timeout=2.0)  # welcome
            await asyncio.wait_for(ws.receive_json(), timeout=2.0)  # bridge_status

            oversized = json.dumps({"type": "ping", "pad": "x" * (MRC_WS_MAX_MSG_BYTES + 1000)})
            await ws.send_str(oversized)

            msg = await asyncio.wait_for(ws.receive(), timeout=2.0)
            self.assertIn(msg.type, (aiohttp.WSMsgType.CLOSE,
                                     aiohttp.WSMsgType.CLOSED,
                                     aiohttp.WSMsgType.ERROR),
                          f'expected the connection to be closed after an '
                          f'oversized frame, got {msg.type!r}')

            # Give the server's own `finally` cleanup a moment to run.
            await asyncio.sleep(0.1)
            self.assertEqual(len(self.bridge.websockets), 0,
                              'the server must have torn down the '
                              'oversized-frame connection')
        finally:
            if not ws.closed:
                await ws.close()


if __name__ == "__main__":
    unittest.main()
