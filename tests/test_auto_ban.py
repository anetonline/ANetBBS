"""Regression tests for configurable login auto-ban (AutoBanConfig).

See anetbbs/web/auth.py (_auto_ban_ip, the /login rate limiter) and
anetbbs/web/admin.py (ip_bans route, 'auto_ban_settings' action).
"""
import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# create_app()'s bootstrap (default-admin seeding, node-slot dirs, etc.)
# writes a handful of small artifacts under the real project's data/
# directory as a side effect, regardless of SQLALCHEMY_DATABASE_URI --
# they're not test output, just normal first-run bootstrap that happens
# to run against the real DATA_DIR since this test file lives in the
# real tests/ package. Snapshot what's there before creating the app and
# remove only what's new afterward, so running this test never leaves
# stray files in the real project tree (or touches anything that was
# already there).
_DATA_DIR = Path(__file__).resolve().parents[1] / 'data'


def _snapshot_data_dir():
    if not _DATA_DIR.is_dir():
        return set()
    return set(_DATA_DIR.iterdir())


class AutoBanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._data_dir_before = _snapshot_data_dir()

        # In-memory sqlite loses tables across connections without
        # StaticPool; use a scratch file DB instead so it persists across
        # the test client's separate requests (same workaround used
        # elsewhere in this project's manual e2e testing).
        import anetbbs.config as cfg_mod
        cls._dbfile = str(Path(__file__).resolve().parent / '.auto_ban_test.db')
        if os.path.exists(cls._dbfile):
            os.remove(cls._dbfile)
        # TestingConfig is a shared class object across the whole test
        # process -- save the original so tearDownClass can restore it,
        # otherwise every test file that runs after this one (in the same
        # pytest/unittest session) inherits this scratch-file path, which
        # no longer exists once THIS class's tearDown deletes it.
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._dbfile}'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User

        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False

        with cls.app.app_context():
            db.create_all()
            admin = User(username='sysop', email='sysop@example.com', is_admin=True)
            admin.set_password('password123')
            db.session.add(admin)
            db.session.commit()

    @classmethod
    def tearDownClass(cls):
        import anetbbs.config as cfg_mod
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri

        # WAL mode (anetbbs/models.py) creates -wal/-shm companion files
        # alongside the main .db -- removing only the main file left
        # these two behind after every run (an untracked, un-gitignored
        # stray in tests/ that leaked into a release tarball before this
        # was caught -- see build-release.sh's rewrite).
        for suffix in ('', '-wal', '-shm'):
            path = cls._dbfile + suffix
            if os.path.exists(path):
                os.remove(path)
        import shutil
        for entry in _snapshot_data_dir() - cls._data_dir_before:
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)

    def setUp(self):
        from anetbbs.models import db, AutoBanConfig, IpBan
        with self.app.app_context():
            # Reset to defaults before each test so they're independent.
            AutoBanConfig.query.delete()
            IpBan.query.delete()
            db.session.commit()

    def test_defaults_match_feature_request(self):
        from anetbbs.models import AutoBanConfig
        with self.app.app_context():
            cfg = AutoBanConfig.get()
            self.assertTrue(cfg.enabled)
            self.assertEqual(cfg.attempt_limit, 10)
            self.assertEqual(cfg.window_seconds, 300)
            self.assertEqual(cfg.ban_duration_hours, 1)

    def test_disabled_config_makes_auto_ban_a_no_op(self):
        from anetbbs.models import db, AutoBanConfig, IpBan
        from anetbbs.web.auth import _auto_ban_ip
        with self.app.app_context():
            cfg = AutoBanConfig.get()
            cfg.enabled = False
            db.session.commit()
            _auto_ban_ip('192.0.2.1')
            self.assertIsNone(IpBan.query.filter_by(cidr='192.0.2.1').first())

    def test_ban_duration_hours_controls_expiry(self):
        from anetbbs.models import db, AutoBanConfig, IpBan
        from anetbbs.web.auth import _auto_ban_ip
        with self.app.app_context():
            cfg = AutoBanConfig.get()
            cfg.ban_duration_hours = 3
            db.session.commit()
            _auto_ban_ip('192.0.2.2')
            ban = IpBan.query.filter_by(cidr='192.0.2.2').first()
            self.assertIsNotNone(ban)
            self.assertIsNotNone(ban.expires_at)
            delta = ban.expires_at - datetime.utcnow()
            self.assertTrue(timedelta(hours=2, minutes=55) < delta < timedelta(hours=3, minutes=5))

    def test_ban_duration_zero_means_permanent(self):
        from anetbbs.models import db, AutoBanConfig, IpBan
        from anetbbs.web.auth import _auto_ban_ip
        with self.app.app_context():
            cfg = AutoBanConfig.get()
            cfg.ban_duration_hours = 0
            db.session.commit()
            _auto_ban_ip('192.0.2.3')
            ban = IpBan.query.filter_by(cidr='192.0.2.3').first()
            self.assertIsNotNone(ban)
            self.assertIsNone(ban.expires_at)

    def test_ban_reason_reflects_configured_values_not_hardcoded_text(self):
        from anetbbs.models import db, AutoBanConfig, IpBan
        from anetbbs.web.auth import _auto_ban_ip
        with self.app.app_context():
            cfg = AutoBanConfig.get()
            cfg.attempt_limit = 7
            cfg.window_seconds = 180
            db.session.commit()
            _auto_ban_ip('192.0.2.4')
            ban = IpBan.query.filter_by(cidr='192.0.2.4').first()
            self.assertIn('7 attempts', ban.reason)
            self.assertIn('3 min', ban.reason)

    def test_admin_form_updates_config(self):
        client = self.app.test_client()
        client.post('/auth/login', data={'username': 'sysop', 'password': 'password123'})
        client.post('/admin/ip-bans', data={
            'action': 'auto_ban_settings',
            'enabled': 'on',
            'attempt_limit': '5',
            'window_minutes': '10',
            'ban_duration_hours': '6',
        })
        from anetbbs.models import AutoBanConfig
        with self.app.app_context():
            cfg = AutoBanConfig.get()
            self.assertEqual(cfg.attempt_limit, 5)
            self.assertEqual(cfg.window_seconds, 600)
            self.assertEqual(cfg.ban_duration_hours, 6)

    def test_unchecked_checkbox_disables(self):
        client = self.app.test_client()
        client.post('/auth/login', data={'username': 'sysop', 'password': 'password123'})
        client.post('/admin/ip-bans', data={
            'action': 'auto_ban_settings',
            # 'enabled' intentionally omitted -- matches an unchecked checkbox
            'attempt_limit': '5',
            'window_minutes': '10',
            'ban_duration_hours': '6',
        })
        from anetbbs.models import AutoBanConfig
        with self.app.app_context():
            self.assertFalse(AutoBanConfig.get().enabled)

    def test_real_login_route_uses_configured_limit_not_old_hardcoded_ten(self):
        from anetbbs.models import db, AutoBanConfig, IpBan
        with self.app.app_context():
            cfg = AutoBanConfig.get()
            cfg.enabled = True
            cfg.attempt_limit = 2
            cfg.window_seconds = 60
            db.session.commit()

        client = self.app.test_client()
        for _ in range(3):
            client.post('/auth/login',
                        data={'username': 'nope', 'password': 'wrong'})

        with self.app.app_context():
            # The test client's real remote_addr (Werkzeug defaults to
            # 127.0.0.1) must be what gets banned.
            self.assertIsNotNone(IpBan.query.filter_by(cidr='127.0.0.1').first())

    def test_spoofed_x_forwarded_for_is_ignored_by_default(self):
        """SECURITY: found in a full auth-security audit -- _client_ip()
        used to trust a client-supplied X-Forwarded-For header
        unconditionally, so an attacker could make the auto-ban land on
        an arbitrary VICTIM IP instead of their own by spoofing this
        header on the request that trips the rate limit, while their own
        real IP (what the rate-limit bucket is actually keyed on) never
        gets banned at all. TRUST_PROXY_HEADERS defaults to False
        (fail closed) -- the spoofed value must be completely ignored,
        and the ban must land on the real connecting IP instead."""
        from anetbbs.models import db, AutoBanConfig, IpBan
        with self.app.app_context():
            self.assertFalse(self.app.config.get('TRUST_PROXY_HEADERS'),
                             'TRUST_PROXY_HEADERS must default to False')
            cfg = AutoBanConfig.get()
            cfg.enabled = True
            cfg.attempt_limit = 2
            cfg.window_seconds = 60
            db.session.commit()

        client = self.app.test_client()
        for _ in range(3):
            client.post('/auth/login',
                        data={'username': 'nope', 'password': 'wrong'},
                        headers={'X-Forwarded-For': '203.0.113.99'})

        with self.app.app_context():
            self.assertIsNone(
                IpBan.query.filter_by(cidr='203.0.113.99').first(),
                'a spoofed X-Forwarded-For must never be banned in its place')
            self.assertIsNotNone(
                IpBan.query.filter_by(cidr='127.0.0.1').first(),
                'the real connecting IP must be banned instead')


if __name__ == '__main__':
    unittest.main()
