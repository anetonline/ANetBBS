"""Regression test for a real bug found investigating a live "I still
have to /identify every single time" report.

sess["remote_ip"] was read in _send_join_payloads() (which sends the
reference client's own USERIP: packet unconditionally on every join,
right alongside its "/identify for MRC Trust" tip) but never written
ANYWHERE in the bridge -- so USERIP: was never sent at all, for any
user, on either the terminal or web client. If the MRC hub uses USERIP
to recognize a returning already-identified connection (which the
reference client's own unconditional send on every join strongly
suggests), never sending it would force a fresh /identify on literally
every join, regardless of how recently the same handle last
identified -- exactly the reported symptom.

Two real callers of remote IP, fixed independently since the bridge
can observe one but not the other:
  - Web client: the bridge CAN see the real caller IP itself, from the
    incoming WebSocket request (honoring X-Forwarded-For since /mrcws
    is reverse-proxied by nginx) -- handle_websocket() now captures it
    into self._ws_remote_ip, keyed by ws_id.
  - Terminal client: its WebSocket connection to the bridge always
    originates from localhost (ws://127.0.0.1:8080), so the bridge has
    no way to observe the real caller's IP for that path at all -- the
    terminal door (mrc_chat.py) now sends it explicitly via an "ip"
    field on join_room, which _handle_join_room prefers over the
    server-observed value when present.
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


def _make_bridge(tmp_dir):
    app = object.__new__(BridgeApp)
    app.config = {"bridge_bbs": "TestBBS"}
    app.db = BridgeDB(tmp_dir)
    app.websockets = {}
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


def _sent_userip_packets(mock_send_packet):
    """Every USERIP:<ip> packet body sent via app.mrc.send_packet."""
    out = []
    for call in mock_send_packet.call_args_list:
        pkt = call.args[0] if call.args else call.kwargs.get("packet", "")
        parsed = MRCProtocol.parse_packet(pkt)
        if parsed["message"].startswith("USERIP:"):
            out.append(parsed["message"][len("USERIP:"):])
    return out


class BridgeUserIpTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _make_bridge(self._tmp.name)
        self.ws_id = 222
        self.ws = _FakeWs()
        self.app.websockets[self.ws_id] = self.ws

    def test_terminal_client_supplied_ip_is_stored_and_sent(self):
        _run(self.app._handle_join_room(
            self.ws_id, {"handle": "Alice", "room": "lobby", "ip": "203.0.113.7"}))

        sess = self.app.db.get_session(str(self.ws_id))
        self.assertEqual(sess["remote_ip"], "203.0.113.7")

        userips = _sent_userip_packets(self.app.mrc.send_packet)
        self.assertIn("203.0.113.7", userips,
                      "USERIP: must actually be sent to the hub on join")

    def test_web_client_server_observed_ip_is_used_when_no_explicit_ip(self):
        # Simulates handle_websocket()'s own X-Forwarded-For capture,
        # which happens before join_room ever arrives.
        self.app._ws_remote_ip[self.ws_id] = "198.51.100.42"

        _run(self.app._handle_join_room(
            self.ws_id, {"handle": "Bob", "room": "lobby"}))

        sess = self.app.db.get_session(str(self.ws_id))
        self.assertEqual(sess["remote_ip"], "198.51.100.42")

        userips = _sent_userip_packets(self.app.mrc.send_packet)
        self.assertIn("198.51.100.42", userips)

    def test_explicit_ip_field_takes_priority_over_server_observed(self):
        # If a client ever supplies BOTH (shouldn't normally happen,
        # but the terminal client's own explicit value is more
        # authoritative for its own path than whatever the bridge
        # happened to observe on the WebSocket transport).
        self.app._ws_remote_ip[self.ws_id] = "198.51.100.42"

        _run(self.app._handle_join_room(
            self.ws_id, {"handle": "Carol", "room": "lobby", "ip": "203.0.113.7"}))

        sess = self.app.db.get_session(str(self.ws_id))
        self.assertEqual(sess["remote_ip"], "203.0.113.7")

    def test_no_ip_available_does_not_send_a_blank_userip_packet(self):
        _run(self.app._handle_join_room(
            self.ws_id, {"handle": "Dave", "room": "lobby"}))

        sess = self.app.db.get_session(str(self.ws_id))
        self.assertEqual(sess.get("remote_ip", ""), "")

        userips = _sent_userip_packets(self.app.mrc.send_packet)
        self.assertEqual(userips, [],
                         'no USERIP: packet should be sent when no IP is known '
                         '(matches _send_join_payloads\' own "if remote_ip:" guard)')

    def test_loopback_ip_is_treated_as_no_ip_matching_reference_client(self):
        """Matches the reference client's own guard (helper_protocol.c:
        only sends USERIP if non-empty AND not "127.0.0.1"). For the
        web path specifically, seeing exactly 127.0.0.1 here is also
        the tell-tale sign X-Forwarded-For isn't being forwarded
        correctly (nginx's own proxy connection, not the real
        browser's address) -- sending it would be actively misleading,
        not just unhelpful."""
        _run(self.app._handle_join_room(
            self.ws_id, {"handle": "Eve", "room": "lobby", "ip": "127.0.0.1"}))

        sess = self.app.db.get_session(str(self.ws_id))
        self.assertEqual(sess.get("remote_ip", ""), "")

        userips = _sent_userip_packets(self.app.mrc.send_packet)
        self.assertEqual(userips, [])


if __name__ == '__main__':
    unittest.main()
