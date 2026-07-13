"""Tests for MRC Phase C (terminal feature-parity rework): the
scrolling ticker/banner in anetbbs/features/mrc_chat.py.

BANNER:/STATS: text previously either got silently discarded (BANNER:)
or displayed inline with no further use (STATS:) -- both are real data
already flowing through the bridge (see mrc/bridge/main.py's
_send_stats_control docstring for why STATS stays opaque text rather
than structured fields). This phase captures both into a ticker pool
without changing STATS:'s existing inline display, and (for BANNER:,
which was never displayed at all) without introducing new inline noise.
"""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.core  # noqa: F401  (resolves a circular import if mrc_chat is imported first)
from anetbbs.features.mrc_chat import MRCChat, TICKER_TIPS


class _FakeSession:
    def __init__(self):
        self.user = {'username': 'tester'}
        self.written = []

    async def write(self, text):
        self.written.append(text)


def _make_chat(handle='StingRay', split_screen=True):
    chat = MRCChat(_FakeSession())
    chat._handle = handle
    chat._split_screen = split_screen
    if split_screen:
        chat._ticker_row = 2
        chat._term_columns = 132
    return chat


def _run(coro):
    return asyncio.run(coro)


class TickerPoolTests(unittest.TestCase):
    def test_static_tips_always_present(self):
        chat = _make_chat()
        items = chat._ticker_items()
        for tip in TICKER_TIPS:
            self.assertIn(tip, items)

    def test_add_ticker_text_strips_pipe_and_ansi(self):
        chat = _make_chat()
        chat._add_ticker_text('|1442 users online\x1b[0m')
        self.assertIn('42 users online', chat._ticker_pool)

    def test_add_ticker_text_ignores_blank(self):
        chat = _make_chat()
        chat._add_ticker_text('   ')
        self.assertEqual(len(chat._ticker_pool), 0)

    def test_add_ticker_text_dedupes_immediate_repeat(self):
        chat = _make_chat()
        chat._add_ticker_text('42 users online')
        chat._add_ticker_text('42 users online')
        self.assertEqual(len(chat._ticker_pool), 1)

    def test_add_ticker_text_allows_repeat_after_other_content(self):
        chat = _make_chat()
        chat._add_ticker_text('42 users online')
        chat._add_ticker_text('something else')
        chat._add_ticker_text('42 users online')
        self.assertEqual(len(chat._ticker_pool), 3)

    def test_live_pool_items_come_before_static_tips(self):
        chat = _make_chat()
        chat._add_ticker_text('live banner text')
        items = chat._ticker_items()
        self.assertEqual(items[0], 'live banner text')


class AdvanceTickerTests(unittest.TestCase):
    def test_short_item_dwells_before_rotating(self):
        chat = _make_chat()
        chat._ticker_pool.clear()   # only static tips, all short
        start_idx = chat._ticker_idx
        for _ in range(3):
            chat._advance_ticker()
            self.assertEqual(chat._ticker_idx, start_idx)  # still dwelling
        chat._advance_ticker()  # 4th tick rotates
        self.assertEqual(chat._ticker_idx, start_idx + 1)

    def test_long_item_scrolls_before_rotating(self):
        chat = _make_chat()
        chat._term_columns = 20
        chat._ticker_pool.clear()
        long_text = 'x' * 100
        chat._add_ticker_text(long_text)
        start_idx = chat._ticker_idx
        chat._advance_ticker()
        self.assertEqual(chat._ticker_idx, start_idx)
        self.assertGreater(chat._ticker_scroll_pos, 0)


class DrawTickerLineTests(unittest.TestCase):
    def test_no_ticker_row_is_a_no_op(self):
        chat = _make_chat()
        chat._ticker_row = None
        _run(chat._draw_ticker_line())
        self.assertEqual(chat.session.written, [])

    def test_draws_current_item_at_ticker_row(self):
        chat = _make_chat()
        chat._ticker_pool.clear()
        chat._add_ticker_text('hello ticker')
        _run(chat._draw_ticker_line())
        joined = ''.join(chat.session.written)
        self.assertIn('\x1b[2;1H', joined)
        self.assertIn('hello ticker', joined)

    def test_draws_matching_border_when_sidebar_enabled(self):
        # Regression test: reported live on the Pi as the border still
        # broken on "one spot" even after the status-bar fix -- this row
        # (the ticker) had no border/sidebar treatment at all yet.
        chat = _make_chat()
        chat._sidebar_enabled = True
        chat._chat_width = 111
        chat._sidebar_width = 20
        chat._ticker_pool.clear()
        chat._add_ticker_text('hello ticker')
        _run(chat._draw_ticker_line())
        joined = ''.join(chat.session.written)
        self.assertIn('\x1b[2;37m│\x1b[0m', joined)

    def test_border_lands_at_chat_width_column(self):
        from anetbbs.features.mrc_chat import _visible_len
        chat = _make_chat()
        chat._sidebar_enabled = True
        chat._chat_width = 111
        chat._sidebar_width = 20
        chat._ticker_pool.clear()
        chat._add_ticker_text('hello ticker')
        _run(chat._draw_ticker_line())
        joined = ''.join(chat.session.written)
        before_border = joined.split('\x1b[2;37m│\x1b[0m')[0]
        # Strip the leading cursor-position + line-clear escape codes,
        # which aren't part of the row's visible text.
        content = before_border.split('\x1b[2K', 1)[1]
        self.assertEqual(_visible_len(content), chat._chat_width)

    def test_no_border_when_sidebar_disabled(self):
        chat = _make_chat()
        chat._sidebar_enabled = False
        chat._ticker_pool.clear()
        chat._add_ticker_text('hello ticker')
        _run(chat._draw_ticker_line())
        joined = ''.join(chat.session.written)
        self.assertNotIn('│', joined)


class HandleEventBannerStatsTests(unittest.TestCase):
    def test_banner_captured_but_not_shown_inline(self):
        chat = _make_chat()
        chat._ticker_pool.clear()
        _run(chat._handle_event({
            'type': 'mrc_message', 'from_user': 'SERVER',
            'message': 'BANNER:Welcome to the network!',
        }))
        self.assertIn('Welcome to the network!', chat._ticker_pool)
        # No inline display for BANNER: -- session.written comes only
        # from _emit()-driven redraws, and split_screen is off in this
        # fixture, so an inline display would show up as a direct
        # session.write() containing the banner text outside the ticker
        # path. Assert nothing was written at all for this event.
        self.assertEqual(chat.session.written, [])

    def test_stats_captured_and_silenced_inline(self):
        # Originally left showing inline (see git history) since it
        # predated the ticker; reported live on the Pi as a raw
        # "STATS:..." line popping up mid-chat once the ticker made the
        # inline copy redundant. Now silenced the same way BANNER: is.
        chat = _make_chat(split_screen=False)
        chat._ticker_pool.clear()
        _run(chat._handle_event({
            'type': 'mrc_message', 'from_user': 'SERVER',
            'message': 'STATS:42 users, 5 rooms, 12 BBSes',
        }))
        self.assertIn('42 users, 5 rooms, 12 BBSes', chat._ticker_pool)
        self.assertEqual(chat.session.written, [])


if __name__ == '__main__':
    unittest.main()
