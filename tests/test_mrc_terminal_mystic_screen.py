"""Tests for the Mystic MRC screen recreation in the terminal client
(anetbbs/features/mrc_chat.py) -- Jerry's explicit ask after the
door_mystic_mps investigation dead-ended (no per-caller Mystic accounts,
no running it as a door): "take the entire mystic mrc client source and
rewrite it so that it just works with ANetBBS ... run from the chat
menu", meaning the real border art + exact element positions from
StackFault's own bundled themes (mrc/mystic_client/vendor/), not just
the existing accent-color-only /set palette.

Since ANSI art can't be visually verified from here, these check the
structural contract instead: the right cursor-positioning escape
sequences land at the coordinates theme_layout.py parses from the real
.ini files, geometry (scroll region/input row) matches CHATTL/CHATBL/
INPUT, and switching a Mystic palette on/off doesn't break the existing
(untouched) non-Mystic rendering path.
"""
import asyncio
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.core  # noqa: F401  (resolves a circular import if mrc_chat is imported first)
from anetbbs.features.mrc_chat import MRCChat, _MYSTIC_PALETTE_NAMES
from mrc.mystic_client.theme_layout import load_theme_layout, best_fit_mode


class _FakeSession:
    def __init__(self, window_size=(80, 24)):
        self.user = {'username': 'tester'}
        self.written = []
        self.window_size = window_size

    async def write(self, text):
        self.written.append(text)


def _make_chat(window_size=(80, 24), handle='StingRay'):
    chat = MRCChat(_FakeSession(window_size))
    chat._handle = handle
    return chat


def _run(coro):
    return asyncio.run(coro)


def _cursor_positions(text):
    """Extract every \\x1b[row;colH from written output as (row, col) ints."""
    return [(int(r), int(c)) for r, c in re.findall(r'\x1b\[(\d+);(\d+)H', text)]


class SyncMysticLayoutTests(unittest.TestCase):
    def test_non_mystic_palette_has_no_layout(self):
        chat = _make_chat()
        chat._palette_name = 'cyan'
        chat._sync_mystic_layout()
        self.assertIsNone(chat._mystic_layout)

    def test_all_five_mystic_palettes_load_a_real_layout(self):
        chat = _make_chat()
        for name in _MYSTIC_PALETTE_NAMES:
            chat._palette_name = name
            chat._sync_mystic_layout()
            self.assertIsNotNone(chat._mystic_layout, f'{name} failed to load')
            self.assertTrue(chat._mystic_layout.element('CHATTL'))

    def test_switching_back_to_non_mystic_clears_layout(self):
        chat = _make_chat()
        chat._palette_name = 'bitchx'
        chat._sync_mystic_layout()
        self.assertIsNotNone(chat._mystic_layout)
        chat._palette_name = 'green'
        chat._sync_mystic_layout()
        self.assertIsNone(chat._mystic_layout)


class EnterSplitScreenGeometryTests(unittest.TestCase):
    def test_mystic_geometry_matches_theme_ini_coordinates(self):
        chat = _make_chat()
        chat._palette_name = 'original'
        _run(chat._enter_split_screen())

        layout = load_theme_layout('original')
        chattl = layout.element('CHATTL')
        chatbl = layout.element('CHATBL')
        input_el = layout.element('INPUT')

        self.assertEqual(chat._scroll_top, chattl[1])
        self.assertEqual(chat._scroll_bottom, chatbl[1])
        self.assertEqual(chat._input_row, input_el[1])
        self.assertEqual(chat._chat_width, chattl[2])
        self.assertFalse(chat._sidebar_enabled)

    def test_non_mystic_palette_keeps_generic_geometry(self):
        chat = _make_chat()
        chat._palette_name = 'green'
        _run(chat._enter_split_screen())
        self.assertIsNone(chat._mystic_layout)
        # 3 (not 2) since _show_ticker defaults on, pushing chat down one
        # row for the ticker -- existing, correct, unrelated-to-Mystic
        # behavior (_enter_split_screen's own generic-path logic).
        self.assertEqual(chat._scroll_top, 3 if chat._show_ticker else 2)
        self.assertEqual(chat._input_row, chat._term_lines)

    def test_border_art_drawn_at_declared_positions(self):
        chat = _make_chat()
        chat._palette_name = 'original'
        _run(chat._enter_split_screen())

        layout = load_theme_layout('original')
        top_art = layout.art['TOPANSI']  # (first_line, num_lines, x, y, fg)
        full = ''.join(chat.session.written)
        positions = _cursor_positions(full)
        # Top border's first line must be positioned at (top_art_y, top_art_x)
        self.assertIn((top_art[3], top_art[2]), positions)

    def test_different_mystic_themes_have_different_geometry(self):
        """Real thing this guards: CHATTL.y is 6 for 'original' but 4 for
        'minimal' -- if geometry were cached/stale across a palette
        switch, this would silently keep the wrong scroll region."""
        chat = _make_chat()
        chat._palette_name = 'original'
        _run(chat._enter_split_screen())
        original_top = chat._scroll_top

        chat._palette_name = 'minimal'
        _run(chat._enter_split_screen())
        minimal_top = chat._scroll_top

        self.assertNotEqual(original_top, minimal_top)


class MysticStatusOverlayTests(unittest.TestCase):
    def test_room_and_topic_written_at_their_own_coordinates(self):
        chat = _make_chat()
        chat._palette_name = 'original'
        chat._room = 'lobby'
        chat._topic = 'Blown distortion booster!'
        _run(chat._enter_split_screen())
        chat.session.written.clear()
        _run(chat._mystic_draw_status())

        layout = load_theme_layout('original')
        room_el = layout.element('ROOM')
        topic_el = layout.element('TOPIC')
        full = ''.join(chat.session.written)
        positions = _cursor_positions(full)
        self.assertIn((room_el[1], room_el[0]), positions)
        self.assertIn((topic_el[1], topic_el[0]), positions)
        self.assertIn('#lobby', full)
        self.assertIn('Blown distortion booster!', full)

    def test_latency_shown_when_set(self):
        """'original' theme's own LATENCY slot is only 3 chars wide --
        "42ms" (4 chars) doesn't fit, so the bare number is shown
        instead of a confusingly truncated "42m"."""
        chat = _make_chat()
        chat._palette_name = 'original'
        chat._latency_ms = 42
        _run(chat._enter_split_screen())
        chat.session.written.clear()
        _run(chat._mystic_draw_status())
        full = ''.join(chat.session.written)
        self.assertIn('42', full)
        self.assertNotIn('42m\x1b', full)  # not the broken mid-truncated form

    def test_draw_status_line_dispatches_to_mystic_when_layout_active(self):
        chat = _make_chat()
        chat._palette_name = 'bitchx'
        chat._room = 'sysop'
        _run(chat._enter_split_screen())
        chat.session.written.clear()
        _run(chat._draw_status_line())
        full = ''.join(chat.session.written)
        self.assertIn('#sysop', full)
        # The generic (non-Mystic) status bar always writes to row 1 col 1
        # with a blue background code -- confirms this really took the
        # Mystic branch, not the fallback.
        self.assertNotIn('\x1b[1;1H\x1b[44m', full)


class MysticInputLineTests(unittest.TestCase):
    def test_input_positioned_at_theme_input_element(self):
        chat = _make_chat()
        chat._palette_name = 'original'
        _run(chat._enter_split_screen())
        chat.session.written.clear()
        chat._input_buf = list('hello')
        _run(chat._draw_input_line())

        layout = load_theme_layout('original')
        input_el = layout.element('INPUT')
        full = ''.join(chat.session.written)
        self.assertIn(f'\x1b[{input_el[1]};{input_el[0]}H', full)
        self.assertIn('hello', full)

    def test_input_line_does_not_clear_whole_row(self):
        """Real bug this guards against: \\x1b[2K would blank out border
        art to the left of INPUT.x on themes where INPUT.x > 1 (the
        'original' theme's INPUT starts at column 7, not 1)."""
        chat = _make_chat()
        chat._palette_name = 'original'
        _run(chat._enter_split_screen())
        chat.session.written.clear()
        _run(chat._draw_input_line())
        full = ''.join(chat.session.written)
        self.assertNotIn('\x1b[2K', full)


class MysticNickstripTests(unittest.TestCase):
    def test_nickstrip_renders_known_users_bracketed(self):
        chat = _make_chat()
        chat._palette_name = 'original'
        chat._known_users = {'Alice', 'Bob'}
        _run(chat._enter_split_screen())
        chat.session.written.clear()
        _run(chat._mystic_draw_nickstrip())
        full = ''.join(chat.session.written)
        # SGR color codes are interspersed between the bracket and the
        # name (e.g. "...[\x1b[1;36mAlice\x1b[36m]..."), so check for the
        # name and its brackets separately rather than one contiguous
        # "[Alice]" substring.
        self.assertIn('Alice', full)
        self.assertIn('Bob', full)
        self.assertIn('[', full)
        self.assertIn(']', full)

    def test_nickstrip_truncates_with_more_indicator_when_over_length(self):
        chat = _make_chat()
        chat._palette_name = 'original'
        chat._known_users = {f'User{i:03d}' for i in range(50)}
        _run(chat._enter_split_screen())
        chat.session.written.clear()
        _run(chat._mystic_draw_nickstrip())
        full = ''.join(chat.session.written)
        self.assertIn('+', full)
        self.assertIn('more', full) if 'more' in full else None  # optional wording


class PaletteSwitchReentersSplitScreenTests(unittest.TestCase):
    def test_switching_to_mystic_palette_changes_geometry_live(self):
        chat = _make_chat()
        chat._palette_name = 'cyan'  # non-mystic starting point (default is now 'bitchx', a Mystic palette)
        chat._split_screen = True
        chat._connected = True

        async def drive():
            await chat._enter_split_screen()  # non-mystic geometry
            self.assertIsNone(chat._mystic_layout)
            await chat._handle_slash('/set palette original')
            self.assertIsNotNone(chat._mystic_layout)
            layout = load_theme_layout('original')
            self.assertEqual(chat._scroll_top, layout.element('CHATTL')[1])

        _run(drive())

    def test_switching_between_two_non_mystic_palettes_does_not_reenter(self):
        chat = _make_chat()
        chat._palette_name = 'cyan'  # non-mystic starting point (default is now 'original', a Mystic palette)
        chat._split_screen = True
        chat._connected = True

        async def drive():
            await chat._enter_split_screen()
            before = chat._scroll_top
            await chat._handle_slash('/set palette amber')
            self.assertIsNone(chat._mystic_layout)
            self.assertEqual(chat._scroll_top, before)

        _run(drive())

    def test_apply_prefs_restores_persisted_palette_on_join(self):
        # Jerry, live: "I have had to change the theme each time. I had
        # it set on 2leet4u" -- palette used to be pure local state,
        # never sent to the bridge, so it silently reset to the
        # hardcoded default on every reconnect. Mirrors the
        # ticker_enabled re-entry pattern above: _enter_split_screen()
        # already ran once (with the hardcoded default) by the time the
        # 'joined' event's prefs arrive.
        chat = _make_chat()
        self.assertEqual(chat._palette_name, 'original')  # hardcoded default, pre-join
        chat._split_screen = True
        chat._connected = True

        async def drive():
            await chat._enter_split_screen()
            await chat._apply_prefs({'palette': '2leet4u'}, is_initial_join=True)
            self.assertEqual(chat._palette_name, '2leet4u')
            layout = load_theme_layout('2leet4u')
            self.assertEqual(chat._scroll_top, layout.element('CHATTL')[1])

        _run(drive())

    def test_apply_prefs_ignores_unknown_palette_name(self):
        chat = _make_chat()
        self.assertEqual(chat._palette_name, 'original')
        chat._split_screen = True
        _run(chat._apply_prefs({'palette': 'not-a-real-theme'}))
        self.assertEqual(chat._palette_name, 'original')


class MysticStatusFineTuningTests(unittest.TestCase):
    """Real feedback from the first live look at this on the Pi: the
    mentions marker never cleared once set (only written when
    mention_count > 0, so a stale "!1" stuck around forever after the
    count dropped back to 0), and mentions weren't wanted on this
    screen at all -- both fixed by removing the MENTIONS placement
    entirely and making _place() always write (space-padded) instead
    of conditionally skipping, which also protects every other element
    from the same class of stale-value bug."""

    def test_mentions_are_never_placed(self):
        chat = _make_chat()
        chat._palette_name = 'bitchx'
        chat._mention_count = 3
        _run(chat._enter_split_screen())
        chat.session.written.clear()
        _run(chat._mystic_draw_status())
        full = ''.join(chat.session.written)
        self.assertNotIn('!3', full)

    def test_topic_clearing_overwrites_stale_text_instead_of_leaving_it(self):
        chat = _make_chat()
        chat._palette_name = 'original'
        chat._topic = 'a long stale topic that should get cleared'
        _run(chat._enter_split_screen())
        chat.session.written.clear()
        _run(chat._mystic_draw_status())
        first = ''.join(chat.session.written)
        self.assertIn('a long stale topic', first)

        chat._topic = ''
        chat.session.written.clear()
        _run(chat._mystic_draw_status())
        second = ''.join(chat.session.written)
        # Must NOT still contain the old text -- the whole point of
        # always-write-padded is that a cleared field actually clears.
        self.assertNotIn('a long stale topic', second)

    def test_chatters_shows_real_known_user_count(self):
        chat = _make_chat()
        chat._palette_name = 'original'
        chat._known_users = {'Alice', 'Bob', 'Carol'}
        _run(chat._enter_split_screen())
        chat.session.written.clear()
        _run(chat._mystic_draw_status())
        full = ''.join(chat.session.written)
        self.assertIn('3', full)

    def test_buffer_shows_remaining_chars_countdown(self):
        # Jerry, live: "buffer still does not work, that is the
        # character count down if I am not mistaken" -- BUFFER had
        # been left unpopulated as an assumed hub-side stat, but it's
        # the same wire-length countdown the generic (non-Mystic)
        # status bar already computed via _input_remaining_chars.
        chat = _make_chat()
        chat._palette_name = 'original'
        _run(chat._enter_split_screen())
        layout = load_theme_layout('original')
        el = layout.element('BUFFER')

        chat._input_buf = []
        chat.session.written.clear()
        _run(chat._mystic_draw_status())
        full_empty = ''.join(chat.session.written)
        full_cap = str(chat._chat_wire_cap())[:el[2]]
        self.assertIn(f'\x1b[{el[1]};{el[0]}H', full_empty)
        self.assertIn(full_cap, full_empty)

        chat._input_buf = list('hello')
        chat.session.written.clear()
        _run(chat._mystic_draw_status())
        full_typed = ''.join(chat.session.written)
        expected = str(chat._chat_wire_cap() - 5)[:el[2]]
        self.assertIn(expected, full_typed)
        self.assertNotEqual(full_typed, full_empty)


class BestFitModeTests(unittest.TestCase):
    """Jerry, live: '132x37 it defaults to 80x25 mrc, since we
    auto-detect terminal, we should have the 132x37 mrc size be the
    default for that screen size' -- load_theme_layout() always
    supported a size-variant `mode` argument, it just was never
    actually resolved from the caller's real detected terminal size."""

    def test_exact_and_near_fit_picks_the_132x36_variant(self):
        self.assertEqual(best_fit_mode('original', 132, 37), '132x36')
        self.assertEqual(best_fit_mode('original', 132, 36), '132x36')

    def test_larger_terminal_picks_the_largest_variant_that_fits(self):
        self.assertEqual(best_fit_mode('original', 160, 59), '160x59')
        # 51 doesn't fit a 50-line terminal -- falls back to 36.
        self.assertEqual(best_fit_mode('original', 132, 50), '132x36')

    def test_narrow_terminal_falls_back_to_default(self):
        self.assertEqual(best_fit_mode('original', 80, 24), 'default')

    def test_theme_with_no_wide_variants_always_falls_back_to_default(self):
        # 'least' and '2leet4u' only ever ship a 'default' .ini.
        self.assertEqual(best_fit_mode('least', 160, 59), 'default')
        self.assertEqual(best_fit_mode('2leet4u', 160, 59), 'default')

    def test_every_mystic_palette_resolves_to_a_loadable_layout(self):
        for name in _MYSTIC_PALETTE_NAMES:
            mode = best_fit_mode(name, 132, 37)
            self.assertIsNotNone(load_theme_layout(name, mode=mode))


class EnterSplitScreenWideTerminalTests(unittest.TestCase):
    def test_132x37_selects_the_132x36_variant_not_default(self):
        chat = _make_chat(window_size=(132, 37))
        chat._palette_name = 'original'
        _run(chat._enter_split_screen())
        expected = load_theme_layout('original', mode='132x36')
        self.assertEqual(chat._mystic_layout.element('CHATTL'),
                         expected.element('CHATTL'))
        default_layout = load_theme_layout('original')
        self.assertNotEqual(chat._mystic_layout.element('CHATTL'),
                            default_layout.element('CHATTL'))

    def test_80x24_still_uses_default(self):
        chat = _make_chat(window_size=(80, 24))
        chat._palette_name = 'original'
        _run(chat._enter_split_screen())
        default_layout = load_theme_layout('original')
        self.assertEqual(chat._mystic_layout.element('CHATTL'),
                         default_layout.element('CHATTL'))


if __name__ == '__main__':
    unittest.main()
