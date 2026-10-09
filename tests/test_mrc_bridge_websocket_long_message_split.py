"""Regression tests for the WebSocket chat-send path's long-message
handling (mrc/bridge/main.py's _handle_send_message/
_handle_direct_message) -- the same bug shape found live on the
native-TCP umrc-client path (github.com/codefenix-dev/uMRC issue #27,
see test_mrc_bridge_tcp_listener.py's MrcTcpLongMessageSplitTests for
the full writeup): a message exceeding the bridge's 140-char wire cap
had its tail silently dropped by _truncate_wire_message() instead of
being split across multiple packets. Both WebSocket handlers hit the
exact same _truncate_wire_message() call before the fix, so they get
the same dedicated coverage rather than relying only on the TCP path's
tests to prove the fix generally.

Uses the same lightweight mock-BridgeApp harness as
test_mrc_bridge_identify_gate.py (object.__new__(BridgeApp) + a fake
upstream-hub AsyncMock) rather than real loopback sockets, since these
two handlers take a plain dict payload directly -- no real network
layer to exercise.
"""
import asyncio
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mrc.bridge.main import BridgeApp, MRCProtocol  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


class _FakeWs:
    def __init__(self):
        self.sent = []

    async def send_json(self, obj):
        self.sent.append(obj)


def _make_bridge(tmp_dir):
    app = object.__new__(BridgeApp)
    app.config = {"bridge_bbs": "TestBBS"}
    from mrc.bridge.db import BridgeDB
    app.db = BridgeDB(tmp_dir)
    app.websockets = {}
    app.mrc_tcp_clients = {}
    app._ws_remote_ip = {}
    app.mrc = AsyncMock()
    app.mrc.connected = True
    app.join_packet_delay_ms = 0
    app.announce_join_part = True
    app.request_banners_on_join = False
    app.request_motd_on_join = False
    app.join_message_tpl = "- {handle} has arrived."
    app.exit_message_tpl = "- {handle} has left chat."
    app.ctcp_room = "ctcp_echo_channel"
    app.userlist_refresh_on_server_events = False
    app.identify_required_mode = False
    app.post_identify_auto_join = False
    app.default_style_prefix = ""
    app.default_style_suffix = ""
    app.default_style_color = "07"
    app.rate_limiter = {}
    app.pending_disconnects = {}
    return app


def _sent_messages(mock_mrc):
    return [MRCProtocol.parse_packet(c.args[0]) for c in mock_mrc.send_packet.call_args_list]


class WebSocketLongMessageSplitTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _make_bridge(self._tmp.name)
        self.ws_id = 111
        self.ws = _FakeWs()
        self.app.websockets[self.ws_id] = self.ws
        _run(self.app._handle_join_room(self.ws_id, {"handle": "Alice", "room": "lobby"}))
        self.app.mrc.send_packet.reset_mock()

    def test_long_room_broadcast_is_split_not_truncated(self):
        sentences = ' '.join(f"This is sentence {i}." for i in range(1, 15))
        long_text = (sentences + ' ').ljust(280, 'E')[:280]
        self.assertEqual(len(long_text), 280)

        _run(self.app._handle_send_message(self.ws_id, {"message": long_text}))

        sent = _sent_messages(self.app.mrc)
        self.assertGreater(len(sent), 1, "a 280-char message must split into more than one packet")
        for m in sent:
            self.assertLessEqual(len(m["message"]), 140)

        reassembled = re.sub(r'\(\d+/\d+\)\s*', '', ' '.join(m["message"] for m in sent))
        for word in long_text.split(' '):
            if word:
                self.assertIn(word, reassembled)

    def test_short_room_broadcast_is_unaffected(self):
        """The common case -- a message well under the cap -- must
        still go out as exactly one packet, same as before this fix."""
        _run(self.app._handle_send_message(self.ws_id, {"message": "hello there"}))
        sent = _sent_messages(self.app.mrc)
        self.assertEqual(len(sent), 1)
        self.assertIn("hello there", sent[0]["message"])

    def test_long_direct_message_is_split_not_truncated(self):
        long_text = ' '.join(f"word{i}" for i in range(1, 60))
        self.assertGreater(len(long_text), 140)

        _run(self.app._handle_direct_message(
            self.ws_id, {"to_user": "NightOwl", "message": long_text}))

        sent = [m for m in _sent_messages(self.app.mrc) if m["to_user"] == "NightOwl"]
        self.assertGreater(len(sent), 1)
        for m in sent:
            self.assertLessEqual(len(m["message"]), 140)
            self.assertEqual(m["to_room"], "")

        reassembled = re.sub(
            r'\(\d+/\d+\)\s*', '', ' '.join(m["message"] for m in sent))
        for word in long_text.split(' '):
            self.assertIn(word, reassembled)

    def test_long_action_is_split_not_truncated(self):
        action_text = ' '.join(f"dances{i}" for i in range(1, 40))
        self.assertGreater(len(action_text), 140)

        _run(self.app._handle_send_message(self.ws_id, {"message": f"* {action_text}"}))

        sent = _sent_messages(self.app.mrc)
        self.assertGreater(len(sent), 1)
        for m in sent:
            self.assertLessEqual(len(m["message"]), 140)
            self.assertTrue(m["message"].startswith("|15* |13"))
            self.assertTrue(m["message"].endswith("|07"))


if __name__ == '__main__':
    unittest.main()
