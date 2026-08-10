"""Tests for the new per-node FILE area subscription UI (Jerry: "I dont
see where you can see what areas a node is subbed to for files, just
message areas... there is not even a way if I were to want to remove
them from an area(s)"). Mirrors test_binkp_subscribe_all.py's shape for
the existing message-area equivalent, but for
hub_admin.binkp_file_subscribe()/binkp_file_subscribe_all(), which
write FileEchoSubscription rows keyed by the node's ftn_address rather
than a dedicated FK table (see hub_admin.py's binkp_node_detail()
comment for why no FileAreaNode table was needed).
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class BinkpFileAreaSubscribeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.binkp_file_area_subscribe_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        cls.app.config['REGISTRY_MODE_ENABLED'] = True
        with cls.app.app_context():
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _client(self):
        from anetbbs.models import User, db
        with self.app.app_context():
            admin = User.query.filter_by(username='binkpfileareatest').first()
            if not admin:
                admin = User(username='binkpfileareatest', is_admin=True,
                            access_level=255,
                            email='binkpfileareatest@example.com')
                admin.set_password('x')
                db.session.add(admin)
                db.session.commit()
            admin_id = admin.id
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(admin_id)
            sess['_fresh'] = True
        return client

    def _make_node(self, addr):
        from anetbbs.models import db, BinkPNode
        with self.app.app_context():
            node = BinkPNode(ftn_address=addr, name='Test', password='x', is_active=True)
            db.session.add(node)
            db.session.commit()
            return node.id

    def test_node_detail_page_shows_file_area_card(self):
        from anetbbs.models import db, FileArea
        with self.app.app_context():
            db.session.add(FileArea(tag='FILES.ONE', name='Files One', is_active=True))
            db.session.commit()
        node_id = self._make_node('1:2/10')

        client = self._client()
        resp = client.get(f'/admin/echomail/hub/binkp/{node_id}')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'File Area Subscriptions', resp.data)
        self.assertIn(b'FILES.ONE', resp.data)

    def test_subscribe_and_unsubscribe_a_single_file_area(self):
        from anetbbs.models import db, FileArea, FileEchoSubscription
        with self.app.app_context():
            area = FileArea(tag='FILES.SUB', name='Sub Test', is_active=True)
            db.session.add(area)
            db.session.commit()
            area_id = area.id
        node_id = self._make_node('1:2/11')

        client = self._client()
        resp = client.post(f'/admin/echomail/hub/binkp/{node_id}/file-subscribe',
                           data={'area_id': area_id, 'action': 'subscribe'},
                           follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            sub = FileEchoSubscription.query.filter_by(
                file_area_id=area_id, peer_address='1:2/11').first()
            self.assertIsNotNone(sub, 'subscribing must create a FileEchoSubscription row')

        resp = client.post(f'/admin/echomail/hub/binkp/{node_id}/file-subscribe',
                           data={'area_id': area_id, 'action': 'unsubscribe'},
                           follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            sub = FileEchoSubscription.query.filter_by(
                file_area_id=area_id, peer_address='1:2/11').first()
            self.assertIsNone(sub, 'unsubscribing must remove the row -- this is '
                             'the "remove a node from a file area" action that '
                             "didn't exist at all before")

    def test_subscribe_all_covers_every_active_file_area_and_skips_existing(self):
        from anetbbs.models import db, FileArea, FileEchoSubscription
        with self.app.app_context():
            a1 = FileArea(tag='SUBALL.ONE', name='One', is_active=True)
            a2 = FileArea(tag='SUBALL.TWO', name='Two', is_active=True)
            a3_inactive = FileArea(tag='SUBALL.THREE', name='Three', is_active=False)
            db.session.add_all([a1, a2, a3_inactive])
            db.session.flush()
            db.session.add(FileEchoSubscription(file_area_id=a1.id, peer_address='1:2/12'))
            db.session.commit()
            a1_id, a2_id, a3_id = a1.id, a2.id, a3_inactive.id
        node_id = self._make_node('1:2/12')

        client = self._client()
        resp = client.post(f'/admin/echomail/hub/binkp/{node_id}/file-subscribe-all',
                           follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            subs = {s.file_area_id for s in
                   FileEchoSubscription.query.filter_by(peer_address='1:2/12').all()}
            self.assertIn(a1_id, subs)
            self.assertIn(a2_id, subs)
            self.assertNotIn(a3_id, subs, 'inactive file areas must not be swept in')
            self.assertEqual(
                len([s for s in FileEchoSubscription.query.filter_by(
                    file_area_id=a1_id, peer_address='1:2/12').all()]), 1,
                'subscribe-all must not duplicate an existing subscription')


if __name__ == '__main__':
    unittest.main()
