"""Regression test for a real bug found live testing a fresh install:
the MRC bridge kept logging "Connecting to MRC server..." at a flat
~1-second cadence forever against a real upstream hub, never showing
any growing backoff, and a one-off manual TLS probe from the same VM
got an immediate connection reset too -- consistent with the hub's own
flood protection reacting to the retry storm.

Root cause: MRCConnection._reconnect_loop() (mrc/bridge/main.py) only
grew its exponential backoff when connect() itself failed outright
(TCP/TLS connect error). But the real failure mode was different:
connect() succeeds (TCP/TLS handshake completes, the handshake packet
is sent) and returns True immediately -- the connection then gets reset
moments later, but that's detected by a *separate* concurrently-running
receive_loop() task, which has no say in the backoff decision at all.
Every such cycle reset the backoff delay straight back to its floor,
so the bridge hammered the hub at a flat cadence with no growing
backoff regardless of how many times in a row this happened.

Fixed: a connection that drops before staying up for a configurable
minimum "stable" duration now counts as a failed cycle -- same real
sleep+backoff-growth treatment as an outright failed connect() call --
instead of falling through to the flat per-second heartbeat retry.
"""
import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mrc.bridge.main import MRCConnection

_real_sleep = asyncio.sleep


class ReconnectBackoffTests(unittest.IsolatedAsyncioTestCase):

    async def test_fast_flap_grows_backoff_instead_of_resetting(self):
        """A connection that succeeds at connect() but drops again
        before reaching the stability threshold must NOT reset the
        backoff -- consecutive fast-flap cycles must produce a growing
        sequence of real sleep delays, not a flat repeated one."""
        config = {
            "mrc_reconnect_initial_delay": 1,
            "mrc_reconnect_max_delay": 30,
            "mrc_reconnect_stable_seconds": 10,  # never reached by a flap
        }
        conn = MRCConnection(config)
        sleep_calls = []

        async def instant_sleep(seconds):
            # Collapse real wall-clock delay to nothing so the test
            # runs in milliseconds regardless of configured backoff
            # values, while still recording what duration was asked
            # for -- the assertion that matters here is about the
            # requested delay sequence, not real elapsed time.
            sleep_calls.append(seconds)

        connect_count = 0

        async def fake_connect():
            nonlocal connect_count
            connect_count += 1
            conn.connected = True
            # Simulate receive_loop() reacting to an immediate reset --
            # the connection never gets a chance to prove itself
            # stable before the next check.
            conn.connected = False
            if connect_count >= 5:
                conn._closing = True
            return True

        conn.connect = fake_connect
        with patch("mrc.bridge.main.asyncio.sleep", instant_sleep):
            await conn._reconnect_loop()

        self.assertEqual(connect_count, 5)
        # Backoff sleeps are anything other than the flat 1-second
        # heartbeat sleep at the bottom of the loop.
        backoff_sleeps = [s for s in sleep_calls if s != 1]
        self.assertGreaterEqual(
            len(backoff_sleeps), 3,
            f"expected several backoff sleeps from repeated fast-flap "
            f"cycles, got {sleep_calls}")
        self.assertTrue(
            all(a <= b for a, b in zip(backoff_sleeps, backoff_sleeps[1:])),
            f"backoff delay must never shrink across consecutive "
            f"fast-flap cycles, got {backoff_sleeps}")
        self.assertGreater(
            backoff_sleeps[-1], backoff_sleeps[0],
            "backoff must actually grow on repeated fast-flap failures, "
            "not stay flat at the initial delay")

    async def test_stable_connection_resets_backoff(self):
        """A connection that stays up past the stability threshold
        before eventually dropping should NOT carry forward a grown
        backoff -- its next retry cycle gets the initial delay again,
        same as this project's other retry-storm fixes always
        distinguish a genuine drop after real service from a
        connect-and-immediately-die flap."""
        config = {
            "mrc_reconnect_initial_delay": 1,
            "mrc_reconnect_max_delay": 30,
            "mrc_reconnect_stable_seconds": 0.05,
        }
        conn = MRCConnection(config)
        sleep_calls = []

        async def instant_sleep(seconds):
            sleep_calls.append(seconds)

        connect_count = 0

        async def fake_connect():
            nonlocal connect_count
            connect_count += 1
            conn.connected = True
            if connect_count == 1:
                # First connection stays up long enough to count as
                # stable (real elapsed time, not mocked -- stable_after
                # is tiny specifically so this sleep can be real and
                # still keep the test fast). Uses the captured
                # pre-patch sleep function since asyncio.sleep itself
                # is patched to be instant for the rest of this test.
                await _real_sleep(0.08)
                conn.connected = False
            else:
                conn.connected = False
                conn._closing = True
            return True

        conn.connect = fake_connect
        with patch("mrc.bridge.main.asyncio.sleep", instant_sleep):
            await conn._reconnect_loop()

        self.assertEqual(connect_count, 2)
        # No backoff-growth sleep should have fired between the stable
        # connection's drop and the next connect() attempt -- only the
        # flat heartbeat "1"s.
        backoff_sleeps = [s for s in sleep_calls if s != 1]
        self.assertEqual(
            backoff_sleeps, [],
            f"a connection that reached stability must not trigger a "
            f"backoff-growth sleep on its next retry, got {sleep_calls}")


if __name__ == '__main__':
    unittest.main()
