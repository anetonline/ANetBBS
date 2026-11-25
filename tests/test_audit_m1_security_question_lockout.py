"""M-1: the security-question attempt cap must hold on the server.

Before the fix the only cap was `sq_attempts` in the client's signed session
cookie: replaying the cookie captured right after /forgot, or simply
submitting /forgot again, reset it, so with rotating IPs an attacker had
unlimited guesses and eventually got a password-reset link.
"""
import os
import tempfile
import unittest
from datetime import datetime, timedelta

import anetbbs.config as cfg_mod


class SecurityQuestionLockoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp = tempfile.mkdtemp(prefix='m1-')
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp}/t.db'
        os.environ['FLASK_ENV'] = 'testing'
        from anetbbs.web_app import create_app
        from anetbbs.models import db, User, UserSecurityAnswer
        cls.app = create_app('testing')
        cls.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False,
                              PUBLIC_BASE_URL='http://localhost')
        with cls.app.app_context():
            db.create_all()
            u = User(username='victim', email='v@example.test', is_verified=True,
                     is_active=True)
            u.set_password('Sup3rSecret!pw')
            db.session.add(u)
            db.session.commit()
            # A single question keeps the correct answer deterministic.
            db.session.add(UserSecurityAnswer(
                user_id=u.id, question='Q-pet?',
                answer_hash=UserSecurityAnswer.hash_answer('rex')))
            db.session.commit()
            cls.uid = u.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_uri

    def setUp(self):
        from anetbbs.models import db, User
        with self.app.app_context():
            u = db.session.get(User, self.uid)
            u.sq_failed_count = 0
            u.sq_locked_until = None
            db.session.commit()

    def _fresh_forgot(self):
        from anetbbs.features import rate_limit as rl
        rl._buckets.clear()
        c = self.app.test_client()
        c.post('/auth/forgot', data={'identifier': 'victim'})
        return c

    def _guess(self, cookie, answer):
        from anetbbs.features import rate_limit as rl
        rl._buckets.clear()            # stands in for rotating client IPs
        c = self.app.test_client()
        c.set_cookie('session', cookie)
        return c.post('/auth/forgot/verify', data={'answer': answer})

    def test_replayed_cookie_cannot_exceed_cap(self):
        c = self._fresh_forgot()
        cookie = c.get_cookie('session').value
        for i in range(5):
            self._guess(cookie, f'wrong{i}')
        r = self._guess(cookie, 'rex')     # correct answer, replayed cookie
        self.assertNotIn('/auth/reset/', r.headers.get('Location', ''))

    def test_new_forgot_session_also_locked(self):
        for i in range(5):
            c = self._fresh_forgot()
            self._guess(c.get_cookie('session').value, f'wrong{i}')
        c = self._fresh_forgot()
        r = self._guess(c.get_cookie('session').value, 'rex')
        self.assertNotIn('/auth/reset/', r.headers.get('Location', ''))
        from anetbbs.models import db, User
        with self.app.app_context():
            self.assertIsNotNone(db.session.get(User, self.uid).sq_locked_until)

    def test_locked_response_looks_like_a_wrong_answer(self):
        from anetbbs.models import db, User
        with self.app.app_context():
            u = db.session.get(User, self.uid)
            u.sq_locked_until = datetime.utcnow() + timedelta(minutes=30)
            db.session.commit()
        c = self._fresh_forgot()
        r = self._guess(c.get_cookie('session').value, 'rex')
        self.assertEqual(r.status_code, 200)
        self.assertIn('Incorrect answer', r.get_data(as_text=True))

    def test_lock_expires_and_success_resets_counter(self):
        from anetbbs.models import db, User
        with self.app.app_context():
            u = db.session.get(User, self.uid)
            u.sq_failed_count = 3
            u.sq_locked_until = datetime.utcnow() - timedelta(seconds=1)
            db.session.commit()
        c = self._fresh_forgot()
        r = self._guess(c.get_cookie('session').value, 'rex')
        self.assertIn('/auth/reset/', r.headers.get('Location', ''))
        with self.app.app_context():
            u = db.session.get(User, self.uid)
            self.assertEqual(u.sq_failed_count, 0)
            self.assertIsNone(u.sq_locked_until)

    def test_lockout_notifies_account_holder(self):
        from anetbbs.models import Notification
        for i in range(5):
            c = self._fresh_forgot()
            self._guess(c.get_cookie('session').value, f'wrong{i}')
        with self.app.app_context():
            self.assertTrue(Notification.query.filter_by(user_id=self.uid).count() >= 1)


if __name__ == '__main__':
    unittest.main()
