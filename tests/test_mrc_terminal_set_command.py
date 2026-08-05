"""Tests for MRC Phase E (feature-parity rework): the terminal /set
command and its supporting state (anetbbs/features/mrc_chat.py).

Also covers _style_payload -- the fix for a real bug found while
building this phase (see test_mrc_terminal_mentions.py's
TypingColorPersistenceTests for the arrow-key-cycling regression test;
these tests cover the same helper from the /set command's side).
"""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.core  # noqa: F401  (resolves a circular import if mrc_chat is imported first)
from anetbbs.features.mrc_chat import MRCChat, _parse_tz_offset


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


class StylePayloadTests(unittest.TestCase):
    def test_empty_style_state_produces_sane_defaults(self):
        chat = _make_chat()
        payload = chat._style_payload()
        self.assertEqual(payload['type'], 'set_style')
        self.assertEqual(payload['prefix'], '')
        self.assertEqual(payload['suffix'], '')
        self.assertEqual(payload['color'], '07')

    def test_overrides_apply_on_top_of_known_state(self):
        chat = _make_chat()
        chat._style = {'prefix': '!', 'suffix': '<', 'color': '05'}
        payload = chat._style_payload(suffix='>>')
        self.assertEqual(payload['prefix'], '!')   # preserved
        self.assertEqual(payload['suffix'], '>>')  # overridden
        self.assertEqual(payload['color'], '05')   # preserved

    def test_color_fields_fall_back_to_base_color(self):
        chat = _make_chat()
        chat._style = {'color': '09'}
        payload = chat._style_payload()
        self.assertEqual(payload['prefix_color'], '09')
        self.assertEqual(payload['handle_color'], '09')
        self.assertEqual(payload['suffix_color'], '09')


class ApplyStyleTests(unittest.TestCase):
    def test_stores_full_style_dict_not_just_typing_color(self):
        chat = _make_chat()
        chat._apply_style({'prefix': '!', 'suffix': '>', 'typing_color': '12'})
        self.assertEqual(chat._style.get('prefix'), '!')
        self.assertEqual(chat._style.get('suffix'), '>')

    def test_non_dict_ignored(self):
        chat = _make_chat()
        chat._apply_style(None)
        self.assertEqual(chat._style, {})


class SetCommandStyleTests(unittest.TestCase):
    def test_set_prefix_sends_full_payload_preserving_suffix(self):
        chat = _make_chat()
        chat._style = {'suffix': '|12>'}
        _run(chat._handle_slash('/set prefix !!'))
        self.assertEqual(len(chat.sent), 1)
        self.assertEqual(chat.sent[0]['type'], 'set_style')
        self.assertEqual(chat.sent[0]['prefix'], '!!')
        self.assertEqual(chat.sent[0]['suffix'], '|12>')

    def test_set_prefix_no_value_shows_usage_no_wire_traffic(self):
        chat = _make_chat()
        _run(chat._handle_slash('/set prefix'))
        self.assertEqual(chat.sent, [])

    def test_set_color_valid_sends_all_color_fields(self):
        chat = _make_chat()
        _run(chat._handle_slash('/set color 09'))
        self.assertEqual(len(chat.sent), 1)
        p = chat.sent[0]
        self.assertEqual(p['color'], '09')
        self.assertEqual(p['prefix_color'], '09')
        self.assertEqual(p['handle_color'], '09')
        self.assertEqual(p['suffix_color'], '09')

    def test_set_color_invalid_rejected_locally(self):
        chat = _make_chat()
        _run(chat._handle_slash('/set color 99'))
        self.assertEqual(chat.sent, [])
        _run(chat._handle_slash('/set color notanumber'))
        self.assertEqual(chat.sent, [])


class SetCommandPrefsTests(unittest.TestCase):
    def test_set_entermsg_sends_set_prefs(self):
        chat = _make_chat()
        _run(chat._handle_slash('/set entermsg Hi {handle}!'))
        self.assertEqual(chat.sent, [{'type': 'set_prefs', 'enter_msg_tpl': 'Hi {handle}!'}])

    def test_set_leavemsg_sends_set_prefs(self):
        chat = _make_chat()
        _run(chat._handle_slash('/set leavemsg Bye {handle}'))
        self.assertEqual(chat.sent, [{'type': 'set_prefs', 'leave_msg_tpl': 'Bye {handle}'}])

    def test_set_quitmsg_sends_set_prefs(self):
        chat = _make_chat()
        _run(chat._handle_slash('/set quitmsg gone fishing'))
        self.assertEqual(chat.sent, [{'type': 'set_prefs', 'quit_msg': 'gone fishing'}])

    def test_set_ticker_on_sends_set_prefs(self):
        chat = _make_chat()
        _run(chat._handle_slash('/set ticker on'))
        self.assertEqual(chat.sent, [{'type': 'set_prefs', 'ticker_enabled': True}])

    def test_set_ticker_invalid_value_rejected_locally(self):
        chat = _make_chat()
        _run(chat._handle_slash('/set ticker maybe'))
        self.assertEqual(chat.sent, [])


class SetCommandLocalOnlyTests(unittest.TestCase):
    def test_set_clock_is_no_longer_a_recognized_field(self):
        """`/set clock on|off` used to toggle the status-bar clock
        widget, which was removed (see test_mrc_terminal_sidebar_and_clock.py)
        in favor of a real ping/latency display -- confirms the field
        is gone cleanly (falls through to the normal "unknown field"
        error) rather than silently no-op'ing or crashing."""
        chat = _make_chat()
        _run(chat._handle_slash('/set clock off'))
        self.assertEqual(chat.sent, [])
        self.assertTrue(any('Unknown /set field' in line for line in chat.session.written))

    def test_set_list_shows_values_no_wire_traffic(self):
        chat = _make_chat()
        chat._style = {'prefix': '!', 'color': '05'}
        chat._enter_msg_tpl = 'custom enter'
        _run(chat._handle_slash('/set list'))
        self.assertEqual(chat.sent, [])
        joined = '\n'.join(chat.session.written)
        self.assertIn('!', joined)
        self.assertIn('custom enter', joined)

    def test_set_no_args_shows_help_no_wire_traffic(self):
        chat = _make_chat()
        _run(chat._handle_slash('/set'))
        self.assertEqual(chat.sent, [])
        self.assertTrue(len(chat.session.written) > 0)

    def test_set_unknown_field_shows_error(self):
        chat = _make_chat()
        _run(chat._handle_slash('/set bogus value'))
        self.assertEqual(chat.sent, [])
        joined = '\n'.join(chat.session.written)
        self.assertIn('Unknown', joined)


class TickerToggleRelayoutTests(unittest.TestCase):
    def test_prefs_ticker_change_triggers_relayout_when_split_screen(self):
        chat = _make_chat()
        chat._split_screen = True
        chat._show_ticker = True
        relayout_calls = []

        async def fake_enter_split_screen():
            relayout_calls.append('enter')
        async def fake_redraw():
            relayout_calls.append('redraw')
        chat._enter_split_screen = fake_enter_split_screen
        chat._redraw_chat_area = fake_redraw

        _run(chat._apply_prefs({'ticker_enabled': False}))
        self.assertFalse(chat._show_ticker)
        self.assertEqual(relayout_calls, ['enter', 'redraw'])

    def test_prefs_ticker_unchanged_does_not_trigger_relayout(self):
        chat = _make_chat()
        chat._split_screen = True
        chat._show_ticker = True
        relayout_calls = []

        async def fake_enter_split_screen():
            relayout_calls.append('enter')
        chat._enter_split_screen = fake_enter_split_screen

        _run(chat._apply_prefs({'ticker_enabled': True}))
        self.assertEqual(relayout_calls, [])


class ParseTzOffsetTests(unittest.TestCase):
    def test_plain_negative_hour(self):
        self.assertEqual(_parse_tz_offset('-5'), -300)

    def test_plain_positive_hour(self):
        self.assertEqual(_parse_tz_offset('+5'), 300)

    def test_hour_and_minutes_with_colon(self):
        self.assertEqual(_parse_tz_offset('+5:30'), 330)
        self.assertEqual(_parse_tz_offset('-5:30'), -330)

    def test_utc_and_z_are_zero(self):
        self.assertEqual(_parse_tz_offset('utc'), 0)
        self.assertEqual(_parse_tz_offset('UTC'), 0)
        self.assertEqual(_parse_tz_offset('z'), 0)

    def test_bare_zero(self):
        self.assertEqual(_parse_tz_offset('0'), 0)

    def test_out_of_range_rejected(self):
        self.assertIsNone(_parse_tz_offset('+15'))
        self.assertIsNone(_parse_tz_offset('-13'))

    def test_garbage_rejected(self):
        self.assertIsNone(_parse_tz_offset('banana'))
        self.assertIsNone(_parse_tz_offset(''))

    def test_named_zone_aliases(self):
        # Raw "+/-5" offsets were reported as confusing live on the Pi;
        # named zones are the requested, friendlier alternative.
        self.assertEqual(_parse_tz_offset('EST'), -300)
        self.assertEqual(_parse_tz_offset('est'), -300)
        self.assertEqual(_parse_tz_offset('EDT'), -240)
        self.assertEqual(_parse_tz_offset('CST'), -360)
        self.assertEqual(_parse_tz_offset('CDT'), -300)
        self.assertEqual(_parse_tz_offset('PST'), -480)
        self.assertEqual(_parse_tz_offset('PDT'), -420)
        self.assertEqual(_parse_tz_offset('GMT'), 0)
        self.assertEqual(_parse_tz_offset('JST'), 540)

    def test_named_zone_takes_precedence_over_numeric_parse(self):
        # 'z' alone is both the UTC alias and would otherwise fail the
        # numeric regex -- confirm the alias path is what's actually
        # matching, not a coincidental fallthrough.
        self.assertEqual(_parse_tz_offset('Z'), 0)


class LocalNowTests(unittest.TestCase):
    def test_default_offset_matches_utc(self):
        from datetime import datetime, timedelta
        chat = _make_chat()
        before = datetime.utcnow()
        now = chat._local_now()
        self.assertLess(abs((now - before).total_seconds()), 2)

    def test_offset_applied(self):
        from datetime import datetime, timedelta
        chat = _make_chat()
        chat._tz_offset_minutes = -300
        expected = datetime.utcnow() + timedelta(minutes=-300)
        actual = chat._local_now()
        self.assertLess(abs((actual - expected).total_seconds()), 2)

    def test_format_tz_offset(self):
        chat = _make_chat()
        chat._tz_offset_minutes = 0
        self.assertEqual(chat._format_tz_offset(), 'UTC+00:00')
        chat._tz_offset_minutes = -300
        self.assertEqual(chat._format_tz_offset(), 'UTC-05:00')
        chat._tz_offset_minutes = 330
        self.assertEqual(chat._format_tz_offset(), 'UTC+05:30')


class SetCommandTzTests(unittest.TestCase):
    def test_set_tz_valid_sends_set_prefs(self):
        chat = _make_chat()
        _run(chat._handle_slash('/set tz -5'))
        self.assertEqual(chat.sent, [{'type': 'set_prefs', 'tz_offset': -300}])

    def test_set_tz_invalid_rejected_locally(self):
        chat = _make_chat()
        _run(chat._handle_slash('/set tz nonsense'))
        self.assertEqual(chat.sent, [])

    def test_apply_prefs_updates_tz_offset(self):
        chat = _make_chat()
        _run(chat._apply_prefs({'tz_offset': -300}))
        self.assertEqual(chat._tz_offset_minutes, -300)


class PaletteTests(unittest.TestCase):
    def test_default_palette_matches_original_hardcoded_colors(self):
        from anetbbs.features.mrc_chat import _TERM_PALETTES
        chat = _make_chat()
        self.assertEqual(chat._palette_name, 'default')
        self.assertEqual(chat._pal('accent_b'), '1;96')
        self.assertEqual(chat._pal('accent'), '36')
        self.assertEqual(chat._pal('dim'), '2;36')
        self.assertIn('green', _TERM_PALETTES)
        self.assertIn('amber', _TERM_PALETTES)

    def test_set_palette_valid_name_switches_and_confirms(self):
        chat = _make_chat()
        chat._split_screen = False
        _run(chat._handle_slash('/set palette green'))
        self.assertEqual(chat._palette_name, 'green')
        joined = '\n'.join(chat.session.written)
        self.assertIn('green', joined)

    def test_set_palette_unknown_name_rejected_no_state_change(self):
        chat = _make_chat()
        _run(chat._handle_slash('/set palette nonexistent'))
        self.assertEqual(chat._palette_name, 'default')

    def test_set_palette_no_args_lists_options(self):
        chat = _make_chat()
        _run(chat._handle_slash('/set palette'))
        joined = '\n'.join(chat.session.written)
        self.assertIn('amber', joined)
        self.assertEqual(chat._palette_name, 'default')

    def test_sidebar_colors_follow_active_palette(self):
        chat = _make_chat()
        chat._known_users = {'Alice'}
        chat._palette_name = 'amber'
        lines = chat._sidebar_lines(2)
        self.assertIn('\x1b[1;33m', lines[0])   # accent_b for amber
        self.assertIn('\x1b[33m', lines[1])     # accent for amber


if __name__ == '__main__':
    unittest.main()
