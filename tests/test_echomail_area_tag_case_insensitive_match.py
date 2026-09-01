"""Regression test for a real live bug Jerry reported (2026-09-01): an
inbound message tagged lowercase 'ann.test' (or any case other than
exactly how the local EchoArea.tag is stored -- conventionally
uppercase, per FTS-0004) was rejected as an unknown area and dropped
to BadAreaLog, even though the area obviously exists locally, just
under its uppercase tag ('ANN.TEST'). Both inbound echomail import
paths did a raw, case-SENSITIVE `EchoArea.query.filter_by(tag=...)`
lookup with no normalization -- unlike every other place in this
codebase that matches a tag against EchoArea.tag (areafix.py/
filefix.py's area_map construction, echomail_admin.py's tag=tag.upper()
on create), which already treats area tags case-insensitively.

Fixed in both:
  - poller.py's _import_message() (the outbound-poll receive path)
  - binkp_server.py's _import_pkt_payload() (the inbound-listener path,
    a peer connecting IN to deliver mail)

by uppercasing the inbound tag once, at the point it's first read,
before it's ever compared against EchoArea.tag.
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


class EchoAreaTagCaseInsensitiveMatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.echomail_tag_case_test.db')
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

    # ------------------------------------------------------------------
    # poller.py's _import_message() -- outbound-poll receive path
    # ------------------------------------------------------------------

    def test_poller_lowercase_inbound_tag_matches_uppercase_stored_area(self):
        from anetbbs.models import db, EchomailNetwork, EchoArea, EchomailMessage, BadAreaLog
        from anetbbs.echomail.poller import _import_message

        with self.app.app_context():
            net = EchomailNetwork(name='PollerCaseTestNet', network_type='binkp')
            db.session.add(net)
            db.session.commit()
            area = EchoArea(network_id=net.id, tag='ANN.TEST', name='Announce Test',
                            is_subscribed=True, is_active=True)
            db.session.add(area)
            db.session.commit()

            rc = _import_message(net, {
                'area_tag': 'ann.test',  # lowercase, exactly Jerry's report
                'from_name': 'NewNode',
                'subject': 'Test message',
                'body': 'body text',
                'msg_id': '<lowercase-tag-msg@test>',
            })

            self.assertEqual(rc, 1,
                             'a lowercase inbound tag must still match the '
                             'uppercase-stored area and import, not drop')
            self.assertIsNotNone(
                EchomailMessage.query.filter_by(
                    area_id=area.id, msg_id='<lowercase-tag-msg@test>').first())
            self.assertIsNone(
                BadAreaLog.query.filter_by(network_id=net.id).first(),
                'must not be recorded as an unknown/bad area')

    def test_poller_mixed_case_inbound_tag_also_matches(self):
        from anetbbs.models import db, EchomailNetwork, EchoArea, EchomailMessage
        from anetbbs.echomail.poller import _import_message

        with self.app.app_context():
            net = EchomailNetwork(name='PollerMixedCaseNet', network_type='binkp')
            db.session.add(net)
            db.session.commit()
            area = EchoArea(network_id=net.id, tag='LOCAL.CHAT', name='Local Chat',
                            is_subscribed=True, is_active=True)
            db.session.add(area)
            db.session.commit()

            rc = _import_message(net, {
                'area_tag': 'Local.Chat',
                'from_name': 'SomeNode',
                'subject': 'Mixed case tag',
                'body': 'body text',
                'msg_id': '<mixed-case-tag-msg@test>',
            })
            self.assertEqual(rc, 1)
            self.assertIsNotNone(
                EchomailMessage.query.filter_by(
                    area_id=area.id, msg_id='<mixed-case-tag-msg@test>').first())

    # ------------------------------------------------------------------
    # binkp_server.py's _import_pkt_payload() -- inbound-listener path
    # ------------------------------------------------------------------

    def _make_network(self, suffix):
        from anetbbs.models import db, EchomailNetwork
        net = EchomailNetwork(name=f'CaseTestNet{suffix}', network_type='binkp',
                              our_address='1:114/30', hub_address='1:114/0')
        db.session.add(net)
        db.session.commit()
        return net.id

    def test_binkp_listener_lowercase_inbound_tag_matches_uppercase_stored_area(self):
        from anetbbs.echomail.binkp import _build_ftn_packet
        from anetbbs.echomail import binkp_server
        from anetbbs.models import db, EchoArea, EchomailMessage, BadAreaLog

        with self.app.app_context():
            net_id = self._make_network('Lower')
            db.session.add(EchoArea(network_id=net_id, tag='ANN.TEST',
                                    name='Announce Test', is_active=True,
                                    is_subscribed=True))
            db.session.commit()

            pkt = _build_ftn_packet(
                [_FakeMsg(area=_FakeArea('ann.test'),
                         msg_id='1:114/30@fidonet u9001')],
                '1:114/30', '1:114/0')
            rc = binkp_server._import_pkt_payload(pkt, net_id, 'lower.pkt')

            self.assertEqual(rc, 1,
                             'a lowercase inbound tag must still match the '
                             'uppercase-stored area and import, not drop')
            self.assertIsNotNone(
                EchomailMessage.query.filter_by(
                    msg_id='1:114/30@fidonet u9001').first())
            self.assertIsNone(
                BadAreaLog.query.filter_by(network_id=net_id).first(),
                'must not be recorded as an unknown/bad area')


if __name__ == '__main__':
    unittest.main()
