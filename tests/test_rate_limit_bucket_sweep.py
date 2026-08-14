"""Regression test for a real gap found in a security audit (ironic,
in the rate limiter itself): anetbbs/features/rate_limit.py's
_buckets dict never removed a key once its bucket went fully stale --
_gc() only prunes timestamps WITHIN the one bucket being checked
right now, never the dict entry itself, and nothing ever revisits a
one-time visitor's key again to notice. Every distinct key ever seen
(client IP by default) got a permanent entry for the life of the
process. Fixed with a probabilistic sweep (_sweep_stale_buckets)
piggybacked on _check() itself.
"""
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.features import rate_limit


class RateLimitBucketSweepTests(unittest.TestCase):
    def setUp(self):
        rate_limit._buckets.clear()
        self.addCleanup(rate_limit._buckets.clear)

    def test_sweep_removes_empty_buckets(self):
        rate_limit._buckets['stale-empty'] = rate_limit.deque()
        rate_limit._sweep_stale_buckets()
        self.assertNotIn('stale-empty', rate_limit._buckets)

    def test_sweep_removes_buckets_whose_newest_entry_is_ancient(self):
        old_bucket = rate_limit.deque()
        old_bucket.append(time.time() - rate_limit._SWEEP_STALE_SECONDS - 60)
        rate_limit._buckets['long-gone'] = old_bucket
        rate_limit._sweep_stale_buckets()
        self.assertNotIn('long-gone', rate_limit._buckets)

    def test_sweep_leaves_recently_active_buckets_alone(self):
        fresh_bucket = rate_limit.deque()
        fresh_bucket.append(time.time())
        rate_limit._buckets['still-active'] = fresh_bucket
        rate_limit._sweep_stale_buckets()
        self.assertIn('still-active', rate_limit._buckets)

    def test_check_still_enforces_the_limit_correctly(self):
        """Regression guard: the sweep must not interfere with normal
        rate-limiting behavior."""
        key = 'normal-behavior-test'
        for _ in range(3):
            self.assertTrue(rate_limit._check(key, limit=3, window=60))
        self.assertFalse(rate_limit._check(key, limit=3, window=60),
                         '4th call within the window must be rejected')

    def test_probabilistic_sweep_eventually_cleans_a_one_time_visitor(self):
        """A key checked exactly once, long enough ago to be fully
        stale, must eventually be swept away by later _check() calls
        for OTHER keys -- proving the leak (a permanent entry for
        every one-time visitor) is actually closed, not just that the
        sweep function works in isolation."""
        stale_bucket = rate_limit.deque()
        stale_bucket.append(time.time() - rate_limit._SWEEP_STALE_SECONDS - 60)
        rate_limit._buckets['one-time-visitor'] = stale_bucket

        orig_prob = rate_limit._SWEEP_PROBABILITY
        rate_limit._SWEEP_PROBABILITY = 1  # force the sweep branch every call
        try:
            rate_limit._check('some-other-key', limit=100, window=60)
        finally:
            rate_limit._SWEEP_PROBABILITY = orig_prob

        self.assertNotIn('one-time-visitor', rate_limit._buckets)


if __name__ == '__main__':
    unittest.main()
