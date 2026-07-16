"""Regression test for the real, live-evidence-backed fix to "still have
to /identify every single time" -- a full raw packet transcript
(MRC_BRIDGE_LOG_LEVEL=DEBUG) captured on the actual live server showed:

  LOGOFF sent (fromRoom=lobby, toRoom=lobby -- spec-correct)
    -> reconnect on the SAME still-open bridge<->hub connection
    -> NEWROOM sent
    -> "Cannot join ROOM, please IDENTIFY to use this handle"

The bridge<->hub TCP connection never dropped between the two joins --
no reconnect, same session throughout -- yet the hub still demanded a
fresh identify immediately after LOGOFF. Sending LOGOFF appears to end
the hub's MRC Trust state for that handle immediately, independent of
the documented "30 days" window (which must apply to something else,
e.g. registration validity, not surviving a logoff/rejoin cycle).

This bridge holds ONE persistent shared connection to the hub per BBS
install across every local caller's join/leave -- there's no need to
tell the hub "this handle is logging off" the way a single-session
client would, since the underlying hub session doesn't actually need
to end. LOGOFF is now never sent on an individual caller leaving a
room (neither the explicit /quit path nor the abrupt-disconnect grace
path); NOTME's "has left chat" message still covers the visible
room-presence announcement other users see.
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
    app.ws_disconnect_grace_seconds = 0.01
    return app


def _sent_bodies(mock_send_packet):
    out = []
    for call in mock_send_packet.call_args_list:
        pkt = call.args[0] if call.args else call.kwargs.get("packet", "")
        out.append(MRCProtocol.parse_packet(pkt)["message"])
    return out


class NoLogoffOnExplicitLeaveTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _make_bridge(self._tmp.name)
        self.ws_id = 111
        self.ws = _FakeWs()
        self.app.websockets[self.ws_id] = self.ws
        self.app.db.save_session(str(self.ws_id), {
            "handle": "StingRay", "nick": "StingRay", "room": "lobby",
            "in_room": True,
        })

    def test_leave_room_does_not_send_logoff(self):
        _run(self.app._handle_leave_room(self.ws_id, {}))
        bodies = _sent_bodies(self.app.mrc.send_packet)
        self.assertFalse(any(b.upper() == "LOGOFF" for b in bodies),
                         "LOGOFF must not be sent on an individual leave -- "
                         "confirmed live to end the hub's MRC Trust state "
                         "for the handle immediately")

    def test_leave_room_still_sends_notme_departure_message(self):
        _run(self.app._handle_leave_room(self.ws_id, {}))
        bodies = _sent_bodies(self.app.mrc.send_packet)
        self.assertTrue(any("has left chat" in b for b in bodies),
                        "the visible room-presence departure message must "
                        "still be sent even though LOGOFF is not")


class NoLogoffOnAbruptDisconnectTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _make_bridge(self._tmp.name)
        self.ws_id = 222
        self.app.db.save_session(str(self.ws_id), {
            "handle": "StingRay", "nick": "StingRay", "room": "lobby",
            "in_room": True,
        })

    def test_delayed_disconnect_does_not_send_logoff(self):
        _run(self.app._delayed_session_logoff(self.ws_id, "StingRay", "lobby"))
        bodies = _sent_bodies(self.app.mrc.send_packet)
        self.assertFalse(any(b.upper() == "LOGOFF" for b in bodies))

    def test_delayed_disconnect_still_sends_notme_departure_message(self):
        _run(self.app._delayed_session_logoff(self.ws_id, "StingRay", "lobby"))
        bodies = _sent_bodies(self.app.mrc.send_packet)
        self.assertTrue(any("has left chat" in b for b in bodies))


if __name__ == '__main__':
    unittest.main()
