"""Regression test: AFK_WARNING_SECONDS must be a real, working entry
in Admin -> Settings (EDITABLE_SETTINGS in anetbbs/web/admin.py) --
Jerry's ask after the feature initially only supported .env editing:
"couldn't we have it in the admin panel somewhere instead?" Follows the
exact same requires_restart=True pattern as its sibling setting,
IDLE_TIMEOUT_SECONDS (both are read the identical way, in the identical
anetbbs/core/session.py Session.start() function, via os.environ.get()
-- not routed through Flask's current_app.config, so a live in-place
patch on save wouldn't actually take effect for either one; a restart
is genuinely needed for a NEW value to reach a newly-connecting
session).

Reuses the exact test setup/pattern from
test_admin_settings_restart_message.py.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class AdminAFKWarningSettingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.admin_afk_setting_test.db')
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
            admin = User(username='afksettingsadmin', email='afksettingsadmin@example.com',
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

    def test_afk_warning_seconds_appears_on_the_settings_page(self):
        client = self._client()
        resp = client.get('/admin/settings')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn('AFK_WARNING_SECONDS', body)
        self.assertIn('AFK warning', body)

    def test_saving_a_value_writes_it_and_flags_restart_required(self):
        client = self._client()
        resp = client.post('/admin/settings', data={'AFK_WARNING_SECONDS': '300'},
                           follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn('sudo systemctl restart anetbbs.service', body)

        # The saved value round-trips back onto the form.
        resp2 = client.get('/admin/settings')
        self.assertIn('300', resp2.get_data(as_text=True))


if __name__ == '__main__':
    unittest.main()
