"""Tests for a real bug found live: callers were blocked from chatting
at all -- couldn't send a single message -- until they ran /identify
against a *registered* MRC account, on every single connect, even for
casual/unregistered handles.

Root-caused against the real reference C client
(anetmrc_v1.3.9/src/helper_protocol.c): it sends NEWROOM and joins the
room unconditionally right after the handshake, never waiting on
identify -- /identify is purely optional ("MRC Trust" for a registered
handle), never a requirement to participate. ANetBBS's bridge invented
an identify-gate (`identify_required_mode`) defaulting to True with no
documented way to discover or disable it (not even in
config.example.json), silently blocking every caller on every install.

Fix: default changed to False (verified-correct, matches the reference
client), and _handle_join_room now actually completes the join
immediately in that case instead of requiring a second flag
(post_identify_auto_join) that was *also* never documented or set.
identify_required_mode=True (opt-in strict mode, for an admin who
deliberately wants it) is unchanged/still supported.
"""
import asyncio
import json
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


def _make_bridge(tmp_dir, identify_required_mode=False, post_identify_auto_join=False):
    app = object.__new__(BridgeApp)
    app.config = {"bridge_bbs": "TestBBS"}
    app.db = BridgeDB(tmp_dir)
    app.websockets = {}
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
    app.identify_required_mode = identify_required_mode
    app.post_identify_auto_join = post_identify_auto_join
    app.default_style_prefix = ""
    app.default_style_suffix = ""
    app.default_style_color = "07"
    app.rate_limiter = {}
    app.pending_disconnects = {}
    return app


class DefaultConfigTests(unittest.TestCase):
    def test_fresh_install_default_does_not_require_identify(self):
        # A real BridgeApp built from a config file that (like every
        # existing install's config.json, and the old
        # config.example.json before this fix) never mentions
        # identify_required_mode at all -- must default to False.
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.json"
            cfg_path.write_text(json.dumps({
                "mrc_host": "example.invalid",
                "mrc_port": 5000,
                "bridge_bbs": "TestBBS",
                "data_dir": str(Path(tmp) / "data"),
            }))
            app = BridgeApp(config_path=str(cfg_path))
            self.assertFalse(app.identify_required_mode)
            self.assertFalse(app.post_identify_auto_join)

    def test_explicit_true_in_config_is_honored(self):
        # An admin who deliberately wants strict mode can still opt in.
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.json"
            cfg_path.write_text(json.dumps({
                "mrc_host": "example.invalid",
                "mrc_port": 5000,
                "bridge_bbs": "TestBBS",
                "data_dir": str(Path(tmp) / "data"),
                "identify_required_mode": True,
            }))
            app = BridgeApp(config_path=str(cfg_path))
            self.assertTrue(app.identify_required_mode)


class ImmediateJoinTests(unittest.TestCase):
    """identify_required_mode=False (the new default): join_room alone
    should be enough to chat, no /identify required."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _make_bridge(self._tmp.name, identify_required_mode=False)
        self.ws_id = 111
        self.ws = _FakeWs()
        self.app.websockets[self.ws_id] = self.ws

    def test_session_is_in_room_immediately_after_join(self):
        _run(self.app._handle_join_room(self.ws_id, {"handle": "Alice", "room": "lobby"}))
        sess = self.app.db.get_session(str(self.ws_id))
        self.assertTrue(sess["in_room"])
        self.assertFalse(sess["waiting_for_identify"])

    def test_can_send_a_message_without_ever_identifying(self):
        _run(self.app._handle_join_room(self.ws_id, {"handle": "Alice", "room": "lobby"}))
        self.ws.sent.clear()
        _run(self.app._handle_send_message(self.ws_id, {"message": "hello"}))
        # No error about "Not in a room yet" -- the real reported bug.
        errors = [m for m in self.ws.sent if m.get("type") == "error"]
        self.assertEqual(errors, [])

    def test_joined_message_confirms_already_in_not_a_pending_step(self):
        # /identify is still mentioned -- matching the reference
        # client's own permanent, non-blocking "Use /identify password
        # for MRC Trust" connect notice -- but purely as an FYI, not
        # phrased as a required follow-up step the way the old
        # "Ready as X. If registered: /identify <pass> then /join room."
        # (still used in strict mode, see StrictModeUnchangedTests) is.
        _run(self.app._handle_join_room(self.ws_id, {"handle": "Alice", "room": "lobby"}))
        joined_evt = next(m for m in self.ws.sent if m.get("type") == "joined")
        self.assertIn("Joined", joined_evt["message"])
        self.assertNotIn("then /join", joined_evt["message"])
        self.assertIn("/identify", joined_evt["message"])

    def test_userlist_sent_exactly_once_not_duplicated(self):
        # _complete_join_after_identify (now called immediately) already
        # sends one via _send_join_payloads -- the old code path also
        # had a second, separate _send_userlist_control call after it,
        # which would double-send once this became the default path.
        _run(self.app._handle_join_room(self.ws_id, {"handle": "Alice", "room": "lobby"}))
        userlist_events = [m for m in self.ws.sent if m.get("type") == "userlist"]
        self.assertLessEqual(len(userlist_events), 1)


class StrictModeUnchangedTests(unittest.TestCase):
    """identify_required_mode=True (opt-in): the pre-existing gated
    behavior must still work for an admin who deliberately wants it."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _make_bridge(self._tmp.name, identify_required_mode=True,
                                 post_identify_auto_join=True)
        self.ws_id = 222
        self.ws = _FakeWs()
        self.app.websockets[self.ws_id] = self.ws

    def test_session_not_in_room_until_identified(self):
        _run(self.app._handle_join_room(self.ws_id, {"handle": "Bob", "room": "lobby"}))
        sess = self.app.db.get_session(str(self.ws_id))
        self.assertFalse(sess["in_room"])
        self.assertTrue(sess["waiting_for_identify"])

    def test_sending_before_identify_is_blocked(self):
        _run(self.app._handle_join_room(self.ws_id, {"handle": "Bob", "room": "lobby"}))
        self.ws.sent.clear()
        _run(self.app._handle_send_message(self.ws_id, {"message": "hello"}))
        errors = [m for m in self.ws.sent if m.get("type") == "error"]
        self.assertEqual(len(errors), 1)
        self.assertIn("Not in a room yet", errors[0]["message"])

    def test_successful_identify_completes_the_join(self):
        _run(self.app._handle_join_room(self.ws_id, {"handle": "Bob", "room": "lobby"}))
        _run(self.app._on_upstream_packet({
            "from_user": "SERVER", "to_user": "CLIENT",
            "message": "You have successfully identified. Welcome back Bob",
        }))
        sess = self.app.db.get_session(str(self.ws_id))
        self.assertTrue(sess["in_room"])
        self.assertFalse(sess["waiting_for_identify"])


class DefaultModeIdentifySelfHealTests(unittest.TestCase):
    """Regression test for a real bug found live on the Pi: in the new
    default (non-strict) mode, _handle_join_room's optimistic join can
    be silently rejected by the hub for a REGISTERED-but-unidentified
    handle (hub reply: "Cannot join ROOM, please IDENTIFY to use this
    handle") -- the bridge still believed in_room=True and went on to
    forward chat sends, which the hub then bounced back with "No route
    to a room from your user, /join a room first." A successful
    /identify must re-send the join so the caller doesn't also have to
    remember to manually /join again afterward."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _make_bridge(self._tmp.name, identify_required_mode=False)
        self.ws_id = 333
        self.ws = _FakeWs()
        self.app.websockets[self.ws_id] = self.ws

    def test_successful_identify_resends_join_even_when_already_in_room(self):
        _run(self.app._handle_join_room(self.ws_id, {"handle": "Carol", "room": "lobby"}))
        sess_before = self.app.db.get_session(str(self.ws_id))
        self.assertTrue(sess_before["in_room"])  # optimistic join already happened

        calls_before = self.app.mrc.send_packet.call_count
        _run(self.app._on_upstream_packet({
            "from_user": "SERVER", "to_user": "CLIENT",
            "message": "You have successfully identified. Welcome back Carol",
        }))
        calls_after = self.app.mrc.send_packet.call_count
        self.assertGreater(calls_after, calls_before)

        sess_after = self.app.db.get_session(str(self.ws_id))
        self.assertTrue(sess_after["in_room"])

    def test_other_handles_identify_does_not_resend_this_sessions_join(self):
        _run(self.app._handle_join_room(self.ws_id, {"handle": "Carol", "room": "lobby"}))
        calls_before = self.app.mrc.send_packet.call_count
        _run(self.app._on_upstream_packet({
            "from_user": "SERVER", "to_user": "CLIENT",
            "message": "You have successfully identified. Welcome back SomeoneElse",
        }))
        calls_after = self.app.mrc.send_packet.call_count
        self.assertEqual(calls_after, calls_before)


if __name__ == '__main__':
    unittest.main()
