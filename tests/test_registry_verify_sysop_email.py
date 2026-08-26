"""Regression test for the federation-registry verify() sysop-email fix.

Before this fix, a peer BBS verifying its contact email only ever
produced an in-app Notification row (features/notify.py) -- nothing
reached the sysop unless they were already looking at the admin UI.
Real report from Jerry: he only found out a peer had registered
because he happened to check manually, "if I had not sent a request
from the Pi to ANetBBS and specifically looked for it." verify() now
also emails SYSOP_EMAIL via the existing mailer.send_email(), matching
how every other sysop-facing SMTP send in this project already works
-- best-effort, a no-op when SMTP/SYSOP_EMAIL isn't configured.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class RegistryVerifySysopEmailTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.registry_verify_email_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['REGISTRY_MODE_ENABLED'] = True
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

    def _make_entry(self, token):
        from anetbbs.models import db, RegistryEntry
        from datetime import datetime
        entry = RegistryEntry(
            host=f'peer-{token}.example.com', name='Peer BBS',
            sysop='PeerOp', contact_email='peer@example.com',
            registration_token=token, is_verified=False,
            is_approved=False, is_listed=False,
            registered_at=datetime.utcnow(),
            last_heartbeat_at=datetime.utcnow())
        db.session.add(entry)
        db.session.commit()
        return entry

    def test_verify_emails_sysop_when_configured(self):
        self.app.config['SYSOP_EMAIL'] = 'sysop@example.com'
        with self.app.app_context():
            self._make_entry('tok-emailtest-1')
        with patch('anetbbs.mailer.send_email') as mock_send:
            mock_send.return_value = (True, '')
            client = self.app.test_client()
            resp = client.get('/registry/verify/tok-emailtest-1')
        self.assertEqual(resp.status_code, 200)
        mock_send.assert_called_once()
        args = mock_send.call_args[0]
        self.assertEqual(args[0], 'sysop@example.com')
        self.assertIn('peer-tok-emailtest-1', args[1])  # host appears in subject

    def test_verify_skips_email_when_sysop_email_blank(self):
        self.app.config['SYSOP_EMAIL'] = ''
        with self.app.app_context():
            self._make_entry('tok-emailtest-2')
        with patch('anetbbs.mailer.send_email') as mock_send:
            client = self.app.test_client()
            resp = client.get('/registry/verify/tok-emailtest-2')
        self.assertEqual(resp.status_code, 200)
        mock_send.assert_not_called()

    def test_verify_still_succeeds_if_email_send_raises(self):
        """A mailer failure must not break the verify() response the
        registrant sees -- best-effort only."""
        self.app.config['SYSOP_EMAIL'] = 'sysop@example.com'
        with self.app.app_context():
            self._make_entry('tok-emailtest-3')
        with patch('anetbbs.mailer.send_email', side_effect=RuntimeError('smtp down')):
            client = self.app.test_client()
            resp = client.get('/registry/verify/tok-emailtest-3')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'verified', resp.data.lower())


if __name__ == '__main__':
    unittest.main()
