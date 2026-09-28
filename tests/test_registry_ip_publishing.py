"""Regression test for a real gap reported live 2026-09-29: the
terminal MSP directory picker's hostname-then-IP fallback
(bbs_ui.py's _msp_pick_online_user(), fixed in v1.1.5) had nothing to
fall back to for ANY anetbbs.lst-sourced directory entry -- every
single one, uniformly, which is why "all BBSes" kept failing even
after that fix shipped.

Root cause, traced through the whole chain:
  1. RegistryEntry.source_ip WAS already captured at register() time
     (web/registry.py) -- but only ever used internally for rate-limit
     abuse detection, never exposed in the published /anetbbs.lst JSON.
  2. anetbbs_directory.py's puller (which upserts BbsDirectoryEntry
     from /anetbbs.lst) never read/stored an IP at all -- there was
     none in the feed to read.
  3. So every anetbbs.lst-sourced BbsDirectoryEntry.ip_address stayed
     NULL forever, no matter how many peers registered/heartbeat.

Fixed by publishing source_ip as 'ip' in /anetbbs.lst (register() AND
heartbeat() both keep it fresh now, not just register()), and having
the puller store it into BbsDirectoryEntry.ip_address the same way the
sbbsimsg.lst puller (directory.py) already did.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class RegistryIpPublishingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent
                          / '.registry_ip_publishing_test.db')
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
        self.client = self.app.test_client()
        self._n = getattr(RegistryIpPublishingTests, '_counter', 0) + 1
        RegistryIpPublishingTests._counter = self._n
        self.host = f'ippeer{self._n}.example.com'

    def _register(self, **overrides):
        payload = {
            'host': self.host, 'name': 'Real BBS', 'sysop': 'RealOp',
            'contact_email': 'real-owner@example.com',
        }
        payload.update(overrides)
        return self.client.post('/registry/api/v1/register', json=payload)

    def _mark_verified_and_approved(self):
        from anetbbs.models import db, RegistryEntry
        with self.app.app_context():
            e = RegistryEntry.query.filter_by(host=self.host).first()
            e.is_verified = True
            e.is_approved = True
            e.is_listed = True
            db.session.commit()

    def _age_last_heartbeat(self, seconds=30):
        from datetime import datetime, timedelta
        from anetbbs.models import db, RegistryEntry
        with self.app.app_context():
            e = RegistryEntry.query.filter_by(host=self.host).first()
            e.last_heartbeat_at = datetime.utcnow() - timedelta(seconds=seconds)
            db.session.commit()

    @patch('anetbbs.mailer.smtp_enabled', return_value=False)
    def test_register_captures_source_ip(self, _mock_smtp):
        self._register()
        from anetbbs.models import RegistryEntry
        with self.app.app_context():
            e = RegistryEntry.query.filter_by(host=self.host).first()
            self.assertTrue(e.source_ip, 'source_ip must be captured at register time')

    @patch('anetbbs.mailer.smtp_enabled', return_value=False)
    def test_anetbbs_lst_publishes_the_ip_field(self, _mock_smtp):
        self._register()
        self._mark_verified_and_approved()
        resp = self.client.get('/anetbbs.lst')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        entries = [b for b in data['bbses'] if b['host'] == self.host]
        self.assertEqual(len(entries), 1)
        self.assertIn('ip', entries[0])
        self.assertTrue(entries[0]['ip'], 'ip field must be non-empty for a registered peer')

    @patch('anetbbs.mailer.smtp_enabled', return_value=False)
    def test_heartbeat_refreshes_source_ip(self, _mock_smtp):
        reg = self._register()
        key = reg.get_json()['heartbeat_key']
        self._age_last_heartbeat()
        from anetbbs.models import db, RegistryEntry
        with self.app.app_context():
            e = RegistryEntry.query.filter_by(host=self.host).first()
            e.source_ip = None  # simulate a legacy row from before this fix
            db.session.commit()
        resp = self.client.post('/registry/api/v1/heartbeat',
                                json={'host': self.host, 'heartbeat_key': key})
        self.assertEqual(resp.status_code, 200)
        with self.app.app_context():
            e = RegistryEntry.query.filter_by(host=self.host).first()
            self.assertTrue(e.source_ip, 'heartbeat must (re)populate source_ip')

    @patch('anetbbs.mailer.smtp_enabled', return_value=False)
    def test_puller_stores_the_published_ip_into_bbs_directory_entry(self, _mock_smtp):
        """End-to-end: register -> publish -> pull -> the terminal
        picker's fallback finally has something to use."""
        self._register()
        self._mark_verified_and_approved()

        from anetbbs.msp.anetbbs_directory import refresh
        from anetbbs.models import BbsDirectoryEntry

        anetbbs_lst = self.client.get('/anetbbs.lst').get_json()

        class _FakeRequestsResp:
            def raise_for_status(self_inner):
                pass

            def json(self_inner):
                return anetbbs_lst

        with patch('anetbbs.msp.anetbbs_directory.requests.get',
                   return_value=_FakeRequestsResp()):
            self.app.config['REGISTRY_URL'] = 'http://hub.example.com'
            with self.app.app_context():
                refresh(self.app)
                entry = BbsDirectoryEntry.query.filter_by(hostname=self.host).first()
                self.assertIsNotNone(entry)
                self.assertTrue(entry.ip_address,
                                'puller must store the published ip into ip_address')


if __name__ == '__main__':
    unittest.main()
