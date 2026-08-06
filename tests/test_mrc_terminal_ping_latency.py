"""Regression tests for the terminal MRC client's ping/latency handling
(anetbbs/features/mrc_chat.py).

Two rounds of history here:

1. `_ping_loop()` sent its outgoing timestamp under the key `msgext`,
   but the bridge (mrc/bridge/main.py) only ever echoes back whatever
   key the caller actually sent as `t` -- with the wrong key, the pong
   reply always carried `t: null` and self._latency_ms silently stayed
   None. PingSendTests covers that fix (still valid, unchanged since).

2. Once that was fixed, the value it computed was still wrong in a
   more fundamental way: 'ping'/'pong' here are a WebSocket-level
   keepalive between the terminal and the LOCAL bridge daemon on the
   SAME machine (mrc/bridge/main.py's handle_websocket just echoes `t`
   straight back) -- not a measurement of the real connection to the
   upstream MRC hub. It displayed near-zero loopback time labeled as
   "latency", which is backwards from what a sysop or caller actually
   wants to see (real MRC-network latency, the thing users in-channel
   already discuss manually -- see project_mrc_ping_latency_status_
   swap.md). Fixed by decoupling the two entirely: pong is now a pure
   no-op (ping is still sent for its own NAT/firewall keepalive
   purpose), and a new `{"type": "latency", "ms": N}` push from the
   bridge -- driven by mrc/bridge/latency.py's real round-trip
   measurement against the actual hub -- is what sets self._latency_ms
   now. PongIsKeepaliveOnlyTests and LatencyEventTests cover this.
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


class PongIsKeepaliveOnlyTests(unittest.TestCase):
    """pong no longer touches _latency_ms at all -- it's local loopback
    time, not real MRC-hub latency. See module docstring, round 2."""

    def test_pong_with_t_field_does_not_set_latency(self):
        chat = _make_chat()
        chat._input_lock = asyncio.Lock()
        sent_at = time.time() - 0.05
        _run(chat._handle_event({'type': 'pong', 't': sent_at}))
        self.assertIsNone(chat._latency_ms)

    def test_pong_never_raises_regardless_of_payload_shape(self):
        chat = _make_chat()
        chat._input_lock = asyncio.Lock()
        _run(chat._handle_event({'type': 'pong'}))
        _run(chat._handle_event({'type': 'pong', 't': None}))
        self.assertIsNone(chat._latency_ms)


class LatencyEventTests(unittest.TestCase):
    """The real source of truth now: a {"type": "latency", "ms": N} push
    from the bridge, driven by mrc/bridge/latency.py's actual round-trip
    measurement against the upstream MRC hub."""

    def test_latency_event_sets_latency_ms(self):
        chat = _make_chat()
        chat._input_lock = asyncio.Lock()
        _run(chat._handle_event({'type': 'latency', 'ms': 123}))
        self.assertEqual(chat._latency_ms, 123)

    def test_latency_event_floors_and_clamps_to_zero(self):
        chat = _make_chat()
        chat._input_lock = asyncio.Lock()
        _run(chat._handle_event({'type': 'latency', 'ms': -5}))
        self.assertEqual(chat._latency_ms, 0)
        _run(chat._handle_event({'type': 'latency', 'ms': 12.9}))
        self.assertEqual(chat._latency_ms, 12)

    def test_latency_event_with_no_ms_leaves_value_unset(self):
        chat = _make_chat()
        chat._input_lock = asyncio.Lock()
        self.assertIsNone(chat._latency_ms)
        _run(chat._handle_event({'type': 'latency'}))
        self.assertIsNone(chat._latency_ms)


if __name__ == '__main__':
    unittest.main()
