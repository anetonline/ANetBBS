"""Regression test for a real access-control bug found in a full audit:
anetbbs/web/auth.py's register() route, with NUV_ENABLED on and email
verification off, used to fall straight through to login_user() after
creating a brand-new, deliberately-unverified account -- completely
bypassing the sysop-approval queue NUV exists to enforce. The queue
was only ever checked on a LATER, separate /login attempt, never at
the moment of registration itself.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class NuvRegistrationNoAutoLoginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.nuv_registration_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        with cls.app.app_context():
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _register(self, client, username, email):
        from anetbbs.models import SECURITY_QUESTIONS
        return client.post('/auth/register', data={
            'username': username,
            'email': email,
            'password': 'correcthorsebatterystaple',
            'password2': 'correcthorsebatterystaple',
            'question_1': SECURITY_QUESTIONS[0], 'answer_1': 'answer1',
            'question_2': SECURITY_QUESTIONS[1], 'answer_2': 'answer2',
            'question_3': SECURITY_QUESTIONS[2], 'answer_3': 'answer3',
        }, follow_redirects=False)

    def test_nuv_enabled_registration_does_not_auto_login(self):
        self.app.config['NUV_ENABLED'] = True
        client = self.app.test_client()
        resp = self._register(client, 'nuvregtest1', 'nuvregtest1@example.com')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Account created', resp.data)
        self.assertIn(b'sysop', resp.data.lower())

        # If login_user() had run, the session would carry a real,
        # authenticated user id for this account -- fetch a
        # login-required page and confirm we're NOT recognized as
        # logged in as this brand-new user.
        with client.session_transaction() as sess:
            self.assertNotIn('_user_id', sess)

    def test_nuv_enabled_registered_user_cannot_yet_log_in(self):
        self.app.config['NUV_ENABLED'] = True
        client = self.app.test_client()
        self._register(client, 'nuvregtest2', 'nuvregtest2@example.com')

        login_client = self.app.test_client()
        resp = login_client.post('/auth/login', data={
            'username': 'nuvregtest2', 'password': 'correcthorsebatterystaple',
        }, follow_redirects=True)
        self.assertIn(b'awaiting sysop approval', resp.data.lower())

    def test_nuv_disabled_registration_still_auto_logs_in(self):
        """Confirm the fix didn't regress the normal (NUV off) path."""
        self.app.config['NUV_ENABLED'] = False
        client = self.app.test_client()
        resp = self._register(client, 'nuvregtest3', 'nuvregtest3@example.com')
        self.assertEqual(resp.status_code, 302)
        with client.session_transaction() as sess:
            self.assertIn('_user_id', sess)


if __name__ == '__main__':
    unittest.main()
