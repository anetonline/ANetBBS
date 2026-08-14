"""Regression test for a residual timing oracle in web/auth.py's
/forgot route, found in a security/performance audit as a follow-up to
test_forgot_password_enumeration.py's own fix.

That earlier fix made every /forgot submission redirect to the SAME
verify page regardless of whether the account exists / has security
questions -- but the "account exists, no security questions on file"
branch still did a synchronous SMTP send (real network I/O to a
possibly slow or greylisting mail server) before responding, while the
other two cases (no such account; account has security questions) did
none of that. An attacker timing responses could still distinguish
"real account with no security questions" from the other two cases
purely by response latency.

Fixed by backgrounding the SMTP send in a daemon thread -- the HTTP
response no longer waits on it. This test proves the request returns
before a deliberately slow mocked send_password_reset_email() call
completes, and that the background thread eventually calls it with the
correct (re-queried, not stale) user.
"""
import os
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class ForgotPasswordTimingOracleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.forgot_password_timing_test.db')
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
            from anetbbs.models import User
            u = User(username='timingoracle_user',
                    email='timingoracle@example.com', is_active=True)
            u.set_password('correcthorsebatterystaple')
            db.session.add(u)
            db.session.commit()
            cls.user_id = u.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def test_smtp_send_is_backgrounded_not_on_the_request_critical_path(self):
        release_event = threading.Event()
        call_record = {}

        def _slow_send(user, reset_url):
            # Blocks until the test explicitly releases it -- simulates
            # a slow/greylisting mail server. If this were still called
            # synchronously, the request itself would hang here.
            release_event.wait(timeout=5)
            call_record['username'] = user.username
            call_record['email'] = user.email
            call_record['reset_url'] = reset_url

        with mock.patch('anetbbs.mailer.smtp_enabled', return_value=True), \
             mock.patch('anetbbs.mailer.send_password_reset_email',
                        side_effect=_slow_send):
            client = self.app.test_client()
            started = time.monotonic()
            resp = client.post('/auth/forgot',
                               data={'identifier': 'timingoracle_user'},
                               follow_redirects=False)
            elapsed = time.monotonic() - started

        self.assertEqual(resp.status_code, 302)
        self.assertLess(elapsed, 2.0,
                        'the request must return long before the 5s-capped '
                        'slow mail send completes -- it must not block on '
                        'SMTP I/O')
        self.assertNotIn('username', call_record,
                         'the mocked send must not have been called yet -- '
                         'it is gated behind release_event.wait()')

        # Now let the backgrounded send actually run, and confirm it
        # eventually fires with the CORRECT (re-queried, not stale)
        # user -- not a wrong/None value from a cross-thread ORM object.
        release_event.set()
        deadline = time.monotonic() + 5
        while 'username' not in call_record and time.monotonic() < deadline:
            time.sleep(0.05)

        self.assertEqual(call_record.get('username'), 'timingoracle_user')
        self.assertEqual(call_record.get('email'), 'timingoracle@example.com')
        self.assertIn('/auth/reset/', call_record.get('reset_url', ''))

    def test_reset_token_is_still_issued_synchronously(self):
        """Baseline / guard against an over-broad fix: the token itself
        (unlike the email) must still be issued by the time the
        request returns -- other code (and
        test_forgot_password_enumeration.py's own existing test) relies
        on this."""
        from anetbbs.models import PasswordResetToken, db
        with mock.patch('anetbbs.mailer.smtp_enabled', return_value=False):
            client = self.app.test_client()
            client.post('/auth/forgot',
                        data={'identifier': 'timingoracle_user'},
                        follow_redirects=False)
        with self.app.app_context():
            tok = (PasswordResetToken.query
                  .filter_by(user_id=self.user_id).first())
            self.assertIsNotNone(tok)


if __name__ == '__main__':
    unittest.main()
