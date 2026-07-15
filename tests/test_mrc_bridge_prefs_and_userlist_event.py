"""Tests for MRC bridge Phase A (multi-client feature-parity rework):
structured `userlist` event, `set_prefs`/`prefs_updated` round-trip,
per-user enter/leave/quit message templates, and profile prefs loaded
into the session at join time.

Follows the same object.__new__() BridgeApp construction pattern as
tests/test_mrc_bridge_userlist.py -- wires up only the attributes each
tested method actually touches, rather than building a real upstream
connection.
"""
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mrc.bridge.main import (
    BridgeApp, _parse_userlist_text, _resolve_message_template, _clamp_tz_offset,
)
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
    app.post_identify_auto_join = True
    app.default_style_prefix = ""
    app.default_style_suffix = ""
    app.default_style_color = "07"
    app.rate_limiter = {}
    app.pending_disconnects = {}
    return app


class ParseUserlistTextTests(unittest.TestCase):
    def test_parses_comma_separated_nicks(self):
        self.assertEqual(
            _parse_userlist_text("USERLIST:Alice,Bob,Carol"),
            ["Alice", "Bob", "Carol"])

    def test_strips_at_site_suffix(self):
        self.assertEqual(
            _parse_userlist_text("USERLIST:Alice@bbs1,Bob@bbs2"),
            ["Alice", "Bob"])

    def test_dedupes_preserving_first_occurrence_order(self):
        self.assertEqual(
            _parse_userlist_text("USERLIST:Alice,Bob,Alice"),
            ["Alice", "Bob"])

    def test_no_colon_returns_empty(self):
        self.assertEqual(_parse_userlist_text("garbage"), [])

    def test_empty_entries_skipped(self):
        self.assertEqual(
            _parse_userlist_text("USERLIST:Alice,,Bob,"),
            ["Alice", "Bob"])


class ResolveMessageTemplateTests(unittest.TestCase):
    def test_uses_global_default_when_no_per_user_template(self):
        msg = _resolve_message_template({}, "leave_msg_tpl", "- {handle} left.", "Alice")
        self.assertEqual(msg, "- Alice left.")

    def test_uses_per_user_template_when_set(self):
        sess = {"leave_msg_tpl": "Bye {handle}!"}
        msg = _resolve_message_template(sess, "leave_msg_tpl", "- {handle} left.", "Alice")
        self.assertEqual(msg, "Bye Alice!")

    def test_extra_appended_in_parens(self):
        msg = _resolve_message_template({}, "leave_msg_tpl", "- {handle} left.",
                                        "Alice", extra="gone fishing")
        self.assertEqual(msg, "- Alice left. (gone fishing)")


class StructuredUserlistEventTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _make_bridge(self._tmp.name)
        self.ws_id = 111
        self.ws = _FakeWs()
        self.app.websockets[self.ws_id] = self.ws
        self.app.db.save_session(str(self.ws_id), {
            "handle": "Alice", "nick": "Alice", "room": "lobby", "in_room": True,
        })

    def test_userlist_reply_sends_both_raw_and_structured_events(self):
        _run(self.app._on_upstream_packet({
            "from_user": "SERVER", "from_site": "hub", "from_room": "lobby",
            "to_user": "CLIENT", "to_room": "lobby",
            "message": "USERLIST:Alice,Bob@otherbbs",
        }))
        types = [m.get("type") for m in self.ws.sent]
        self.assertIn("mrc_message", types)
        self.assertIn("userlist", types)
        userlist_evt = next(m for m in self.ws.sent if m.get("type") == "userlist")
        self.assertEqual(userlist_evt["users"], ["Alice", "Bob"])
        self.assertEqual(userlist_evt["room"], "lobby")

    def test_non_userlist_client_message_has_no_structured_event(self):
        _run(self.app._on_upstream_packet({
            "from_user": "SERVER", "from_site": "hub", "from_room": "lobby",
            "to_user": "CLIENT", "to_room": "lobby",
            "message": "STATS:whatever text the hub sends",
        }))
        types = [m.get("type") for m in self.ws.sent]
        self.assertIn("mrc_message", types)
        self.assertNotIn("userlist", types)


class SetPrefsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _make_bridge(self._tmp.name)
        self.ws_id = 222
        self.ws = _FakeWs()
        self.app.websockets[self.ws_id] = self.ws
        self.app.db.save_session(str(self.ws_id), {
            "handle": "Alice", "nick": "Alice", "room": "lobby", "in_room": True,
        })

    def test_set_prefs_persists_to_session_and_profile(self):
        _run(self.app._handle_set_prefs(self.ws_id, {
            "twit_list": ["Mallory", "Eve"],
            "broadcast_shield": True,
            "ticker_enabled": False,
            "leave_msg_tpl": "Later, {handle}",
        }))
        self.assertEqual(len(self.ws.sent), 1)
        self.assertEqual(self.ws.sent[0]["type"], "prefs_updated")
        self.assertEqual(sorted(self.ws.sent[0]["prefs"]["twit_list"]),
                         sorted(["Mallory", "Eve"]))
        self.assertTrue(self.ws.sent[0]["prefs"]["broadcast_shield"])

        sess = self.app.db.get_session(str(self.ws_id))
        self.assertEqual(sorted(sess["twit_list"]), sorted(["Mallory", "Eve"]))
        self.assertTrue(sess["broadcast_shield"])
        self.assertFalse(sess["ticker_enabled"])

        prof = self.app.db.get_profile("Alice")
        self.assertEqual(sorted(prof["twit_list"]), sorted(["Mallory", "Eve"]))
        self.assertEqual(prof["leave_msg_tpl"], "Later, {handle}")

    def test_reserved_handles_cannot_be_twitted(self):
        _run(self.app._handle_set_prefs(self.ws_id, {
            "twit_list": ["SERVER", "CLIENT", "Mallory"],
        }))
        prefs = self.ws.sent[0]["prefs"]
        self.assertEqual(prefs["twit_list"], ["Mallory"])

    def test_no_recognized_fields_returns_error(self):
        _run(self.app._handle_set_prefs(self.ws_id, {"bogus_field": 1}))
        self.assertEqual(self.ws.sent[0]["type"], "error")

    def test_join_room_loads_persisted_prefs_into_session(self):
        _run(self.app._handle_set_prefs(self.ws_id, {
            "twit_list": ["Mallory"], "broadcast_shield": True,
        }))
        # Simulate a fresh join (new ws_id, same handle) picking up the
        # previously saved profile prefs.
        ws_id2 = 333
        ws2 = _FakeWs()
        self.app.websockets[ws_id2] = ws2
        _run(self.app._handle_join_room(ws_id2, {"handle": "Alice", "room": "lobby"}))
        sess2 = self.app.db.get_session(str(ws_id2))
        self.assertEqual(sess2["twit_list"], ["Mallory"])
        self.assertTrue(sess2["broadcast_shield"])


class ClampTzOffsetTests(unittest.TestCase):
    def test_valid_offset_passes_through(self):
        self.assertEqual(_clamp_tz_offset(-300), -300)

    def test_out_of_range_clamped(self):
        self.assertEqual(_clamp_tz_offset(-9999), -720)
        self.assertEqual(_clamp_tz_offset(9999), 840)

    def test_non_numeric_defaults_to_zero(self):
        self.assertEqual(_clamp_tz_offset("garbage"), 0)
        self.assertEqual(_clamp_tz_offset(None), 0)


class TzOffsetPrefsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _make_bridge(self._tmp.name)
        self.ws_id = 444
        self.ws = _FakeWs()
        self.app.websockets[self.ws_id] = self.ws
        self.app.db.save_session(str(self.ws_id), {
            "handle": "Alice", "nick": "Alice", "room": "lobby", "in_room": True,
        })

    def test_set_prefs_persists_tz_offset(self):
        _run(self.app._handle_set_prefs(self.ws_id, {"tz_offset": -300}))
        self.assertEqual(self.ws.sent[0]["prefs"]["tz_offset"], -300)
        sess = self.app.db.get_session(str(self.ws_id))
        self.assertEqual(sess["tz_offset"], -300)
        prof = self.app.db.get_profile("Alice")
        self.assertEqual(prof["tz_offset"], -300)

    def test_set_prefs_clamps_out_of_range_tz_offset(self):
        _run(self.app._handle_set_prefs(self.ws_id, {"tz_offset": 99999}))
        self.assertEqual(self.ws.sent[0]["prefs"]["tz_offset"], 840)

    def test_join_room_loads_persisted_tz_offset(self):
        _run(self.app._handle_set_prefs(self.ws_id, {"tz_offset": -300}))
        ws_id2 = 555
        ws2 = _FakeWs()
        self.app.websockets[ws_id2] = ws2
        _run(self.app._handle_join_room(ws_id2, {"handle": "Alice", "room": "lobby"}))
        sess2 = self.app.db.get_session(str(ws_id2))
        self.assertEqual(sess2["tz_offset"], -300)

    def test_default_tz_offset_is_zero(self):
        self.assertEqual(self.app._session_prefs({})["tz_offset"], 0)


class DefaultRoomTwitFilterClockFormatPrefsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _make_bridge(self._tmp.name)
        self.ws_id = 666
        self.ws = _FakeWs()
        self.app.websockets[self.ws_id] = self.ws
        self.app.db.save_session(str(self.ws_id), {
            "handle": "Alice", "nick": "Alice", "room": "lobby", "in_room": True,
        })

    def test_default_room_normalized_and_persisted(self):
        # MRCProtocol.norm_room() strips a leading '#' and swaps spaces
        # for underscores -- it does NOT lowercase (matches how `room`
        # itself is handled everywhere else in this file, e.g.
        # _handle_join_room). The terminal client lowercases client-side
        # before ever sending /set defaultroom, same as it does for
        # /join -- this test exercises the bridge layer alone.
        _run(self.app._handle_set_prefs(self.ws_id, {"default_room": "#My Room"}))
        self.assertEqual(self.ws.sent[0]["prefs"]["default_room"], "My_Room")
        prof = self.app.db.get_profile("Alice")
        self.assertEqual(prof["default_room"], "My_Room")

    def test_default_room_loaded_on_next_join(self):
        _run(self.app._handle_set_prefs(self.ws_id, {"default_room": "sysops"}))
        ws_id2 = 667
        ws2 = _FakeWs()
        self.app.websockets[ws_id2] = ws2
        _run(self.app._handle_join_room(ws_id2, {"handle": "Alice", "room": "lobby"}))
        sess2 = self.app.db.get_session(str(ws_id2))
        self.assertEqual(sess2["default_room"], "sysops")

    def test_default_room_defaults_empty(self):
        self.assertEqual(self.app._session_prefs({})["default_room"], "")

    def test_clock_format_invalid_value_falls_back_to_24(self):
        _run(self.app._handle_set_prefs(self.ws_id, {"clock_format": "banana"}))
        self.assertEqual(self.ws.sent[0]["prefs"]["clock_format"], "24")

    def test_clock_format_12_persists(self):
        _run(self.app._handle_set_prefs(self.ws_id, {"clock_format": "12"}))
        prof = self.app.db.get_profile("Alice")
        self.assertEqual(prof["clock_format"], "12")

    def test_clock_format_defaults_24(self):
        self.assertEqual(self.app._session_prefs({})["clock_format"], "24")

    def test_twit_filter_enabled_defaults_true(self):
        self.assertTrue(self.app._session_prefs({})["twit_filter_enabled"])

    def test_twit_filter_enabled_can_be_turned_off_and_persists(self):
        _run(self.app._handle_set_prefs(self.ws_id, {"twit_filter_enabled": False}))
        self.assertFalse(self.ws.sent[0]["prefs"]["twit_filter_enabled"])
        ws_id2 = 668
        ws2 = _FakeWs()
        self.app.websockets[ws_id2] = ws2
        _run(self.app._handle_join_room(ws_id2, {"handle": "Alice", "room": "lobby"}))
        sess2 = self.app.db.get_session(str(ws_id2))
        self.assertFalse(sess2["twit_filter_enabled"])


class LeaveRoomQuitMessageTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _make_bridge(self._tmp.name)
        self.ws_id = 444
        self.ws = _FakeWs()
        self.app.websockets[self.ws_id] = self.ws
        self.app.db.save_session(str(self.ws_id), {
            "handle": "Alice", "nick": "Alice", "room": "lobby", "in_room": True,
        })

    def test_explicit_quit_message_appended_in_parens(self):
        _run(self.app._handle_leave_room(self.ws_id, {"message": "gone fishing"}))
        sent_packets = [c.args[0] for c in self.app.mrc.send_packet.await_args_list]
        self.assertTrue(any("gone fishing" in p for p in sent_packets))

    def test_no_message_uses_plain_template(self):
        _run(self.app._handle_leave_room(self.ws_id, {}))
        sent_packets = [c.args[0] for c in self.app.mrc.send_packet.await_args_list]
        self.assertTrue(any("has left chat" in p for p in sent_packets))
        self.assertFalse(any("(" in p and ")" in p for p in sent_packets))

    def test_saved_quit_msg_used_when_no_explicit_override(self):
        sess = self.app.db.get_session(str(self.ws_id))
        sess["quit_msg"] = "brb"
        self.app.db.save_session(str(self.ws_id), sess)
        _run(self.app._handle_leave_room(self.ws_id, {}))
        sent_packets = [c.args[0] for c in self.app.mrc.send_packet.await_args_list]
        self.assertTrue(any("brb" in p for p in sent_packets))

    def test_explicit_override_wins_over_saved_quit_msg(self):
        sess = self.app.db.get_session(str(self.ws_id))
        sess["quit_msg"] = "brb"
        self.app.db.save_session(str(self.ws_id), sess)
        _run(self.app._handle_leave_room(self.ws_id, {"message": "for real this time"}))
        sent_packets = [c.args[0] for c in self.app.mrc.send_packet.await_args_list]
        self.assertTrue(any("for real this time" in p for p in sent_packets))
        self.assertFalse(any("brb" in p for p in sent_packets))


class StatsControlTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _make_bridge(self._tmp.name)

    def test_send_stats_control_sends_stats_packet(self):
        _run(self.app._send_stats_control("lobby"))
        self.app.mrc.send_packet.assert_awaited_once()
        sent = self.app.mrc.send_packet.await_args.args[0]
        self.assertIn("STATS", sent)


if __name__ == '__main__':
    unittest.main()
