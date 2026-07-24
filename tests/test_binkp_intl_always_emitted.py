"""Regression test for a real live bug: _build_ftn_packet() only wrote
the @INTL kludge for netmail when the sender's zone differed from the
recipient's -- `if sz != mz: kludge_head.append(f'INTL ...')`. FTS-0001's
per-message binary routing header has NO zone field at all (only
node/net); @INTL is the ONLY place zone info travels on the wire for
netmail, and skipping it whenever our own zone happened to match the
recipient's assumed the receiving system would default untagged mail to
that same zone.

Confirmed false in practice: our hub (zone 1200) sent an AreaFix reply
to a real downstream node also on zone 1200 (1200:1/4). Because both
sides were zone 1200, no @INTL was emitted -- and the receiving
system's own tosser (SBBSecho), which has a PRIMARY/default AKA on a
different zone (a common real-world multi-AKA configuration -- a
node's main identity is often a FidoNet zone-1 address, with
smaller-network zones like 1200 as secondary AKAs), filed the message
under its zone-1 identity instead: its own log showed "Craig Hendricks
(1:1/4)" instead of "(1200:1/4)" for a reply addressed to 1200:1/4.

Fix: always emit @INTL for netmail, regardless of whether zones match.
It's valid and universally supported by every real FTN mailer either
way, so there's no downside to no longer trying to omit it as an
"optimization".
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class _FakeMsg:
    def __init__(self, *, from_name='Tester', to_name='Recipient',
                subject='Test', body='Hello', to_address='', from_address=''):
        self.area = None   # None => netmail
        self.from_name = from_name
        self.to_name = to_name
        self.subject = subject
        self.body = body
        self.tear_line = None
        self.origin_line = None
        self.kludges = None
        self.seenby = None
        self.path = None
        self.chrs = 'CP437 2'
        self.msg_id = None
        self.reply_id = None
        self.to_address = to_address
        self.from_address = from_address


class IntlAlwaysEmittedTests(unittest.TestCase):
    def _built_body(self, msg, our_addr, hub_addr='1200:1/1'):
        from anetbbs.echomail.binkp import _build_ftn_packet, FTN_PKT_HEADER_SIZE
        pkt = _build_ftn_packet([msg], our_addr, hub_addr)
        return pkt[FTN_PKT_HEADER_SIZE:]

    def test_same_zone_netmail_still_gets_intl(self):
        """The exact live bug shape: sender and recipient both on zone
        1200 -- @INTL must still be present so a multi-AKA receiving
        system doesn't default the message to a different zone."""
        msg = _FakeMsg(to_address='1200:1/4', from_address='1200:1/1')
        body = self._built_body(msg, our_addr='1200:1/1')
        self.assertIn(b'INTL 1200:1/4 1200:1/1', body,
            "@INTL must be emitted even when the sender and recipient "
            "are on the same zone -- FTS-0001's binary per-message "
            "header has no zone field at all, so this is the only "
            "place zone info travels on the wire")

    def test_different_zone_netmail_still_gets_intl(self):
        """Guard against a too-narrow fix: the pre-existing cross-zone
        case must keep working."""
        msg = _FakeMsg(to_address='1:123/3003', from_address='1200:1/1')
        body = self._built_body(msg, our_addr='1200:1/1')
        self.assertIn(b'INTL 1:123/3003 1200:1/1', body)

    def test_echomail_never_gets_intl(self):
        """@INTL is netmail-only (FTS-4001) -- must not leak onto public
        echomail area posts."""
        from anetbbs.echomail.binkp import _build_ftn_packet, FTN_PKT_HEADER_SIZE

        class _FakeArea:
            tag = 'TEST_AREA'

        msg = _FakeMsg(subject='Echomail post')
        msg.area = _FakeArea()
        pkt = _build_ftn_packet([msg], '1200:1/1', '1200:1/1')
        body = pkt[FTN_PKT_HEADER_SIZE:]
        self.assertNotIn(b'INTL', body)


if __name__ == '__main__':
    unittest.main()
