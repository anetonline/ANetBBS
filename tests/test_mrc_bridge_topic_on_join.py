"""Regression test for a real live report: "the topic does not show up
unless I change it. then it shows" -- and, after a first (wrong) fix
attempt, still "still not showing yet."

The first theory (the hub needs an explicit query, so send a bare
"TOPIC" command at join like BANNERS/MOTD/CHATTERS) turned out to be
based on a wrong protocol assumption -- checked against the real
reference client source (anetmrc_v1.3.9/src/helper_protocol.c): its own
connect sequence sends handshake/capabilities/userip/termsize/bbsmeta/
info_fields/NEWROOM/IAMHERE and nothing else; there is no topic query in
its vocabulary at all (a bare "/topic" with no argument just prints a
local usage error, never touches the wire). NEWTOPIC: is the only
outbound topic-related packet it ever sends, and that's the *set*
command. Which means the hub must reply to NEWROOM itself with an
unprompted ROOMTOPIC: -- and that reply was simply being dropped.

The real root cause: `_complete_join_after_identify` only flipped
`sess["in_room"]` to True *after* `_send_join_payloads()` finished
sending BANNERS/MOTD/userlist-control/CHATTERS (each a real round trip
to the hub, ~500ms+ minimum just from the artificial _sleep_delay()s,
more with real network latency). But every SERVER->CLIENT room
broadcast -- ROOMTOPIC included -- is only delivered to sessions where
`in_room` is already True (`_on_upstream_packet`'s "special == CLIENT"
branch, via `_sessions_in_room()`). Any hub reply arriving during that
whole payload-sending window, including an unprompted ROOMTOPIC right
after NEWROOM, was silently dropped for that session -- explaining both
the original symptom and why the join-time TOPIC query "fix" changed
nothing (its own reply, if the hub even understood it, would have
arrived during that same still-not-in_room window).

Fix: `in_room` (and the associated session save + _sync_mystic_rooms())
now happens right after NEWROOM is sent, before _send_join_payloads()
runs -- matching the moment the hub itself actually considers the
caller joined.
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
    app.announce_join_part = False
    app.request_banners_on_join = True
    app.request_motd_on_join = True
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


class InRoomSetBeforePayloadSequenceTests(unittest.TestCase):
    def test_in_room_is_true_before_send_join_payloads_runs(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        app = _make_bridge(tmp.name)
        ws_id_str = "111"
        app.websockets[111] = _FakeWs()
        sess = {"handle": "Alice", "nick": "Alice", "room": "lobby", "in_room": False}
        app.db.save_session(ws_id_str, sess)

        seen = {}

        async def fake_send_join_payloads(eff_nick, room, remote_ip=""):
            seen["in_room"] = app.db.get_session(ws_id_str).get("in_room")

        app._send_join_payloads = fake_send_join_payloads

        _run(app._complete_join_after_identify(ws_id_str, sess))

        self.assertTrue(
            seen.get("in_room"),
            "in_room must already be True before _send_join_payloads runs, "
            "or any hub reply arriving during that ~500ms+ sequence "
            "(including an unprompted ROOMTOPIC right after NEWROOM) "
            "gets silently dropped -- see _sessions_in_room()'s in_room gate")

    def test_sync_mystic_rooms_also_runs_before_send_join_payloads(self):
        # Same class of gap the mid-session-room-change and reconnect
        # fixes closed elsewhere -- the mystic backend's file-IPC
        # watcher needs to already be polling the room's inbound
        # directory before any reply could plausibly arrive, not after.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        app = _make_bridge(tmp.name)
        ws_id_str = "222"
        app.websockets[222] = _FakeWs()
        sess = {"handle": "Bob", "nick": "Bob", "room": "lobby", "in_room": False}
        app.db.save_session(ws_id_str, sess)

        call_order = []
        app._sync_mystic_rooms = AsyncMock(side_effect=lambda: call_order.append("sync"))

        async def fake_send_join_payloads(eff_nick, room, remote_ip=""):
            call_order.append("payloads")

        app._send_join_payloads = fake_send_join_payloads

        _run(app._complete_join_after_identify(ws_id_str, sess))

        self.assertEqual(call_order, ["sync", "payloads"])


class EarlyRoomTopicReplyDeliveredTests(unittest.TestCase):
    """End-to-end: a ROOMTOPIC-shaped CLIENT broadcast that arrives
    while _send_join_payloads() is still mid-sequence must still reach
    the caller's websocket, not get dropped."""

    def test_roomtopic_arriving_mid_payload_sequence_is_delivered(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        app = _make_bridge(tmp.name)
        ws_id = 333
        ws = _FakeWs()
        app.websockets[ws_id] = ws
        ws_id_str = str(ws_id)
        sess = {"handle": "Carol", "nick": "Carol", "room": "lobby", "in_room": False}
        app.db.save_session(ws_id_str, sess)

        async def fake_send_join_payloads(eff_nick, room, remote_ip=""):
            # Simulates the hub replying to NEWROOM with an unprompted
            # ROOMTOPIC: while this sequence is still running.
            await app._on_upstream_packet({
                "from_user": "SERVER", "from_site": "hub", "from_room": "lobby",
                "to_user": "CLIENT", "to_room": "lobby",
                "message": "ROOMTOPIC:lobby:Welcome to the lobby",
            })

        app._send_join_payloads = fake_send_join_payloads

        _run(app._complete_join_after_identify(ws_id_str, sess))

        bodies = [m.get("message", "") for m in ws.sent if m.get("type") == "mrc_message"]
        self.assertTrue(
            any("ROOMTOPIC:lobby:Welcome to the lobby" in b for b in bodies),
            f"ROOMTOPIC reply arriving mid-join-sequence was dropped, sent={ws.sent}")


if __name__ == '__main__':
    unittest.main()
