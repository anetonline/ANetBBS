"""Regression test for a real live bug reported by a peer sysop
("Firehawke" -- do not use real name in public docs): a remote peer
redelivering the same backlog to ANetBBS's inbound BinkP listener
(anetbbs/echomail/binkp_server.py, used when a peer connects IN to
deliver mail -- as opposed to anetbbs/echomail/poller.py, used when
ANetBBS dials OUT) got every message re-imported as brand new on every
single delivery, unbounded. Live report: ~570 messages "received" on
every inbound connection, repeated every few minutes.

Root cause, found by comparing against the already-correct sibling
functions in poller.py: `_import_message()` and `_import_netmail()`
both check for an existing row by MSGID before inserting.
`_import_pkt_payload()` (this listener's own import path) did neither
-- echomail never even extracted MSGID onto the row at all, and
netmail extracted it but never checked for an existing one first.

Builds a real FTS-0001 packet via `_build_ftn_packet()` (the same
function poller.py itself uses to build outbound packets) so this
exercises the real parse+import round trip, not a hand-built dict.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class _FakeMsg:
    """Minimal object satisfying _build_ftn_packet()'s attribute reads --
    mirrors the real shape of an EchomailMessage / _NetmailAdapter."""
    def __init__(self, *, area=None, from_name='Tester', to_name='All',
                subject='Test', body='Hello', msg_id=None, reply_id=None,
                to_address='', from_address=''):
        self.area = area
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
        self.msg_id = msg_id
        self.reply_id = reply_id
        self.to_address = to_address
        self.from_address = from_address


class _FakeArea:
    def __init__(self, tag):
        self.tag = tag


def _fresh_app(db_path):
    import anetbbs.config as cfg_mod
    if os.path.exists(db_path):
        os.remove(db_path)
    cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path}'
    os.environ['FLASK_ENV'] = 'testing'
    from anetbbs.web_app import create_app
    app = create_app('testing')
    app.config['TESTING'] = True
    return app


class InboundDedupTests(unittest.TestCase):
    def setUp(self):
        # _fresh_app() overwrites TestingConfig.SQLALCHEMY_DATABASE_URI
        # globally -- must be restored, or any test file that runs later
        # in the same pytest process and calls create_app('testing')
        # tries to open this test's already-deleted temp DB file.
        import anetbbs.config as cfg_mod
        self._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        self.addCleanup(
            lambda: setattr(cfg_mod.TestingConfig,
                            'SQLALCHEMY_DATABASE_URI', self._orig_db_uri))
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _fresh_app(str(Path(self._tmp.name) / 'inbound_dedup.db'))

    def _make_network(self):
        from anetbbs.models import db, EchomailNetwork
        with self.app.app_context():
            net = EchomailNetwork(name='TestNet', network_type='binkp',
                                  our_address='1:114/30',
                                  hub_address='1:114/0')
            db.session.add(net)
            db.session.commit()
            return net.id

    def test_redelivered_echomail_packet_is_not_reimported(self):
        from anetbbs.echomail.binkp import _build_ftn_packet
        from anetbbs.echomail import binkp_server

        net_id = self._make_network()
        msg = _FakeMsg(area=_FakeArea('TEST_AREA'), subject='Re-delivered',
                       msg_id='1:114/30@fidonet 5f3a9b2c.abcd')
        pkt = _build_ftn_packet([msg], '1:114/30', '1:114/0')

        with self.app.app_context():
            first = binkp_server._import_pkt_payload(pkt, net_id, 'a.pkt')
            second = binkp_server._import_pkt_payload(pkt, net_id, 'b.pkt')

            self.assertEqual(first, 1,
                            'first delivery of a new MSGID must import')
            self.assertEqual(second, 0,
                            'redelivery of the SAME MSGID must be skipped, '
                            'not counted as a fresh import -- this is the '
                            'exact "mass duplicates" bug reported live')

            from anetbbs.models import EchomailMessage
            rows = EchomailMessage.query.filter_by(
                msg_id='1:114/30@fidonet 5f3a9b2c.abcd').all()
            self.assertEqual(len(rows), 1,
                            'exactly one row must exist after 2 deliveries '
                            'of the same MSGID, not 2')

    def test_different_msgids_both_import(self):
        from anetbbs.echomail.binkp import _build_ftn_packet
        from anetbbs.echomail import binkp_server

        net_id = self._make_network()
        msg1 = _FakeMsg(area=_FakeArea('TEST_AREA'), subject='First',
                        msg_id='1:114/30@fidonet aaaa0001')
        msg2 = _FakeMsg(area=_FakeArea('TEST_AREA'), subject='Second',
                        msg_id='1:114/30@fidonet aaaa0002')
        pkt1 = _build_ftn_packet([msg1], '1:114/30', '1:114/0')
        pkt2 = _build_ftn_packet([msg2], '1:114/30', '1:114/0')

        with self.app.app_context():
            n1 = binkp_server._import_pkt_payload(pkt1, net_id, 'a.pkt')
            n2 = binkp_server._import_pkt_payload(pkt2, net_id, 'b.pkt')
            self.assertEqual(n1, 1)
            self.assertEqual(n2, 1,
                            'a genuinely different MSGID must still import '
                            'normally -- dedup must not over-trigger')

    def test_redelivered_netmail_packet_is_not_reimported(self):
        from anetbbs.echomail.binkp import _build_ftn_packet
        from anetbbs.echomail import binkp_server

        net_id = self._make_network()
        msg = _FakeMsg(area=None, from_name='Bob', to_name='Alice',
                       subject='Hi', msg_id='1:114/30@fidonet nmail0001',
                       to_address='1:114/0', from_address='1:114/30')
        pkt = _build_ftn_packet([msg], '1:114/30', '1:114/0')

        with self.app.app_context():
            first = binkp_server._import_pkt_payload(pkt, net_id, 'a.pkt')
            second = binkp_server._import_pkt_payload(pkt, net_id, 'b.pkt')
            self.assertEqual(first, 1)
            self.assertEqual(second, 0,
                            'redelivered netmail with the same MSGID must '
                            'also be deduped')

            from anetbbs.models import NetmailMessage
            rows = NetmailMessage.query.filter_by(
                msgid='1:114/30@fidonet nmail0001').all()
            self.assertEqual(len(rows), 1)

    def test_netmail_with_fresh_msgid_each_time_still_content_deduped(self):
        """Real live bug: a peer (SBBSecho) polling INTO this listener
        independently of -- and around the same ~10 minute cadence as --
        our own outbound poll of the same hub kept resending the same
        "Area Management Request"/"List of Available Areas" netmail with
        a freshly regenerated MSGID every time, defeating the exact-
        MSGID check above. poller.py's _import_netmail() (used for OUR
        OWN outbound polls) already got a content-based dedup fallback
        in v1.0b2.143/145, but this separate inbound-listener import
        path had none at all -- confirmed live it kept inserting a
        fresh row every single delivery regardless of that fix, since a
        peer can poll IN independently of when we poll OUT."""
        from anetbbs.echomail.binkp import _build_ftn_packet
        from anetbbs.echomail import binkp_server

        net_id = self._make_network()
        msg1 = _FakeMsg(area=None, from_name='SBBSecho', to_name='sysop',
                        subject='Area Management Request',
                        body='List of available areas...',
                        msg_id='1:114/30@fidonet aaaa1111',
                        to_address='1:114/0', from_address='1:3634/12')
        msg2 = _FakeMsg(area=None, from_name='SBBSecho', to_name='sysop',
                        subject='Area Management Request',
                        body='List of available areas...',
                        msg_id='1:114/30@fidonet bbbb2222',
                        to_address='1:114/0', from_address='1:3634/12')
        pkt1 = _build_ftn_packet([msg1], '1:114/30', '1:114/0')
        pkt2 = _build_ftn_packet([msg2], '1:114/30', '1:114/0')

        with self.app.app_context():
            first = binkp_server._import_pkt_payload(pkt1, net_id, 'a.pkt')
            second = binkp_server._import_pkt_payload(pkt2, net_id, 'b.pkt')
            self.assertEqual(first, 1)
            self.assertEqual(second, 0,
                            'same sender+subject+network resent with a '
                            'fresh MSGID must still be recognized as a '
                            'duplicate, not counted as a fresh import')

            from anetbbs.models import NetmailMessage
            rows = NetmailMessage.query.filter_by(
                from_name='SBBSecho', subject='Area Management Request').all()
            self.assertEqual(len(rows), 1)

    def test_content_dedup_does_not_over_trigger_on_different_subject(self):
        """Guard against the content-based fallback being too broad --
        a genuinely different message from the same sender must still
        import normally."""
        from anetbbs.echomail.binkp import _build_ftn_packet
        from anetbbs.echomail import binkp_server

        net_id = self._make_network()
        msg1 = _FakeMsg(area=None, from_name='SBBSecho', to_name='sysop',
                        subject='Area Management Request',
                        msg_id='1:114/30@fidonet cccc3333',
                        to_address='1:114/0', from_address='1:3634/12')
        msg2 = _FakeMsg(area=None, from_name='SBBSecho', to_name='sysop',
                        subject='Something Else Entirely',
                        msg_id='1:114/30@fidonet dddd4444',
                        to_address='1:114/0', from_address='1:3634/12')
        pkt1 = _build_ftn_packet([msg1], '1:114/30', '1:114/0')
        pkt2 = _build_ftn_packet([msg2], '1:114/30', '1:114/0')

        with self.app.app_context():
            first = binkp_server._import_pkt_payload(pkt1, net_id, 'a.pkt')
            second = binkp_server._import_pkt_payload(pkt2, net_id, 'b.pkt')
            self.assertEqual(first, 1)
            self.assertEqual(second, 1,
                            'a different subject from the same sender '
                            'must not be treated as a duplicate')


if __name__ == '__main__':
    unittest.main()
