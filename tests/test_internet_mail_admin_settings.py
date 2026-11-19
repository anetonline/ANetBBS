"""Regression tests for anetbbs/web/admin.py's mail_settings() route --
mirrors smtp_settings()'s shape (MailConfig.get(), save, re-render).
Confirms a non-admin is rejected and a real save round-trips through
MailConfig correctly (enabled/domain/min_access_level/max_message_size).
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class AdminMailSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.internet_mail_admin_settings_test.db')
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
            admin = User(username='mailadmintest', email='ma@example.com',
                        password_hash='x', is_admin=True, access_level=100)
            regular = User(username='mailregulartest', email='mr@example.com',
                           password_hash='x', is_admin=False, access_level=10)
            db.session.add_all([admin, regular])
            db.session.commit()
            cls.admin_id = admin.id
            cls.regular_id = regular.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _login(self, client, user_id):
        with client.session_transaction() as sess:
            sess['_user_id'] = str(user_id)
            sess['_fresh'] = True

    def test_non_admin_cannot_reach_settings(self):
        client = self.app.test_client()
        self._login(client, self.regular_id)
        resp = client.get('/admin/mail')
        self.assertIn(resp.status_code, (403, 302))

    def test_admin_can_view_settings(self):
        client = self.app.test_client()
        self._login(client, self.admin_id)
        resp = client.get('/admin/mail')
        self.assertEqual(resp.status_code, 200)

    def test_saving_settings_round_trips_through_mailconfig(self):
        client = self.app.test_client()
        self._login(client, self.admin_id)
        resp = client.post('/admin/mail', data={
            'enabled': 'y',
            'domain': 'bbs.a-net.fyi',
            'min_access_level': '50',
            'max_message_size_mb': '10',
        }, follow_redirects=False)
        self.assertEqual(resp.status_code, 302)

        with self.app.app_context():
            from anetbbs.models import MailConfig
            cfg = MailConfig.get()
            self.assertTrue(cfg.enabled)
            self.assertEqual(cfg.domain, 'bbs.a-net.fyi')
            self.assertEqual(cfg.min_access_level, 50)
            self.assertEqual(cfg.max_message_size, 10 * 1024 * 1024)

    def test_unchecking_enabled_disables_it(self):
        client = self.app.test_client()
        self._login(client, self.admin_id)
        client.post('/admin/mail', data={
            'domain': 'bbs.a-net.fyi',
            'min_access_level': '100',
            'max_message_size_mb': '25',
            # 'enabled' omitted -- unchecked checkbox
        }, follow_redirects=False)

        with self.app.app_context():
            from anetbbs.models import MailConfig
            cfg = MailConfig.get()
            self.assertFalse(cfg.enabled)


if __name__ == '__main__':
    unittest.main()
