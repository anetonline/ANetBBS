"""Regression test: web/auth.py's register() route had no try/except
around the flush that persists a new User row -- validate_username()/
validate_email() pre-check for a collision, but on a genuine race (two
concurrent registrations of the same username/email, both passing that
pre-check before either commits) the flush raised an unhandled
IntegrityError, surfacing as a raw 500 instead of a friendly "already
taken" message. Same bug/fix as core/user_manager.py's create_user()
(the terminal registration path).
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class RegisterRaceConditionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.register_race_test.db')
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

    def _register_payload(self, username, email):
        from anetbbs.models import SECURITY_QUESTIONS
        return {
            'username': username, 'email': email,
            'password': 'correcthorsebatterystaple',
            'password2': 'correcthorsebatterystaple',
            'question_1': SECURITY_QUESTIONS[0], 'answer_1': 'answer1',
            'question_2': SECURITY_QUESTIONS[1], 'answer_2': 'answer2',
            'question_3': SECURITY_QUESTIONS[2], 'answer_3': 'answer3',
        }

    def test_username_race_shows_friendly_error_not_a_500(self):
        from sqlalchemy.exc import IntegrityError
        from anetbbs.models import db as real_db

        real_flush = real_db.session.flush
        calls = {'n': 0}

        def _flaky_flush(*a, **kw):
            calls['n'] += 1
            if calls['n'] == 1:
                raise IntegrityError(
                    'INSERT INTO users ...', {},
                    Exception('UNIQUE constraint failed: users.username'))
            return real_flush(*a, **kw)

        client = self.app.test_client()
        with patch.object(real_db.session, 'flush', side_effect=_flaky_flush):
            resp = client.post('/auth/register',
                               data=self._register_payload('raceweb', 'raceweb@example.com'),
                               follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'username is already taken', resp.data.lower())

    def test_email_race_shows_friendly_error_not_a_500(self):
        from sqlalchemy.exc import IntegrityError
        from anetbbs.models import db as real_db

        real_flush = real_db.session.flush
        calls = {'n': 0}

        def _flaky_flush(*a, **kw):
            calls['n'] += 1
            if calls['n'] == 1:
                raise IntegrityError(
                    'INSERT INTO users ...', {},
                    Exception('UNIQUE constraint failed: users.email'))
            return real_flush(*a, **kw)

        client = self.app.test_client()
        with patch.object(real_db.session, 'flush', side_effect=_flaky_flush):
            resp = client.post('/auth/register',
                               data=self._register_payload('raceweb2', 'raceweb2@example.com'),
                               follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'email address is already registered', resp.data.lower())

    def test_normal_registration_still_works(self):
        client = self.app.test_client()
        resp = client.post('/auth/register',
                           data=self._register_payload('raceweb3', 'raceweb3@example.com'),
                           follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        with self.app.app_context():
            from anetbbs.models import User
            self.assertIsNotNone(User.query.filter_by(username='raceweb3').first())


if __name__ == '__main__':
    unittest.main()
