"""Regression test for a real gap found in a security/performance
audit: web_app.py's load_user() (the Flask-Login user_loader -- the
ONLY place current_user gets re-established on every request, for both
the plain session cookie and the "remember me" cookie path) was fixed
once already to recheck User.is_active/is_locked on every request
instead of only at the moment of a fresh login -- see
test_banned_locked_user_session_revocation.py. That same audit pass
never touched a third, structurally identical account-standing flag:
is_verified (the NUV / email-verify gate). web/auth.py's login() route
still only ever consults is_verified at the MOMENT of a fresh login
(with an admin bypass -- `not is_verified and not user.is_admin`); an
admin who un-verifies an already-logged-in user afterwards (admin.py's
edit_user() route can flip is_verified back to False, and the bulk
"un-approve" action does the same) had zero effect on that user's
ALREADY-established session, exactly the same bug shape as the
is_active/is_locked gap.

This test simulates exactly that: inject a session for a user who is
still verified at the time the session is created (matching a real
prior login), THEN un-verify them server-side, and confirm the SAME
already-existing session immediately stops being treated as
authenticated on its very next request -- without the client ever
logging out or logging back in. Also confirms the admin bypass still
holds (an admin account with is_verified=False, however that state
arose, must not be locked out by this fix).
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class LoadUserRechecksIsVerifiedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.load_user_is_verified_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        with cls.app.app_context():
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    _counter = 0

    def _make_user(self, **kwargs):
        from anetbbs.models import db, User
        LoadUserRechecksIsVerifiedTests._counter += 1
        n = LoadUserRechecksIsVerifiedTests._counter
        with self.app.app_context():
            kwargs.setdefault('is_verified', True)
            u = User(username=f'verifytest{n}', email=f'verifytest{n}@example.com',
                    is_active=True, **kwargs)
            u.set_password('x')
            db.session.add(u)
            db.session.commit()
            return u.id

    def _session_client_for(self, user_id):
        """Simulates a session established while the user was still
        verified -- matches how a real browser session looks the
        instant before a sysop revokes their verification."""
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(user_id)
            sess['_fresh'] = True
        return client

    def test_unverifying_a_user_immediately_revokes_their_existing_session(self):
        from anetbbs.models import db, User
        user_id = self._make_user()
        client = self._session_client_for(user_id)

        # Confirm the pre-existing session actually works before the
        # sysop revokes verification.
        resp = client.get('/messages/')
        self.assertNotEqual(resp.status_code, 401)
        with client.session_transaction() as sess:
            self.assertEqual(sess.get('_user_id'), str(user_id))

        # Sysop revokes verification (admin.py's edit_user() flips is_verified).
        with self.app.app_context():
            u = db.session.get(User, user_id)
            u.is_verified = False
            db.session.commit()

        # Same client, same cookies, no re-login -- the existing session
        # must no longer be treated as authenticated.
        resp = client.get('/messages/', follow_redirects=False)
        self.assertIn(resp.status_code, (302, 401, 403))
        if resp.status_code == 302:
            self.assertIn('/auth/login', resp.headers.get('Location', ''))

    def test_an_ordinary_verified_user_session_is_unaffected(self):
        """The fix must not accidentally log everyone out."""
        user_id = self._make_user()
        client = self._session_client_for(user_id)
        resp = client.get('/messages/', follow_redirects=False)
        self.assertNotIn(resp.status_code, (302, 401, 403))

    def test_load_user_returns_none_directly_for_an_unverified_user(self):
        """Unit-level check of the actual user_loader, independent of
        which route happens to be gated -- the real regression guard."""
        from anetbbs.web_app import login_manager
        user_id = self._make_user(is_verified=False)
        with self.app.app_context():
            self.assertIsNone(login_manager._user_callback(str(user_id)))

    def test_load_user_still_returns_an_unverified_admin(self):
        """Mirrors auth.py login()'s own admin bypass (`not is_verified
        and not user.is_admin`) -- an admin account must never be
        lockable via is_verified, however it got set False."""
        from anetbbs.web_app import login_manager
        user_id = self._make_user(is_verified=False, is_admin=True)
        with self.app.app_context():
            u = login_manager._user_callback(str(user_id))
            self.assertIsNotNone(u)
            self.assertEqual(u.id, user_id)

    def test_load_user_still_returns_the_user_for_a_normal_verified_account(self):
        from anetbbs.web_app import login_manager
        user_id = self._make_user()
        with self.app.app_context():
            u = login_manager._user_callback(str(user_id))
            self.assertIsNotNone(u)
            self.assertEqual(u.id, user_id)


if __name__ == '__main__':
    unittest.main()
