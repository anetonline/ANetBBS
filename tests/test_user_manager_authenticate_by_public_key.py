"""Unit tests for anetbbs/core/user_manager.py's
authenticate_by_public_key() -- the non-cryptographic half of SSH
public-key auth (gap-analysis follow-up round, Phase D). The actual
signature/possession proof is asyncssh's job (see
tests/test_ssh_public_key_auth_e2e.py for that real end-to-end
coverage); this file covers the decision logic this function alone is
responsible for: does the fingerprint match a key registered to this
specific username, and does the account pass the same active/locked/
verified gates authenticate() already applies for password logins.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class AuthenticateByPublicKeyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp_db = str(Path(__file__).resolve().parent / '.auth_pubkey_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'
        os.environ['DATABASE_URL'] = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI

        from anetbbs.web_app import create_app
        from anetbbs.models import db
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()

        from anetbbs.core.user_manager import UserManager
        cls.manager = UserManager()

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)

    def setUp(self):
        self.ctx = self.app.app_context()
        self.ctx.push()
        from anetbbs.models import db, User, UserSSHKey
        db.session.query(UserSSHKey).delete()
        db.session.query(User).delete()
        db.session.commit()

        self.user = User(username='pkuser', email='pkuser@example.com',
                         is_active=True, is_locked=False, is_verified=True)
        self.user.set_password('irrelevant-not-used-by-key-auth')
        db.session.add(self.user)
        db.session.commit()
        db.session.add(UserSSHKey(user_id=self.user.id,
                                  public_key='ssh-ed25519 AAAAfake test',
                                  fingerprint='SHA256:fake-fingerprint-abc',
                                  label='test'))
        db.session.commit()

    def tearDown(self):
        self.ctx.pop()

    def test_matching_fingerprint_and_username_succeeds(self):
        result = self.manager.authenticate_by_public_key(
            'pkuser', 'SHA256:fake-fingerprint-abc')
        self.assertIsNotNone(result)
        self.assertEqual(result['username'], 'pkuser')

    def test_username_is_case_insensitive(self):
        result = self.manager.authenticate_by_public_key(
            'PkUsEr', 'SHA256:fake-fingerprint-abc')
        self.assertIsNotNone(result)

    def test_unregistered_fingerprint_fails(self):
        result = self.manager.authenticate_by_public_key(
            'pkuser', 'SHA256:never-registered')
        self.assertIsNone(result)

    def test_key_registered_to_a_different_user_does_not_authenticate_this_one(self):
        from anetbbs.models import db, User, UserSSHKey
        other = User(username='otheruser', email='other@example.com')
        other.set_password('x')
        db.session.add(other)
        db.session.commit()
        db.session.add(UserSSHKey(user_id=other.id,
                                  public_key='ssh-ed25519 AAAAother',
                                  fingerprint='SHA256:others-key',
                                  label='other'))
        db.session.commit()

        # otheruser's key must not authenticate pkuser
        result = self.manager.authenticate_by_public_key(
            'pkuser', 'SHA256:others-key')
        self.assertIsNone(result)
        # ...but correctly authenticates its real owner
        result2 = self.manager.authenticate_by_public_key(
            'otheruser', 'SHA256:others-key')
        self.assertIsNotNone(result2)

    def test_nonexistent_username_fails(self):
        result = self.manager.authenticate_by_public_key(
            'no-such-user', 'SHA256:fake-fingerprint-abc')
        self.assertIsNone(result)

    def test_inactive_account_is_rejected(self):
        from anetbbs.models import db
        self.user.is_active = False
        db.session.commit()
        result = self.manager.authenticate_by_public_key(
            'pkuser', 'SHA256:fake-fingerprint-abc')
        self.assertIsNone(result)

    def test_locked_account_is_rejected(self):
        from anetbbs.models import db
        self.user.is_locked = True
        db.session.commit()
        result = self.manager.authenticate_by_public_key(
            'pkuser', 'SHA256:fake-fingerprint-abc')
        self.assertIsNone(result)

    def test_unverified_non_admin_account_is_rejected(self):
        from anetbbs.models import db
        self.user.is_verified = False
        db.session.commit()
        result = self.manager.authenticate_by_public_key(
            'pkuser', 'SHA256:fake-fingerprint-abc')
        self.assertIsNone(result)

    def test_unverified_admin_account_still_succeeds(self):
        from anetbbs.models import db
        self.user.is_verified = False
        self.user.is_admin = True
        db.session.commit()
        result = self.manager.authenticate_by_public_key(
            'pkuser', 'SHA256:fake-fingerprint-abc')
        self.assertIsNotNone(result)

    def test_successful_auth_updates_login_bookkeeping(self):
        before = self.user.login_count or 0
        self.manager.authenticate_by_public_key(
            'pkuser', 'SHA256:fake-fingerprint-abc')
        # authenticate_by_public_key() commits through its own raw
        # SQLAlchemy session (see user_manager.py's module docstring --
        # independent of Flask-SQLAlchemy's db.session by design, same
        # as authenticate()). db.session's identity map doesn't know
        # about that commit on its own; expire_all() forces a fresh
        # read instead of serving the stale cached object.
        from anetbbs.models import db, User
        db.session.expire_all()
        refreshed = User.query.filter_by(username='pkuser').first()
        self.assertEqual(refreshed.login_count, before + 1)
        self.assertIsNotNone(refreshed.last_login)


if __name__ == '__main__':
    unittest.main()
