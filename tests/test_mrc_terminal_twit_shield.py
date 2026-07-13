"""Tests for MRC Phase D (feature-parity rework): terminal twit/ignore
list + broadcast shield in anetbbs/features/mrc_chat.py.

Filtering happens client-side (not bridge-side) by design -- the
bridge fans one event stream out to potentially many local clients, so
per-viewer muting can't be enforced centrally; the bridge's job is
only to persist the list/toggle (see mrc/bridge/main.py's
set_prefs/prefs_updated, tested separately in
test_mrc_bridge_prefs_and_userlist_event.py). These tests cover the
terminal's own filtering logic and its /twit and /shield commands.
"""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.core  # noqa: F401  (resolves a circular import if mrc_chat is imported first)
from anetbbs.features.mrc_chat import MRCChat


class _FakeSession:
    def __init__(self):
        self.user = {'username': 'tester'}
        self.written = []

    async def write(self, text):
        self.written.append(text)


def _make_chat(handle='StingRay'):
    chat = MRCChat(_FakeSession())
    chat._split_screen = False  # simplest _emit() path
    chat._handle = handle
    chat.sent = []

    async def _fake_send_json(obj):
        chat.sent.append(obj)
    chat._send_json = _fake_send_json
    return chat


def _run(coro):
    return asyncio.run(coro)


class ApplyPrefsTests(unittest.TestCase):
    def test_joined_event_populates_twit_list_and_shield(self):
        chat = _make_chat()
        _run(chat._handle_event({
            'type': 'joined', 'handle': 'StingRay', 'room': 'lobby',
            'prefs': {'twit_list': ['Mallory', 'Eve'], 'broadcast_shield': True},
        }))
        self.assertEqual(chat._twit_list, {'mallory', 'eve'})
        self.assertTrue(chat._broadcast_shield)

    def test_prefs_updated_event_syncs_state(self):
        chat = _make_chat()
        _run(chat._handle_event({
            'type': 'prefs_updated',
            'prefs': {'twit_list': ['Trudy'], 'broadcast_shield': False},
        }))
        self.assertEqual(chat._twit_list, {'trudy'})
        self.assertFalse(chat._broadcast_shield)

    def test_non_dict_prefs_ignored_without_error(self):
        chat = _make_chat()
        _run(chat._handle_event({'type': 'joined', 'handle': 'X', 'room': 'lobby'}))
        self.assertEqual(chat._twit_list, set())


class TwitFilterTests(unittest.TestCase):
    def test_message_from_twitted_user_dropped_and_counted(self):
        chat = _make_chat()
        chat._twit_list = {'mallory'}
        _run(chat._handle_event({
            'type': 'mrc_message', 'from_user': 'Mallory', 'from_site': 'evilbbs',
            'from_room': 'lobby', 'to_user': '', 'to_room': 'lobby',
            'message': 'buy crypto now',
        }))
        self.assertEqual(chat._twit_blocked_count, 1)
        self.assertEqual(chat.session.written, [])

    def test_case_insensitive_match(self):
        chat = _make_chat()
        chat._twit_list = {'mallory'}
        _run(chat._handle_event({
            'type': 'mrc_message', 'from_user': 'MALLORY', 'from_room': 'lobby',
            'to_room': 'lobby', 'message': 'hi',
        }))
        self.assertEqual(chat._twit_blocked_count, 1)

    def test_message_from_non_twitted_user_shown(self):
        chat = _make_chat()
        chat._twit_list = {'mallory'}
        _run(chat._handle_event({
            'type': 'mrc_message', 'from_user': 'Alice', 'from_room': 'lobby',
            'to_room': 'lobby', 'message': 'hello everyone',
        }))
        self.assertEqual(chat._twit_blocked_count, 0)
        self.assertTrue(any('hello everyone' in w for w in chat.session.written))


class BroadcastShieldFilterTests(unittest.TestCase):
    def test_broadcast_shaped_message_dropped_when_shield_on(self):
        chat = _make_chat()
        chat._broadcast_shield = True
        _run(chat._handle_event({
            'type': 'mrc_message', 'from_user': 'SysopA', 'from_room': '',
            'to_user': '', 'to_room': '', 'message': 'server maintenance in 5 min',
        }))
        self.assertEqual(chat._shield_blocked_count, 1)
        self.assertEqual(chat.session.written, [])

    def test_broadcast_shaped_message_shown_when_shield_off(self):
        chat = _make_chat()
        chat._broadcast_shield = False
        _run(chat._handle_event({
            'type': 'mrc_message', 'from_user': 'SysopA', 'from_room': '',
            'to_user': '', 'to_room': '', 'message': 'server maintenance in 5 min',
        }))
        self.assertEqual(chat._shield_blocked_count, 0)
        self.assertTrue(any('server maintenance' in w for w in chat.session.written))

    def test_room_message_not_treated_as_broadcast_even_with_shield_on(self):
        chat = _make_chat()
        chat._broadcast_shield = True
        _run(chat._handle_event({
            'type': 'mrc_message', 'from_user': 'Alice', 'from_room': 'lobby',
            'to_user': '', 'to_room': 'lobby', 'message': 'normal room chat',
        }))
        self.assertEqual(chat._shield_blocked_count, 0)
        self.assertTrue(any('normal room chat' in w for w in chat.session.written))


class TwitCommandTests(unittest.TestCase):
    def test_twit_add_sends_updated_list(self):
        chat = _make_chat()
        chat._twit_list = {'existing'}
        _run(chat._handle_slash('/twit add Mallory'))
        self.assertEqual(len(chat.sent), 1)
        self.assertEqual(chat.sent[0]['type'], 'set_prefs')
        self.assertEqual(set(chat.sent[0]['twit_list']), {'existing', 'mallory'})

    def test_twit_del_sends_updated_list(self):
        chat = _make_chat()
        chat._twit_list = {'mallory', 'eve'}
        _run(chat._handle_slash('/twit del mallory'))
        self.assertEqual(chat.sent[0]['twit_list'], ['eve'])

    def test_twit_add_rejects_reserved_name(self):
        chat = _make_chat()
        _run(chat._handle_slash('/twit add SERVER'))
        self.assertEqual(chat.sent, [])

    def test_twit_clear_sends_empty_list(self):
        chat = _make_chat()
        chat._twit_list = {'mallory'}
        _run(chat._handle_slash('/twit clear'))
        self.assertEqual(chat.sent[0]['twit_list'], [])

    def test_twit_list_shows_current_entries_no_wire_traffic(self):
        chat = _make_chat()
        chat._twit_list = {'mallory'}
        _run(chat._handle_slash('/twit list'))
        self.assertEqual(chat.sent, [])
        self.assertTrue(any('mallory' in w.lower() for w in chat.session.written))


class ShieldCommandTests(unittest.TestCase):
    def test_shield_on_sends_set_prefs(self):
        chat = _make_chat()
        _run(chat._handle_slash('/shield on'))
        self.assertEqual(chat.sent, [{'type': 'set_prefs', 'broadcast_shield': True}])

    def test_shield_off_sends_set_prefs(self):
        chat = _make_chat()
        _run(chat._handle_slash('/shield off'))
        self.assertEqual(chat.sent, [{'type': 'set_prefs', 'broadcast_shield': False}])

    def test_shield_no_arg_shows_status_no_wire_traffic(self):
        chat = _make_chat()
        chat._broadcast_shield = True
        _run(chat._handle_slash('/shield'))
        self.assertEqual(chat.sent, [])

    def test_broadcast_refused_locally_when_shield_on(self):
        chat = _make_chat()
        chat._broadcast_shield = True
        _run(chat._handle_slash('/broadcast server going down'))
        self.assertEqual(chat.sent, [])

    def test_broadcast_sent_when_shield_off(self):
        chat = _make_chat()
        chat._broadcast_shield = False
        _run(chat._handle_slash('/broadcast server going down'))
        self.assertEqual(len(chat.sent), 1)
        self.assertEqual(chat.sent[0]['type'], 'server_cmd')


if __name__ == '__main__':
    unittest.main()
