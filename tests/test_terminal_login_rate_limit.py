"""Regression test for a real gap found in a full auth-security audit:
UserManager.authenticate() (anetbbs/core/user_manager.py -- the shared
backend for telnet/SSH/rlogin/PETSCII login, called from every
core/session.py login path) had ZERO rate-limiting, IP-ban check, or
lockout of any kind, unlike web/auth.py's /login route (protected by
AutoBanConfig + IpBan + a sliding-window rate limiter). A client could
retry username/password combinations against the terminal forever with
no delay, counter, or lockout -- worse on SSH specifically, since
asyncssh's own validate_password() always returns True by design (to
capture the password for the prefill flow), so even the SSH transport
layer itself never rejected an attempt.

Fixed by adding an optional `ip` parameter to authenticate() that
checks/enforces the SAME AutoBanConfig/IpBan/IpWhitelist models
web/auth.py already uses, and wiring core/session.py's four call sites
to pass the connecting peer's IP.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class TerminalLoginRateLimitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.terminal_login_ratelimit_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()
            u = User(username='termratelimituser', email='trl@example.com',
                     is_active=True)
            u.set_password('correcthorsebatterystaple')
            db.session.add(u)
            db.session.commit()

        import anetbbs.core.user_manager as um_mod
        from sqlalchemy.orm import sessionmaker
        with cls.app.app_context():
            test_engine = db.engine
        cls._orig_um_session = um_mod._Session
        um_mod._Session = sessionmaker(bind=test_engine, future=True, expire_on_commit=False)
        cls.um_mod = um_mod

    @classmethod
    def tearDownClass(cls):
        cls.um_mod._Session = cls._orig_um_session
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def setUp(self):
        # features/rate_limit.py's bucket store is a module-level dict
        # shared across the whole process -- clear it between tests so
        # one test's attempts don't bleed into another's bucket.
        from anetbbs.features.rate_limit import _buckets
        _buckets.clear()

    def _clear_ip_state(self):
        from anetbbs.models import db, IpBan, AutoBanConfig
        with self.app.app_context():
            IpBan.query.delete()
            cfg = AutoBanConfig.get()
            cfg.enabled = True
            cfg.attempt_limit = 3
            cfg.window_seconds = 60
            cfg.ban_duration_hours = 1
            db.session.commit()

    def test_repeated_failed_attempts_trip_the_rate_limit_and_ban(self):
        from anetbbs.models import IpBan
        self._clear_ip_state()
        um = self.um_mod.UserManager()

        results = []
        for _ in range(5):
            results.append(um.authenticate('termratelimituser', 'wrongpassword',
                                           ip='198.51.100.201'))

        self.assertTrue(all(r is None for r in results),
                        'every attempt with a wrong password must fail')
        with self.app.app_context():
            self.assertIsNotNone(
                IpBan.query.filter_by(cidr='198.51.100.201').first(),
                'repeated failed attempts must trip the auto-ban, same as '
                'the web login route')

    def test_banned_ip_is_rejected_even_with_the_correct_password(self):
        from anetbbs.models import db, IpBan
        self._clear_ip_state()
        with self.app.app_context():
            db.session.add(IpBan(cidr='203.0.113.201', reason='test ban'))
            db.session.commit()

        um = self.um_mod.UserManager()
        result = um.authenticate('termratelimituser', 'correcthorsebatterystaple',
                                 ip='203.0.113.201')
        self.assertIsNone(result,
                          'a banned IP must be rejected even with valid '
                          'credentials -- checked before the password at all')

    def test_correct_password_from_a_fresh_ip_still_works(self):
        """Baseline / guard against a too-broad fix."""
        self._clear_ip_state()
        um = self.um_mod.UserManager()
        result = um.authenticate('termratelimituser', 'correcthorsebatterystaple',
                                 ip='192.0.2.201')
        self.assertIsNotNone(result)
        self.assertEqual(result['username'], 'termratelimituser')

    def test_auto_ban_disabled_never_blocks(self):
        from anetbbs.models import db, AutoBanConfig, IpBan
        with self.app.app_context():
            IpBan.query.delete()
            cfg = AutoBanConfig.get()
            cfg.enabled = False
            db.session.commit()

        um = self.um_mod.UserManager()
        for _ in range(10):
            um.authenticate('termratelimituser', 'wrongpassword', ip='198.51.100.202')

        with self.app.app_context():
            self.assertIsNone(
                IpBan.query.filter_by(cidr='198.51.100.202').first(),
                'auto-ban disabled must mean no ban regardless of attempt count')

    def test_no_ip_provided_skips_the_check_entirely(self):
        """Best-effort: a caller that can't resolve a peer address must
        not lose the ability to authenticate at all."""
        self._clear_ip_state()
        um = self.um_mod.UserManager()
        result = um.authenticate('termratelimituser', 'correcthorsebatterystaple',
                                 ip=None)
        self.assertIsNotNone(result)


if __name__ == '__main__':
    unittest.main()
