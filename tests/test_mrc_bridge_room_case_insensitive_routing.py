"""Regression test for the real root cause behind a live report
(2026-09-22): "it showed motd and chatters and I was still not in
lobby, I'm still not in the lobby."

The join itself had genuinely succeeded, both hub-side (confirmed
directly against a real captured wire trace: "Welcome to #lobby, the
room is now occupied by 15 user(s)", a full USERLIST including the
caller's own nick) and bridge-side (in_room correctly flipped True).
What was actually broken was room-broadcast DELIVERY: MRCProtocol.
norm_room() strips a leading '#' and swaps spaces for underscores, but
never case-folded -- and the real MRC hub's own room-scoped packets
consistently use lowercase room names ("lobby") regardless of the case
a NEWROOM command asked to join with. The web UI's own default room
value is "Lobby" (capital L), so a session that joined via that
default had its own stored room ("Lobby") compared, byte-for-byte,
against every incoming broadcast's lowercase "lobby" in
_sessions_in_room() -- never matching. The caller kept receiving their
own personally-addressed replies (MOTD, CHATTERS, /who -- routed by
nick, not room) completely normally, which is exactly why it looked
like the join half-worked: everything routed by nick was fine, and
everything routed by room (every other user's chat, ROOMTOPIC, join/
leave notices for anyone else) was being silently dropped.

Fixed by case-folding inside norm_room() itself -- the single
normalization chokepoint already used for both storing a session's
room and matching an incoming packet's room field against it -- rather
than patching every individual comparison site (which is exactly how
an earlier, narrower fix same day for the /join no-op guard still
missed this).
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
    app._pending_banner_nick = None
    app._pending_banner_until = 0.0
    app._last_umrc_stats = (0, 0, 0, 0)
    return app


class RoomBroadcastDeliveryIsCaseInsensitiveTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _make_bridge(self._tmp.name)
        self.ws_id = 501
        self.ws = _FakeWs()
        self.app.websockets[self.ws_id] = self.ws

    def _other_users_chat_packet(self, to_room):
        return {
            "from_user": "calcmandan", "from_site": "Air_&_Wave_BBS",
            "from_room": to_room, "to_user": "", "to_room": to_room,
            "message": "hello from the real room",
        }

    def test_session_joined_with_mixed_case_room_still_receives_lowercase_broadcast(self):
        # Matches the exact real report: joined via "Lobby" (the web
        # UI's own default), the hub's own chat broadcasts use "lobby".
        _run(self.app._handle_join_room(
            self.ws_id, {"handle": "StingRay", "room": "Lobby"}))
        self.ws.sent.clear()

        _run(self.app._on_upstream_packet(self._other_users_chat_packet("lobby")))

        chat = [m for m in self.ws.sent if m.get("type") == "mrc_message"
                and m.get("from_user") == "calcmandan"]
        self.assertEqual(len(chat), 1,
                          "room chat from another user must reach a session "
                          "that joined with a different-case room name")

    def test_session_joined_lowercase_receives_mixed_case_broadcast(self):
        # The reverse direction, for completeness.
        _run(self.app._handle_join_room(
            self.ws_id, {"handle": "StingRay", "room": "lobby"}))
        self.ws.sent.clear()

        _run(self.app._on_upstream_packet(self._other_users_chat_packet("Lobby")))

        chat = [m for m in self.ws.sent if m.get("type") == "mrc_message"
                and m.get("from_user") == "calcmandan"]
        self.assertEqual(len(chat), 1)

    def test_session_in_a_genuinely_different_room_does_not_receive_it(self):
        # No regression: room scoping itself must still work -- this
        # isn't "deliver everything to everyone," just case-insensitive
        # matching of the same room.
        _run(self.app._handle_join_room(
            self.ws_id, {"handle": "StingRay", "room": "gopher"}))
        self.ws.sent.clear()

        _run(self.app._on_upstream_packet(self._other_users_chat_packet("lobby")))

        chat = [m for m in self.ws.sent if m.get("type") == "mrc_message"
                and m.get("from_user") == "calcmandan"]
        self.assertEqual(chat, [])


if __name__ == '__main__':
    unittest.main()
