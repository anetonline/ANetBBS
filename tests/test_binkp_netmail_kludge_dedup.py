"""Regression test for a real gap found in a full echomail-subsystem
audit: web/netmail.py's compose() route pre-builds and stores a full
kludge set (PID, TZUTC, REPLY, INTL, FMPT/TOPT) on NetmailMessage.
kludges when the message is first composed. _build_ftn_packet()
(anetbbs/echomail/binkp.py) later re-derives its OWN copies of
REPLY/INTL/FMPT/TOPT from the message's address/reply_id fields, but
only ever stripped CHRS/MSGID/TID/PID from the "existing" kludges
before appending its own -- REPLY/INTL/FMPT/TOPT sailed through
unfiltered, so any inter-zone/point/reply netmail composed via the web
UI shipped each of those kludges TWICE on the wire. Separately, PID
was stripped from `existing` but never regenerated anywhere, so
outbound netmail never actually carried a PID kludge despite compose()
building one.

Fixed by extending the strip filter to also drop REPLY/INTL/FMPT/TOPT
(regenerated fresh below, matching the existing CHRS/MSGID/TID/PID
treatment) and adding a PID kludge back into kludge_head.
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class _FakeMsg:
    def __init__(self, *, from_name='Tester', to_name='Recipient',
                subject='Test', body='Hello', to_address='', from_address='',
                kludges=None, reply_id=''):
        self.area = None   # None => netmail
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


class NetmailKludgeDedupTests(unittest.TestCase):
    def _build_and_parse(self, msg, our_addr, hub_addr='1:114/0'):
        from anetbbs.echomail.binkp import _build_ftn_packet, _parse_ftn_packet
        pkt = _build_ftn_packet([msg], our_addr, hub_addr)
        parsed = _parse_ftn_packet(pkt)
        self.assertEqual(len(parsed), 1)
        return parsed[0]

    def _kludge_heads(self, parsed):
        return [k.split(' ', 1)[0].split(':', 1)[0].upper()
                for k in parsed.get('kludges', [])]

    def test_web_composed_netmail_does_not_duplicate_intl_fmpt_topt_reply(self):
        """Mirrors exactly what web/netmail.py's compose() pre-builds and
        stores -- a full kludge set already containing REPLY/INTL/FMPT/
        TOPT -- for an inter-zone, point-addressed, threaded reply."""
        pre_built_kludges = [
            'PID: ANETBBS 1.0',
            'TZUTC: 0000',
            'REPLY: 1:114/30 abc123',
            'INTL 1200:1/2 1:114/30',
            'FMPT 7',
            'TOPT 5',
        ]
        msg = _FakeMsg(to_address='1200:1/2.5', from_address='1:114/30.7',
                       kludges=pre_built_kludges, reply_id='1:114/30 abc123')
        parsed = self._build_and_parse(msg, our_addr='1:114/30.7')

        heads = self._kludge_heads(parsed)
        for kludge_name in ('REPLY', 'INTL', 'FMPT', 'TOPT'):
            self.assertEqual(
                heads.count(kludge_name), 1,
                f'{kludge_name} must appear exactly once on the wire, got '
                f'{heads.count(kludge_name)}: {parsed["kludges"]!r}')

    def test_outbound_netmail_carries_a_pid_kludge(self):
        """PID was stripped from any pre-existing kludge set but never
        regenerated anywhere -- outbound netmail never actually carried
        one despite compose() building one."""
        msg = _FakeMsg(to_address='1:123/3003', from_address='1200:1/1',
                       kludges=['PID: SOMETHING OLD 1.0'])
        parsed = self._build_and_parse(msg, our_addr='1200:1/1')

        heads = self._kludge_heads(parsed)
        self.assertEqual(heads.count('PID'), 1,
                         f'expected exactly one PID kludge, got: {parsed["kludges"]!r}')
        pid_line = next(k for k in parsed['kludges']
                        if k.upper().startswith('PID'))
        self.assertIn('ANETBBS', pid_line)

    def test_netmail_with_no_prior_kludges_still_gets_single_copies(self):
        """Sanity check: a message with no stored kludges at all (the
        common non-web-composed case) must still produce exactly one
        copy of each generated kludge, not zero or extras."""
        msg = _FakeMsg(to_address='1200:1/2.5', from_address='1:114/30.7',
                       reply_id='1:114/30 xyz789')
        parsed = self._build_and_parse(msg, our_addr='1:114/30.7')

        heads = self._kludge_heads(parsed)
        for kludge_name in ('PID', 'REPLY', 'INTL', 'FMPT', 'TOPT'):
            self.assertEqual(heads.count(kludge_name), 1, f'{kludge_name}: {heads}')


if __name__ == '__main__':
    unittest.main()
