"""Regression test for a login timing side-channel found in a
security/performance audit, in both login paths this app has:

- web/auth.py's login(): `if user is None or not
  user.check_password(...)`. Python's `or` short-circuits, so a
  NONEXISTENT username never reached check_password() (and therefore
  never ran the deliberately-slow password hash verification) at all
  -- a real username with a wrong password took measurably longer to
  respond than a nonexistent one, a distinguishable username-
  enumeration side channel independent of the (already identical)
  error message.
- core/user_manager.py's authenticate() (telnet/SSH/rlogin): the same
  shape, `if user is None: return None` before ever calling
  check_password_hash(), PLUS a second instance of the same bug --
  `if not user.is_active: return None` also short-circuited past the
  hash check, leaking whether an EXISTING username is currently
  deactivated.

Fixed by always running a real hash verification -- against a fixed
dummy hash when there's no real user/password to check against --
so every rejection path pays the same, deliberately-slow cost.

These tests verify the MECHANISM (check_password_hash is actually
called on every path, not skipped) rather than asserting on real
wall-clock timing, which is inherently flaky in CI.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class WebLoginTimingSideChannelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.login_timing_web_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'
        cfg_mod.TestingConfig.WTF_CSRF_ENABLED = False

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        with cls.app.app_context():
            db.create_all()
            u = User(username='timingrealuser', email='timingrealuser@example.com',
                    is_active=True)
            u.set_password('correcthorsebatterystaple')
            db.session.add(u)
            db.session.commit()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def test_nonexistent_username_still_calls_check_password_hash(self):
        from anetbbs.web import auth as auth_mod
        client = self.app.test_client()
        with mock.patch.object(auth_mod, 'check_password_hash',
                               wraps=auth_mod.check_password_hash) as spy:
            resp = client.post('/auth/login', data={
                'username': 'this_username_does_not_exist_at_all',
                'password': 'whatever',
            })
        self.assertEqual(resp.status_code, 302)
        spy.assert_called_once()
        # Must be checked against the fixed dummy hash, not skipped.
        self.assertEqual(spy.call_args[0][0], auth_mod._DUMMY_PASSWORD_HASH)

    def test_existing_username_wrong_password_calls_check_password_hash_once(self):
        """Baseline -- confirms the real-user path still calls the
        real hash check exactly once too (via User.check_password()'s
        own check_password_hash() call), so both paths pay an equal,
        single hash-verification cost."""
        import anetbbs.models as models_mod
        client = self.app.test_client()
        with mock.patch.object(models_mod, 'check_password_hash',
                               wraps=models_mod.check_password_hash) as spy:
            resp = client.post('/auth/login', data={
                'username': 'timingrealuser',
                'password': 'wrong-password',
            })
        self.assertEqual(resp.status_code, 302)
        spy.assert_called_once()

    def test_correct_login_still_works(self):
        """Guard against an over-eager fix breaking the happy path."""
        client = self.app.test_client()
        resp = client.post('/auth/login', data={
            'username': 'timingrealuser',
            'password': 'correcthorsebatterystaple',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b'Invalid username or password', resp.data)


class TerminalLoginTimingSideChannelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.login_timing_terminal_test.db')
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
            u = User(username='terminaltiminguser',
                    email='terminaltiminguser@example.com', is_active=True)
            u.set_password('correcthorsebatterystaple')
            db.session.add(u)
            db.session.commit()

        import anetbbs.core.user_manager as um_mod
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        cls._orig_session = um_mod._Session
        engine = create_engine(f'sqlite:///{cls._tmp_db}', future=True)
        um_mod._Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)

    @classmethod
    def tearDownClass(cls):
        import anetbbs.core.user_manager as um_mod
        um_mod._Session = cls._orig_session
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def test_nonexistent_username_still_calls_check_password_hash(self):
        import anetbbs.core.user_manager as um_mod
        mgr = um_mod.UserManager()
        with mock.patch.object(um_mod, 'check_password_hash',
                               wraps=um_mod.check_password_hash) as spy:
            result = mgr.authenticate('no_such_terminal_user_at_all', 'whatever')
        self.assertIsNone(result)
        spy.assert_called_once()
        self.assertEqual(spy.call_args[0][0], um_mod._DUMMY_PASSWORD_HASH)

    def test_existing_username_wrong_password_calls_check_password_hash(self):
        import anetbbs.core.user_manager as um_mod
        mgr = um_mod.UserManager()
        with mock.patch.object(um_mod, 'check_password_hash',
                               wraps=um_mod.check_password_hash) as spy:
            result = mgr.authenticate('terminaltiminguser', 'wrong-password')
        self.assertIsNone(result)
        spy.assert_called_once()

    def test_correct_login_still_works(self):
        import anetbbs.core.user_manager as um_mod
        mgr = um_mod.UserManager()
        result = mgr.authenticate('terminaltiminguser', 'correcthorsebatterystaple')
        self.assertIsNotNone(result)


if __name__ == '__main__':
    unittest.main()
