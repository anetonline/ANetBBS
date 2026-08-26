"""Regression tests for the two CRITICAL federation-registry findings
from the 2026-08-26 MSP security audit (anetbbs/web/registry.py).

Finding 1 -- heartbeat() required nothing but a `host` string matching
an existing row (no ownership proof at all). Since `host` is published
verbatim by the same hub at /anetbbs.lst, anyone could silently
overwrite a listed peer's public name/sysop/location/notes with zero
notification. Fixed with a per-entry `heartbeat_key`, issued at
register time, compared with hmac.compare_digest.

Finding 2 -- re-registering a KNOWN host under a NEW contact_email
reset is_verified/is_listed but left is_approved untouched, AND the
verify_token/verify_url was returned directly in the /register JSON
response (no auth required to call /register) instead of being
emailed -- so an attacker could register any listed host under their
own email, immediately self-serve "verification" from the response
body, and ride the ORIGINAL sysop approval straight back onto the
public list via the background probe's auto-relist, with no fresh
sysop action anywhere in the chain. Fixed by (a) resetting
is_approved=False too when contact_email changes, and (b) emailing
verify_url to contact_email instead of returning it in the response
(with a documented fallback to the old behavior only when SMTP isn't
configured at all).
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class RegistryHijackFixesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.registry_hijack_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['REGISTRY_MODE_ENABLED'] = True
        cls.app.config['SYSOP_EMAIL'] = 'hubsysop@example.com'
        with cls.app.app_context():
            from anetbbs.models import db
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def setUp(self):
        # Fresh client + a unique host per test to avoid cross-test
        # collisions (RegistryEntry.host is unique).
        self.client = self.app.test_client()
        self._n = getattr(RegistryHijackFixesTests, '_counter', 0) + 1
        RegistryHijackFixesTests._counter = self._n
        self.host = f'peer{self._n}.example.com'

    def _register(self, **overrides):
        payload = {
            'host': self.host, 'name': 'Real BBS', 'sysop': 'RealOp',
            'contact_email': 'real-owner@example.com',
        }
        payload.update(overrides)
        return self.client.post('/registry/api/v1/register', json=payload)

    def _get_entry(self):
        from anetbbs.models import RegistryEntry
        with self.app.app_context():
            e = RegistryEntry.query.filter_by(host=self.host).first()
            return {
                'is_verified': e.is_verified, 'is_approved': e.is_approved,
                'is_listed': e.is_listed, 'heartbeat_key': e.heartbeat_key,
                'contact_email': e.contact_email,
                'registration_token': e.registration_token,
            }

    def _age_last_heartbeat(self, seconds=30):
        """register() itself sets last_heartbeat_at=now, which trips
        the real 10s host-level rate-limit floor on an immediate
        heartbeat call in a test. Push it back so tests can call
        heartbeat() right after register() without waiting on a real
        clock, without disabling the rate limiter itself."""
        from datetime import datetime, timedelta
        from anetbbs.models import db, RegistryEntry
        with self.app.app_context():
            e = RegistryEntry.query.filter_by(host=self.host).first()
            e.last_heartbeat_at = datetime.utcnow() - timedelta(seconds=seconds)
            db.session.commit()

    def _mark_verified_and_approved(self):
        """Simulate the legitimate lifecycle: verify + sysop approval."""
        from anetbbs.models import db, RegistryEntry
        with self.app.app_context():
            e = RegistryEntry.query.filter_by(host=self.host).first()
            e.is_verified = True
            e.is_approved = True
            e.is_listed = True
            db.session.commit()

    # -- Finding 1: heartbeat authorization -----------------------------

    @patch('anetbbs.mailer.smtp_enabled', return_value=False)
    def test_heartbeat_without_key_is_rejected(self, _mock_smtp):
        self._register()
        self._age_last_heartbeat()
        resp = self.client.post('/registry/api/v1/heartbeat',
                                json={'host': self.host, 'name': 'DEFACED'})
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(self._get_entry()['contact_email'],
                         'real-owner@example.com')

    @patch('anetbbs.mailer.smtp_enabled', return_value=False)
    def test_heartbeat_with_wrong_key_is_rejected_and_does_not_mutate(self, _mock_smtp):
        self._register()
        self._age_last_heartbeat()
        resp = self.client.post('/registry/api/v1/heartbeat',
                                json={'host': self.host, 'name': 'DEFACED',
                                     'heartbeat_key': 'totally-wrong-key'})
        self.assertEqual(resp.status_code, 401)
        # The real name must NOT have been overwritten.
        from anetbbs.models import RegistryEntry
        with self.app.app_context():
            e = RegistryEntry.query.filter_by(host=self.host).first()
            self.assertEqual(e.name, 'Real BBS')

    @patch('anetbbs.mailer.smtp_enabled', return_value=False)
    def test_heartbeat_with_correct_key_succeeds(self, _mock_smtp):
        reg = self._register()
        key = reg.get_json()['heartbeat_key']
        self._age_last_heartbeat()
        resp = self.client.post('/registry/api/v1/heartbeat',
                                json={'host': self.host, 'name': 'Updated Name',
                                     'heartbeat_key': key})
        self.assertEqual(resp.status_code, 200)
        from anetbbs.models import RegistryEntry
        with self.app.app_context():
            e = RegistryEntry.query.filter_by(host=self.host).first()
            self.assertEqual(e.name, 'Updated Name')

    @patch('anetbbs.mailer.smtp_enabled', return_value=False)
    def test_a_stranger_who_only_knows_the_public_host_cannot_deface_it(self, _mock_smtp):
        """The exact attack scenario: host is public (published at
        /anetbbs.lst), attacker knows nothing else."""
        self._register()
        self._mark_verified_and_approved()
        self._age_last_heartbeat()
        attacker_resp = self.client.post(
            '/registry/api/v1/heartbeat',
            json={'host': self.host, 'name': 'PWNED', 'sysop': 'attacker',
                 'notes': 'defaced'})
        self.assertEqual(attacker_resp.status_code, 401)
        entry = self._get_entry()
        self.assertTrue(entry['is_listed'])  # still legitimately listed
        from anetbbs.models import RegistryEntry
        with self.app.app_context():
            e = RegistryEntry.query.filter_by(host=self.host).first()
            self.assertEqual(e.name, 'Real BBS')
            self.assertEqual(e.sysop, 'RealOp')

    # -- Finding 2: re-registration hijack chain -------------------------

    @patch('anetbbs.mailer.smtp_enabled', return_value=False)
    def test_reregistration_with_new_email_resets_approval_too(self, _mock_smtp):
        """Before the fix: is_verified/is_listed reset on email change,
        but is_approved stayed True -- letting the background auto-
        relist republish the row under the new (attacker) identity
        with no fresh sysop action. Now is_approved resets too."""
        self._register()
        self._mark_verified_and_approved()
        self._age_last_heartbeat()
        entry_before = self._get_entry()
        self.assertTrue(entry_before['is_approved'])

        # "Attacker" (or anyone) re-registers the SAME host with a
        # DIFFERENT contact_email.
        self._register(contact_email='attacker@evil.example.com',
                       name='Hijacked BBS', sysop='attacker')

        entry_after = self._get_entry()
        self.assertFalse(entry_after['is_verified'])
        self.assertFalse(entry_after['is_approved'],
                         'is_approved must reset when contact_email changes '
                         '-- this is the actual hijack-chain fix')
        self.assertFalse(entry_after['is_listed'])

    @patch('anetbbs.mailer.smtp_enabled', return_value=True)
    @patch('anetbbs.mailer.send_email', return_value=(True, ''))
    def test_register_response_does_not_leak_verify_token_when_smtp_configured(
            self, mock_send, _mock_smtp):
        """The core of the fix: when the hub CAN email the token, it
        must not also hand it to whoever just POSTed /register --
        that's the whole hijack primitive."""
        resp = self._register()
        body = resp.get_json()
        self.assertNotIn('verify_token', body)
        self.assertNotIn('verify_url', body)
        # But heartbeat_key IS still needed synchronously by the caller.
        self.assertIn('heartbeat_key', body)
        self.assertTrue(body['heartbeat_key'])

    @patch('anetbbs.mailer.smtp_enabled', return_value=True)
    @patch('anetbbs.mailer.send_email', return_value=(True, ''))
    def test_verify_email_actually_goes_to_the_submitted_contact_email(
            self, mock_send, _mock_smtp):
        self._register(contact_email='real-owner@example.com')
        mock_send.assert_called_once()
        args = mock_send.call_args[0]
        self.assertEqual(args[0], 'real-owner@example.com')
        # The verify link itself must be in the email body somewhere.
        self.assertIn('/registry/verify/', args[2])

    @patch('anetbbs.mailer.smtp_enabled', return_value=False)
    def test_verify_url_still_returned_when_smtp_not_configured(self, _mock_smtp):
        """Preserves the original documented fallback for installs
        with no SMTP -- otherwise a registrant could never verify at
        all on such an install."""
        resp = self._register()
        body = resp.get_json()
        self.assertIn('verify_url', body)
        self.assertIn('/registry/verify/', body['verify_url'])

    @patch('anetbbs.mailer.smtp_enabled', return_value=True)
    @patch('anetbbs.mailer.send_email', return_value=(True, ''))
    def test_reregistration_with_same_email_does_not_reverify_or_reemail(
            self, mock_send, _mock_smtp):
        """A legitimate metadata refresh (same email) shouldn't force
        the registrant through email verification again, and
        shouldn't spam a re-verification email either."""
        self._register()
        self._mark_verified_and_approved()
        mock_send.reset_mock()

        self._register(name='Real BBS (updated)')  # same contact_email

        entry = self._get_entry()
        self.assertTrue(entry['is_verified'])
        self.assertTrue(entry['is_approved'])
        mock_send.assert_not_called()

    @patch('anetbbs.mailer.smtp_enabled', return_value=False)
    def test_reregistration_always_issues_a_fresh_heartbeat_key(self, _mock_smtp):
        """Self-heal path: a legitimate peer whose stored key is stale
        (predates this fix, or the hub row was reset) must be able to
        recover a working key just by re-registering, with no email
        re-verification needed if contact_email is unchanged."""
        first = self._register()
        key1 = first.get_json()['heartbeat_key']
        self._age_last_heartbeat()
        second = self._register()
        key2 = second.get_json()['heartbeat_key']
        self.assertNotEqual(key1, key2)
        self._age_last_heartbeat()
        # And the NEW key actually works for a heartbeat.
        hb = self.client.post('/registry/api/v1/heartbeat',
                              json={'host': self.host, 'heartbeat_key': key2})
        self.assertEqual(hb.status_code, 200)


if __name__ == '__main__':
    unittest.main()
