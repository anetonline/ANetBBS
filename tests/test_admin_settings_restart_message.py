"""Regression test for a real bug: the Admin -> Settings "restart
required" message told every sysop to restart 4 separate systemd
services (anetbbs.service, anetbbs-telnet.service, anetbbs-ssh.service,
anetbbs-rlogin.service). anetbbs-rlogin.service never existed as a real
unit at all, and anetbbs-telnet.service/anetbbs-ssh.service were merged
into the single combined anetbbs.service (see deploy/anetbbs.service's
own comment, and update.sh's active migration-away-from-split-services
logic) -- telnet/SSH/rlogin/PETSCII all run in that one process now,
gated individually by their own *_ENABLED flags in .env. Reported live
by the sysop after the message showed up unchanged post-PETSCII-launch.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class AdminSettingsRestartMessageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.admin_settings_restart_msg_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        with cls.app.app_context():
            db.create_all()
            admin = User(username='settingsadmin', email='settingsadmin@example.com',
                        password_hash='x', is_admin=True, access_level=100)
            db.session.add(admin)
            db.session.commit()
            cls.admin_id = admin.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _client(self):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True
        return client

    def test_restart_message_only_mentions_the_combined_service(self):
        client = self._client()
        # TELNET_PORT is one of the restart-flagged EDITABLE_SETTINGS entries.
        resp = client.post('/admin/settings', data={'TELNET_PORT': '2233'},
                           follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn('sudo systemctl restart anetbbs.service', body)
        self.assertNotIn('anetbbs-telnet', body)
        self.assertNotIn('anetbbs-ssh', body)
        self.assertNotIn('anetbbs-rlogin', body,
                         'anetbbs-rlogin.service was never a real systemd unit')

    def test_settings_page_hint_text_only_mentions_the_combined_service(self):
        client = self._client()
        resp = client.get('/admin/settings')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn('sudo systemctl restart anetbbs.service', body)
        self.assertNotIn('anetbbs-telnet', body)
        self.assertNotIn('anetbbs-ssh', body)
        self.assertNotIn('anetbbs-rlogin', body)


if __name__ == '__main__':
    unittest.main()
