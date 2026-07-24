"""Regression test for a real live bug: _build_ftn_packet()'s address
parser never stripped the '@domain' suffix before splitting node from
point, so ANY qualified address with no point number but a domain
suffix (this network's own convention, e.g. '1200:1/4@anet') tried
int('4@anet') and always raised -- silently falling back to the
placeholder address 1:1/1.0 for EVERY outbound packet built with that
address as either side.

Confirmed live: the downstream node's own tosser log showed our hub's
outbound packets addressed "from 1200:1/1 to 1:1/1" instead of the
real destination -- every reply/echomail this hub sent to that node
(or any node on a network using this @domain-suffixed, point-less
addressing convention) carried a corrupted destination header.
"""
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class _FakeMsg:
    def __init__(self):
        self.area = None  # netmail
        self.from_name = 'Tester'
        self.to_name = 'Recipient'
        self.subject = 'Test'
        self.body = 'Hello'
        self.tear_line = None
        self.origin_line = None
        self.kludges = None
        self.seenby = None
        self.path = None
        self.chrs = 'CP437 2'
        self.msg_id = None
        self.reply_id = None
        self.to_address = ''
        self.from_address = ''


class BinkpAddressDomainSuffixTests(unittest.TestCase):
    def _dest_node(self, pkt):
        return struct.unpack_from('<H', pkt, 2)[0]

    def _dest_net(self, pkt):
        return struct.unpack_from('<H', pkt, 22)[0]

    def test_qualified_address_with_no_point_and_domain_suffix_parses_correctly(self):
        """The exact live bug shape: '1200:1/4@anet' must resolve to
        node 4, not fall back to node 1."""
        from anetbbs.echomail.binkp import _build_ftn_packet
        pkt = _build_ftn_packet([_FakeMsg()], '1200:1/1@anet', '1200:1/4@anet')
        self.assertEqual(self._dest_node(pkt), 4)
        self.assertEqual(self._dest_net(pkt), 1)

    def test_qualified_address_with_point_and_domain_suffix_still_works(self):
        from anetbbs.echomail.binkp import _build_ftn_packet
        pkt = _build_ftn_packet([_FakeMsg()], '1200:1/1@anet', '1200:1/4.5@anet')
        self.assertEqual(self._dest_node(pkt), 4)

    def test_plain_address_with_no_domain_suffix_unaffected(self):
        from anetbbs.echomail.binkp import _build_ftn_packet
        pkt = _build_ftn_packet([_FakeMsg()], '1:114/30', '1:114/99')
        self.assertEqual(self._dest_node(pkt), 99)

    def test_genuinely_malformed_address_still_falls_back_safely(self):
        from anetbbs.echomail.binkp import _build_ftn_packet
        pkt = _build_ftn_packet([_FakeMsg()], '1:114/30', 'not-an-address-at-all')
        self.assertEqual(self._dest_node(pkt), 1)  # documented 1:1/1.0 fallback


if __name__ == '__main__':
    unittest.main()
