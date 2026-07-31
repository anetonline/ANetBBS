"""Regression test for a real live bug found the day after v1.0.4's
"reply to netmail via its arrival network" fix shipped: a sysop's reply
to a real zone-1 Fidonet netmail went out From an unrelated zone-1200
AKA (his "primary" AKA, configured for a completely different network)
instead of the Fidonet network's own correct our_address -- the
receiving hub's own upstream relay flagged the origin address as
unroutable ("Originating address 1200:1/1 unknown ... Please do NOT
answer ... that mail would be bounced!").

Root cause: find_aka_for_network() (anetbbs/echomail/routing.py) fell
back to the user's primary AKA (or just the first one) whenever none of
their AKAs matched the target network's own zone -- pre-empting the
caller's own network.our_address fallback, which is guaranteed correct
for that specific network by definition. A zone-mismatched AKA is
confidently WRONG, not a reasonable "better than nothing" guess.

Fixed by returning None on no zone match (same as the existing
no-AKAs-at-all case), letting both callers (netmail.py's compose(),
telegram.py's send()) fall through to network.our_address as already
documented in their own `aka.address if aka else network.our_address`
lines -- which never actually ran while this function always returned
*something* non-None.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.echomail.routing import find_aka_for_network


def _aka(address, is_primary=False):
    a = MagicMock()
    a.address = address
    a.is_primary = is_primary
    return a


def _user(*akas):
    u = MagicMock()
    u.akas = list(akas)
    return u


def _network(our_address):
    n = MagicMock()
    n.our_address = our_address
    return n


class FindAkaForNetworkZoneMismatchTests(unittest.TestCase):
    def test_matching_zone_aka_is_returned(self):
        user = _user(_aka('1200:1/1', is_primary=True), _aka('1:123/3003'))
        net = _network('1:123/0')
        result = find_aka_for_network(user, net)
        self.assertIsNotNone(result)
        self.assertEqual(result.address, '1:123/3003')

    def test_no_zone_match_returns_none_not_the_primary_aka(self):
        """The exact live bug: the only/primary AKA is for a totally
        different network's zone -- must not be returned as a
        confidently-wrong guess."""
        user = _user(_aka('1200:1/1', is_primary=True))
        net = _network('1:123/0')  # zone 1 -- no AKA matches
        result = find_aka_for_network(user, net)
        self.assertIsNone(result,
                          'a zone-mismatched AKA must never be returned -- '
                          'the caller needs None so it falls back to '
                          "the network's own correct our_address")

    def test_no_zone_match_with_multiple_akas_still_returns_none(self):
        user = _user(_aka('1200:1/1', is_primary=True), _aka('21:1/100'))
        net = _network('1:123/0')  # zone 1 -- neither AKA matches
        self.assertIsNone(find_aka_for_network(user, net))

    def test_no_akas_at_all_returns_none(self):
        user = _user()
        net = _network('1:123/0')
        self.assertIsNone(find_aka_for_network(user, net))

    def test_none_user_or_network_returns_none(self):
        self.assertIsNone(find_aka_for_network(None, _network('1:123/0')))
        self.assertIsNone(find_aka_for_network(_user(_aka('1:1/1')), None))

    def test_caller_fallback_pattern_yields_correct_address_on_mismatch(self):
        """End-to-end sanity check of the exact pattern both real
        callers (netmail.py, telegram.py) use."""
        user = _user(_aka('1200:1/1', is_primary=True))
        net = _network('1:123/3003')
        aka = find_aka_for_network(user, net)
        from_address = aka.address if aka else net.our_address
        self.assertEqual(from_address, '1:123/3003')


if __name__ == '__main__':
    unittest.main()
