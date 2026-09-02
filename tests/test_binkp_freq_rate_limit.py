"""Regression test for a real Low finding from a security/performance
audit (2026-09-02): anetbbs/echomail/binkp_server.py dispatches any
NNNNnnnn.req-named inbound file to freq.process_inbound_req() with no
net_id/downstream_node_id gate at all -- reachable by a fully
anonymous "crashmail" connection. Each accepted FREQ can queue up to
freq.MAX_FILES_PER_REQUEST (100) HatchQueue rows; since outbound hatch
delivery only ever happens through the two authenticated code paths
(a real BinkPNode.ftn_address or EchomailNetwork.hub_address exact
match), an anonymous requester's queued rows can never actually be
delivered -- they just accumulate as permanently-dead rows, a slow,
unbounded DB-growth vector under repeat anonymous connections.

Fixed with the same per-IP + global sliding-window rate limit already
used for the identical "no auth possible, bound the damage instead"
shape in the SYSTAT UDP responder (msp/systat.py) and the MSP
responder (msp/server.py).
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.echomail.binkp_server import (
    _freq_rate_limited, _FREQ_PER_IP_LIMIT, _FREQ_GLOBAL_LIMIT,
)
from anetbbs.features import rate_limit as rate_limit_mod


class BinkpFreqRateLimitTests(unittest.TestCase):
    def setUp(self):
        # The rate limiter is a shared module-level in-memory store --
        # clear it before each test so one test's usage doesn't affect
        # another's.
        rate_limit_mod._buckets.clear()
        self.addCleanup(rate_limit_mod._buckets.clear)

    def test_requests_under_the_limit_are_not_rate_limited(self):
        addr = '203.0.113.10'
        for _ in range(_FREQ_PER_IP_LIMIT):
            self.assertFalse(_freq_rate_limited(addr))

    def test_per_ip_limit_kicks_in_after_the_configured_count(self):
        addr = '203.0.113.11'
        for _ in range(_FREQ_PER_IP_LIMIT):
            _freq_rate_limited(addr)
        self.assertTrue(
            _freq_rate_limited(addr),
            f'the ({_FREQ_PER_IP_LIMIT + 1})th FREQ from the same IP within '
            f'the window must be rate-limited')

    def test_a_different_ip_is_not_affected_by_anothers_limit(self):
        addr_a = '203.0.113.12'
        addr_b = '203.0.113.13'
        for _ in range(_FREQ_PER_IP_LIMIT):
            _freq_rate_limited(addr_a)
        self.assertTrue(_freq_rate_limited(addr_a))
        self.assertFalse(
            _freq_rate_limited(addr_b),
            "a different source IP's FREQ requests must not be blocked by "
            "another IP's per-IP limit")

    def test_global_limit_kicks_in_even_across_many_distinct_ips(self):
        """A single attacker rotating through many source IPs must
        still eventually be bounded by the global cap, not just the
        per-IP one."""
        for i in range(_FREQ_GLOBAL_LIMIT):
            addr = f'203.0.113.{20 + (i % 200)}'
            _freq_rate_limited(addr)
        self.assertTrue(
            _freq_rate_limited('203.0.113.250'),
            'the global FREQ rate limit must trigger once the total count '
            'across all source IPs exceeds the configured cap, even for a '
            'brand-new IP that has never been seen before')


if __name__ == '__main__':
    unittest.main()
