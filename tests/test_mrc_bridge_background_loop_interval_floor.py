"""Regression test for a real gap found in a security/performance audit:
BridgeApp.keepalive_loop()/_periodic_userlist_refresh()/
_periodic_stats_refresh() (mrc/bridge/main.py) each read their interval
straight out of config with no lower bound at all:

    interval = float(self.config.get("iamhere_interval_seconds", 60))
    while True:
        await asyncio.sleep(interval)
        ...

An accidental "0" (or negative) value for iamhere_interval_seconds /
userlist_refresh_interval_seconds / stats_refresh_interval_seconds in
mrc/bridge/config.json turns the loop into a busy-loop with no real
delay between iterations, hammering the upstream MRC hub with IAMHERE/
USERLIST/STATS packets as fast as the event loop can cycle -- plausibly
exactly the kind of flood that gets the bridge's IP rate-limited by the
hub's own abuse protection, the same failure mode _reconnect_loop()
already goes to considerable lengths to avoid (see
test_mrc_bridge_reconnect_backoff.py).

Fixed with a shared _safe_loop_interval() helper that clamps to a
5-second floor, used by all three loops.
"""
import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mrc.bridge.main import (
    BridgeApp, _safe_loop_interval, _MIN_BACKGROUND_LOOP_INTERVAL)


class SafeLoopIntervalUnitTests(unittest.TestCase):
    """Direct tests of the pure helper -- the actual regression guard
    for the misconfiguration itself."""

    def test_zero_is_clamped_to_the_floor(self):
        self.assertEqual(
            _safe_loop_interval({"iamhere_interval_seconds": 0},
                                "iamhere_interval_seconds", 60),
            _MIN_BACKGROUND_LOOP_INTERVAL)

    def test_negative_is_clamped_to_the_floor(self):
        self.assertEqual(
            _safe_loop_interval({"iamhere_interval_seconds": -5},
                                "iamhere_interval_seconds", 60),
            _MIN_BACKGROUND_LOOP_INTERVAL)

    def test_non_numeric_falls_back_to_default_not_a_crash(self):
        self.assertEqual(
            _safe_loop_interval({"iamhere_interval_seconds": "garbage"},
                                "iamhere_interval_seconds", 60),
            60)

    def test_a_reasonable_configured_value_passes_through_unchanged(self):
        self.assertEqual(
            _safe_loop_interval({"iamhere_interval_seconds": 45},
                                "iamhere_interval_seconds", 60),
            45)

    def test_missing_key_uses_the_default(self):
        self.assertEqual(_safe_loop_interval({}, "iamhere_interval_seconds", 60), 60)


class BackgroundLoopMisconfigurationTests(unittest.IsolatedAsyncioTestCase):
    """End-to-end: a real BridgeApp background loop constructed with a
    misconfigured 0-second interval must never actually sleep for less
    than the floor -- confirmed via the real asyncio.sleep call site,
    not just the helper in isolation."""

    def _make_app(self, config):
        app = object.__new__(BridgeApp)
        app.config = config
        app.mrc = AsyncMock()
        app.mrc.connected = False  # loop body no-ops; only sleep() timing matters
        return app

    async def test_keepalive_loop_never_busy_loops_on_zero_interval(self):
        app = self._make_app({"iamhere_interval_seconds": 0})
        sleep_calls = []
        call_count = 0

        async def fake_sleep(seconds):
            nonlocal call_count
            sleep_calls.append(seconds)
            call_count += 1
            if call_count >= 3:
                raise asyncio.CancelledError()

        import mrc.bridge.main as main_mod
        orig_sleep = main_mod.asyncio.sleep
        main_mod.asyncio.sleep = fake_sleep
        try:
            with self.assertRaises(asyncio.CancelledError):
                await app.keepalive_loop()
        finally:
            main_mod.asyncio.sleep = orig_sleep

        self.assertTrue(sleep_calls, "keepalive_loop must call asyncio.sleep")
        self.assertTrue(
            all(s >= _MIN_BACKGROUND_LOOP_INTERVAL for s in sleep_calls),
            f"a misconfigured 0-second interval must be clamped to the "
            f"floor before being handed to asyncio.sleep -- got {sleep_calls}")

    async def test_periodic_userlist_refresh_never_busy_loops_on_negative_interval(self):
        app = self._make_app({"userlist_refresh_interval_seconds": -1})
        sleep_calls = []
        call_count = 0

        async def fake_sleep(seconds):
            nonlocal call_count
            sleep_calls.append(seconds)
            call_count += 1
            if call_count >= 3:
                raise asyncio.CancelledError()

        import mrc.bridge.main as main_mod
        orig_sleep = main_mod.asyncio.sleep
        main_mod.asyncio.sleep = fake_sleep
        try:
            with self.assertRaises(asyncio.CancelledError):
                await app._periodic_userlist_refresh()
        finally:
            main_mod.asyncio.sleep = orig_sleep

        self.assertTrue(sleep_calls)
        self.assertTrue(all(s >= _MIN_BACKGROUND_LOOP_INTERVAL for s in sleep_calls))

    async def test_periodic_stats_refresh_never_busy_loops_on_zero_interval(self):
        app = self._make_app({"stats_refresh_interval_seconds": 0})
        sleep_calls = []
        call_count = 0

        async def fake_sleep(seconds):
            nonlocal call_count
            sleep_calls.append(seconds)
            call_count += 1
            if call_count >= 3:
                raise asyncio.CancelledError()

        import mrc.bridge.main as main_mod
        orig_sleep = main_mod.asyncio.sleep
        main_mod.asyncio.sleep = fake_sleep
        try:
            with self.assertRaises(asyncio.CancelledError):
                await app._periodic_stats_refresh()
        finally:
            main_mod.asyncio.sleep = orig_sleep

        self.assertTrue(sleep_calls)
        self.assertTrue(all(s >= _MIN_BACKGROUND_LOOP_INTERVAL for s in sleep_calls))


if __name__ == '__main__':
    unittest.main()
