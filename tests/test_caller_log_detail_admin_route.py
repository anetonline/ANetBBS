"""Regression test for the new admin.caller_log_detail route (the
per-session drill-down Jerry asked for). Confirms it's admin-gated and
renders the correlated UserActivity events for the CallerLog row
clicked from, in chronological order.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class CallerLogDetailAdminRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.caller_log_detail_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User, CallerLog, UserActivity
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()
            admin = User(username='clddadmin', email='cldd@example.com',
                        is_admin=True, is_active=True)
            admin.set_password('adminpassword123')
            plain = User(username='clddplain', email='clddp@example.com',
                        is_active=True)
            plain.set_password('plainpassword123')
            db.session.add_all([admin, plain])
            db.session.commit()
            cls.admin_id = admin.id
            cls.plain_id = plain.id

            cl = CallerLog(user_id=plain.id, username='clddplain',
                           service='telnet', ip_address='9.9.9.9')
            db.session.add(cl)
            db.session.commit()
            cls.cl_id = cl.id

            db.session.add(UserActivity(
                user_id=plain.id, activity_type='login',
                caller_log_id=cl.id, service='telnet'))
            db.session.add(UserActivity(
                user_id=plain.id, activity_type='menu:games',
                caller_log_id=cl.id, service='telnet'))
            db.session.add(UserActivity(
                user_id=plain.id, activity_type='door_played',
                details='Lord', caller_log_id=cl.id, service='telnet'))
            # Unrelated session -- must NOT show up.
            db.session.add(UserActivity(
                user_id=plain.id, activity_type='login',
                caller_log_id=None, service='web'))
            db.session.commit()

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

    def test_non_admin_is_denied(self):
        client = self._client_as(self.plain_id)
        resp = client.get(f'/admin/caller-log/{self.cl_id}')
        self.assertIn(resp.status_code, (302, 403))

    def test_admin_sees_only_this_sessions_events_in_order(self):
        client = self._client_as(self.admin_id)
        resp = client.get(f'/admin/caller-log/{self.cl_id}')
        self.assertEqual(resp.status_code, 200)
        body = resp.data.decode()
        idx_login = body.find('login')
        idx_games = body.find('menu:games')
        idx_door = body.find('door_played')
        self.assertNotEqual(idx_login, -1)
        self.assertNotEqual(idx_games, -1)
        self.assertNotEqual(idx_door, -1)
        self.assertLess(idx_login, idx_games,
                        'events must render in chronological order')
        self.assertLess(idx_games, idx_door)
        self.assertIn('Lord', body)
        # The unrelated web-login event (caller_log_id=None) must not
        # leak into this session's drill-down.
        self.assertEqual(body.count('badge bg-secondary">login'), 1)


if __name__ == '__main__':
    unittest.main()
