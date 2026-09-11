"""Regression test for a real Critical XSS bug found in a security/
performance audit: templates/gemini/view.html renders each gemtext
"=> <target> [label]" link line straight into `<a href="{{ target }}">`
with no scheme validation at all -- unlike every other user-content
link renderer in this codebase (web/render_msg.py's _linkify(), which
only ever linkifies https?://, and rss/poller.py's
_is_safe_http_url(), which closed this exact same class of bug for RSS
feed links). A GeminiCapsule is self-published by any registered user
with no sysop review, so a line like
"=> javascript:alert(document.cookie) Click me" rendered as a real
clickable same-origin link before the fix.

Fixed by anetbbs.web.gemini._sanitize_gemtext_links(), applied to the
content before it reaches the HTML view template.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class GeminiGemtextLinkXssTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.gemini_xss_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User, GeminiCapsule
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        with cls.app.app_context():
            db.create_all()

            user = User(username='gemxsstestuser', email='gxt@example.com',
                       password_hash='x', is_admin=False, access_level=100)
            db.session.add(user)
            db.session.commit()

            cap = GeminiCapsule(
                user_id=user.id,
                title="XSS test capsule",
                content=(
                    '=> javascript:alert(document.cookie) Click me\n'
                    '=> https://example.com/ A real safe link\n'
                    '=> /wiki/home A relative link\n'
                ),
                is_published=True,
            )
            db.session.add(cap)
            db.session.commit()
            cls.username = user.username

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def test_javascript_uri_never_appears_as_an_href(self):
        client = self.app.test_client()
        resp = client.get(f'/gemini/{self.username}')
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b'href="javascript:', resp.data,
                         'a javascript: URI must never be rendered as a '
                         'clickable href on a published gemini capsule')

    def test_safe_https_link_still_renders_as_a_clickable_link(self):
        client = self.app.test_client()
        resp = client.get(f'/gemini/{self.username}')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'href="https://example.com/"', resp.data)

    def test_relative_link_still_renders_as_a_clickable_link(self):
        client = self.app.test_client()
        resp = client.get(f'/gemini/{self.username}')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'href="/wiki/home"', resp.data)

    def test_raw_gemtext_endpoint_is_untouched(self):
        """raw_gemtext() serves text/gemini, never HTML-rendered by a
        browser -- the original unsanitized content is fine there."""
        client = self.app.test_client()
        resp = client.get(f'/gemini/{self.username}.gmi')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'=> javascript:alert(document.cookie) Click me',
                      resp.data)


if __name__ == '__main__':
    unittest.main()
