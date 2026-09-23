"""Regression tests for a real live bug report (2026-09-23): the
general cross-protocol "*** X just logged in/out ***" alert
(core/session.py's _start_presence_alert_watchdog) stayed on screen
indefinitely while a caller was inside terminal MRC chat, instead of
clearing after a while, because that watchdog wrote straight to the
raw session stream -- bypassing MRC chat's own fixed-layout scroll-
region screen model entirely. In split-screen mode that raw write
landed wherever the physical cursor happened to sit, not a row MRC's
own row/column bookkeeping knew about, so it could only ever get
papered over by the NEXT unrelated chat redraw -- during a quiet
stretch of chat, it just sat there.

Covers the MRCChat side of the fix: _show_transient_notice renders
like a normal chat line but actually expires -- gone from both
_active_transient_lines() and the next redraw -- after its ttl,
instead of depending on other chat activity to eventually redraw over
it. See tests/test_presence_alerts.py's
test_presence_alert_watchdog_routes_through_mrc_chat_notice_sink_when_present
for the sink-routing half of this fix, in core/session.py.
"""
import asyncio
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.core  # noqa: F401  (resolves a circular import if mrc_chat is imported first)
from anetbbs.features.mrc_chat import MRCChat


class _FakeSession:
    def __init__(self):
        self.user = {'username': 'tester'}
        self.written = []
        self.window_size = (80, 24)

    async def write(self, text):
        self.written.append(text)

    async def clear_screen(self):
        pass


def _make_chat(handle='StingRay'):
    chat = MRCChat(_FakeSession())
    chat._handle = handle
    chat._connected = True
    return chat


def _run(coro):
    return asyncio.run(coro)


def _written_text(chat):
    return ''.join(str(w) for w in chat.session.written)


class PlainScrollFallbackTests(unittest.TestCase):
    """PETSCII/ASCII (or split-screen toggled off) has no managed
    scroll region to redraw from -- a plain terminal scroll naturally
    carries old text away on its own, and there's no way to reach back
    and erase something that already scrolled past. Falls back to the
    same raw write the watchdog used to always do, with no timed
    bookkeeping at all."""

    def test_non_split_screen_falls_back_to_raw_write(self):
        chat = _make_chat()
        chat._split_screen = False
        _run(chat._show_transient_notice('*** Ariel just logged in ***'))
        self.assertIn('Ariel just logged in', _written_text(chat))
        self.assertEqual(chat._transient_notices, [])


class TransientNoticeExpiryTests(unittest.TestCase):
    """Split-screen mode: the notice is real, managed content -- shows
    up immediately and is gone after its ttl on its own, without
    depending on any other chat activity happening in between."""

    def test_notice_appears_immediately_and_triggers_a_redraw(self):
        chat = _make_chat()
        self.assertTrue(chat._split_screen)  # default

        redraws = []

        async def _fake_redraw():
            redraws.append(list(chat._active_transient_lines()))
        chat._redraw_chat_area = _fake_redraw

        _run(chat._show_transient_notice('*** Ariel just logged in ***', ttl=30))

        self.assertEqual(len(chat._transient_notices), 1)
        lines = chat._active_transient_lines()
        self.assertTrue(any('Ariel just logged in' in ln for ln in lines))
        self.assertTrue(
            any('Ariel just logged in' in ln for r in redraws for ln in r),
            f'the immediate redraw triggered by _show_transient_notice must '
            f'have seen the new notice: {redraws}')

    def test_notice_expires_on_its_own_and_triggers_a_clearing_redraw(self):
        # Deliberately ONE asyncio.run() for the whole test: the expiry
        # timer is a real asyncio.create_task() started inside
        # _show_transient_notice, tied to whatever event loop is
        # running at that moment (matching a real chat session, which
        # runs on one continuous loop for its whole lifetime). Calling
        # asyncio.run() a second time here would close that loop and
        # orphan the pending task before its 0.01s sleep ever finished
        # -- a test-harness bug, not a real one, caught by this failing
        # exactly that way on the first pass.
        chat = _make_chat()

        redraws = []

        async def _fake_redraw():
            redraws.append(list(chat._active_transient_lines()))
        chat._redraw_chat_area = _fake_redraw

        async def _scenario():
            await chat._show_transient_notice('*** Ariel just logged in ***', ttl=0.01)
            self.assertEqual(len(chat._transient_notices), 1)
            for _ in range(200):
                await asyncio.sleep(0.01)
                if not chat._transient_notices:
                    return
        _run(_scenario())

        self.assertEqual(chat._transient_notices, [],
            'notice must be gone on its own after its ttl elapses, with no '
            'new chat activity required to push it out')
        self.assertEqual(redraws[-1], [],
            'the expiry-triggered redraw must show it already gone')

    def test_active_transient_lines_filters_by_time_even_without_the_expiry_task(self):
        """A defense-in-depth check independent of
        _show_transient_notice's own delayed removal task: a redraw
        that happens to fire just before that task wakes up can never
        show a notice past its own ttl, since _active_transient_lines()
        itself re-filters by wall-clock time on every call."""
        chat = _make_chat()
        chat._transient_notices = [
            [time.monotonic() - 1, object(), ['already expired']],
            [time.monotonic() + 30, object(), ['still fresh']],
        ]
        lines = chat._active_transient_lines()
        self.assertEqual(lines, ['still fresh'])
        self.assertEqual(len(chat._transient_notices), 1)

    def test_multiple_notices_expire_independently(self):
        # One asyncio.run() for the whole test -- see the comment in
        # test_notice_expires_on_its_own_and_triggers_a_clearing_redraw
        # for why splitting this across separate asyncio.run() calls
        # would orphan the expiry timer task.
        chat = _make_chat()
        chat._redraw_chat_area = lambda: asyncio.sleep(0)  # no-op coroutine

        async def _scenario():
            await chat._show_transient_notice('*** Ariel just logged in ***', ttl=0.01)
            await chat._show_transient_notice('*** Bob just logged out ***', ttl=30)
            for _ in range(200):
                await asyncio.sleep(0.01)
                if len(chat._transient_notices) == 1:
                    return
        _run(_scenario())

        lines = chat._active_transient_lines()
        self.assertFalse(any('Ariel' in ln for ln in lines))
        self.assertTrue(any('Bob just logged out' in ln for ln in lines))


class RedrawIncludesTransientLinesTests(unittest.TestCase):
    def test_redraw_chat_area_renders_transient_notice_text_to_the_session(self):
        """End-to-end through the REAL _redraw_chat_area (not a stub),
        confirming the notice text actually reaches the terminal."""
        chat = _make_chat()
        try:
            _run(chat._show_transient_notice('*** Ariel just logged in ***', ttl=30))
        except Exception as exc:
            self.fail(f'_show_transient_notice raised against a minimal fake '
                      f'session: {exc!r}')
        self.assertIn('Ariel just logged in', _written_text(chat))


class NoticeSinkWiringTests(unittest.TestCase):
    """Source-inspection technique (same as test_presence_alerts.py's
    test_presence_alert_task_is_cancelled_on_session_teardown) since
    _connect_and_chat's connect/finally: block isn't independently
    callable without a real bridge connection."""

    def test_connect_and_chat_registers_and_clears_the_sink(self):
        import inspect
        source = inspect.getsource(MRCChat._connect_and_chat)
        self.assertIn(
            'self.session._mrc_chat_notice_sink = self._show_transient_notice',
            source,
            'MRCChat must register itself as the notice sink while active')
        self.assertIn(
            'self.session._mrc_chat_notice_sink = None',
            source,
            'the sink must be cleared again on the way out')


if __name__ == '__main__':
    unittest.main()
