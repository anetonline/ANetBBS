"""Regression test for a real live bug reported by a peer sysop
("Firehawke" -- do not use real name in public docs): outbound echomail
origin lines never included the sysop's own FTN address at all, on any
network -- e.g. "ANetBBS - A Modern BBS System" instead of the expected
FTN convention "<text> (<our address>)" (compare a real peer's own
correctly-formed origin line: "RBB Test Robot @ FIDOTEST (2:221/360)").

Root cause: _build_ftn_packet() in anetbbs/echomail/binkp.py used
`msg.origin_line or f'ANETBBS ({our_addr})'` -- the address-inclusive
fallback only fires when origin_line is falsy, but the web compose
route (anetbbs/web/echomail.py) always populates origin_line from
ECHOMAIL_ORIGIN_LINE (a single global tagline string with no address),
so that fallback branch was unreachable in practice. With multi-hub-
identity support, `our_addr` is already correctly resolved per-network
for each outbound packet -- the bug was purely that it never got used.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class _FakeArea:
    def __init__(self, tag):
        self.tag = tag


class _FakeMsg:
    def __init__(self, *, area=None, from_name='Tester', to_name='All',
                subject='Test', body='Hello', origin_line=None,
                msg_id=None, reply_id=None, to_address='', from_address=''):
        self.area = area
        self.from_name = from_name
        self.to_name = to_name
        self.subject = subject
        self.body = body
        self.tear_line = None
        self.origin_line = origin_line
        self.kludges = None
        self.seenby = None
        self.path = None
        self.chrs = 'CP437 2'
        self.msg_id = msg_id
        self.reply_id = reply_id
        self.to_address = to_address
        self.from_address = from_address


class OriginLineAddressTests(unittest.TestCase):
    def _build_and_parse(self, msg, our_addr='1:114/30', hub_addr='1:114/0'):
        from anetbbs.echomail.binkp import _build_ftn_packet, _parse_ftn_packet
        pkt = _build_ftn_packet([msg], our_addr, hub_addr)
        parsed = _parse_ftn_packet(pkt)
        self.assertEqual(len(parsed), 1)
        return parsed[0]['origin_line']

    def test_configured_origin_text_gets_the_network_address_appended(self):
        """The exact real bug: a normal compose-route message, with
        origin_line set to a plain global tagline (no address) --
        matches ECHOMAIL_ORIGIN_LINE's real-world default exactly."""
        msg = _FakeMsg(area=_FakeArea('FIDOTEST'),
                       origin_line='ANetBBS - A Modern BBS System')
        origin = self._build_and_parse(msg, our_addr='1:114/30')
        self.assertTrue(origin.endswith('(1:114/30)'),
                        f'origin line must end with our own address: {origin!r}')
        self.assertIn('ANetBBS - A Modern BBS System', origin)

    def test_different_networks_get_their_own_address(self):
        """Multi-hub-identity: the SAME origin text, sent out under a
        different network's own address, must reflect THAT address."""
        msg = _FakeMsg(area=_FakeArea('SOMEAREA'), origin_line='A-Net Online')
        origin_a = self._build_and_parse(msg, our_addr='1:114/30')
        origin_b = self._build_and_parse(msg, our_addr='1200:1/3')
        self.assertTrue(origin_a.endswith('(1:114/30)'))
        self.assertTrue(origin_b.endswith('(1200:1/3)'))

    def test_blank_origin_line_still_gets_a_usable_default(self):
        msg = _FakeMsg(area=_FakeArea('FIDOTEST'), origin_line=None)
        origin = self._build_and_parse(msg, our_addr='1:114/30')
        self.assertTrue(origin.endswith('(1:114/30)'))

    def test_origin_text_already_ending_in_parens_is_not_double_appended(self):
        """Defensive: a sysop who already hand-included an address in
        their custom ECHOMAIL_ORIGIN_LINE must not get it duplicated."""
        msg = _FakeMsg(area=_FakeArea('FIDOTEST'),
                       origin_line='My BBS (1:114/30)')
        origin = self._build_and_parse(msg, our_addr='1:114/30')
        self.assertEqual(origin.count('('), 1)


if __name__ == '__main__':
    unittest.main()
