"""Regression test for a real Low-severity finding from a security/
performance audit (2026-08-31): admin.py's edit_user() and
manage_user() routes had no self-guard against an admin stripping
their own admin/active/lock status -- unlike delete_user() and
toggle_ban() in the same file, which both already refuse to act on
current_user.id. An admin editing their own account through either
form (e.g. a stray unchecked checkbox on submit) could unwittingly
demote or deactivate themselves with no other admin necessarily around
to undo it.

Fixed by refusing just the self-demoting fields (is_admin/is_active,
plus is_locked for manage_user()) when the target account is the one
making the request, while still applying every other field on the
form normally (own email/display name/access level edits are
unaffected).
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class AdminSelfDemoteGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.admin_self_demote_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _make_admin(self, username):
        from anetbbs.models import db, User
        u = User(username=username, email=f'{username}@example.com',
                is_admin=True, is_active=True)
        u.set_password('adminselftestpass123')
        db.session.add(u)
        db.session.commit()
        return u.id

    def _client_as(self, user_id):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(user_id)
            sess['_fresh'] = True
        return client

    def test_edit_user_cannot_strip_own_admin_flag(self):
        from anetbbs.models import User
        with self.app.app_context():
            admin_id = self._make_admin('selfdemoteedit')

        client = self._client_as(admin_id)
        resp = client.post(f'/admin/users/{admin_id}/edit', data={
            'username': 'selfdemoteedit',
            'email': 'selfdemoteedit@example.com',
            'is_admin': '',       # unchecked -- attempting self-demotion
            'is_active': 'y',
            'new_password': '',
            'confirm_password': '',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            u = User.query.get(admin_id)
            self.assertTrue(u.is_admin,
                            'admin must not be able to strip their own '
                            'admin flag via edit_user()')

    def test_edit_user_cannot_deactivate_self(self):
        from anetbbs.models import User
        with self.app.app_context():
            admin_id = self._make_admin('selfdeactedit')

        client = self._client_as(admin_id)
        resp = client.post(f'/admin/users/{admin_id}/edit', data={
            'username': 'selfdeactedit',
            'email': 'selfdeactedit@example.com',
            'is_admin': 'y',
            'is_active': '',      # unchecked -- attempting self-deactivation
            'new_password': '',
            'confirm_password': '',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            u = User.query.get(admin_id)
            self.assertTrue(u.is_active,
                            'admin must not be able to deactivate '
                            'themselves via edit_user()')

    def test_edit_user_still_applies_other_self_edits(self):
        """The guard must be narrow -- editing your own non-privilege
        fields (email) must still work normally."""
        from anetbbs.models import User
        with self.app.app_context():
            admin_id = self._make_admin('selfeditother')

        client = self._client_as(admin_id)
        client.post(f'/admin/users/{admin_id}/edit', data={
            'username': 'selfeditother',
            'email': 'changed@example.com',
            'is_admin': 'y',
            'is_active': 'y',
            'new_password': '',
            'confirm_password': '',
        }, follow_redirects=True)

        with self.app.app_context():
            u = User.query.get(admin_id)
            self.assertEqual(u.email, 'changed@example.com')

    def test_edit_user_can_still_demote_a_different_admin(self):
        """The guard must be self-only -- an admin editing SOMEONE
        ELSE'S account must still be able to change that account's
        admin/active flags normally."""
        from anetbbs.models import User
        with self.app.app_context():
            admin_id = self._make_admin('selfeditactor')
            other_id = self._make_admin('selfeditvictim')

        client = self._client_as(admin_id)
        client.post(f'/admin/users/{other_id}/edit', data={
            'username': 'selfeditvictim',
            'email': 'selfeditvictim@example.com',
            'is_admin': '',
            'is_active': 'y',
            'new_password': '',
            'confirm_password': '',
        }, follow_redirects=True)

        with self.app.app_context():
            other = User.query.get(other_id)
            self.assertFalse(other.is_admin,
                             'demoting a DIFFERENT admin must still work')

    def test_manage_user_cannot_strip_own_admin_active_or_lock(self):
        from anetbbs.models import User
        with self.app.app_context():
            admin_id = self._make_admin('selfdemotemanage')

        client = self._client_as(admin_id)
        resp = client.post(f'/admin/users/{admin_id}/manage', data={
            'action': 'save',
            'email': 'selfdemotemanage@example.com',
            'access_level': '100',
            # is_admin/is_active omitted (unchecked), is_locked set --
            # all three are self-demoting attempts.
            'is_locked': 'y',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            u = User.query.get(admin_id)
            self.assertTrue(u.is_admin,
                            'admin must not be able to strip their own '
                            'admin flag via manage_user()')
            self.assertTrue(u.is_active,
                            'admin must not be able to deactivate '
                            'themselves via manage_user()')
            self.assertFalse(u.is_locked,
                             'admin must not be able to lock themselves '
                             'out via manage_user()')

    def test_manage_user_still_applies_other_self_edits(self):
        from anetbbs.models import User
        with self.app.app_context():
            admin_id = self._make_admin('selfmanageother')

        client = self._client_as(admin_id)
        client.post(f'/admin/users/{admin_id}/manage', data={
            'action': 'save',
            'email': 'selfmanageother@example.com',
            'access_level': '75',
            'is_admin': 'y',
            'is_active': 'y',
        }, follow_redirects=True)

        with self.app.app_context():
            u = User.query.get(admin_id)
            self.assertEqual(u.access_level, 75)


if __name__ == '__main__':
    unittest.main()
