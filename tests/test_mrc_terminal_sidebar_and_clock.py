"""Tests for MRC Phase B (terminal feature-parity rework): the nick-list
sidebar and latency status-bar widget in anetbbs/features/mrc_chat.py
(originally a clock widget, replaced -- see LatencyWidgetTests below).

ANetBBS's terminal UI has no existing sidebar-rendering precedent
anywhere (confirmed by research before implementing this) -- the
DECSTBM split-screen primitive only constrains vertical scrolling, so
the sidebar rides along on the SAME row writes as the chat text
(_redraw_chat_area), not a separate scroll region. These tests cover
the pure logic (_sidebar_lines) and the width-negotiation branch in
_enter_split_screen that decides whether the sidebar is enabled at all.
"""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.core  # noqa: F401  (resolves a circular import if mrc_chat is imported first)
from anetbbs.features.mrc_chat import MRCChat


class _FakeSession:
    def __init__(self, window_size=None):
        self.user = {'username': 'tester'}
        self.written = []
        self.window_size = window_size

    async def write(self, text):
        self.written.append(text)
    # Deliberately no read_raw -- matches a terminal that doesn't
    # support the CPR probe; _enter_split_screen must fail fast and
    # fall back to whatever window_size/defaults it already has,
    # not hang for the full 1.5s deadline.


def _make_chat(window_size=None, handle='StingRay'):
    chat = MRCChat(_FakeSession(window_size))
    chat._handle = handle
    return chat


def _run(coro):
    return asyncio.run(coro)


class SidebarLinesTests(unittest.TestCase):
    def test_empty_roster_still_shows_header_and_blank_padding(self):
        chat = _make_chat()
        chat._known_users = set()
        lines = chat._sidebar_lines(5)
        self.assertEqual(len(lines), 5)
        self.assertIn('Users (0)', lines[0])
        self.assertEqual(lines[1:], ['', '', '', ''])

    def test_users_sorted_case_insensitively(self):
        chat = _make_chat()
        chat._known_users = {'zeb', 'Alice', 'bob'}
        lines = chat._sidebar_lines(4)
        # lines[0] is the header; 1..3 are the sorted nicks
        self.assertIn('Alice', lines[1])
        self.assertIn('bob', lines[2])
        self.assertIn('zeb', lines[3])

    def test_overflow_shows_more_indicator_not_a_crash(self):
        chat = _make_chat()
        chat._known_users = {f'user{i}' for i in range(20)}
        lines = chat._sidebar_lines(5)   # header + 4 body rows
        self.assertEqual(len(lines), 5)
        self.assertIn('more', lines[-1])

    def test_returns_empty_list_for_zero_rows(self):
        chat = _make_chat()
        chat._known_users = {'Alice'}
        self.assertEqual(chat._sidebar_lines(0), [])

    def test_entries_bounded_by_sidebar_width(self):
        chat = _make_chat()
        chat._sidebar_width = 10
        chat._known_users = {'a_very_long_username_that_overflows'}
        lines = chat._sidebar_lines(2)
        from anetbbs.features.mrc_chat import _visible_len
        self.assertLessEqual(_visible_len(lines[1]), 10)


class SplitScreenSidebarNegotiationTests(unittest.TestCase):
    def test_wide_terminal_enables_sidebar_and_narrows_chat_width(self):
        chat = _make_chat(window_size=(132, 50))
        _run(chat._enter_split_screen())
        self.assertTrue(chat._sidebar_enabled)
        self.assertEqual(chat._chat_width, 132 - chat._sidebar_width - 1)

    def test_narrow_terminal_disables_sidebar_full_width_chat(self):
        chat = _make_chat(window_size=(80, 24))
        _run(chat._enter_split_screen())
        self.assertFalse(chat._sidebar_enabled)
        self.assertEqual(chat._chat_width, chat._term_columns)

    def test_exactly_100_cols_is_the_enable_threshold(self):
        chat = _make_chat(window_size=(100, 30))
        _run(chat._enter_split_screen())
        self.assertTrue(chat._sidebar_enabled)


class SidebarBorderAlignmentTests(unittest.TestCase):
    """Regression test for a real bug reported live on the Pi: a stale
    over-width line in _display_lines (wrapped against a wider
    _chat_width before the sidebar narrowed it, e.g. a resize or the
    sidebar being enabled mid-session) pushed the '|' border out of its
    column instead of being truncated to fit -- see _redraw_chat_area."""

    def test_overwidth_line_does_not_shift_border_column(self):
        chat = _make_chat(window_size=(132, 50))
        _run(chat._enter_split_screen())
        # Simulate a line stored back when _chat_width was wider than it
        # is now -- longer than the current chat_width.
        chat._display_lines.append('X' * (chat._chat_width + 40))
        chat.session.written.clear()
        _run(chat._redraw_chat_area())
        joined = ''.join(chat.session.written)
        from anetbbs.features.mrc_chat import _visible_len
        for row_text in joined.split('\x1b[2K')[1:]:
            if '\x1b[2;37m│\x1b[0m' not in row_text:
                continue
            before_border = row_text.split('\x1b[2;37m│\x1b[0m')[0]
            self.assertLessEqual(_visible_len(before_border), chat._chat_width)


class BellCharacterVisibleLenTests(unittest.TestCase):
    """Regression test for a third real bug reported live on the Pi:
    one specific row's border still landed one column early even after
    the sidebar/status-bar/ticker fixes above. Root cause: mention/DM
    alerts prepend a bare BEL ('\\x07', see _handle_event) ahead of the
    highlighted text to ring the terminal bell -- zero-width on a real
    terminal, but _visible_len counted it as one visible column since
    _ANSI_SEQ_RE only matched ESC-prefixed sequences, not BEL. Happened
    to surface on a message that mentioned the caller's own handle."""

    def test_visible_len_treats_bell_as_zero_width(self):
        from anetbbs.features.mrc_chat import _visible_len
        self.assertEqual(_visible_len('\x07hello'), len('hello'))
        self.assertEqual(_visible_len('hello'), _visible_len('\x07hello'))

    def test_truncate_visible_does_not_count_bell(self):
        from anetbbs.features.mrc_chat import _truncate_visible, _visible_len
        out = _truncate_visible('\x07hello world', 5)
        self.assertEqual(_visible_len(out), 5)

    def test_mentioned_message_row_lands_at_same_border_column_as_others(self):
        chat = _make_chat(window_size=(132, 50))
        _run(chat._enter_split_screen())
        chat.session.written.clear()
        _run(chat._handle_event({
            'type': 'mrc_message',
            'from_user': 'Johnny5',
            'from_site': 'hub',
            'message': f'Welcome to MRC {chat._handle}! Glad to have you.',
        }))
        joined = ''.join(chat.session.written)
        from anetbbs.features.mrc_chat import _visible_len
        rows_checked = 0
        for row_text in joined.split('\x1b[2K')[1:]:
            if '\x1b[2;37m│\x1b[0m' not in row_text:
                continue
            before_border = row_text.split('\x1b[2;37m│\x1b[0m')[0]
            self.assertEqual(_visible_len(before_border), chat._chat_width)
            rows_checked += 1
        self.assertGreater(rows_checked, 0)


class StatusBarBorderAlignmentTests(unittest.TestCase):
    """Regression test for a second real bug reported live on the Pi
    after the SidebarBorderAlignmentTests fix above: the status bar
    (row 1) was drawn full terminal-width with no border at all, so its
    right-aligned badges (mention count, clock) landed past where every
    chat row's '|' sits, with nothing to match it -- "the one spot"
    still broken. Fixed by measuring/padding the bar against chat_width
    and drawing the same border + a blank nick-column-width gap."""

    def test_status_bar_draws_matching_border_when_sidebar_enabled(self):
        chat = _make_chat(window_size=(132, 50))
        _run(chat._enter_split_screen())
        chat.session.written.clear()
        _run(chat._draw_status_line())
        joined = ''.join(chat.session.written)
        self.assertIn('\x1b[2;37m│\x1b[0m', joined)

    def test_status_bar_border_lands_at_chat_width_column(self):
        from anetbbs.features.mrc_chat import _visible_len
        chat = _make_chat(window_size=(132, 50))
        _run(chat._enter_split_screen())
        chat.session.written.clear()
        _run(chat._draw_status_line())
        joined = ''.join(chat.session.written)
        before_border = joined.split('\x1b[2;37m│\x1b[0m')[0]
        self.assertEqual(_visible_len(before_border), chat._chat_width)

    def test_no_border_drawn_when_sidebar_disabled(self):
        chat = _make_chat(window_size=(80, 24))
        _run(chat._enter_split_screen())
        chat.session.written.clear()
        _run(chat._draw_status_line())
        joined = ''.join(chat.session.written)
        self.assertNotIn('│', joined)


class LatencyWidgetTests(unittest.TestCase):
    """The status bar's old clock widget (_show_clock) was removed and
    replaced with a real ping/latency display (Jerry: per-message
    timestamps already show the time on every line, so a second clock
    at the top was redundant -- latency was the thing actually
    missing). See mrc_chat.py's own _draw_status_line comment and
    _ping_loop's fix for the real msgext/t wire-protocol bug that had
    silently kept self._latency_ms at None the entire time."""

    def test_status_line_includes_latency_when_known(self):
        chat = _make_chat(window_size=(132, 50))
        _run(chat._enter_split_screen())
        chat.session.written.clear()
        chat._latency_ms = 42
        _run(chat._draw_status_line())
        joined = ''.join(chat.session.written)
        self.assertIn('42ms', joined)

    def test_status_line_omits_latency_when_unknown(self):
        chat = _make_chat(window_size=(132, 50))
        _run(chat._enter_split_screen())
        chat._latency_ms = None
        chat.session.written.clear()
        _run(chat._draw_status_line())
        joined = ''.join(chat.session.written)
        self.assertNotIn('ms\x1b[0m', joined)

    def test_status_line_no_longer_renders_a_clock(self):
        """Direct regression guard for the removed widget itself --
        confirms no HH:MM-shaped substring appears anywhere in the
        status bar regardless of latency state (the room tag has no
        digits, so any HH:MM match could only have come from a clock)."""
        chat = _make_chat(window_size=(132, 50))
        _run(chat._enter_split_screen())
        chat._latency_ms = 7
        chat.session.written.clear()
        _run(chat._draw_status_line())
        joined = ''.join(chat.session.written)
        import re
        self.assertNotRegex(joined, r'\b\d{2}:\d{2}\b')


if __name__ == '__main__':
    unittest.main()
