"""Regression test for a real gap found live (2026-09-21) testing 2
simultaneous local MRC sessions (one via umrc-client, one via the web
client) against the same bridge: the hub's `BANNER:` reply carries NO
room or session scoping at all (to_user=CLIENT, from_room and to_room
both empty -- confirmed against a real wire capture:
`SERVER~~~CLIENT~~~BANNER:Listen to ArakNet Underground Scene Radio at
radio.araknet.xyz~`). _on_upstream_packet's existing fallback for an
unscoped CLIENT reply broadcasts to every locally in-room session, so
every session's own join-time BANNERS request (on by default) flooded
every OTHER already-connected session with a duplicate banner burst
too -- with N simultaneous users, everyone saw N copies.

Fixed by tracking which single nick is actually expecting a BANNER
reply right now (set right before the request goes out in
_send_join_payloads, with a short deadline) and routing to just them
while that's still live, falling through to the original
broadcast-everyone behavior otherwise -- a genuine unscoped
server-wide announcement with nobody currently joining should still
reach everyone, same as before this fix.
"""
import asyncio
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mrc.bridge.main import BridgeApp  # noqa: E402
from mrc.bridge.db import BridgeDB  # noqa: E402


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
    app._last_umrc_stats = (0, 0, 0, 0)
    app._pending_banner_nick = None
    app._pending_banner_until = 0.0
    app.mrc = AsyncMock()
    app.mrc.connected = True
    app.join_packet_delay_ms = 0
    app.announce_join_part = False
    app.request_banners_on_join = True
    app.request_motd_on_join = False
    app.join_message_tpl = "- {handle} has arrived."
    app.exit_message_tpl = "- {handle} has left chat."
    app.ctcp_room = "ctcp_echo_channel"
    app.userlist_refresh_on_server_events = False
    app.identify_required_mode = False
    app.post_identify_auto_join = True
    app.default_style_prefix = ""
    app.default_style_suffix = ""
    app.default_style_color = "07"
    app.rate_limiter = {}
    app.pending_disconnects = {}
    return app


def _banner_reply(text="Check /QUOTE CHANGELOG for new features"):
    # Real captured shape: from_room/to_room both empty, to_user=CLIENT.
    return {
        "from_user": "SERVER", "from_site": "", "from_room": "",
        "to_user": "CLIENT", "to_room": "",
        "message": f"BANNER:{text}",
    }


class BannerJoinScopingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _make_bridge(self._tmp.name)

        self.ws_a = _FakeWs()
        self.app.websockets[111] = self.ws_a
        self.app.db.save_session("111", {
            "handle": "Alice", "nick": "Alice", "room": "lobby", "in_room": True,
        })

        self.ws_b = _FakeWs()
        self.app.websockets[222] = self.ws_b
        self.app.db.save_session("222", {
            "handle": "Bob", "nick": "Bob", "room": "lobby", "in_room": True,
        })

    def test_banner_reply_only_reaches_the_pending_joiner(self):
        """Alice already in chat; Bob just joined (his own
        _send_join_payloads set the pending target) -- the hub's
        BANNER reply for Bob's request must reach only Bob, not
        Alice."""
        _run(self.app._send_join_payloads("Bob", "lobby"))
        self.ws_a.sent.clear()
        self.ws_b.sent.clear()

        _run(self.app._on_upstream_packet(_banner_reply()))

        self.assertEqual(len(self.ws_b.sent), 1)
        self.assertEqual(self.ws_b.sent[0]["message"], "BANNER:Check /QUOTE CHANGELOG for new features")
        self.assertEqual(self.ws_a.sent, [],
                        "Alice was already in chat and did not request "
                        "banners -- must not receive Bob's own join banner")

    def test_second_join_does_not_leak_to_first_joiner_either(self):
        """The reverse direction: Alice joins (gets her own banners),
        then Bob joins right after -- Bob's banner reply must not also
        go to Alice a second time."""
        _run(self.app._send_join_payloads("Alice", "lobby"))
        self.ws_a.sent.clear()
        self.ws_b.sent.clear()

        _run(self.app._send_join_payloads("Bob", "lobby"))
        self.ws_a.sent.clear()
        self.ws_b.sent.clear()

        _run(self.app._on_upstream_packet(_banner_reply()))

        self.assertEqual(len(self.ws_b.sent), 1)
        self.assertEqual(self.ws_a.sent, [])

    def test_unscoped_reply_with_no_pending_join_still_broadcasts(self):
        """No join in progress (pending target expired/unset) -- a
        genuine unscoped server-wide announcement must still reach
        every in-room session, same as before this fix."""
        self.app._pending_banner_nick = None
        self.app._pending_banner_until = 0.0

        _run(self.app._on_upstream_packet(_banner_reply("Server going down for maintenance")))

        self.assertEqual(len(self.ws_a.sent), 1)
        self.assertEqual(len(self.ws_b.sent), 1)

    def test_expired_pending_target_falls_back_to_broadcast(self):
        self.app._pending_banner_nick  = "Bob"
        self.app._pending_banner_until = time.monotonic() - 1.0  # already expired

        _run(self.app._on_upstream_packet(_banner_reply()))

        self.assertEqual(len(self.ws_a.sent), 1)
        self.assertEqual(len(self.ws_b.sent), 1)

    def test_non_banner_unscoped_client_reply_unaffected(self):
        """Only BANNER: replies get the scoped-routing treatment --
        confirm this doesn't accidentally change behavior for some
        other unscoped CLIENT message type."""
        self.app._pending_banner_nick  = "Bob"
        self.app._pending_banner_until = time.monotonic() + 5.0

        _run(self.app._on_upstream_packet({
            "from_user": "SERVER", "from_site": "", "from_room": "",
            "to_user": "CLIENT", "to_room": "",
            "message": "NOTIFY:something else entirely",
        }))

        self.assertEqual(len(self.ws_a.sent), 1)
        self.assertEqual(len(self.ws_b.sent), 1)


if __name__ == '__main__':
    unittest.main()
