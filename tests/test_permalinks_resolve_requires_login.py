"""Regression test for a real Low-severity finding from a security/
performance audit (2026-08-31): permalinks.py's resolve() (`/m/<slug>`)
had no @login_required at all, unlike get_link() (the route that
mints a slug) in the same file. An entirely anonymous visitor with a
leaked or guessed slug could distinguish "this permalink resolves to
something real" (a 302 redirect) from "never existed" (404) for a PM
or netmail permalink with zero identity or rate-limit exposure.

Fixed by requiring login on resolve(), the same way every other route
touching potentially-private content in this app does. This doesn't
fully close the narrower same-account "exists but isn't yours"
distinction (the target route's own 403 is still distinguishable from
a 404) -- see the route's own comment for why that's an accepted
residual gap rather than duplicating each target module's full
ownership logic here.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class PermalinksResolveRequiresLoginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.permalinks_login_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User, MessageSlug
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        with cls.app.app_context():
            db.create_all()
            user = User(username='permalinktester', email='plt@example.com',
                       password_hash='x', is_admin=False, access_level=100)
            db.session.add(user)
            db.session.commit()
            cls.user_id = user.id

            slug_row = MessageSlug(slug='abc123', kind='pm', target_id=999999,
                                   created_by_id=user.id)
            db.session.add(slug_row)
            db.session.commit()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _client_as(self, user_id):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(user_id)
            sess['_fresh'] = True
        return client

    def test_anonymous_request_is_redirected_to_login_not_resolved(self):
        client = self.app.test_client()  # no session -- anonymous
        resp = client.get('/m/abc123', follow_redirects=False)
        self.assertEqual(
            resp.status_code, 302,
            'an anonymous visitor must be redirected to login, not '
            f'have the slug resolved -- got {resp.status_code}')
        self.assertIn('/login', resp.headers.get('Location', ''))

    def test_anonymous_request_gets_the_same_redirect_for_a_real_or_fake_slug(self):
        """The actual oracle-closing behavior: an anonymous visitor
        must not be able to tell a REAL slug apart from a made-up one
        -- both must produce the identical login-redirect, not a 302
        for one and a 404 for the other."""
        client = self.app.test_client()
        real = client.get('/m/abc123', follow_redirects=False)
        fake = client.get('/m/zzzzzz', follow_redirects=False)
        self.assertEqual(real.status_code, fake.status_code)
        self.assertEqual(
            real.headers.get('Location', '').split('?')[0],
            fake.headers.get('Location', '').split('?')[0],
            'both must redirect to the same login page, not leak which '
            'slug is real via a different response')

    def test_authenticated_request_still_resolves_a_real_slug(self):
        client = self._client_as(self.user_id)
        # resolve() itself always 302s for a valid slug (on to the
        # target route -- pm.read here) regardless of whether that
        # downstream route will ultimately succeed; what matters is
        # WHERE it redirects. target_id 999999 doesn't correspond to a
        # real PrivateMessage, so following further would 404
        # downstream (pm.read's own get_or_404) -- that's a separate
        # concern from resolve() itself no longer blocking a logged-in
        # user with a login redirect.
        resp = client.get('/m/abc123', follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertNotIn('/login', resp.headers.get('Location', ''),
                         'a logged-in user must not be redirected to '
                         'login for a real slug')
        self.assertIn('/messages/', resp.headers.get('Location', ''),
                      'must redirect toward the real pm.read target')


if __name__ == '__main__':
    unittest.main()
