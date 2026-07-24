"""Regression test for a real bug found in a full access-control audit:
User.is_locked was never a real database column (models.py had is_locked
on Post and WikiPage, never on User) -- admin.py's lock_user()/edit_user()
set a plain, never-persisted Python attribute, so the "Lock User" admin
feature silently did nothing on both the web and terminal. Fixed by
adding the real column (models.py) plus a startup migration
(web_app.py's _ensure_column) for existing installs.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class AdminLockUserPersistsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.admin_lock_user_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        with cls.app.app_context():
            db.create_all()

            admin = User(username='lockadmintest', email='lat@example.com',
                        is_admin=True, is_active=True)
            admin.set_password('adminpassword123')
            target = User(username='locktargettest', email='ltt@example.com',
                         is_active=True)
            target.set_password('targetpassword123')
            db.session.add_all([admin, target])
            db.session.commit()
            cls.admin_id = admin.id
            cls.target_id = target.id

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

    def test_lock_toggle_persists_across_a_fresh_query(self):
        from anetbbs.models import db, User
        client = self._client_as(self.admin_id)
        resp = client.post(f'/admin/users/{self.target_id}/lock', follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            refreshed = db.session.get(User, self.target_id)
            self.assertTrue(refreshed.is_locked,
                           'is_locked must survive a fresh DB query, not just '
                           'the in-memory object from the request that set it')

    def test_locked_user_cannot_log_in_via_web(self):
        from anetbbs.models import db, User
        with self.app.app_context():
            u = db.session.get(User, self.target_id)
            u.is_locked = True
            db.session.commit()

        anon = self.app.test_client()
        resp = anon.post('/auth/login', data={
            'username': 'locktargettest', 'password': 'targetpassword123',
        }, follow_redirects=True)
        self.assertIn(b'account is locked', resp.data.lower())
        with anon.session_transaction() as sess:
            self.assertNotIn('_user_id', sess)


if __name__ == '__main__':
    unittest.main()
