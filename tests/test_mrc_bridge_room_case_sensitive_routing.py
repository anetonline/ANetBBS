"""Regression test for a real correction made the same day as the bug
it originally chased.

The original live report (2026-09-22): "it showed motd and chatters
and I was still not in lobby." Root-caused correctly at the time: the
join had genuinely succeeded (confirmed against a real captured wire
trace -- "Welcome to #lobby, the room is now occupied by 15 user(s)",
a full USERLIST including the caller's own nick), but room-broadcast
DELIVERY was silently broken, because the web UI's own default room
value was "Lobby" (capital L) while the hub's own canonical name for
that specific well-known default room is lowercase "lobby" -- so a
session that joined via that default had its own stored room compared,
byte-for-byte, against every incoming broadcast's "lobby" in
_sessions_in_room() and never matched.

The FIX first applied that day was wrong, though: it made norm_room()
case-fold every room name, generalizing from the one case actually
tested ("Lobby" vs "lobby", the default room, which the hub happens to
treat case-insensitively as its own well-known alias) into "MRC room
names are always case-insensitive." That's false -- confirmed directly
by the person running this BBS, who has real users with genuinely
distinct, case-sensitive custom rooms on the network. Lowercasing
every room name would have silently merged those into one room.

Reverted. The real fix is narrower: the web UI's own default room
value was changed from "Lobby" to lowercase "lobby" (matching the
hub's own canonical case for that one specific default room), while
room matching everywhere else stays exactly as typed -- case-sensitive,
same as the real network. This file now guards THAT correction: rooms
that differ only by case are genuinely different rooms, and must not
receive each other's broadcasts.
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


class RoomNamesAreCaseSensitiveTests(unittest.TestCase):
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

    def test_session_does_not_receive_a_broadcast_for_a_different_case_room(self):
        # "MyRoom" and "myroom" are genuinely different rooms on the
        # real network -- a session in one must not see traffic
        # addressed to the other, even though they differ only by case.
        _run(self.app._handle_join_room(
            self.ws_id, {"handle": "StingRay", "room": "MyRoom"}))
        self.ws.sent.clear()

        _run(self.app._on_upstream_packet(self._other_users_chat_packet("myroom")))

        chat = [m for m in self.ws.sent if m.get("type") == "mrc_message"
                and m.get("from_user") == "calcmandan"]
        self.assertEqual(chat, [],
                          "a different-case room name is a different room -- "
                          "its traffic must not leak into this session")

    def test_session_receives_a_broadcast_for_the_exact_same_case_room(self):
        _run(self.app._handle_join_room(
            self.ws_id, {"handle": "StingRay", "room": "MyRoom"}))
        self.ws.sent.clear()

        _run(self.app._on_upstream_packet(self._other_users_chat_packet("MyRoom")))

        chat = [m for m in self.ws.sent if m.get("type") == "mrc_message"
                and m.get("from_user") == "calcmandan"]
        self.assertEqual(len(chat), 1)

    def test_session_joined_with_lowercase_default_receives_the_hub_own_lobby_broadcasts(self):
        # The actual real-world scenario the original bug report was
        # about: a fresh join via the web UI's own default room value
        # (now lowercase "lobby", matching the hub's own canonical name
        # for it) must receive that room's real traffic normally.
        _run(self.app._handle_join_room(
            self.ws_id, {"handle": "StingRay", "room": "lobby"}))
        self.ws.sent.clear()

        _run(self.app._on_upstream_packet(self._other_users_chat_packet("lobby")))

        chat = [m for m in self.ws.sent if m.get("type") == "mrc_message"
                and m.get("from_user") == "calcmandan"]
        self.assertEqual(len(chat), 1)


if __name__ == '__main__':
    unittest.main()
