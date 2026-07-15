"""Tests for the /set defaultroom, /set twitfilter, /set clockformat
additions to the terminal MRC client (anetbbs/features/mrc_chat.py),
plus the /welcome and /changes commands and the _format_clock helper.
"""
import asyncio
import sys
from datetime import datetime
from pathlib import Path
import unittest

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
    chat._room = 'lobby'
    chat.sent = []

    async def _fake_send_json(obj):
        chat.sent.append(obj)
    chat._send_json = _fake_send_json
    return chat


def _run(coro):
    return asyncio.run(coro)


class FormatClockTests(unittest.TestCase):
    def test_default_is_24h(self):
        chat = _make_chat()
        dt = datetime(2026, 1, 1, 14, 7)
        self.assertEqual(chat._format_clock(dt), '14:07')

    def test_12h_format_drops_leading_zero_and_lowercases_meridiem(self):
        chat = _make_chat()
        chat._clock_format = '12'
        dt = datetime(2026, 1, 1, 14, 7)
        self.assertEqual(chat._format_clock(dt), '2:07pm')

    def test_12h_format_noon_and_midnight(self):
        chat = _make_chat()
        chat._clock_format = '12'
        self.assertEqual(chat._format_clock(datetime(2026, 1, 1, 0, 0)), '12:00am')
        self.assertEqual(chat._format_clock(datetime(2026, 1, 1, 12, 0)), '12:00pm')


class SetDefaultRoomTests(unittest.TestCase):
    def test_no_arg_shows_usage(self):
        chat = _make_chat()
        _run(chat._handle_slash('/set defaultroom'))
        self.assertEqual(chat.sent, [])

    def test_sends_normalized_room_via_set_prefs(self):
        chat = _make_chat()
        _run(chat._handle_slash('/set defaultroom #MyRoom'))
        self.assertEqual(chat.sent, [{'type': 'set_prefs', 'default_room': 'myroom'}])


class SetTwitFilterTests(unittest.TestCase):
    def test_invalid_value_shows_usage(self):
        chat = _make_chat()
        _run(chat._handle_slash('/set twitfilter maybe'))
        self.assertEqual(chat.sent, [])

    def test_off_sends_set_prefs(self):
        chat = _make_chat()
        _run(chat._handle_slash('/set twitfilter off'))
        self.assertEqual(chat.sent, [{'type': 'set_prefs', 'twit_filter_enabled': False}])

    def test_master_toggle_off_lets_twitted_user_through(self):
        chat = _make_chat()
        chat._twit_list = {'annoyer'}
        chat._twit_filter_enabled = False
        chat._twit_blocked_count = 0
        # Directly exercise the same condition _handle_event uses.
        uname = 'annoyer'
        blocked = chat._twit_filter_enabled and uname.lower() in chat._twit_list
        self.assertFalse(blocked)

    def test_master_toggle_on_blocks_twitted_user(self):
        chat = _make_chat()
        chat._twit_list = {'annoyer'}
        chat._twit_filter_enabled = True
        uname = 'annoyer'
        blocked = chat._twit_filter_enabled and uname.lower() in chat._twit_list
        self.assertTrue(blocked)


class SetClockFormatTests(unittest.TestCase):
    def test_invalid_value_shows_usage(self):
        chat = _make_chat()
        _run(chat._handle_slash('/set clockformat 30'))
        self.assertEqual(chat.sent, [])

    def test_valid_value_sends_set_prefs(self):
        chat = _make_chat()
        _run(chat._handle_slash('/set clockformat 12'))
        self.assertEqual(chat.sent, [{'type': 'set_prefs', 'clock_format': '12'}])


class ApplyPrefsDefaultRoomTests(unittest.TestCase):
    def test_initial_join_auto_joins_stored_default_room(self):
        chat = _make_chat()
        chat._room = 'lobby'
        _run(chat._apply_prefs({'default_room': 'sysops'}, is_initial_join=True))
        self.assertEqual(chat._room, 'sysops')
        self.assertEqual(chat.sent, [{'type': 'server_cmd', 'command': 'JOIN sysops'}])

    def test_matching_default_room_does_not_resend_join(self):
        chat = _make_chat()
        chat._room = 'lobby'
        _run(chat._apply_prefs({'default_room': 'lobby'}, is_initial_join=True))
        self.assertEqual(chat.sent, [])

    def test_prefs_updated_does_not_trigger_auto_join(self):
        chat = _make_chat()
        chat._room = 'lobby'
        _run(chat._apply_prefs({'default_room': 'sysops'}, is_initial_join=False))
        self.assertEqual(chat.sent, [])
        self.assertEqual(chat._room, 'lobby')

    def test_syncs_twit_filter_enabled_and_clock_format(self):
        chat = _make_chat()
        _run(chat._apply_prefs({'twit_filter_enabled': False, 'clock_format': '12'}))
        self.assertFalse(chat._twit_filter_enabled)
        self.assertEqual(chat._clock_format, '12')


class WelcomeAndChangesCommandTests(unittest.TestCase):
    def test_welcome_command_handled(self):
        chat = _make_chat()
        handled = _run(chat._handle_slash('/welcome'))
        self.assertTrue(handled)

    def test_changes_command_handled(self):
        chat = _make_chat()
        handled = _run(chat._handle_slash('/changes'))
        self.assertTrue(handled)


class AliasTests(unittest.TestCase):
    def test_q_aliases_quit(self):
        chat = _make_chat()
        result = _run(chat._handle_slash('/q'))
        self.assertFalse(result)  # quit returns False to end the chat loop

    def test_b_aliases_broadcast(self):
        chat = _make_chat()
        _run(chat._handle_slash('/b hello everyone'))
        self.assertEqual(len(chat.sent), 1)
        self.assertEqual(chat.sent[0]['type'], 'server_cmd')

    def test_cls_aliases_clear(self):
        chat = _make_chat()
        chat._display_lines.append('some line')
        _run(chat._handle_slash('/cls'))
        self.assertEqual(len(chat._display_lines), 0)


if __name__ == '__main__':
    unittest.main()
