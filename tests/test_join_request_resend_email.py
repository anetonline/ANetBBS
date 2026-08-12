"""Regression/coverage tests for the network-join credentials-email
resend feature (Jerry: "the last two people said they have not
received [the auto-sent join-approval email]... should have a resend
feature I would assume").

Real gap found alongside this: the original send was always a
one-shot, best-effort attempt at approval time with NOTHING persisted
about whether it actually succeeded -- only a transient flash message
and a log line. NetworkJoinRequest.email_sent_at/email_last_attempt_at/
email_error (new columns) fix that, and _send_join_approval_email()
(anetbbs/web/hub_admin.py) is the single function both the automatic
send-on-approval and the manual resend action call, so the two can
never drift apart and a resend always reuses the SAME already-
generated credentials rather than regenerating (which would silently
invalidate whatever the applicant may have already received/used).
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class _JoinResendTestBase(unittest.TestCase):
    DB_SUFFIX = 'base'

    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent /
                          f'.join_resend_{cls.DB_SUFFIX}_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User, HubIdentity, EchomailNetwork
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['REGISTRY_MODE_ENABLED'] = True
        with cls.app.app_context():
            db.create_all()

            admin = User(username=f'joinresendadmin{cls.DB_SUFFIX}', is_admin=True,
                        email=f'admin{cls.DB_SUFFIX}@example.com')
            admin.set_password('x')
            db.session.add(admin)

            identity = HubIdentity(name='TestHub', slug=f'testhub-resend-{cls.DB_SUFFIX}',
                                   binkp_zone=1200, binkp_net=1,
                                   is_default=True, is_active=True)
            db.session.add(identity)
            db.session.flush()

            net = EchomailNetwork(
                name='TestHubNet', network_type='binkp', is_active=True,
                our_address='1200:1/1', hub_address='1200:1/1',
                binkp_port=24555, areafix_password='hubareapw',
                binkp_password='hubbinkpw', hub_identity_id=identity.id)
            db.session.add(net)
            db.session.commit()
            cls.identity_id = identity.id
            cls.admin_id = admin.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _admin_client(self):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True
        return client

    def _make_request(self, suffix):
        from anetbbs.models import db, NetworkJoinRequest
        with self.app.app_context():
            req = NetworkJoinRequest(
                hub_identity_id=self.identity_id,
                name=f'Applicant{suffix}', bbs_name=f'TestBBS{suffix}',
                email=f'applicant{suffix}@example.com',
                binkp_ftn_address=f'1200:1/{900 + hash(suffix) % 90}',
                rules_ack=True)
            db.session.add(req)
            db.session.commit()
            return req.id


class ApprovalPersistsEmailStatusTests(_JoinResendTestBase):
    DB_SUFFIX = 'approval_status'

    def test_successful_send_sets_sent_at_and_clears_error(self):
        from anetbbs.models import NetworkJoinRequest
        req_id = self._make_request('A')
        client = self._admin_client()
        with patch('anetbbs.mailer.smtp_enabled', return_value=True), \
             patch('anetbbs.mailer.send_email', return_value=(True, None)):
            client.post(f'/admin/echomail/hub/join/requests/{req_id}/approve',
                       follow_redirects=True)

        with self.app.app_context():
            req = NetworkJoinRequest.query.get(req_id)
            self.assertIsNotNone(req.email_sent_at)
            self.assertIsNotNone(req.email_last_attempt_at)
            self.assertIsNone(req.email_error)

    def test_failed_send_records_error_without_sent_at(self):
        from anetbbs.models import NetworkJoinRequest
        req_id = self._make_request('B')
        client = self._admin_client()
        with patch('anetbbs.mailer.smtp_enabled', return_value=True), \
             patch('anetbbs.mailer.send_email',
                   return_value=(False, 'relay refused connection')):
            client.post(f'/admin/echomail/hub/join/requests/{req_id}/approve',
                       follow_redirects=True)

        with self.app.app_context():
            req = NetworkJoinRequest.query.get(req_id)
            self.assertIsNone(req.email_sent_at)
            self.assertIsNotNone(req.email_last_attempt_at)
            self.assertEqual(req.email_error, 'relay refused connection')

    def test_smtp_not_configured_records_neither_sent_nor_attempt(self):
        """_send_join_approval_email() returns early, before touching
        email_last_attempt_at, when SMTP isn't configured at all --
        that's a config gap, not a delivery attempt."""
        from anetbbs.models import NetworkJoinRequest
        req_id = self._make_request('C')
        client = self._admin_client()
        with patch('anetbbs.mailer.smtp_enabled', return_value=False):
            client.post(f'/admin/echomail/hub/join/requests/{req_id}/approve',
                       follow_redirects=True)

        with self.app.app_context():
            req = NetworkJoinRequest.query.get(req_id)
            self.assertIsNone(req.email_sent_at)
            self.assertIsNone(req.email_last_attempt_at)
            self.assertIsNone(req.email_error)


class ResendReusesCredentialsTests(_JoinResendTestBase):
    DB_SUFFIX = 'resend_reuse'

    def test_resend_reuses_the_same_password_not_a_new_one(self):
        from anetbbs.models import NetworkJoinRequest
        req_id = self._make_request('D')
        client = self._admin_client()
        with patch('anetbbs.mailer.smtp_enabled', return_value=True), \
             patch('anetbbs.mailer.send_email', return_value=(True, None)):
            client.post(f'/admin/echomail/hub/join/requests/{req_id}/approve',
                       follow_redirects=True)

        with self.app.app_context():
            original_password = NetworkJoinRequest.query.get(req_id).generated_binkp_password
        self.assertTrue(original_password)

        with patch('anetbbs.mailer.smtp_enabled', return_value=True), \
             patch('anetbbs.mailer.send_email', return_value=(True, None)) as mock_send:
            client.post(f'/admin/echomail/hub/join/requests/{req_id}/resend-email',
                       follow_redirects=True)

        self.assertTrue(mock_send.called)
        body = mock_send.call_args[0][2]
        self.assertIn(original_password, body,
                      'resend must re-send the SAME already-generated '
                      'credentials, never regenerate a new password')

        with self.app.app_context():
            req = NetworkJoinRequest.query.get(req_id)
            self.assertEqual(req.generated_binkp_password, original_password,
                             'resend must not mutate the stored password')

    def test_resend_on_pending_request_is_rejected(self):
        req_id = self._make_request('E')
        client = self._admin_client()
        with patch('anetbbs.mailer.send_email') as mock_send:
            resp = client.post(
                f'/admin/echomail/hub/join/requests/{req_id}/resend-email',
                follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(mock_send.called,
                         'must not attempt to email an un-approved request')

    def test_failed_resend_after_earlier_success_sets_error_but_keeps_sent_at(self):
        """A later failed resend must not erase evidence that an EARLIER
        send succeeded -- email_sent_at is set-on-success-only and never
        cleared by a later failure; email_error is what the UI checks
        FIRST so a stale success timestamp can't mask a fresh failure."""
        from anetbbs.models import NetworkJoinRequest
        req_id = self._make_request('F')
        client = self._admin_client()
        with patch('anetbbs.mailer.smtp_enabled', return_value=True), \
             patch('anetbbs.mailer.send_email', return_value=(True, None)):
            client.post(f'/admin/echomail/hub/join/requests/{req_id}/approve',
                       follow_redirects=True)

        with self.app.app_context():
            first_sent_at = NetworkJoinRequest.query.get(req_id).email_sent_at
        self.assertIsNotNone(first_sent_at)

        with patch('anetbbs.mailer.smtp_enabled', return_value=True), \
             patch('anetbbs.mailer.send_email',
                   return_value=(False, 'mailbox unavailable')):
            client.post(f'/admin/echomail/hub/join/requests/{req_id}/resend-email',
                       follow_redirects=True)

        with self.app.app_context():
            req = NetworkJoinRequest.query.get(req_id)
            self.assertEqual(req.email_sent_at, first_sent_at,
                             'the earlier successful send timestamp must survive')
            self.assertEqual(req.email_error, 'mailbox unavailable')


class ResendUiRendersTests(_JoinResendTestBase):
    DB_SUFFIX = 'ui_render'

    def test_list_and_detail_pages_render_with_resend_button(self):
        req_id = self._make_request('G')
        client = self._admin_client()
        with patch('anetbbs.mailer.smtp_enabled', return_value=True), \
             patch('anetbbs.mailer.send_email', return_value=(True, None)):
            client.post(f'/admin/echomail/hub/join/requests/{req_id}/approve',
                       follow_redirects=True)

        r1 = client.get('/admin/echomail/hub/join/requests')
        self.assertEqual(r1.status_code, 200)
        body1 = r1.data.decode()
        self.assertIn('resend-email', body1)
        self.assertIn('Sent', body1)

        r2 = client.get(f'/admin/echomail/hub/join/requests/{req_id}')
        self.assertEqual(r2.status_code, 200)
        body2 = r2.data.decode()
        self.assertIn('resend-email', body2)
        self.assertIn('Resend', body2)


if __name__ == '__main__':
    unittest.main()
