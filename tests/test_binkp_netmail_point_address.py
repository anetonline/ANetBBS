"""Regression test found during a netmail send/receive correctness pass
(prompted by fixing the M_GOT timestamp bug -- see
test_binkp_got_ack_includes_size.py / test_binkp_server_got_ack_mtime.py):
point-addressed netmail (e.g. "1200:1/2.5") silently lost its point
number on import.

_build_ftn_packet() (the SEND side) never puts the point inside @INTL's
own address strings -- only @FMPT (origin point) and @TOPT (destination
point) carry it, per FTS-4008/FSC-0035. _parse_ftn_packet() (the RECEIVE
side) parsed both kludges and then discarded them (`pass` for @FMPT;
@TOPT wasn't even matched at all), so from_address/to_address always
came back as bare "zone:net/node" for any point system, even though our
own send side just emitted the point separately moments earlier -- a
real send/receive round-trip mismatch, not just a parsing gap.

Also: binkp_server.py's inbound-listener netmail import
(_import_pkt_payload) had its OWN, separate re-derivation of
from_address/to_address from @INTL alone (no @FMPT/@TOPT, and no
header-net/node fallback if @INTL was absent either) instead of using
_parse_ftn_packet's own (now-fixed) fields -- the same "second
unpatched path" pattern this codebase has hit repeatedly for logic that
exists in both binkp.py and binkp_server.py.
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


class NetmailPointAddressRoundTripTests(unittest.TestCase):
    def _build_and_parse(self, msg, our_addr, hub_addr='1:114/0'):
        from anetbbs.echomail.binkp import _build_ftn_packet, _parse_ftn_packet
        pkt = _build_ftn_packet([msg], our_addr, hub_addr)
        parsed = _parse_ftn_packet(pkt)
        self.assertEqual(len(parsed), 1)
        return parsed[0]

    def test_destination_point_survives_the_round_trip(self):
        """Netmail TO a point system (e.g. a downstream BinkPNode
        registered as "1200:1/2.5") must keep its point number after
        being sent and re-parsed -- this is exactly what happens when
        our own outbound .pkt is later imported (e.g. by the same
        listener that received the M_GOT for it, or by a peer echoing
        it back). Uses different zones for the two sides (as the
        boss-to-boss sanity check below does) so @INTL is actually
        emitted -- _build_ftn_packet only emits @INTL when zones differ,
        and this test is specifically about the @FMPT/@TOPT point fix,
        not the separate (pre-existing, documented) zone-less fallback
        used when @INTL is absent."""
        msg = _FakeMsg(to_address='1200:1/2.5', from_address='1:114/30')
        parsed = self._build_and_parse(msg, our_addr='1:114/30')
        self.assertEqual(parsed['to_address'], '1200:1/2.5',
            "the destination point (.5) must survive send+receive, not "
            "collapse to '1200:1/2'")

    def test_origin_point_survives_the_round_trip(self):
        """Netmail FROM a point system must keep its point number too."""
        msg = _FakeMsg(to_address='1:114/30', from_address='1200:1/2.5')
        parsed = self._build_and_parse(msg, our_addr='1200:1/2.5')
        self.assertEqual(parsed['from_address'], '1200:1/2.5',
            "the origin point (.5) must survive send+receive, not "
            "collapse to '1200:1/2'")

    def test_boss_to_boss_netmail_unaffected(self):
        """Sanity check: ordinary non-point (boss-to-boss) netmail must
        not gain a spurious point suffix."""
        msg = _FakeMsg(to_address='1:123/3003', from_address='1200:1/1')
        parsed = self._build_and_parse(msg, our_addr='1200:1/1')
        self.assertEqual(parsed['to_address'], '1:123/3003')
        self.assertEqual(parsed['from_address'], '1200:1/1')


class ServerImportUsesParsedAddressesTests(unittest.TestCase):
    """binkp_server.py's _import_pkt_payload() must use
    _parse_ftn_packet's own from_address/to_address (including the
    @FMPT/@TOPT fix above) rather than re-deriving from @INTL locally."""

    def test_import_pkt_payload_preserves_point_addresses(self):
        from unittest.mock import patch
        from anetbbs.echomail.binkp import _build_ftn_packet

        # Different zones (as the boss-to-boss sanity check above uses)
        # so @INTL is actually emitted by the send side -- this test is
        # about the @FMPT/@TOPT point fix specifically, not the separate
        # zone-less fallback used when @INTL is absent.
        msg = _FakeMsg(to_address='1200:1/2.5', from_address='1:114/30',
                       subject='Point Test')
        pkt = _build_ftn_packet([msg], '1:114/30', '1:114/0')

        captured = {}

        class _FakeQuery:
            def filter_by(self, **kw):
                return self
            def filter(self, *a, **kw):
                return self
            def first(self):
                return None
            def get(self, *a, **kw):
                return None

        class _FakeNetmailMessage:
            # Class-level placeholders so `NetmailMessage.network_id ==
            # network_id`-style column comparisons in the dedup filter()
            # calls evaluate as plain None-comparisons instead of raising
            # AttributeError before .filter() is ever even reached.
            query = _FakeQuery()
            network_id = None
            direction = None
            from_name = None
            from_address = None
            subject = None
            from datetime import datetime as _dt
            received_at = _dt.max

            def __init__(self, **kwargs):
                self.id = 1
                captured.update(kwargs)

        class _NoOpSession:
            def add(self, obj):
                pass
            def flush(self):
                pass
            def commit(self):
                pass

        with patch('anetbbs.models.NetmailMessage', _FakeNetmailMessage), \
             patch('anetbbs.models.EchomailMessage') as fake_echomail, \
             patch('anetbbs.models.EchoArea') as fake_area, \
             patch('anetbbs.models.EchomailNetwork') as fake_network, \
             patch('anetbbs.models.db') as fake_db, \
             patch('anetbbs.echomail.routing.resolve_netmail_recipient',
                   lambda *a, **k: None):
            fake_echomail.query = _FakeQuery()
            fake_area.query = _FakeQuery()
            fake_network.query = _FakeQuery()
            fake_db.session = _NoOpSession()
            from anetbbs.echomail import binkp_server as mod
            mod._import_pkt_payload(pkt, network_id=1, filename='test.pkt')

        self.assertEqual(captured.get('to_address'), '1200:1/2.5',
            "_import_pkt_payload must preserve the destination point "
            "number, not just whatever @INTL alone contains")


if __name__ == '__main__':
    unittest.main()
