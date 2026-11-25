"""H-4: an anonymous /registry/api/v1/register for an EXISTING host must not
change, delist, or re-key that entry without proof of ownership.

Before the fix the existing-host branch always rotated heartbeat_key
(returning the new key to the anonymous caller), overwrote name/ports/
source_ip, and -- with a different email -- delisted the entry and handed
back a verify_token.
"""
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

import anetbbs.config as cfg_mod

ATTACKER = {'REMOTE_ADDR': '198.51.100.66'}
OWNER = {'REMOTE_ADDR': '203.0.113.10'}


class RegistryOwnershipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp = tempfile.mkdtemp(prefix='h4-')
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp}/t.db'
        os.environ['FLASK_ENV'] = 'testing'
        from anetbbs.web_app import create_app
        from anetbbs.models import db
        cls.app = create_app('testing')
        cls.app.config.update(TESTING=True, REGISTRY_MODE_ENABLED=True, SMTP_HOST='')
        with cls.app.app_context():
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_uri

    def setUp(self):
        from anetbbs.models import db, RegistryEntry
        with self.app.app_context():
            RegistryEntry.query.delete()
            db.session.add(RegistryEntry(
                host='victim.example', name='Victim BBS',
                contact_email='sysop@victim.example', msp_port=18, systat_port=11,
                registration_token='t', heartbeat_key='REALKEY',
                is_verified=True, is_approved=True, is_listed=True, is_active=True,
                source_ip='203.0.113.10', registered_at=datetime(2026, 1, 1),
                last_heartbeat_at=datetime.utcnow() - timedelta(hours=1)))
            db.session.commit()
        self.c = self.app.test_client()

    def _state(self):
        from anetbbs.models import RegistryEntry
        with self.app.app_context():
            e = RegistryEntry.query.filter_by(host='victim.example').first()
            return dict(listed=e.is_listed, verified=e.is_verified,
                        approved=e.is_approved, key=e.heartbeat_key,
                        ip=e.source_ip, name=e.name, msp=e.msp_port,
                        email=e.contact_email)

    def _register(self, env, **fields):
        body = {'host': 'victim.example', 'name': 'Victim BBS',
                'contact_email': 'sysop@victim.example'}
        body.update(fields)
        return self.c.post('/registry/api/v1/register', json=body, environ_base=env)

    def test_keyless_different_email_changes_nothing(self):
        before = self._state()
        r = self._register(ATTACKER, name='pwned', contact_email='x@attacker.invalid')
        self.assertEqual(r.status_code, 409)
        body = r.get_json()
        self.assertNotIn('heartbeat_key', body)
        self.assertNotIn('verify_token', body)
        self.assertNotIn('sysop@victim.example', r.get_data(as_text=True))
        self.assertEqual(self._state(), before)
        hb = self.c.post('/registry/api/v1/heartbeat',
                         json={'host': 'victim.example', 'heartbeat_key': 'REALKEY'},
                         environ_base=OWNER)
        self.assertEqual(hb.status_code, 200)

    def test_keyless_same_email_cannot_rewrite_listing(self):
        before = self._state()
        r = self._register(ATTACKER, name='Victim BBS (moved)', msp_port=4444)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self._state(), before)
        listing = self.c.get('/anetbbs.lst').get_json()['bbses']
        self.assertEqual([(b['ip'], b['msp_port']) for b in listing],
                         [('203.0.113.10', 18)])

    def test_wrong_key_is_treated_as_keyless(self):
        before = self._state()
        r = self._register(ATTACKER, heartbeat_key='GUESS', msp_port=4444)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self._state(), before)

    def test_owner_with_current_key_can_update(self):
        r = self._register(OWNER, heartbeat_key='REALKEY', msp_port=2018)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        st = self._state()
        self.assertEqual(st['msp'], 2018)
        self.assertTrue(st['listed'])
        self.assertEqual(r.get_json()['heartbeat_key'], st['key'])
        self.assertNotEqual(st['key'], 'REALKEY')

    def test_key_holder_changing_email_gets_no_inband_verify_token(self):
        r = self._register(OWNER, heartbeat_key='REALKEY', contact_email='new@victim.example')
        self.assertEqual(r.status_code, 200)
        self.assertNotIn('verify_token', r.get_json())
        self.assertFalse(self._state()['verified'])

    def test_email_confirmed_rekey_from_same_ip(self):
        from anetbbs.models import RegistryEntry
        sent = []
        with mock.patch('anetbbs.mailer.smtp_enabled', return_value=True), \
                mock.patch('anetbbs.mailer.send_email',
                           side_effect=lambda to, subj, body: sent.append((to, body)) or (True, None)):
            r = self._register(OWNER, msp_port=2018)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(sent[0][0], 'sysop@victim.example')
        with self.app.app_context():
            token = RegistryEntry.query.filter_by(host='victim.example').first().rekey_token
        self.assertIn(token, sent[0][1])
        # GET only shows a confirm form; it must not confirm by itself.
        self.assertEqual(self.c.get(f'/registry/rekey/{token}').status_code, 200)
        self.assertEqual(self._register(OWNER).status_code, 409)
        self.assertEqual(self.c.post(f'/registry/rekey/{token}').status_code, 200)
        # A different IP still cannot use the confirmation.
        self.assertEqual(self._register(ATTACKER, msp_port=4444).status_code, 409)
        r = self._register(OWNER, msp_port=2018)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(self._state()['msp'], 2018)
        self.assertTrue(r.get_json()['heartbeat_key'])
        # One-shot: the token is gone.
        self.assertEqual(self.c.post(f'/registry/rekey/{token}').status_code, 404)

    def test_verify_link_needs_post(self):
        r = self.c.post('/registry/api/v1/register', json={
            'host': 'fresh2.example', 'name': 'Fresh2', 'contact_email': 'a@fresh2.example'},
            environ_base=OWNER)
        token = r.get_json()['verify_token']
        from anetbbs.models import RegistryEntry
        self.assertEqual(self.c.get(f'/registry/verify/{token}').status_code, 200)
        with self.app.app_context():
            self.assertFalse(RegistryEntry.query.filter_by(host='fresh2.example').first().is_verified)
        self.c.post(f'/registry/verify/{token}')
        with self.app.app_context():
            self.assertTrue(RegistryEntry.query.filter_by(host='fresh2.example').first().is_verified)

    def test_new_host_registration_unchanged(self):
        r = self.c.post('/registry/api/v1/register', json={
            'host': 'fresh.example', 'name': 'Fresh', 'contact_email': 'a@fresh.example'},
            environ_base=OWNER)
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertTrue(body['heartbeat_key'])
        self.assertIn('verify_token', body)   # no-SMTP hub, brand-new entry


if __name__ == '__main__':
    unittest.main()
