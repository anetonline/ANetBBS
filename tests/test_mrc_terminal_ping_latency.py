"""Regression tests for the terminal MRC client's real ping/latency
round trip (anetbbs/features/mrc_chat.py).

Real bug found live: `_ping_loop()` sent its outgoing timestamp under
the key `msgext`, but the bridge (mrc/bridge/main.py) only ever echoes
back whatever key the caller actually sent as `t` (matching the web
client's own `{type:'ping', t: Date.now()}` convention) -- with the
wrong key, the bridge's pong reply always carried `t: null`, the
terminal's own pong handler never found a usable timestamp, and
self._latency_ms silently stayed None for the entire session. The
status bar's own latency widget (see test_mrc_terminal_sidebar_and_clock.py)
therefore never rendered -- this is the actual root cause Jerry's "the
status bar is missing ping/latency" report traced back to, not a
missing feature.
"""
import asyncio
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

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
    chat._split_screen = False
    chat._handle = handle
    chat.sent = []

    async def _fake_send_json(obj):
        chat.sent.append(obj)
    chat._send_json = _fake_send_json
    return chat


def _run(coro):
    return asyncio.run(coro)


class PingSendTests(unittest.TestCase):
    def test_ping_loop_sends_t_field_matching_the_web_clients_convention(self):
        """Direct reproduction of the real bug, exercising the actual
        production _ping_loop() coroutine (not a re-implementation of
        its send statement) -- confirms the outgoing ping packet uses
        the 't' key (what the bridge actually relays back in its pong),
        not the old 'msgext' key it silently used to send. PING_INTERVAL
        (60s) is faked out via a patched asyncio.sleep so this doesn't
        need a real 60-second wait."""
        chat = _make_chat()
        chat._connected = True
        chat._last_input_time = time.time()

        real_sleep = asyncio.sleep

        async def _fast_sleep(seconds):
            # Let the loop's own PING_INTERVAL sleep resolve instantly;
            # any other (accidental) sleep call still behaves normally.
            await real_sleep(0)

        async def _drive():
            with patch('anetbbs.features.mrc_chat.asyncio.sleep', _fast_sleep):
                task = asyncio.ensure_future(chat._ping_loop())
                # Give the loop enough real turns to complete one full
                # sleep-then-send cycle.
                for _ in range(5):
                    await real_sleep(0)
                    if chat.sent:
                        break
                chat._connected = False
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        _run(_drive())
        self.assertEqual(len(chat.sent), 1)
        self.assertIn('t', chat.sent[0])
        self.assertNotIn('msgext', chat.sent[0])
        self.assertEqual(chat.sent[0]['type'], 'ping')


class PongReceiveTests(unittest.TestCase):
    def test_pong_with_t_field_computes_real_latency(self):
        chat = _make_chat()
        chat._input_lock = asyncio.Lock()
        sent_at = time.time() - 0.05  # 50ms ago
        _run(chat._handle_event({'type': 'pong', 't': sent_at}))
        self.assertIsNotNone(chat._latency_ms)
        # Allow generous slack for test-runner scheduling jitter --
        # this only needs to prove a real, positive, roughly-50ms
        # round trip was computed, not exact timing.
        self.assertGreaterEqual(chat._latency_ms, 0)
        self.assertLess(chat._latency_ms, 5000)

    def test_pong_with_only_the_old_msgext_field_still_works_as_a_fallback(self):
        """A rolling deploy could briefly have an old bridge build
        still echoing the pre-fix field name -- confirms the fallback
        chain (t, then msgext, then echo) doesn't regress that case."""
        chat = _make_chat()
        chat._input_lock = asyncio.Lock()
        sent_at = time.time() - 0.01
        _run(chat._handle_event({'type': 'pong', 'msgext': sent_at}))
        self.assertIsNotNone(chat._latency_ms)

    def test_pong_with_no_usable_timestamp_leaves_latency_unset(self):
        chat = _make_chat()
        chat._input_lock = asyncio.Lock()
        self.assertIsNone(chat._latency_ms)
        _run(chat._handle_event({'type': 'pong', 't': None}))
        self.assertIsNone(chat._latency_ms)


if __name__ == '__main__':
    unittest.main()
