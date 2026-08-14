"""Regression test for a real gap found in a security/performance
audit: web_app.py's load_user() (the Flask-Login user_loader -- the
ONLY place current_user gets re-established on every request, for
both the plain session cookie and the "remember me" cookie path) never
checked User.is_active/is_locked. Those columns were only ever
consulted at the MOMENT of a fresh login (web/auth.py's /auth/login
route). A sysop banning (toggle_ban, flips is_active) or locking
(lock_user, flips is_locked) a user via the admin panel had zero
effect on that user's ALREADY-established session -- they could keep
using the site under a session/cookie that predates the ban/lock,
for as long as that cookie remained valid (up to Flask-Login's own
365-day "remember me" default, since REMEMBER_COOKIE_DURATION isn't
configured anywhere in this app).

This test simulates exactly that: inject a session for a user who is
still active/unlocked at the time the session is created (matching a
real prior login), THEN ban/lock them server-side, and confirm the
SAME already-existing session immediately stops being treated as
authenticated on its very next request -- without the client ever
logging out or logging back in.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class BannedLockedUserSessionRevocationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.banned_locked_session_test.db')
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
        BannedLockedUserSessionRevocationTests._counter += 1
        n = BannedLockedUserSessionRevocationTests._counter
        with self.app.app_context():
            u = User(username=f'banlocktest{n}', email=f'banlocktest{n}@example.com',
                    is_active=True, **kwargs)
            u.set_password('x')
            db.session.add(u)
            db.session.commit()
            return u.id

    def _session_client_for(self, user_id):
        """Simulates a session established while the user was still in
        good standing -- matches how a real browser session looks the
        instant before a sysop bans/locks that user."""
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(user_id)
            sess['_fresh'] = True
        return client

    def test_banning_a_user_immediately_revokes_their_existing_session(self):
        from anetbbs.models import db, User
        user_id = self._make_user()
        client = self._session_client_for(user_id)

        # Confirm the pre-existing session actually works before the ban.
        resp = client.get('/messages/')
        self.assertNotEqual(resp.status_code, 401)
        with client.session_transaction() as sess:
            self.assertEqual(sess.get('_user_id'), str(user_id))

        # Sysop bans the user (admin.py's toggle_ban flips is_active).
        with self.app.app_context():
            u = db.session.get(User, user_id)
            u.is_active = False
            db.session.commit()

        # Same client, same cookies, no re-login -- the existing session
        # must no longer be treated as authenticated.
        resp = client.get('/messages/', follow_redirects=False)
        self.assertIn(resp.status_code, (302, 401, 403))
        if resp.status_code == 302:
            self.assertIn('/auth/login', resp.headers.get('Location', ''))

    def test_locking_a_user_immediately_revokes_their_existing_session(self):
        from anetbbs.models import db, User
        user_id = self._make_user()
        client = self._session_client_for(user_id)

        resp = client.get('/messages/')
        self.assertNotEqual(resp.status_code, 401)

        with self.app.app_context():
            u = db.session.get(User, user_id)
            u.is_locked = True
            db.session.commit()

        resp = client.get('/messages/', follow_redirects=False)
        self.assertIn(resp.status_code, (302, 401, 403))
        if resp.status_code == 302:
            self.assertIn('/auth/login', resp.headers.get('Location', ''))

    def test_an_ordinary_active_unlocked_user_session_is_unaffected(self):
        """The fix must not accidentally log everyone out."""
        user_id = self._make_user()
        client = self._session_client_for(user_id)
        resp = client.get('/messages/', follow_redirects=False)
        self.assertNotIn(resp.status_code, (302, 401, 403))

    def test_load_user_returns_none_directly_for_a_locked_user(self):
        """Unit-level check of the actual user_loader, independent of
        which route happens to be gated -- the real regression guard."""
        from anetbbs.web_app import login_manager
        user_id = self._make_user(is_locked=True)
        with self.app.app_context():
            self.assertIsNone(login_manager._user_callback(str(user_id)))

    def test_load_user_returns_none_directly_for_a_banned_inactive_user(self):
        from anetbbs.web_app import login_manager
        from anetbbs.models import db, User
        user_id = self._make_user()
        with self.app.app_context():
            u = db.session.get(User, user_id)
            u.is_active = False
            db.session.commit()
            self.assertIsNone(login_manager._user_callback(str(user_id)))

    def test_load_user_still_returns_the_user_for_a_normal_account(self):
        from anetbbs.web_app import login_manager
        user_id = self._make_user()
        with self.app.app_context():
            u = login_manager._user_callback(str(user_id))
            self.assertIsNotNone(u)
            self.assertEqual(u.id, user_id)


if __name__ == '__main__':
    unittest.main()
