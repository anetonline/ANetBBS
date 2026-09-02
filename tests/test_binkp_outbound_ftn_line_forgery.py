"""Regression test for a real High finding from a security/performance
audit (2026-09-02): anetbbs/echomail/binkp.py's _build_ftn_packet()
had a comment claiming user-typed tear/origin lines were stripped
before assembly ("Strip our own tear/origin if user accidentally
embedded them") -- they never actually were. Any logged-in user
composing an echomail/netmail message (web/echomail.py, web/netmail.py,
or the terminal composers) could type a plain-text line like
"AREA:SYSOP_ONLY_AREA" or forge a "* Origin: SomeoneElse (1:2/3.4)"
line -- no special encoding needed -- and have it treated as
authoritative FTN control data by any downstream tosser that scans
EVERY line for these prefixes rather than only the true first line,
including THIS codebase's OWN inbound parser (_parse_ftn_packet, same
file). A literal SOH (0x01) byte additionally forges fake kludges.

Fixed by neutralizing any body line matching AREA:/SEEN-BY:/tear
(---)/Origin: (case-insensitive), or containing a raw SOH byte, before
composing the outbound message. Proven here via a full round-trip
through this codebase's OWN _build_ftn_packet() -> _parse_ftn_packet(),
the same pattern already established in
test_binkp_netmail_kludge_dedup.py.
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class _FakeArea:
    def __init__(self, tag):
        self.tag = tag


class _FakeMsg:
    def __init__(self, *, from_name='Tester', to_name='Recipient',
                subject='Test', body='Hello', to_address='', from_address='',
                kludges=None, reply_id='', area=None):
        self.area = area   # None => netmail
        self.from_name = from_name
        self.to_name = to_name
        self.subject = subject
        self.body = body
        self.tear_line = None
        self.origin_line = None
        self.kludges = json.dumps(kludges) if kludges is not None else None
        self.seenby = None
        self.path = None
        self.chrs = 'CP437 2'
        self.msg_id = None
        self.reply_id = reply_id
        self.to_address = to_address
        self.from_address = from_address


class BinkpOutboundFtnLineForgeryTests(unittest.TestCase):
    def _build_and_parse(self, msg, our_addr, hub_addr='1:114/0'):
        from anetbbs.echomail.binkp import _build_ftn_packet, _parse_ftn_packet
        pkt = _build_ftn_packet([msg], our_addr, hub_addr)
        parsed = _parse_ftn_packet(pkt)
        self.assertEqual(len(parsed), 1)
        return parsed[0]

    def test_forged_area_line_in_netmail_body_does_not_leak_as_a_real_area_tag(self):
        msg = _FakeMsg(
            to_address='1:123/3003', from_address='1200:1/1',
            body='Hey, check this out:\nAREA:SYSOP_ONLY_AREA\nThanks!')
        p = self._build_and_parse(msg, our_addr='1200:1/1')
        self.assertIsNone(
            p['area_tag'],
            'a forged AREA: line inside a NETMAIL body must not leak '
            'through as a real area tag on re-parse')
        self.assertNotIn('AREA:SYSOP_ONLY_AREA', p['body'])

    def test_forged_seenby_line_in_echomail_body_is_not_merged_into_real_routing_data(self):
        msg = _FakeMsg(
            from_address='1200:1/1',
            body='Normal text\nSEEN-BY: 9999/9999\nMore text',
            area=_FakeArea('GENERAL'))
        p = self._build_and_parse(msg, our_addr='1200:1/1')
        self.assertNotIn(
            '9999/9999', p.get('seenby') or [],
            'a forged SEEN-BY: line inside the message body must not be '
            'merged into the real routing SEEN-BY list')
        self.assertNotIn('SEEN-BY:', p['body'])

    def test_forged_origin_line_does_not_survive_into_the_body(self):
        msg = _FakeMsg(
            to_address='1:123/3003', from_address='1200:1/1',
            body='Hi there\n * Origin: Impersonator (9:9/9.9)\nBye')
        p = self._build_and_parse(msg, our_addr='1200:1/1')
        self.assertNotIn('Impersonator', p['body'])
        self.assertNotIn('9:9/9.9', p['body'])
        # The real origin (ours, from our_addr) must still be present --
        # the fix strips a FORGED origin line from the body, it doesn't
        # break the real one build_message() adds afterward.
        self.assertIn('1200:1/1', p.get('origin_line') or '')

    def test_forged_soh_kludge_byte_does_not_inject_a_fake_kludge(self):
        msg = _FakeMsg(
            to_address='1:123/3003', from_address='1200:1/1',
            body='Hello\x01MSGID: 9:9/9.9 deadbeef\nBye')
        p = self._build_and_parse(msg, our_addr='1200:1/1')
        forged = [k for k in p['kludges']
                 if 'deadbeef' in k or '9:9/9.9' in k]
        self.assertEqual(forged, [],
                         f'a raw SOH byte in the body must not inject a '
                         f'fake kludge, got: {p["kludges"]!r}')

    def test_ordinary_message_content_is_unaffected(self):
        """Sanity check: the fix must not mangle a normal message body
        that happens to contain a dash-prefixed line (a common markdown/
        ASCII-art pattern) that ISN'T a real tear-line-shaped '---'."""
        msg = _FakeMsg(
            to_address='1:123/3003', from_address='1200:1/1',
            body='Some thoughts:\n- point one\n- point two\nThanks!')
        p = self._build_and_parse(msg, our_addr='1200:1/1')
        self.assertIn('point one', p['body'])
        self.assertIn('point two', p['body'])


if __name__ == '__main__':
    unittest.main()
