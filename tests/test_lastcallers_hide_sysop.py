"""Tests for the "hide sysop from Last Callers" display option.

Jerry's request: a sysop who logs in multiple times a day can flood
the user-facing Last Callers displays with themselves instead of real
users. LASTCALLERS_HIDE_SYSOP (default off, unchanged behavior) filters
CallerLog rows for a local login by an is_admin user out of every
user-facing display (terminal full view, terminal inline "Last 10
Callers" block, web oneliners page) via the shared
anetbbs.features.lastcallers.recent_callers_query() helper -- but NOT
the admin audit view, which always shows everything.

InterBBS-imported CallerLog rows never carry a real local user_id (see
interbbs_sync.py's sync_lastcallers_inbound), so they must never be
affected by this filter -- there's no local User to check, and a
sysop's own login-hiding preference says nothing about a remote BBS's
callers.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _make_app(db_path):
    import anetbbs.config as cfg_mod
    if os.path.exists(db_path):
        os.remove(db_path)
    cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path}'
    os.environ['FLASK_ENV'] = 'testing'
    from anetbbs.web_app import create_app
    app = create_app('testing')
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    return app


class RecentCallersQueryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import anetbbs.config as cfg_mod
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI

    @classmethod
    def tearDownClass(cls):
        import anetbbs.config as cfg_mod
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _make_app(str(Path(self._tmp.name) / 'lc_hide.db'))
        with self.app.app_context():
            from anetbbs.models import db
            db.create_all()

    def _seed(self):
        from anetbbs.models import db, User, CallerLog
        with self.app.app_context():
            sysop = User(username='stingray', email='stingray@example.com',
                        password_hash='x', is_admin=True)
            regular = User(username='jerry_user', email='ju@example.com',
                           password_hash='x', is_admin=False)
            db.session.add_all([sysop, regular])
            db.session.commit()
            db.session.add_all([
                CallerLog(user_id=sysop.id, username=sysop.username, service='telnet'),
                CallerLog(user_id=sysop.id, username=sysop.username, service='ssh'),
                CallerLog(user_id=regular.id, username=regular.username, service='web'),
                # InterBBS-imported row: no local user_id at all.
                CallerLog(username='RemoteUser', service='ssh',
                          origin_bbs='OtherBBS', remote_msg_id='ABC123'),
            ])
            db.session.commit()

    def test_default_shows_everyone(self):
        from anetbbs.features.lastcallers import recent_callers_query
        self._seed()
        with self.app.app_context():
            self.app.config['LASTCALLERS_HIDE_SYSOP'] = False
            rows = recent_callers_query(50).all()
            self.assertEqual(len(rows), 4)

    def test_hide_sysop_excludes_local_admin_logins(self):
        from anetbbs.features.lastcallers import recent_callers_query
        self._seed()
        with self.app.app_context():
            self.app.config['LASTCALLERS_HIDE_SYSOP'] = True
            rows = recent_callers_query(50).all()
            usernames = {r.username for r in rows}
            self.assertNotIn('stingray', usernames)
            self.assertIn('jerry_user', usernames)
            self.assertEqual(len(rows), 2)

    def test_hide_sysop_never_affects_interbbs_imported_rows(self):
        """No local user_id means no local User to check -- an imported
        row must always survive the filter regardless of the setting."""
        from anetbbs.features.lastcallers import recent_callers_query
        self._seed()
        with self.app.app_context():
            self.app.config['LASTCALLERS_HIDE_SYSOP'] = True
            rows = recent_callers_query(50).all()
            usernames = {r.username for r in rows}
            self.assertIn('RemoteUser', usernames)

    def test_legacy_null_is_admin_is_treated_as_not_admin(self):
        """A migrated/legacy User row with is_admin IS NULL (nullable
        column, no DB-level default) must not get excluded by SQL's
        three-valued NULL logic -- only an explicit True hides a row."""
        from anetbbs.models import db, User, CallerLog
        from anetbbs.features.lastcallers import recent_callers_query
        with self.app.app_context():
            db.create_all()
            legacy = User(username='legacy_user', email='lu@example.com',
                          password_hash='x')
            db.session.add(legacy)
            db.session.commit()
            # Force is_admin to a raw NULL, bypassing the ORM-level default.
            db.session.execute(db.text(
                'UPDATE users SET is_admin = NULL WHERE id = :id'),
                {'id': legacy.id})
            db.session.commit()
            db.session.add(CallerLog(user_id=legacy.id, username='legacy_user',
                                     service='telnet'))
            db.session.commit()

            self.app.config['LASTCALLERS_HIDE_SYSOP'] = True
            rows = recent_callers_query(50).all()
            self.assertIn('legacy_user', {r.username for r in rows})


class LastCallersAdminSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import anetbbs.config as cfg_mod
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI

    @classmethod
    def tearDownClass(cls):
        import anetbbs.config as cfg_mod
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri

    def setUp(self):
        import tempfile
        from unittest.mock import patch
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _make_app(str(Path(self._tmp.name) / 'lc_admin.db'))
        with self.app.app_context():
            from anetbbs.models import db
            db.create_all()
        scratch_env = str(Path(self._tmp.name) / 'scratch.env')
        patcher = patch('anetbbs.web.lastcallers_admin._env_path', return_value=scratch_env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _login_as_admin(self, client):
        from anetbbs.models import db, User
        with self.app.app_context():
            admin = User.query.filter_by(username='admin').first()
            if admin is None:
                admin = User(username='admin', email='admin@example.com', is_admin=True)
                admin.set_password('password123')
                db.session.add(admin)
                db.session.commit()
            admin_id = admin.id
        with client.session_transaction() as sess:
            sess['_user_id'] = str(admin_id)
            sess['_fresh'] = True

    def test_settings_persists_hide_sysop(self):
        client = self.app.test_client()
        self._login_as_admin(client)

        resp = client.post('/admin/lastcallers/settings', data={
            'hide_sysop': 'on',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(self.app.config.get('LASTCALLERS_HIDE_SYSOP'))
        self.assertIsInstance(self.app.config.get('LASTCALLERS_HIDE_SYSOP'), bool)

    def test_settings_unchecked_disables_hide_sysop(self):
        client = self.app.test_client()
        self._login_as_admin(client)

        client.post('/admin/lastcallers/settings', data={'hide_sysop': 'on'})
        self.assertTrue(self.app.config.get('LASTCALLERS_HIDE_SYSOP'))

        client.post('/admin/lastcallers/settings', data={})
        self.assertFalse(self.app.config.get('LASTCALLERS_HIDE_SYSOP'))

    def test_admin_index_shows_everyone_regardless_of_setting(self):
        """The admin audit list is not filtered -- only the user-facing
        displays are."""
        from anetbbs.models import db, User, CallerLog
        with self.app.app_context():
            sysop = User(username='stingray', email='s@example.com',
                        password_hash='x', is_admin=True)
            db.session.add(sysop)
            db.session.commit()
            db.session.add(CallerLog(user_id=sysop.id, username='stingray', service='telnet'))
            db.session.commit()

        client = self.app.test_client()
        self._login_as_admin(client)
        client.post('/admin/lastcallers/settings', data={'hide_sysop': 'on'})

        resp = client.get('/admin/lastcallers/')
        self.assertIn(b'stingray', resp.data)


if __name__ == '__main__':
    unittest.main()
