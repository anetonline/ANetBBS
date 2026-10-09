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
        from anetbbs.models import db, User
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['PUBLIC_WATCH_ENABLED'] = True
        with cls.app.app_context():
            db.create_all()
            u = User(username='csptester', email='csptester@example.com',
                     is_active=True)
            u.set_password('password12345')
            db.session.add(u)
            db.session.commit()
            cls.user_id = u.id

    def _logged_in_client(self):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(self.user_id)
            sess['_fresh'] = True
        return client

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

    def test_default_csp_allows_blob_image_sources(self):
        """Real bug found live: the web ANSI editor's Image Import and
        Reference Image trace mode both preview a user-selected local
        file via URL.createObjectURL()/<img src="blob:...">. Confirmed
        via a real headless-browser run that the site-wide CSP's
        img-src (missing `blob:`) silently blocked that image load --
        im.onload simply never fired, no visible error, so the import
        appeared to do nothing. A blob: URL can only ever reference a
        Blob the page's own script created, never attacker-controlled
        input, so this is safe to allow site-wide."""
        client = self.app.test_client()
        resp = client.get('/')
        csp = resp.headers.get('Content-Security-Policy', '')
        img_src = next((d for d in csp.split(';') if d.strip().startswith('img-src')), '')
        self.assertIn('blob:', img_src)

    def test_dos_frame_csp_allows_the_emulatorjs_cdn_in_every_directive(self):
        """Real live bug (sysop browser console, 2026-10-09): every OTHER
        directive here allowed https://cdn.emulatorjs.org, but style-src
        didn't -- EmulatorJS loads its own stylesheet from there, and the
        blocked load cascaded into its "minified files missing" fallback
        path, which then crashed outright, breaking DOOM/Duke3D entirely
        with nothing logged server-side (a pure client-side CSP block
        never reaches Flask's access log, which is exactly why this
        slipped by unnoticed -- the games.dos_frame docstring this file's
        own module docstring already references had no actual test
        covering it). Checks every directive generically instead of just
        style-src, so the same gap in any OTHER directive would also be
        caught, not just a repeat of this one specific bug."""
        from anetbbs.models import db, Game
        with self.app.app_context():
            game = Game.query.filter_by(slug='doom').first()
            if game is None:
                game = Game.query.filter_by(game_type='door_dos_browser').first()
            if game is None:
                self.skipTest('no door_dos_browser game seeded (doom.zip/'
                             'duke3d.zip missing from data/dos-games/) -- '
                             'nothing to test the CSP against')
            slug = game.slug
            if not game.is_active:
                game.is_active = True
                db.session.commit()

        client = self._logged_in_client()
        resp = client.get(f'/games/dos-frame/{slug}')
        self.assertEqual(resp.status_code, 200)
        csp = resp.headers.get('Content-Security-Policy', '')
        self.assertIn('cdn.emulatorjs.org', csp)
        for directive in ('script-src', 'style-src', 'worker-src',
                          'connect-src', 'img-src'):
            rule = next((d.strip() for d in csp.split(';')
                        if d.strip().startswith(directive)), None)
            self.assertIsNotNone(rule, f'{directive} missing from dos_frame CSP entirely')
            self.assertIn('https://cdn.emulatorjs.org', rule,
                          f'{directive} does not allow the EmulatorJS CDN: {rule!r}')
        # Also still isolated via COOP/COEP, same as the module docstring
        # describes -- a quick sanity check this test is on the right page.
        self.assertEqual(resp.headers.get('Cross-Origin-Opener-Policy'), 'same-origin')
        self.assertEqual(resp.headers.get('Cross-Origin-Embedder-Policy'), 'credentialless')

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
