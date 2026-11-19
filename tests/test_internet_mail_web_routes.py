"""Regression tests for anetbbs/web/mail.py: the before_request gate
(whole feature off, or this user not opted in, must redirect rather
than 500 or leak another user's mail), real .paginate() on inbox/sent
(mirroring tests/test_files_and_pm_pagination.py's PM coverage, not
netmail.py's unbounded .limit(200) anti-pattern), ownership checks on
read/delete, and compose() actually calling send_internet_mail().
"""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class _BaseWebMailTestCase(unittest.TestCase):
    _db_name = '.internet_mail_web_routes_test.db'

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
        cls.app.config['WTF_CSRF_ENABLED'] = False

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def setUp(self):
        from anetbbs.models import db, MailConfig, SmtpConfig
        with self.app.app_context():
            cfg = MailConfig.get()
            cfg.enabled = True
            cfg.domain = 'bbs.a-net.fyi'
            smtp = SmtpConfig.get()
            smtp.enabled = True
            smtp.host = 'relay.example.com'
            smtp.from_address = 'sysop@bbs.a-net.fyi'
            db.session.commit()

    def _make_user(self, username, email_enabled=True, email_local_part=None):
        from anetbbs.models import db, User
        with self.app.app_context():
            u = User(username=username, email=f'{username}@example.com',
                    password_hash='x', email_enabled=email_enabled,
                    email_local_part=email_local_part or username)
            db.session.add(u)
            db.session.commit()
            return u.id

    def _login(self, client, user_id):
        with client.session_transaction() as sess:
            sess['_user_id'] = str(user_id)
            sess['_fresh'] = True


class MailGateTests(_BaseWebMailTestCase):
    _db_name = '.internet_mail_web_gate_test.db'

    def test_user_not_opted_in_is_redirected_to_profile_edit(self):
        user_id = self._make_user('notoptedinweb', email_enabled=False)
        client = self.app.test_client()
        self._login(client, user_id)
        resp = client.get('/mail/', follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/profile', resp.headers['Location'])

    def test_whole_feature_disabled_redirects_even_for_opted_in_user(self):
        from anetbbs.models import db, MailConfig
        with self.app.app_context():
            MailConfig.get().enabled = False
            db.session.commit()
        user_id = self._make_user('featureoffweb')
        client = self.app.test_client()
        self._login(client, user_id)
        resp = client.get('/mail/', follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/profile', resp.headers['Location'])

    def test_anonymous_visitor_is_redirected_not_500d(self):
        client = self.app.test_client()
        resp = client.get('/mail/', follow_redirects=False)
        self.assertEqual(resp.status_code, 302)


class MailInboxPaginationTests(_BaseWebMailTestCase):
    _db_name = '.internet_mail_web_pagination_test.db'

    def test_inbox_and_sent_are_paginated(self):
        from anetbbs.models import db, InternetMail
        user_id = self._make_user('paginationmailuser')
        with self.app.app_context():
            for i in range(55):
                db.session.add(InternetMail(
                    user_id=user_id, direction='inbound', status='received',
                    from_addr='friend@example.com', to_addr='paginationmailuser@bbs.a-net.fyi',
                    subject=f'Inbound {i}', body='x'))
                db.session.add(InternetMail(
                    user_id=user_id, direction='outbound', status='sent',
                    from_addr='paginationmailuser@bbs.a-net.fyi', to_addr='friend@example.com',
                    subject=f'Outbound {i}', body='x'))
            db.session.commit()

        client = self.app.test_client()
        self._login(client, user_id)

        resp = client.get('/mail/')
        body = resp.get_data(as_text=True)
        self.assertIn('Inbound 54', body)
        self.assertIn('Page 1 of 2', body)
        resp2 = client.get('/mail/?page=2')
        body2 = resp2.get_data(as_text=True)
        self.assertIn('Inbound 0', body2)
        self.assertNotIn('Inbound 54', body2)

        resp3 = client.get('/mail/sent')
        self.assertIn('Page 1 of 2', resp3.get_data(as_text=True))


class MailOwnershipTests(_BaseWebMailTestCase):
    _db_name = '.internet_mail_web_ownership_test.db'

    def test_reading_another_users_mail_is_403(self):
        from anetbbs.models import db, InternetMail
        owner_id = self._make_user('ownerweb')
        intruder_id = self._make_user('intruderweb')
        with self.app.app_context():
            msg = InternetMail(user_id=owner_id, direction='inbound',
                               status='received', from_addr='x@example.com',
                               to_addr='ownerweb@bbs.a-net.fyi',
                               subject='Private', body='secret')
            db.session.add(msg)
            db.session.commit()
            msg_id = msg.id

        client = self.app.test_client()
        self._login(client, intruder_id)
        resp = client.get(f'/mail/{msg_id}')
        self.assertEqual(resp.status_code, 403)

    def test_deleting_another_users_mail_is_403(self):
        from anetbbs.models import db, InternetMail
        owner_id = self._make_user('ownerweb2')
        intruder_id = self._make_user('intruderweb2')
        with self.app.app_context():
            msg = InternetMail(user_id=owner_id, direction='inbound',
                               status='received', from_addr='x@example.com',
                               to_addr='ownerweb2@bbs.a-net.fyi',
                               subject='Private', body='secret')
            db.session.add(msg)
            db.session.commit()
            msg_id = msg.id

        client = self.app.test_client()
        self._login(client, intruder_id)
        resp = client.post(f'/mail/{msg_id}/delete')
        self.assertEqual(resp.status_code, 403)

        with self.app.app_context():
            from anetbbs.models import InternetMail as IM
            self.assertIsNotNone(IM.query.get(msg_id),
                                 "intruder's forbidden delete must not have deleted the row")

    def test_reading_own_inbound_mail_marks_it_read(self):
        from anetbbs.models import db, InternetMail, Notification
        owner_id = self._make_user('readownweb')
        with self.app.app_context():
            msg = InternetMail(user_id=owner_id, direction='inbound',
                               status='received', from_addr='x@example.com',
                               to_addr='readownweb@bbs.a-net.fyi',
                               subject='Hi', body='hello')
            db.session.add(msg)
            db.session.commit()
            msg_id = msg.id
            db.session.add(Notification(
                user_id=owner_id, kind='internet_mail',
                title='New Internet Mail', body='A new message is waiting.',
                target_url=f'/mail/{msg_id}'))
            db.session.commit()

        client = self.app.test_client()
        self._login(client, owner_id)
        resp = client.get(f'/mail/{msg_id}')
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            from anetbbs.models import InternetMail as IM, Notification as N
            refreshed = IM.query.get(msg_id)
            self.assertIsNotNone(refreshed.read_at)
            self.assertEqual(refreshed.status, 'read')
            notice = N.query.filter_by(user_id=owner_id, kind='internet_mail').one()
            self.assertTrue(notice.is_read)

    def test_unread_internet_mail_appears_in_top_notification_bell(self):
        from anetbbs.models import db, Notification
        user_id = self._make_user('bellmailuser')
        with self.app.app_context():
            db.session.add(Notification(
                user_id=user_id, kind='internet_mail', title='New Internet Mail',
                body='A new message is waiting.', target_url='/mail/123'))
            db.session.commit()
        client = self.app.test_client()
        self._login(client, user_id)
        resp = client.get('/mail/')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('id="notifBellBadge"', html)
        self.assertIn('>1</span>', html)


class MailComposeTests(_BaseWebMailTestCase):
    _db_name = '.internet_mail_web_compose_test.db'

    def test_compose_success_sends_and_redirects_to_sent(self):
        user_id = self._make_user('composeweb', email_local_part='composeweb')
        client = self.app.test_client()
        self._login(client, user_id)

        with mock.patch('anetbbs.mailer.send_email_as', return_value=(True, '')):
            resp = client.post('/mail/compose', data={
                'to_address': 'friend@example.com',
                'subject': 'Hello',
                'content': 'Test body',
            }, follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/mail/sent', resp.headers['Location'])

        with self.app.app_context():
            from anetbbs.models import InternetMail
            row = InternetMail.query.filter_by(user_id=user_id, direction='outbound').first()
            self.assertIsNotNone(row)
            self.assertEqual(row.status, 'sent')

    def test_compose_relay_failure_does_not_redirect(self):
        user_id = self._make_user('composefailweb', email_local_part='composefailweb')
        client = self.app.test_client()
        self._login(client, user_id)

        with mock.patch('anetbbs.mailer.send_email_as',
                        return_value=(False, 'relay down')):
            resp = client.post('/mail/compose', data={
                'to_address': 'friend@example.com',
                'subject': 'Hello',
                'content': 'Test body',
            }, follow_redirects=False)
        self.assertEqual(resp.status_code, 200)
        self.assertIn('relay down', resp.get_data(as_text=True))

    def test_compose_invalid_address_shows_form_error(self):
        user_id = self._make_user('composebadaddrweb', email_local_part='composebadaddrweb')
        client = self.app.test_client()
        self._login(client, user_id)

        resp = client.post('/mail/compose', data={
            'to_address': 'not-an-email',
            'subject': 'Hello',
            'content': 'Test body',
        }, follow_redirects=False)
        self.assertEqual(resp.status_code, 200)
        with self.app.app_context():
            from anetbbs.models import InternetMail
            self.assertEqual(
                InternetMail.query.filter_by(user_id=user_id).count(), 0)


if __name__ == '__main__':
    unittest.main()
