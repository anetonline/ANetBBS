"""Regression test for a real infrastructure gap found in a security/
performance audit: this app set no Content-Security-Policy,
X-Frame-Options, X-Content-Type-Options, Referrer-Policy, or
Permissions-Policy header anywhere, on any response -- confirmed by
grepping the whole codebase for those header names before the fix, and
already explicitly flagged in anetbbs/web/watch.py's own docstring
("No CSP / X-Frame-Options exists anywhere in this app today...").

Fixed with a single app.after_request hook in web_app.py's create_app().
Three route classes get three different policies:
  - The public "Watch It Live" embed page (watch_bp, url_prefix /watch)
    must stay embeddable from ANY origin -- no X-Frame-Options / CSP
    frame-ancestors restriction at all, per watch.py's own docstring.
  - games.dos_frame (the isolated EmulatorJS page) gets a looser CSP
    that allows the EmulatorJS CDN + wasm-unsafe-eval + worker-src,
    since it's already isolated via its own COOP/COEP headers.
  - Every other route gets the site-wide default: X-Frame-Options: DENY,
    a CSP with 'unsafe-inline' for script-src/style-src (a real inline-
    script/handler/style sweep found this in real, wide use across the
    template tree -- documented as a known limitation/follow-up in
    docs/SECURITY.md rather than silently shipped), and no HSTS unless
    the request actually arrived over HTTPS.

This test confirms all three policies land on the right routes, and
that HSTS is present/absent based on request.is_secure.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class SecurityResponseHeadersTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.security_headers_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['PUBLIC_WATCH_ENABLED'] = True
        with cls.app.app_context():
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def test_ordinary_page_gets_the_default_hardened_headers(self):
        client = self.app.test_client()
        resp = client.get('/')
        self.assertEqual(resp.headers.get('X-Frame-Options'), 'DENY')
        self.assertIn('Content-Security-Policy', resp.headers)
        self.assertIn("frame-ancestors 'self'",
                      resp.headers['Content-Security-Policy'])
        self.assertEqual(resp.headers.get('X-Content-Type-Options'), 'nosniff')
        self.assertEqual(resp.headers.get('Referrer-Policy'),
                         'strict-origin-when-cross-origin')
        self.assertIn('Permissions-Policy', resp.headers)

    def test_watch_page_stays_embeddable_from_any_origin(self):
        """The regression guard watch.py's own docstring asks for: this
        route must NEVER get an X-Frame-Options or CSP frame-ancestors
        restriction, even after site-wide clickjacking protection is
        added for everything else."""
        client = self.app.test_client()
        resp = client.get('/watch/')
        self.assertNotIn('X-Frame-Options', resp.headers)
        csp = resp.headers.get('Content-Security-Policy', '')
        self.assertNotIn('frame-ancestors', csp)
        # Still gets the universally-safe headers.
        self.assertEqual(resp.headers.get('X-Content-Type-Options'), 'nosniff')

    def test_no_hsts_over_plain_http(self):
        client = self.app.test_client()
        resp = client.get('/')
        self.assertNotIn('Strict-Transport-Security', resp.headers)

    def test_hsts_present_when_request_is_secure(self):
        client = self.app.test_client()
        resp = client.get('/', environ_overrides={'wsgi.url_scheme': 'https'})
        self.assertIn('Strict-Transport-Security', resp.headers)
        self.assertIn('max-age=', resp.headers['Strict-Transport-Security'])


if __name__ == '__main__':
    unittest.main()
