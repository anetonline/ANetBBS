"""Regression test for a real MRC protocol-compliance audit finding:
/me actions were budgeted against the bare 140-char hub limit with no
reservation for the wrapper the bridge itself adds before transmission
(_handle_send_message's action branch: "|15* |13{nick} {text}|07",
fixed colors, NOT the user's own style). Anything within that wrapper's
length of the limit had its tail silently cut off by
_truncate_wire_message() with no warning -- the exact class of bug
handle_overhead/dm_overhead already existed to prevent for plain chat
and DMs, just never extended to /me.

Fixed by computing and pushing an action_overhead figure to clients on
join, the same established pattern as handle_overhead/dm_overhead, so
mrc_chat.py (terminal) and index.html (web) can both budget accurately
instead of assuming zero overhead.
"""
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mrc.bridge.main import BridgeApp
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


class SessionActionOverheadTests(unittest.TestCase):
    """Unit tests of the wire-length math itself."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _make_bridge(self._tmp.name)

    def test_matches_the_actual_wrapper_the_bridge_sends(self):
        sess = {"handle": "StingRay", "nick": "StingRay"}
        overhead = self.app._session_action_overhead(sess)
        # Must exactly match _handle_send_message's real construction:
        # f"|15* |13{nick} {text}|07" minus the text itself.
        wrapper_without_text = "|15* |13StingRay |07"
        self.assertEqual(overhead, len(wrapper_without_text))

    def test_scales_with_nick_length(self):
        short = self.app._session_action_overhead({"handle": "Al", "nick": "Al"})
        long_ = self.app._session_action_overhead(
            {"handle": "ReallyLongHandle", "nick": "ReallyLongHandle"})
        self.assertEqual(long_ - short, len("ReallyLongHandle") - len("Al"))

    def test_uses_effective_nick_not_bare_handle(self):
        # A nick reassigned by the hub (collision suffix) must be what
        # gets budgeted against, since that's what actually goes on the
        # wire -- see _session_effective_nick.
        sess = {"handle": "StingRay", "nick": "StingRay2"}
        overhead = self.app._session_action_overhead(sess)
        self.assertEqual(overhead, len("|15* |13StingRay2 |07"))


class JoinedPayloadIncludesActionOverheadTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _make_bridge(self._tmp.name)
        self.ws_id = 111
        self.ws = _FakeWs()
        self.app.websockets[self.ws_id] = self.ws

    def test_joined_payload_carries_action_overhead(self):
        _run(self.app._handle_join_room(
            self.ws_id, {"handle": "StingRay", "room": "lobby"}))

        joined = next((m for m in self.ws.sent if m.get("type") == "joined"), None)
        self.assertIsNotNone(joined, "no 'joined' payload was sent")
        self.assertIn("action_overhead", joined)
        self.assertEqual(joined["action_overhead"],
                         self.app._session_action_overhead(
                             self.app.db.get_session(str(self.ws_id))))

    def test_action_overhead_is_a_positive_int(self):
        _run(self.app._handle_join_room(
            self.ws_id, {"handle": "Bob", "room": "lobby"}))
        joined = next((m for m in self.ws.sent if m.get("type") == "joined"), None)
        self.assertIsInstance(joined["action_overhead"], int)
        self.assertGreater(joined["action_overhead"], 0)


if __name__ == '__main__':
    unittest.main()
