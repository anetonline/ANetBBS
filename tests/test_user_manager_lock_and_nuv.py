"""Regression tests for real access-control bugs found in a full audit:
anetbbs/core/user_manager.py (the telnet/SSH/rlogin auth backend) never
checked User.is_locked or User.is_verified at all, unlike the web
login route -- a sysop-locked account, or one still awaiting NUV
sysop approval, logged in over the terminal exactly as if nothing
were wrong. create_user() also always created accounts pre-verified
regardless of NUV_ENABLED, letting anyone dial in and self-register
straight past the sysop-approval queue the web path enforces.

user_manager.py keeps its own module-level SQLAlchemy engine/session
factory (resolved once, at first import, independent of Flask) --
these tests rebind that module-level _Session to the test app's own
engine so behavior is verified against a clean, isolated database
regardless of what other test files may have already imported this
module against.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class UserManagerLockAndNuvTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.user_manager_lock_nuv_test.db')
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

            locked = User(username='lockeduser', email='locked@example.com',
                         is_active=True, is_locked=True)
            locked.set_password('correcthorsebatterystaple')
            unverified = User(username='unverifieduser', email='unverif@example.com',
                             is_active=True, is_verified=False)
            unverified.set_password('correcthorsebatterystaple')
            normal = User(username='normaluser', email='normal@example.com',
                         is_active=True)
            normal.set_password('correcthorsebatterystaple')
            admin_unverified = User(username='adminunverified', email='au@example.com',
                                    is_active=True, is_admin=True, is_verified=False)
            admin_unverified.set_password('correcthorsebatterystaple')
            db.session.add_all([locked, unverified, normal, admin_unverified])
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

    def test_locked_account_cannot_authenticate(self):
        from anetbbs.core.user_manager import UserManager
        um = UserManager()
        result = um.authenticate('lockeduser', 'correcthorsebatterystaple')
        self.assertIsNone(result)

    def test_unverified_account_cannot_authenticate(self):
        from anetbbs.core.user_manager import UserManager
        um = UserManager()
        result = um.authenticate('unverifieduser', 'correcthorsebatterystaple')
        self.assertIsNone(result)

    def test_normal_account_can_still_authenticate(self):
        from anetbbs.core.user_manager import UserManager
        um = UserManager()
        result = um.authenticate('normaluser', 'correcthorsebatterystaple')
        self.assertIsNotNone(result)
        self.assertEqual(result['username'], 'normaluser')

    def test_unverified_admin_can_still_authenticate(self):
        """Mirrors web/auth.py's login(): the is_verified gate explicitly
        exempts admins (an admin flipped unverified by a bulk edit must
        not lock the sysop out)."""
        from anetbbs.core.user_manager import UserManager
        um = UserManager()
        result = um.authenticate('adminunverified', 'correcthorsebatterystaple')
        self.assertIsNotNone(result)

    def test_create_user_with_nuv_enabled_starts_unverified_and_is_gated(self):
        from anetbbs.core.user_manager import UserManager
        os.environ['NUV_ENABLED'] = 'true'
        try:
            import importlib
            import anetbbs.config as _cfg
            importlib.reload(_cfg)
            um = UserManager()
            # NUV_ENABLED is read via get_config() inside create_user();
            # patch it directly to avoid relying on process-wide reload
            # timing/caching of the config module.
            import unittest.mock as mock
            with mock.patch('anetbbs.config.get_config') as mock_get_config:
                mock_get_config.return_value.NUV_ENABLED = True
                result = um.create_user('nuvtermuser', 'correcthorsebatterystaple',
                                        'nuvterm@example.com')
            self.assertEqual(result, 'ok_pending')
            # The pending account must not be able to log in yet.
            auth_result = um.authenticate('nuvtermuser', 'correcthorsebatterystaple')
            self.assertIsNone(auth_result)
        finally:
            os.environ.pop('NUV_ENABLED', None)
            # Real test-isolation bug found live (2026-09-01): the
            # importlib.reload(_cfg) above re-evaluates Config's
            # module-level `NUV_ENABLED = os.environ.get(...)` class
            # attribute (computed once at class-body-definition time,
            # not per-request) while the env var above is set to
            # 'true' -- baking True into Config/TestingConfig/
            # ProductionConfig.NUV_ENABLED for the rest of the pytest
            # PROCESS, not just this test. Popping the env var alone
            # doesn't undo that -- the module has to be reloaded AGAIN,
            # now that the env var is gone, to actually restore the
            # class attribute back to its real default (False). Without
            # this, every OTHER test file that runs later in the same
            # process and relies on registration/login working without
            # an NUV approval gate (e.g.
            # test_registration_auto_login_caller_log.py) silently hits
            # "Awaiting Approval" instead.
            import importlib
            importlib.reload(cfg_mod)

    def test_create_user_with_nuv_disabled_is_immediately_usable(self):
        from anetbbs.core.user_manager import UserManager
        import unittest.mock as mock
        um = UserManager()
        with mock.patch('anetbbs.config.get_config') as mock_get_config:
            mock_get_config.return_value.NUV_ENABLED = False
            result = um.create_user('regularnewuser', 'correcthorsebatterystaple',
                                    'regularnew@example.com')
        self.assertEqual(result, 'ok')
        auth_result = um.authenticate('regularnewuser', 'correcthorsebatterystaple')
        self.assertIsNotNone(auth_result)


if __name__ == '__main__':
    unittest.main()
