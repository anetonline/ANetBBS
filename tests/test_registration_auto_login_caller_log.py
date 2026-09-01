"""Regression test for a real live bug reported by Jerry (2026-09-01):
a newly self-registered user ("Vanny") was visibly active online (seen
chatting in MRC) but Admin -> Users showed "Last Login: Never, Logins:
0", and the account appeared nowhere in the caller log.

Root cause: register()'s auto-login path (used whenever NUV/email
verification aren't gating the account) calls login_user() directly on
the brand-new account, completely bypassing login()'s own
user.update_login() call and CallerLog write -- the ONLY path into an
authenticated session that skipped both. A user's first-ever session,
immediately after registering, left zero trace in the exact two places
a sysop checks who's using the system and when.

Same bug shape, same root cause location, as the PresenceEvent gap
already fixed once before at this same call site (see
test_presence_alerts.py's test_web_registration_records_a_presence_event,
2026-08-27) -- register() calling login_user() directly instead of
going through login()'s own bookkeeping keeps recurring here because
every piece of session-start bookkeeping has to be duplicated by hand
instead of the two routes sharing one helper.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class RegistrationAutoLoginCallerLogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.registration_caller_log_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def test_registration_auto_login_writes_a_caller_log_row(self):
        from anetbbs.models import SECURITY_QUESTIONS
        client = self.app.test_client()
        resp = client.post('/auth/register', data={
            'username': 'vanny', 'email': 'vanny@example.com',
            'password': 'password12345', 'password2': 'password12345',
            'question_1': SECURITY_QUESTIONS[0], 'answer_1': 'answer one',
            'question_2': SECURITY_QUESTIONS[1], 'answer_2': 'answer two',
            'question_3': SECURITY_QUESTIONS[2], 'answer_3': 'answer three',
        }, follow_redirects=False)
        self.assertIn(resp.status_code, (302, 303), resp.get_data(as_text=True))

        with self.app.app_context():
            from anetbbs.models import User, CallerLog
            vanny = User.query.filter_by(username='vanny').first()
            self.assertIsNotNone(vanny, 'registration itself did not succeed')

            cl = CallerLog.query.filter_by(user_id=vanny.id).first()
            self.assertIsNotNone(
                cl, 'a brand-new registered-and-auto-logged-in user must '
                    'still show up in the caller log for that very first session')
            self.assertEqual(cl.username, 'vanny')
            self.assertEqual(cl.service, 'web')

    def test_registration_auto_login_updates_login_count_and_last_login(self):
        from anetbbs.models import SECURITY_QUESTIONS
        client = self.app.test_client()
        client.post('/auth/register', data={
            'username': 'wendy', 'email': 'wendy@example.com',
            'password': 'password12345', 'password2': 'password12345',
            'question_1': SECURITY_QUESTIONS[0], 'answer_1': 'answer one',
            'question_2': SECURITY_QUESTIONS[1], 'answer_2': 'answer two',
            'question_3': SECURITY_QUESTIONS[2], 'answer_3': 'answer three',
        }, follow_redirects=False)

        with self.app.app_context():
            from anetbbs.models import User
            wendy = User.query.filter_by(username='wendy').first()
            self.assertIsNotNone(wendy)
            self.assertEqual(
                wendy.login_count, 1,
                'login_count must reflect this first session, not stay at 0 '
                '("Never"/"0 logins" in Admin -> Users, despite the account '
                'being actively online)')
            self.assertIsNotNone(wendy.last_login)


if __name__ == '__main__':
    unittest.main()
