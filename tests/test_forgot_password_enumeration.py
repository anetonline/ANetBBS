"""Regression tests for a real security gap found in a full auth-security
audit: web/auth.py's /forgot route redirected to the security-question
verify page ONLY when a real, active account with security questions on
file matched the submitted identifier, and fell through to a generic
"if that account exists" message otherwise. Since registration requires
3 security questions, that redirect target was a reliable username/email
enumeration oracle for almost every real account.

Fixed so every submission redirects to the SAME verify page -- a
nonexistent (or answerless) account gets a random DECOY question that
can never be answered correctly, indistinguishable by redirect target
or page content from the real flow.

Also covers: the verify page's new attempt cap (a wrong guess no longer
lets the same question be retried indefinitely in one session).
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class ForgotPasswordEnumerationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.forgot_password_enum_test.db')
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

            from anetbbs.models import User, UserSecurityAnswer, SECURITY_QUESTIONS
            with_sq = User(username='forgotwithsq', email='withsq@example.com',
                           is_active=True)
            with_sq.set_password('correcthorsebatterystaple')
            db.session.add(with_sq)
            db.session.commit()
            db.session.add(UserSecurityAnswer(
                user_id=with_sq.id, question=SECURITY_QUESTIONS[0],
                answer_hash=UserSecurityAnswer.hash_answer('fluffy')))
            db.session.commit()
            cls.with_sq_id = with_sq.id

            no_sq = User(username='forgotnosq', email='nosq@example.com',
                        is_active=True)
            no_sq.set_password('correcthorsebatterystaple')
            db.session.add(no_sq)
            db.session.commit()
            cls.no_sq_id = no_sq.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def setUp(self):
        # features/rate_limit.py's bucket store is a module-level dict
        # shared across the whole pytest process (and across test
        # files) -- both /forgot and /forgot/verify are now rate-
        # limited, and this class alone makes more requests to
        # /forgot/verify than its 10-per-5-min limit across all its
        # tests combined, since every test shares one client IP
        # (127.0.0.1). Clear between tests so this class is self-
        # contained regardless of what else shares the process.
        from anetbbs.features.rate_limit import _buckets
        _buckets.clear()

    def _submit_forgot(self, client, identifier):
        return client.post('/auth/forgot', data={'identifier': identifier},
                           follow_redirects=False)

    def test_existing_account_with_security_questions_redirects_to_verify(self):
        client = self.app.test_client()
        resp = self._submit_forgot(client, 'forgotwithsq')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/auth/forgot/verify', resp.headers['Location'])

    def test_nonexistent_account_also_redirects_to_verify(self):
        """SECURITY: this is the core of the fix -- a nonexistent
        account must produce the exact same redirect as a real one."""
        client = self.app.test_client()
        resp = self._submit_forgot(client, 'this_account_does_not_exist_at_all')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/auth/forgot/verify', resp.headers['Location'])

    def test_existing_account_without_security_questions_also_redirects_to_verify(self):
        """This account's REAL recovery path is the email/journal token
        (still issued in the background, see the next test), but the
        visible page must not distinguish it from a nonexistent account."""
        client = self.app.test_client()
        resp = self._submit_forgot(client, 'forgotnosq')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/auth/forgot/verify', resp.headers['Location'])

    def test_answerless_account_still_gets_a_real_reset_token_issued(self):
        from anetbbs.models import PasswordResetToken
        client = self.app.test_client()
        self._submit_forgot(client, 'forgotnosq')
        with self.app.app_context():
            tok = PasswordResetToken.query.filter_by(user_id=self.no_sq_id).first()
            self.assertIsNotNone(tok,
                                 'an answerless account must still get a real, '
                                 'usable reset token issued in the background')

    def test_nonexistent_account_gets_no_token_issued(self):
        from anetbbs.models import PasswordResetToken
        with self.app.app_context():
            before = PasswordResetToken.query.count()
        client = self.app.test_client()
        self._submit_forgot(client, 'this_account_does_not_exist_at_all_2')
        with self.app.app_context():
            after = PasswordResetToken.query.count()
        self.assertEqual(before, after)

    def test_verify_page_renders_for_both_real_and_decoy_sessions(self):
        """The verify page itself must not error out or behave visibly
        differently for a decoy vs. a real session."""
        client = self.app.test_client()
        self._submit_forgot(client, 'forgotwithsq')
        real_resp = client.get('/auth/forgot/verify')
        self.assertEqual(real_resp.status_code, 200)

        client2 = self.app.test_client()
        self._submit_forgot(client2, 'nonexistent_user_xyz')
        decoy_resp = client2.get('/auth/forgot/verify')
        self.assertEqual(decoy_resp.status_code, 200)

    def test_decoy_question_can_never_be_answered_correctly(self):
        client = self.app.test_client()
        self._submit_forgot(client, 'nonexistent_user_xyz')
        resp = client.post('/auth/forgot/verify', data={'answer': 'fluffy'},
                           follow_redirects=False)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Incorrect answer', resp.data)

    def test_correct_answer_on_real_account_issues_reset_token(self):
        from anetbbs.models import PasswordResetToken
        client = self.app.test_client()
        self._submit_forgot(client, 'forgotwithsq')
        resp = client.post('/auth/forgot/verify', data={'answer': 'fluffy'},
                           follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/auth/reset/', resp.headers['Location'])
        with self.app.app_context():
            self.assertIsNotNone(
                PasswordResetToken.query.filter_by(user_id=self.with_sq_id).first())

    def test_wrong_answer_on_real_account_does_not_issue_token(self):
        client = self.app.test_client()
        self._submit_forgot(client, 'forgotwithsq')
        resp = client.post('/auth/forgot/verify', data={'answer': 'wronganswer'},
                           follow_redirects=False)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Incorrect answer', resp.data)

    def test_attempt_cap_kicks_in_after_five_wrong_guesses(self):
        client = self.app.test_client()
        self._submit_forgot(client, 'forgotwithsq')
        for _ in range(5):
            resp = client.post('/auth/forgot/verify', data={'answer': 'nope'},
                               follow_redirects=False)
            self.assertEqual(resp.status_code, 200)
        # The 6th attempt must be rejected and bounce back to /forgot.
        resp = client.post('/auth/forgot/verify', data={'answer': 'nope'},
                           follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/auth/forgot', resp.headers['Location'])

    def test_direct_access_to_verify_with_no_session_redirects_to_forgot(self):
        client = self.app.test_client()
        resp = client.get('/auth/forgot/verify', follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/auth/forgot', resp.headers['Location'])


if __name__ == '__main__':
    unittest.main()
