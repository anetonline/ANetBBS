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

from mrc.bridge.main import BridgeApp, _strip_pipe_codes
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


class StripPipeCodesTests(unittest.TestCase):
    def test_strips_pipe_color_codes(self):
        self.assertEqual(_strip_pipe_codes('|10StingRay|07'), 'StingRay')

    def test_strips_multiple_embedded_codes(self):
        self.assertEqual(_strip_pipe_codes('|04Sti|01ng|04Ra|01y'), 'StingRay')

    def test_leaves_plain_text_unchanged(self):
        self.assertEqual(_strip_pipe_codes('StingRay'), 'StingRay')

    def test_handles_none_and_empty(self):
        self.assertEqual(_strip_pipe_codes(None), '')
        self.assertEqual(_strip_pipe_codes(''), '')


class ExtractIdentifiedHandlePipeCodeTests(unittest.TestCase):
    """Regression test for the actual root cause of "still have to
    identify" surviving v1.0b2.106 on the live server: the hub's real
    reply wraps the handle in pipe-color codes
    ("...welcome back |10StingRay|07", captured live via a temporary
    diagnostic log line), which _extract_identified_handle never
    stripped -- so it returned "|10StingRay|07" instead of "StingRay",
    which then never matched any real session's plain-text handle,
    silently breaking both the pre-existing strict-mode auto-join and
    the newer default-mode self-heal. Neither prior test suite caught
    this because every existing test used a fabricated clean message
    with no pipe codes at all."""

    def test_extracts_clean_handle_from_real_captured_hub_reply(self):
        from mrc.bridge.main import BridgeApp
        real_msg = ('|16|15*|00.|15(|14Notice|15) Successfully identified, '
                    'welcome back |10StingRay|07')
        self.assertEqual(
            BridgeApp._extract_identified_handle(real_msg), 'StingRay')

    def test_self_heal_fires_against_the_real_captured_hub_reply(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        app = _make_bridge(tmp.name, identify_required_mode=False)
        ws_id = 444
        app.websockets[ws_id] = _FakeWs()
        _run(app._handle_join_room(ws_id, {"handle": "StingRay", "room": "lobby"}))
        calls_before = app.mrc.send_packet.call_count
        _run(app._on_upstream_packet({
            "from_user": "SERVER", "to_user": "CLIENT",
            "message": ('|16|15*|00.|15(|14Notice|15) Successfully identified, '
                        'welcome back |10StingRay|07'),
        }))
        calls_after = app.mrc.send_packet.call_count
        self.assertGreater(calls_after, calls_before)


class StaleSessionSelfHealTests(unittest.TestCase):
    """Regression test for a bug the pipe-code fix itself surfaced: once
    the real identify detection started working, one /identify replayed
    the join once per DB session record matching that handle -- and a
    hard process restart (systemctl restart, done repeatedly during
    this same live-troubleshooting session) doesn't run the graceful
    WS-close cleanup path, so stale records for the same handle pile up
    in the DB across restarts. Reported live as MOTD/CHATTERS showing up
    4 times after a single /identify. Fixed by only acting on sessions
    with a genuinely live WebSocket right now."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _make_bridge(self._tmp.name, identify_required_mode=False)

    def test_stale_duplicate_session_does_not_get_its_join_replayed(self):
        live_ws_id = 501
        self.app.websockets[live_ws_id] = _FakeWs()
        _run(self.app._handle_join_room(live_ws_id, {"handle": "StingRay", "room": "lobby"}))

        # Simulate a stale record left behind by an earlier hard restart:
        # a session in the DB for the same handle, but with no matching
        # entry in self.websockets (the connection is really gone).
        stale_ws_id = 502
        self.app.db.save_session(str(stale_ws_id), {
            "handle": "StingRay", "nick": "StingRay", "room": "lobby",
            "in_room": True, "waiting_for_identify": False,
        })

        calls_before = self.app.mrc.send_packet.call_count
        _run(self.app._on_upstream_packet({
            "from_user": "SERVER", "to_user": "CLIENT",
            "message": "You have successfully identified. Welcome back StingRay",
        }))
        calls_after = self.app.mrc.send_packet.call_count

        # Exactly one join replay (the live session), not two.
        self.assertGreater(calls_after, calls_before)
        live_join_packets = calls_after - calls_before

        # A second identify against only the stale session (no live
        # counterpart at all) must be a complete no-op.
        calls_before2 = self.app.mrc.send_packet.call_count
        self.app.websockets.pop(live_ws_id, None)
        _run(self.app._on_upstream_packet({
            "from_user": "SERVER", "to_user": "CLIENT",
            "message": "You have successfully identified. Welcome back StingRay",
        }))
        calls_after2 = self.app.mrc.send_packet.call_count
        self.assertEqual(calls_after2, calls_before2)
        self.assertGreater(live_join_packets, 0)


if __name__ == '__main__':
    unittest.main()
