"""Regression test for a real gap found live: during the CP437/ANSI art
rendering incident (see project_flat_ansi_art_crlf_collapse_v237
memory), the sysop had no way to remove a bad message from the local
view or its still-pending outbound BinkPHoldQueue entries short of
hand-written SQL against the live database -- exactly the kind of
one-off, error-prone, unrepeatable operation a real UI/route should
handle instead.

anetbbs/web/echomail.py's new delete_message() route: admin-only (unlike
boards.delete_post, a regular user has no standing to remove shared FTN
network content even if they authored it), hard-deletes the message, and
cleans up BinkPHoldQueue (the not-yet-sent outbound queue -- the exact
step previously requiring hand SQL), EchomailReadStatus, and
QWKNodeLastSent's dangling high-water-mark pointer.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class EchomailDeleteMessageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.echomail_delete_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import (db, User, EchomailNetwork, EchoArea,
                                    BinkPNode, QWKNode)
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        with cls.app.app_context():
            db.create_all()

            admin = User(username='delmsgadmin', email='dma@example.com',
                        password_hash='x', access_level=100, is_admin=True)
            regular = User(username='delmsguser', email='dmu@example.com',
                          password_hash='x', access_level=100, is_admin=False)
            db.session.add_all([admin, regular])
            db.session.commit()
            cls.admin_id = admin.id
            cls.regular_id = regular.id

            net = EchomailNetwork(name='DelMsgNet', network_type='binkp',
                                  is_active=True, our_address='9:9/1')
            db.session.add(net)
            db.session.flush()
            cls.net_id = net.id

            node = BinkPNode(name='DelMsgPeer', ftn_address='9:9/2',
                             password='', is_active=True, network_id=net.id)
            db.session.add(node)
            db.session.flush()
            cls.node_id = node.id

            qwk_node = QWKNode(packet_id='DELMSG', name='DelMsgQwk',
                               password='x', is_active=True)
            db.session.add(qwk_node)

            area = EchoArea(network_id=net.id, tag='DELMSG.TEST',
                            name='Delete Test', is_active=True,
                            is_subscribed=True, is_sysop_only=False,
                            min_access_level=10)
            db.session.add(area)
            db.session.commit()
            cls.area_id = area.id
            cls.qwk_node_id = qwk_node.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _client_as(self, user_id):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(user_id)
            sess['_fresh'] = True
        return client

    def _make_message_with_references(self, subject):
        from anetbbs.models import (db, EchomailMessage, BinkPHoldQueue,
                                    EchomailReadStatus, QWKNodeLastSent)
        with self.app.app_context():
            msg = EchomailMessage(
                area_id=self.area_id, network_id=self.net_id,
                from_name='Tester', to_name='All', subject=subject,
                body='test body', direction='outbound')
            db.session.add(msg)
            db.session.flush()
            msg_id = msg.id

            db.session.add(BinkPHoldQueue(node_id=self.node_id,
                                          message_id=msg_id, status='pending'))
            db.session.add(EchomailReadStatus(user_id=self.admin_id,
                                              message_id=msg_id))
            # QWKNodeLastSent is a subscription row (unique per node+area),
            # not per-message -- reuse the one existing row and just
            # repoint its high-water-mark pointer, matching how a real
            # QWK delivery run would update it.
            qns = QWKNodeLastSent.query.filter_by(
                node_id=self.qwk_node_id, echo_area_id=self.area_id).first()
            if qns is None:
                qns = QWKNodeLastSent(node_id=self.qwk_node_id,
                                      echo_area_id=self.area_id)
                db.session.add(qns)
            qns.last_message_id = msg_id
            db.session.commit()
            return msg_id

    def test_regular_user_cannot_delete(self):
        msg_id = self._make_message_with_references('Regular user blocked')
        client = self._client_as(self.regular_id)
        resp = client.post(f'/echomail/{self.area_id}/{msg_id}/delete')
        self.assertEqual(resp.status_code, 403)

        from anetbbs.models import EchomailMessage
        with self.app.app_context():
            self.assertIsNotNone(EchomailMessage.query.get(msg_id),
                                 'message must survive a non-admin delete attempt')

    def test_admin_delete_removes_message_and_hold_queue_and_read_status(self):
        msg_id = self._make_message_with_references('Admin delete works')
        client = self._client_as(self.admin_id)
        resp = client.post(f'/echomail/{self.area_id}/{msg_id}/delete',
                           follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        from anetbbs.models import (EchomailMessage, BinkPHoldQueue,
                                    EchomailReadStatus, QWKNodeLastSent)
        with self.app.app_context():
            self.assertIsNone(EchomailMessage.query.get(msg_id))
            self.assertEqual(
                BinkPHoldQueue.query.filter_by(message_id=msg_id).count(), 0,
                'the not-yet-sent outbound queue entry must be removed -- '
                'this is the exact cleanup step that previously required '
                'hand-written SQL')
            self.assertEqual(
                EchomailReadStatus.query.filter_by(message_id=msg_id).count(), 0)
            qns = QWKNodeLastSent.query.filter_by(
                node_id=self.qwk_node_id, echo_area_id=self.area_id).first()
            self.assertIsNotNone(qns, 'the subscription row itself should '
                                 'survive -- only its dangling pointer is cleared')
            self.assertIsNone(qns.last_message_id)

    def test_delete_nonexistent_message_404s(self):
        client = self._client_as(self.admin_id)
        resp = client.post(f'/echomail/{self.area_id}/999999/delete')
        self.assertEqual(resp.status_code, 404)


if __name__ == '__main__':
    unittest.main()
