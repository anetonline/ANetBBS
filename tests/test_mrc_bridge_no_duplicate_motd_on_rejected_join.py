"""Regression test for a real live UX complaint (2026-09-22): "the
biggest part ... not even JUST the having to re-identify, but the
blasted MOTD and chatters rolls past where it says you have to
identify. then you identify, and it shows the motd and chatters
again... motd and chatters should ONLY be shown after joining the
lobby."

Root cause: _complete_join_after_identify requested BANNERS/MOTD/
CHATTERS unconditionally, every single time it ran -- so a join the
hub went on to reject ("Cannot join ROOM, please IDENTIFY") still
showed a full MOTD/chatter-list scroll, immediately followed (once the
rejection notice itself arrived) by the "please identify" message, and
then a SECOND full MOTD/CHATTERS scroll once the caller actually
identified and the self-heal in _on_upstream_packet ran this same
function again for the join that really succeeded.

Fixed by having the rejection handler stamp a last_join_rejected_at
timestamp on the session, and _complete_join_after_identify check --
right before it would otherwise claim the room and request the
payloads -- whether THIS join attempt was rejected while it was still
sending the earlier packets (has-arrived/NEWROOM); if so, it stops
without requesting BANNERS/MOTD/CHATTERS at all, leaving the
identify-triggered re-run to show them exactly once, for the join that
actually succeeded.
"""
import asyncio
import sys
import tempfile
import time
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
    out = []
    for call in mock_send_packet.call_args_list:
        pkt = call.args[0] if call.args else call.kwargs.get("packet", "")
        out.append(MRCProtocol.parse_packet(pkt)["message"])
    return out


def _count_chatters_requests(mock_send_packet):
    return sum(1 for m in _sent_message_kinds(mock_send_packet)
                if m.upper() == "CHATTERS")


class NoDuplicateMotdOnRejectedJoinTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _make_bridge(self._tmp.name)
        self.ws_id = 901
        self.ws = _FakeWs()
        self.app.websockets[self.ws_id] = self.ws

    def _reject(self, handle):
        return self.app._on_upstream_packet({
            "from_user": "SERVER", "to_user": handle,
            "message": ('|16|15*|00.|15(|14Notice|15) Cannot join ROOM, '
                        'please IDENTIFY to use this handle'),
        })

    def _arm_rejection_mid_flight(self, handle):
        """Patches app.mrc.send_packet so that, on its very first call
        inside the join attempt about to run (the USERIP send, per the
        ordering fix above), it stamps last_join_rejected_at into the
        session -- simulating the rejection notice actually arriving
        during the has-arrived/NEWROOM send window, the way it really
        does on the wire (confirmed: well within the 80ms default
        join_packet_delay_ms in every real capture), rather than
        depending on real asyncio interleaving in a test with delay=0."""
        real_send_packet = self.app.mrc.send_packet
        state = {"armed": True}

        async def side_effect(pkt):
            if state["armed"]:
                state["armed"] = False
                sess = self.app.db.get_session(str(self.ws_id)) or {}
                sess["handle"] = handle
                sess["last_join_rejected_at"] = time.monotonic()
                self.app.db.save_session(str(self.ws_id), sess)
            return await real_send_packet(pkt)

        self.app.mrc.send_packet = AsyncMock(side_effect=side_effect)

    def test_rejected_initial_join_does_not_request_chatters(self):
        self._arm_rejection_mid_flight("StingRay2")
        _run(self.app._handle_join_room(
            self.ws_id, {"handle": "StingRay2", "room": "lobby"}))

        self.assertEqual(_count_chatters_requests(self.app.mrc.send_packet), 0)
        sess = self.app.db.get_session(str(self.ws_id))
        self.assertFalse(sess["in_room"])

    def test_successful_identify_after_rejection_shows_motd_exactly_once(self):
        self._arm_rejection_mid_flight("StingRay2")
        _run(self.app._handle_join_room(
            self.ws_id, {"handle": "StingRay2", "room": "lobby"}))
        self.assertEqual(_count_chatters_requests(self.app.mrc.send_packet), 0)

        _run(self.app._on_upstream_packet({
            "from_user": "SERVER", "to_user": "CLIENT",
            "message": "You have successfully identified. Welcome back StingRay2",
        }))

        # Exactly one CHATTERS request across the whole sequence: the
        # rejected initial attempt's request was suppressed, the
        # successful post-identify re-join is the only one that fires.
        self.assertEqual(_count_chatters_requests(self.app.mrc.send_packet), 1)

        sess = self.app.db.get_session(str(self.ws_id))
        self.assertTrue(sess["in_room"])

    def test_normal_unrejected_join_still_requests_chatters_as_before(self):
        # No regression for the common (non-rejected) case: a join that
        # never gets a rejection notice must still show its one MOTD/
        # CHATTERS normally.
        _run(self.app._handle_join_room(
            self.ws_id, {"handle": "StingRay", "room": "lobby"}))
        self.assertEqual(_count_chatters_requests(self.app.mrc.send_packet), 1)

        sess = self.app.db.get_session(str(self.ws_id))
        self.assertTrue(sess["in_room"])


if __name__ == '__main__':
    unittest.main()
