"""Regression tests for Internet Mail outbound send (Phase 2):
anetbbs/mailer.py's send_email_as() refactor (send_email() must keep
delegating to it with SmtpConfig's own from-address, not a user's), and
anetbbs/mail/outbound.py's send_internet_mail() (gating, address
validation, and the InternetMail row written on every real attempt).

Follows this codebase's own mocking convention for mailer.py
(tests/test_forgot_password_timing_oracle.py): mock.patch at the
anetbbs.mailer.<function> boundary, not deep smtplib mocking.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class _BaseAppTestCase(unittest.TestCase):
    _db_name = '.internet_mail_outbound_test.db'

    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / cls._db_name)
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

    def setUp(self):
        # Both MailConfig and SmtpConfig are DB-row singletons that
        # persist across test methods sharing this class's one SQLite
        # file (only tearDownClass resets the file) -- reset both to a
        # known-disabled baseline before every test, or an earlier
        # test's _enable_*_config() call leaks into a later test that
        # expects the feature OFF.
        from anetbbs.models import db, MailConfig, SmtpConfig
        with self.app.app_context():
            cfg = MailConfig.get()
            cfg.enabled = False
            cfg.domain = ''
            smtp = SmtpConfig.get()
            smtp.enabled = False
            smtp.host = ''
            smtp.from_address = ''
            db.session.commit()

    def _make_user(self, username, email_enabled=True, email_local_part=None):
        from anetbbs.models import db, User
        u = User(username=username, email=f'{username}@example.com',
                password_hash='x', email_enabled=email_enabled,
                email_local_part=email_local_part)
        db.session.add(u)
        db.session.commit()
        return u

    def _enable_mail_config(self, domain='bbs.a-net.fyi', enabled=True):
        from anetbbs.models import MailConfig
        cfg = MailConfig.get()
        cfg.enabled = enabled
        cfg.domain = domain
        from anetbbs.models import db
        db.session.commit()
        return cfg

    def _enable_smtp_config(self):
        from anetbbs.models import SmtpConfig, db
        cfg = SmtpConfig.get()
        cfg.enabled = True
        cfg.host = 'relay.example.com'
        cfg.from_address = 'sysop@bbs.a-net.fyi'
        db.session.commit()
        return cfg


class SendEmailAsRefactorTests(_BaseAppTestCase):
    _db_name = '.internet_mail_mailer_refactor_test.db'

    def test_send_email_delegates_to_send_email_as_with_sysop_identity(self):
        from anetbbs import mailer
        with self.app.app_context():
            self._enable_smtp_config()
            with mock.patch('anetbbs.mailer.send_email_as',
                            return_value=(True, '')) as mocked:
                ok, err = mailer.send_email('someone@example.com', 'Subj', 'Body')
            self.assertTrue(ok)
            mocked.assert_called_once()
            args = mocked.call_args[0]
            # (from_addr, from_name, to_address, subject, body_text, body_html=None)
            self.assertEqual(args[0], 'sysop@bbs.a-net.fyi')
            self.assertEqual(args[2], 'someone@example.com')

    def test_send_email_fails_fast_without_calling_relay_when_disabled(self):
        from anetbbs import mailer
        with self.app.app_context():
            with mock.patch('anetbbs.mailer.send_email_as') as mocked:
                ok, err = mailer.send_email('someone@example.com', 'Subj', 'Body')
            self.assertFalse(ok)
            mocked.assert_not_called()


class SendInternetMailGatingTests(_BaseAppTestCase):
    _db_name = '.internet_mail_outbound_gating_test.db'

    def test_disabled_mail_config_blocks_send_and_writes_no_row(self):
        from anetbbs.mail.outbound import send_internet_mail
        from anetbbs.models import InternetMail
        with self.app.app_context():
            self._enable_smtp_config()
            self._enable_mail_config(enabled=False)
            user = self._make_user('nomailfeature')
            with mock.patch('anetbbs.mailer.send_email_as') as mocked:
                ok, err = send_internet_mail(user, 'ext@example.com', 'Hi', 'body')
            self.assertFalse(ok)
            mocked.assert_not_called()
            self.assertEqual(InternetMail.query.filter_by(user_id=user.id).count(), 0)

    def test_no_relay_configured_blocks_send_and_writes_no_row(self):
        from anetbbs.mail.outbound import send_internet_mail
        from anetbbs.models import InternetMail
        with self.app.app_context():
            self._enable_mail_config(enabled=True)
            user = self._make_user('norelay')
            with mock.patch('anetbbs.mailer.send_email_as') as mocked:
                ok, err = send_internet_mail(user, 'ext@example.com', 'Hi', 'body')
            self.assertFalse(ok)
            mocked.assert_not_called()
            self.assertEqual(InternetMail.query.filter_by(user_id=user.id).count(), 0)

    def test_user_not_opted_in_blocks_send_and_writes_no_row(self):
        from anetbbs.mail.outbound import send_internet_mail
        from anetbbs.models import InternetMail
        with self.app.app_context():
            self._enable_smtp_config()
            self._enable_mail_config(enabled=True)
            user = self._make_user('notoptedin', email_enabled=False)
            with mock.patch('anetbbs.mailer.send_email_as') as mocked:
                ok, err = send_internet_mail(user, 'ext@example.com', 'Hi', 'body')
            self.assertFalse(ok)
            mocked.assert_not_called()
            self.assertEqual(InternetMail.query.filter_by(user_id=user.id).count(), 0)

    def test_invalid_recipient_rejected_before_any_network_call(self):
        from anetbbs.mail.outbound import send_internet_mail
        from anetbbs.models import InternetMail
        with self.app.app_context():
            self._enable_smtp_config()
            self._enable_mail_config(enabled=True)
            user = self._make_user('badaddr')
            with mock.patch('anetbbs.mailer.send_email_as') as mocked:
                ok, err = send_internet_mail(user, 'not-an-email', 'Hi', 'body')
            self.assertFalse(ok)
            mocked.assert_not_called()
            self.assertEqual(InternetMail.query.filter_by(user_id=user.id).count(), 0)


class SendInternetMailSuccessPathTests(_BaseAppTestCase):
    _db_name = '.internet_mail_outbound_success_test.db'

    def test_successful_send_writes_sent_row_with_users_own_from_address(self):
        from anetbbs.mail.outbound import send_internet_mail
        from anetbbs.models import InternetMail
        with self.app.app_context():
            self._enable_smtp_config()
            self._enable_mail_config(enabled=True, domain='bbs.a-net.fyi')
            user = self._make_user('stingray', email_local_part='stingray')
            with mock.patch('anetbbs.mailer.send_email_as',
                            return_value=(True, '')) as mocked:
                ok, err = send_internet_mail(user, 'friend@example.com',
                                             'Hello', 'test body')
            self.assertTrue(ok)
            mocked.assert_called_once()
            from_addr = mocked.call_args[0][0]
            self.assertEqual(from_addr, 'stingray@bbs.a-net.fyi')

            row = InternetMail.query.filter_by(user_id=user.id).first()
            self.assertIsNotNone(row)
            self.assertEqual(row.direction, 'outbound')
            self.assertEqual(row.status, 'sent')
            self.assertEqual(row.from_addr, 'stingray@bbs.a-net.fyi')
            self.assertEqual(row.to_addr, 'friend@example.com')
            self.assertIsNotNone(row.sent_at)

    def test_blank_local_part_falls_back_to_slugified_username(self):
        from anetbbs.mail.outbound import send_internet_mail
        with self.app.app_context():
            self._enable_smtp_config()
            self._enable_mail_config(enabled=True, domain='bbs.a-net.fyi')
            user = self._make_user('Sting Ray', email_local_part=None)
            with mock.patch('anetbbs.mailer.send_email_as',
                            return_value=(True, '')) as mocked:
                send_internet_mail(user, 'friend@example.com', 'Hi', 'b')
            from_addr = mocked.call_args[0][0]
            self.assertEqual(from_addr, 'stingray@bbs.a-net.fyi')

    def test_failed_relay_send_writes_failed_row_not_sent(self):
        from anetbbs.mail.outbound import send_internet_mail
        from anetbbs.models import InternetMail
        with self.app.app_context():
            self._enable_smtp_config()
            self._enable_mail_config(enabled=True)
            user = self._make_user('relayfail', email_local_part='relayfail')
            with mock.patch('anetbbs.mailer.send_email_as',
                            return_value=(False, 'connection refused')):
                ok, err = send_internet_mail(user, 'friend@example.com', 'Hi', 'b')
            self.assertFalse(ok)
            row = InternetMail.query.filter_by(user_id=user.id).first()
            self.assertIsNotNone(row)
            self.assertEqual(row.status, 'failed')
            self.assertIsNone(row.sent_at)


if __name__ == '__main__':
    unittest.main()
