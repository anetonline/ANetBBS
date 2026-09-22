"""Regression test for a real bug found live 2026-09-22, comparing an
actual A/B test the operator ran: the SAME MRC handle, through the
SAME shared upstream bridge connection, needed /identify every time it
joined via ANetBBS's own web and terminal clients, but never needed it
at all when a real umrc-client (github.com/codefenix-dev/uMRC) joined
instead.

Root-caused directly against uMRC's own real C source
(main.c, ~lines 2573-2622): the reference client sends its USERIP:
packet as one of its initial post-connect packets, strictly *before*
its own join announcement and NEWROOM --

    if (sendCmdPacket(&mrcSock, "IAMHERE", "")) { ... }
    ...
    if (strlen(gUserIP) > 0 && strcmp(gUserIP, "127.0.0.1") != 0) {
        sendCmdPacket(&mrcSock, "USERIP:", gUserIP);
    }
    // Announce user and place user into room after initial packets
    sendMsgPacket(&mrcSock, "NOTME", "", "", user.joinMessage);
    sendCmdPacket(&mrcSock, "NEWROOM::", gRoom);

ANetBBS's bridge sent USERIP: only afterward, inside
_send_join_payloads (called after NEWROOM and after in_room was
already flipped True) -- so by the time the hub evaluated the room
join, it had no IP to match against MRC Trust and fell back to
demanding /identify, even for a handle with genuinely valid recent
Trust. Fixed by sending USERIP first, matching the reference client's
own order exactly, via a new _send_userip() helper called at the start
of both _complete_join_after_identify (initial join) and
_apply_room_change (mid-chat /join <room>).
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


def _sent_message_kinds(mock_send_packet):
    """The .message field of every packet sent via app.mrc.send_packet,
    in call order -- lets a test assert relative ordering directly."""
    out = []
    for call in mock_send_packet.call_args_list:
        pkt = call.args[0] if call.args else call.kwargs.get("packet", "")
        out.append(MRCProtocol.parse_packet(pkt)["message"])
    return out


class UserIpPrecedesJoinOnInitialJoinTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _make_bridge(self._tmp.name)
        self.ws_id = 701
        self.ws = _FakeWs()
        self.app.websockets[self.ws_id] = self.ws

    def test_userip_sent_before_the_join_announcement(self):
        _run(self.app._handle_join_room(
            self.ws_id, {"handle": "StingRay2", "room": "lobby", "ip": "192.168.1.145"}))

        kinds = _sent_message_kinds(self.app.mrc.send_packet)
        userip_idx = next(i for i, m in enumerate(kinds) if m.startswith("USERIP:"))
        arrived_idx = next(i for i, m in enumerate(kinds) if "has arrived" in m)
        self.assertLess(userip_idx, arrived_idx,
                         f"USERIP must precede the join announcement, got order: {kinds}")

    def test_userip_sent_before_newroom(self):
        _run(self.app._handle_join_room(
            self.ws_id, {"handle": "StingRay2", "room": "lobby", "ip": "192.168.1.145"}))

        kinds = _sent_message_kinds(self.app.mrc.send_packet)
        userip_idx = next(i for i, m in enumerate(kinds) if m.startswith("USERIP:"))
        newroom_idx = next(i for i, m in enumerate(kinds) if m.upper().startswith("NEWROOM"))
        self.assertLess(userip_idx, newroom_idx,
                         f"USERIP must precede NEWROOM, got order: {kinds}")

    def test_no_known_ip_still_joins_fine_with_no_userip_packet(self):
        # No "ip" field and no _ws_remote_ip entry -- must not crash,
        # and simply sends no USERIP: packet at all (existing guard).
        _run(self.app._handle_join_room(self.ws_id, {"handle": "Dave", "room": "lobby"}))
        kinds = _sent_message_kinds(self.app.mrc.send_packet)
        self.assertTrue(any("has arrived" in m for m in kinds))
        self.assertFalse(any(m.startswith("USERIP:") for m in kinds))


class UserIpPrecedesJoinOnRoomChangeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _make_bridge(self._tmp.name)

    def test_userip_sent_before_join_announcement_on_room_change(self):
        session_id = "801"
        sess = {
            "handle": "StingRay2", "nick": "StingRay2", "room": "lobby",
            "remote_ip": "192.168.1.145", "in_room": True,
            "waiting_for_identify": False,
        }
        _run(self.app.db.save_session_async(session_id, sess))

        _run(self.app._apply_room_change(session_id, sess, "gopher"))

        kinds = _sent_message_kinds(self.app.mrc.send_packet)
        userip_idx = next(i for i, m in enumerate(kinds) if m.startswith("USERIP:"))
        arrived_idx = next(i for i, m in enumerate(kinds) if "has arrived" in m)
        self.assertLess(userip_idx, arrived_idx,
                         f"USERIP must precede the join announcement, got order: {kinds}")


if __name__ == '__main__':
    unittest.main()
