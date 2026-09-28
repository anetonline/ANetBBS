"""Regression test for a real bug reported live 2026-09-29: any
@login_required page (not just the new web landing "matrix" mod's
terminal link) dumped a not-yet-logged-in visitor back on the home
page after login instead of returning them to where they were
originally headed.

Root cause: the login FORM (templates/auth/login.html) posted to a
plain `/login` with no query string, so a `next` value that arrived on
the initial GET (e.g. Flask-Login's own @login_required redirect,
`?next=/terminal/`) never survived to the actual POST -- by the time
the form was submitted, `request.args.get('next')` on that POST was
always empty, and web/auth.py's login() silently fell back to the home
page every time.

Fixed by carrying `next` through as a hidden form field (only rendered
when one was actually present on the GET) and checking
`request.form.get('next')` as a fallback in the login route.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class LoginNextRedirectPreservedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent
                          / '.login_next_redirect_test.db')
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
            u = User(username='nextredirecttest', email='nrt@example.com',
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

    def test_login_page_renders_hidden_next_field_when_present(self):
        client = self.app.test_client()
        resp = client.get('/auth/login?next=/terminal/')
        html = resp.get_data(as_text=True)
        self.assertIn('name="next"', html)
        self.assertIn('value="/terminal/"', html)

    def test_login_page_omits_next_field_when_absent(self):
        client = self.app.test_client()
        resp = client.get('/auth/login')
        html = resp.get_data(as_text=True)
        self.assertNotIn('name="next"', html)

    def test_real_login_required_flow_returns_to_originally_requested_page(self):
        """The actual end-to-end flow: hit a @login_required page while
        logged out (e.g. /terminal/), get redirected to login with
        ?next=..., submit the login FORM (not a raw query-string POST --
        this is the part that was actually broken), and land back on
        the originally requested page, not the home page."""
        client = self.app.test_client()
        redirect_resp = client.get('/terminal/', follow_redirects=False)
        self.assertEqual(redirect_resp.status_code, 302)
        location = redirect_resp.headers['Location']
        self.assertIn('next=', location)
        self.assertIn('login', location)

        login_get = client.get(location)
        html = login_get.get_data(as_text=True)
        self.assertIn('name="next"', html)

        post_resp = client.post('/auth/login', data={
            'username': 'nextredirecttest',
            'password': 'correcthorsebatterystaple',
            'next': '/terminal/',
        }, follow_redirects=False)
        self.assertEqual(post_resp.status_code, 302)
        self.assertEqual(post_resp.headers['Location'], '/terminal/',
                         'expected to return to /terminal/, the real bug '
                         'sent every login back to the home page instead')

    def test_login_without_next_still_goes_home(self):
        client = self.app.test_client()
        resp = client.post('/auth/login', data={
            'username': 'nextredirecttest',
            'password': 'correcthorsebatterystaple',
        }, follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers['Location'], '/')

    def test_unsafe_next_is_still_rejected(self):
        # The open-redirect guard must still apply even though `next`
        # now also arrives via the form body, not just the query string.
        client = self.app.test_client()
        resp = client.post('/auth/login', data={
            'username': 'nextredirecttest',
            'password': 'correcthorsebatterystaple',
            'next': '//evil.example.com/phish',
        }, follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers['Location'], '/')


if __name__ == '__main__':
    unittest.main()
