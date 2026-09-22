"""Regression test for the opt-in mrc_send_logoff_on_leave config flag
(default off -- see test_mrc_bridge_no_logoff_on_leave.py for why, and
BridgeApp.__init__'s own comment for the fuller history).

This does NOT prove LOGOFF is safe to send by default -- that's an
open, unresolved question pending real-hub re-verification (a prior
live test already used the spec-correct wire format and still broke
re-joining; see MRCProtocol.create_logoff's own docstring). This only
proves the flag itself works correctly: off by default (matching the
sibling test file), and when explicitly enabled, sends the real,
spec-correct LOGOFF packet.
"""
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mrc.bridge.main import BridgeApp, MRCProtocol
from mrc.bridge.db import BridgeDB


def _run(coro):
    return asyncio.run(coro)


class _FakeWs:
    def __init__(self):
        self.sent = []

    async def send_json(self, obj):
        self.sent.append(obj)


def _make_bridge(tmp_dir, send_logoff_on_leave):
    app = object.__new__(BridgeApp)
    app.config = {"bridge_bbs": "TestBBS"}
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
    app.ws_disconnect_grace_seconds = 0.01
    app.send_logoff_on_leave = send_logoff_on_leave
    return app


def _sent_packets(mock_send_packet):
    out = []
    for call in mock_send_packet.call_args_list:
        pkt = call.args[0] if call.args else call.kwargs.get("packet", "")
        out.append(MRCProtocol.parse_packet(pkt))
    return out


class LogoffOptInTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ws_id = 111
        self.ws = _FakeWs()

    def _join(self, app):
        app.websockets[self.ws_id] = self.ws
        app.db.save_session(str(self.ws_id), {
            "handle": "StingRay", "nick": "StingRay", "room": "lobby",
            "in_room": True,
        })

    def test_default_still_does_not_send_logoff(self):
        app = _make_bridge(self._tmp.name, send_logoff_on_leave=False)
        self._join(app)
        _run(app._handle_leave_room(self.ws_id, {}))
        packets = _sent_packets(app.mrc.send_packet)
        self.assertFalse(any(p["message"].upper() == "LOGOFF" for p in packets))

    def test_enabled_sends_spec_correct_logoff_packet(self):
        app = _make_bridge(self._tmp.name, send_logoff_on_leave=True)
        self._join(app)
        _run(app._handle_leave_room(self.ws_id, {}))
        packets = _sent_packets(app.mrc.send_packet)
        logoff = next((p for p in packets if p["message"].upper() == "LOGOFF"), None)
        self.assertIsNotNone(logoff, "LOGOFF must be sent when the flag is on")
        self.assertEqual(logoff["from_user"], "StingRay")
        self.assertEqual(logoff["to_user"], "SERVER")
        # Per MRCProtocol.create_logoff's own docstring, matching the
        # documented spec template exactly: BOTH fromRoom and toRoom
        # populated with the room name.
        self.assertEqual(logoff["from_room"], "lobby")
        self.assertEqual(logoff["to_room"], "lobby")

    def test_enabled_still_sends_notme_departure_message(self):
        app = _make_bridge(self._tmp.name, send_logoff_on_leave=True)
        self._join(app)
        _run(app._handle_leave_room(self.ws_id, {}))
        packets = _sent_packets(app.mrc.send_packet)
        self.assertTrue(any("has left chat" in p["message"] for p in packets))

    def test_enabled_logoff_sent_after_notme_not_before(self):
        """Order matters for a real hub trace to be readable -- NOTME
        (the visible departure announcement) should still land first,
        LOGOFF last, matching the pre-existing call order in
        _leave_room_and_cleanup."""
        app = _make_bridge(self._tmp.name, send_logoff_on_leave=True)
        self._join(app)
        _run(app._handle_leave_room(self.ws_id, {}))
        packets = _sent_packets(app.mrc.send_packet)
        messages = [p["message"] for p in packets]
        notme_idx = next(i for i, m in enumerate(messages) if "has left chat" in m)
        logoff_idx = next(i for i, m in enumerate(messages) if m.upper() == "LOGOFF")
        self.assertLess(notme_idx, logoff_idx)


if __name__ == '__main__':
    unittest.main()
