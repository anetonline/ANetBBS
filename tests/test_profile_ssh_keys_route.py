"""Regression test for /profile/ssh-keys (anetbbs/web/profile.py's
ssh_keys() route), the web UI for registering/revoking SSH public
keys for password-free SSH login (gap-analysis follow-up round,
Phase D).
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncssh

import anetbbs.config as cfg_mod


class ProfileSshKeysRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp_db = str(Path(__file__).resolve().parent / '.profile_sshkeys_test.db')
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
            if not User.query.filter_by(username='sshkeyweb').first():
                u = User(username='sshkeyweb', email='sshkeyweb@example.com')
                u.set_password('testpass123')
                db.session.add(u)
                db.session.commit()

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)

    def setUp(self):
        from anetbbs.models import db, UserSSHKey, User
        with self.app.app_context():
            user = User.query.filter_by(username='sshkeyweb').first()
            UserSSHKey.query.filter_by(user_id=user.id).delete()
            db.session.commit()
        self.client = self.app.test_client()
        self.client.post('/auth/login',
                         data={'username': 'sshkeyweb', 'password': 'testpass123'},
                         follow_redirects=True)

    def _gen_pub_key(self):
        key = asyncssh.generate_private_key('ssh-ed25519')
        pub = key.export_public_key().decode()
        fingerprint = asyncssh.import_public_key(pub).get_fingerprint()
        return pub, fingerprint

    def test_add_valid_key_registers_it(self):
        pub, fp = self._gen_pub_key()
        resp = self.client.post('/profile/ssh-keys',
                                data={'action': 'add', 'public_key': pub, 'label': 'laptop'},
                                follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        from anetbbs.models import UserSSHKey
        with self.app.app_context():
            row = UserSSHKey.query.filter_by(fingerprint=fp).first()
            self.assertIsNotNone(row)
            self.assertEqual(row.label, 'laptop')

    def test_invalid_key_text_is_rejected(self):
        resp = self.client.post('/profile/ssh-keys',
                                data={'action': 'add', 'public_key': 'not a real key at all'},
                                follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        # Scoped to this test's own user -- other test methods in this
        # class register keys for OTHER users (sshkeyweb_other/
        # sshkeyweb_victim) that setUp() deliberately doesn't touch, so
        # a global UserSSHKey.query.count() would be test-order-
        # dependent instead of actually checking what this test cares
        # about (real bug caught writing this file).
        from anetbbs.models import UserSSHKey, User
        with self.app.app_context():
            user = User.query.filter_by(username='sshkeyweb').first()
            self.assertEqual(UserSSHKey.query.filter_by(user_id=user.id).count(), 0)

    def test_duplicate_key_is_rejected_not_re_added(self):
        pub, fp = self._gen_pub_key()
        self.client.post('/profile/ssh-keys',
                         data={'action': 'add', 'public_key': pub}, follow_redirects=True)
        self.client.post('/profile/ssh-keys',
                         data={'action': 'add', 'public_key': pub}, follow_redirects=True)
        from anetbbs.models import UserSSHKey
        with self.app.app_context():
            self.assertEqual(UserSSHKey.query.filter_by(fingerprint=fp).count(), 1)

    def test_key_registered_to_another_user_cannot_be_re_registered(self):
        """A key someone else already registered must not silently
        transfer/duplicate to a second account -- confirmed via the
        route's own generic rejection message, and confirmed here that
        no second UserSSHKey row for the same fingerprint is created."""
        from anetbbs.models import db, User, UserSSHKey
        pub, fp = self._gen_pub_key()
        with self.app.app_context():
            other = User.query.filter_by(username='sshkeyweb_other').first()
            if other is None:
                other = User(username='sshkeyweb_other', email='other@example.com')
                other.set_password('x')
                db.session.add(other)
                db.session.commit()
            db.session.add(UserSSHKey(user_id=other.id, public_key=pub, fingerprint=fp))
            db.session.commit()

        resp = self.client.post('/profile/ssh-keys',
                                data={'action': 'add', 'public_key': pub}, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        with self.app.app_context():
            self.assertEqual(UserSSHKey.query.filter_by(fingerprint=fp).count(), 1)

    def test_delete_removes_only_the_owning_users_key(self):
        pub, fp = self._gen_pub_key()
        self.client.post('/profile/ssh-keys',
                         data={'action': 'add', 'public_key': pub}, follow_redirects=True)
        from anetbbs.models import UserSSHKey
        with self.app.app_context():
            key_id = UserSSHKey.query.filter_by(fingerprint=fp).first().id

        self.client.post('/profile/ssh-keys', data={'action': 'delete', 'key_id': key_id})
        with self.app.app_context():
            self.assertIsNone(UserSSHKey.query.get(key_id))

    def test_cannot_delete_another_users_key(self):
        from anetbbs.models import db, User, UserSSHKey
        pub, fp = self._gen_pub_key()
        with self.app.app_context():
            other = User.query.filter_by(username='sshkeyweb_victim').first()
            if other is None:
                other = User(username='sshkeyweb_victim', email='victim@example.com')
                other.set_password('x')
                db.session.add(other)
                db.session.commit()
            row = UserSSHKey(user_id=other.id, public_key=pub, fingerprint=fp)
            db.session.add(row)
            db.session.commit()
            other_key_id = row.id

        self.client.post('/profile/ssh-keys', data={'action': 'delete', 'key_id': other_key_id})
        with self.app.app_context():
            self.assertIsNotNone(UserSSHKey.query.get(other_key_id),
                                 "a user must not be able to delete another user's key")

    def test_ssh_keys_page_requires_login(self):
        anon = self.app.test_client()
        resp = anon.get('/profile/ssh-keys', follow_redirects=False)
        self.assertIn(resp.status_code, (302, 401, 403))


if __name__ == '__main__':
    unittest.main()
