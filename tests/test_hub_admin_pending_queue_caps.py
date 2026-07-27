"""Regression test: the pending-request lists on the QWK Node Requests
and Network Join Requests admin pages had no cap at all, unlike their
"recently reviewed" counterparts (capped at 50) -- both are fed by
public, unauthenticated forms, so a spam/scan burst could grow either
list unbounded and slow the page down. Capped at 500 (generous vs. any
realistic legitimate backlog), with a warning flash if the cap is ever
actually hit so a real backlog beyond it isn't silently hidden from the
sysop.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class PendingQueueCapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.pending_queue_cap_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
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

    def _admin_client(self, username):
        from anetbbs.models import db, User
        with self.app.app_context():
            u = User.query.filter_by(username=username).first()
            if not u:
                u = User(username=username, is_admin=True,
                        email=f'{username}@example.com')
                u.set_password('x')
                db.session.add(u)
                db.session.commit()
            uid = u.id
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(uid)
            sess['_fresh'] = True
        return client

    def test_qwk_node_requests_page_loads_under_normal_volume(self):
        from anetbbs.models import db, QWKNodeRequest
        with self.app.app_context():
            for i in range(5):
                db.session.add(QWKNodeRequest(
                    bbs_name=f'NormalQwkBBS{i}', bbs_address=f'test{i}.example.com',
                    sysop_name='Sysop', email=f'q{i}@example.com',
                    packet_id=f'QN{i:03d}', status='pending'))
            db.session.commit()

        client = self._admin_client('qwk_normal_admin')
        resp = client.get('/admin/echomail/hub/qwk/requests', follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertNotIn('showing the oldest 500', body)

    def test_join_requests_page_loads_under_normal_volume(self):
        from anetbbs.models import db, NetworkJoinRequest
        with self.app.app_context():
            for i in range(5):
                db.session.add(NetworkJoinRequest(
                    bbs_name=f'NormalJoinBBS{i}', email=f'j{i}@example.com',
                    rules_ack=True, status='pending'))
            db.session.commit()

        client = self._admin_client('join_normal_admin')
        resp = client.get('/admin/echomail/hub/join/requests', follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertNotIn('showing the oldest 500', body)

    def test_join_requests_warns_and_caps_when_flooded(self):
        from anetbbs.models import db, NetworkJoinRequest
        with self.app.app_context():
            db.session.bulk_save_objects([
                NetworkJoinRequest(bbs_name=f'FloodBBS{i}',
                                   email=f'flood{i}@example.com',
                                   rules_ack=True, status='pending')
                for i in range(501)
            ])
            db.session.commit()

        client = self._admin_client('join_flood_admin')
        resp = client.get('/admin/echomail/hub/join/requests', follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn('showing the oldest 500', body,
                      'a real flood past the cap must warn the sysop, not '
                      'silently hide the overflow')


if __name__ == '__main__':
    unittest.main()
