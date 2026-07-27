"""Regression test: binkp_server.py's inbound listener
(_import_pkt_payload, used whenever a peer connects IN to deliver mail)
used to auto-create a brand-new, immediately-active, immediately-
subscribed EchoArea for ANY unrecognized tag, and never re-checked an
EXISTING area's is_subscribed/is_active before importing into it either.

poller.py's own _import_message() (the outbound-dial receive path)
already routed unrecognized/unsubscribed BinkP tags to BadAreaLog
instead (SBBSecho's BadAreaFile semantics, sysop reviews before
anything becomes visible) -- this listener path never got the same
treatment, so any peer that could complete a BinkP handshake (a
downstream node, or the hub that already dials us) could make new echo
areas silently appear on the BBS with zero sysop review.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class _FakeMsg:
    def __init__(self, *, area, subject='Test', msg_id=None):
        self.area = area
        self.from_name = 'Tester'
        self.to_name = 'All'
        self.subject = subject
        self.body = 'Hello'
        self.tear_line = None
        self.origin_line = None
        self.kludges = None
        self.seenby = None
        self.path = None
        self.chrs = 'CP437 2'
        self.msg_id = msg_id
        self.reply_id = None
        self.to_address = ''
        self.from_address = ''


class _FakeArea:
    def __init__(self, tag):
        self.tag = tag


class BinkpListenerBadAreaLogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.binkp_listener_bad_area_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _make_network(self, suffix):
        from anetbbs.models import db, EchomailNetwork
        net = EchomailNetwork(name=f'BadAreaNet{suffix}', network_type='binkp',
                              our_address='1:114/30', hub_address='1:114/0')
        db.session.add(net)
        db.session.commit()
        return net.id

    def test_unknown_area_tag_is_not_auto_created(self):
        from anetbbs.echomail.binkp import _build_ftn_packet
        from anetbbs.echomail import binkp_server
        from anetbbs.models import EchoArea, BadAreaLog

        with self.app.app_context():
            net_id = self._make_network('Unknown')
            pkt = _build_ftn_packet(
                [_FakeMsg(area=_FakeArea('NEVER.SEEN'), msg_id='1:114/30@fidonet u0001')],
                '1:114/30', '1:114/0')

            rc = binkp_server._import_pkt_payload(pkt, net_id, 'a.pkt')

            self.assertEqual(rc, 0, 'an unrecognized tag must not count as imported')
            self.assertIsNone(EchoArea.query.filter_by(tag='NEVER.SEEN').first(),
                              'an unrecognized tag must NOT silently create an area')
            bad = BadAreaLog.query.filter_by(network_id=net_id, tag='NEVER.SEEN').first()
            self.assertIsNotNone(bad, 'unrecognized tag must be logged for sysop review')
            self.assertEqual(bad.reason, 'unknown')

    def test_unsubscribed_existing_area_is_not_imported_into(self):
        from anetbbs.echomail.binkp import _build_ftn_packet
        from anetbbs.echomail import binkp_server
        from anetbbs.models import db, EchoArea, EchomailMessage, BadAreaLog

        with self.app.app_context():
            net_id = self._make_network('Unsubbed')
            db.session.add(EchoArea(network_id=net_id, tag='PAUSED.AREA',
                                    name='Paused', is_active=True,
                                    is_subscribed=False))
            db.session.commit()

            pkt = _build_ftn_packet(
                [_FakeMsg(area=_FakeArea('PAUSED.AREA'), msg_id='1:114/30@fidonet u0002')],
                '1:114/30', '1:114/0')
            rc = binkp_server._import_pkt_payload(pkt, net_id, 'b.pkt')

            self.assertEqual(rc, 0)
            self.assertEqual(EchomailMessage.query.filter_by(
                msg_id='1:114/30@fidonet u0002').count(), 0,
                'mail for an unsubscribed area must not be imported')
            bad = BadAreaLog.query.filter_by(network_id=net_id, tag='PAUSED.AREA').first()
            self.assertIsNotNone(bad)
            self.assertEqual(bad.reason, 'unsubscribed')

    def test_known_subscribed_area_still_imports_normally(self):
        """Baseline / guard against a too-broad fix."""
        from anetbbs.echomail.binkp import _build_ftn_packet
        from anetbbs.echomail import binkp_server
        from anetbbs.models import db, EchoArea, EchomailMessage

        with self.app.app_context():
            net_id = self._make_network('Known')
            db.session.add(EchoArea(network_id=net_id, tag='LIVE.AREA',
                                    name='Live', is_active=True,
                                    is_subscribed=True))
            db.session.commit()

            pkt = _build_ftn_packet(
                [_FakeMsg(area=_FakeArea('LIVE.AREA'), msg_id='1:114/30@fidonet u0003')],
                '1:114/30', '1:114/0')
            rc = binkp_server._import_pkt_payload(pkt, net_id, 'c.pkt')

            self.assertEqual(rc, 1)
            self.assertEqual(EchomailMessage.query.filter_by(
                msg_id='1:114/30@fidonet u0003').count(), 1)


if __name__ == '__main__':
    unittest.main()
