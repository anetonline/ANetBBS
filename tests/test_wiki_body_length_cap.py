"""Regression test: nothing capped a wiki page body's length -- the
only bound was Flask's app-wide MAX_CONTENT_LENGTH (110 MB, sized for
file uploads, a completely different use case). A page body is
user-authored text/markup; even a very long real article runs tens of
KB, so an oversized submission could bloat WikiPage.body (an unbounded
db.Text column) and cost real CPU on every view/preview render
(render_wiki() runs on both). Found in a security/performance audit.

Fixed with a dedicated _WIKI_BODY_MAX_CHARS cap (500,000 chars) applied
in both the real save path (edit()) and the live-preview AJAX endpoint
(preview()) -- matches the existing reject-with-a-flash-message pattern
already used for shoutbox/telegram length caps elsewhere in this
codebase, rather than silently truncating a user's submitted content.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class WikiBodyLengthCapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.wiki_body_cap_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'
        cfg_mod.TestingConfig.WTF_CSRF_ENABLED = False
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

    def _client_as(self, user_id):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(user_id)
            sess['_fresh'] = True
        return client

    def _make_user(self, username, is_admin=True):
        from anetbbs.models import db, User
        with self.app.app_context():
            user = User(username=username, email=f'{username}@example.com',
                       password_hash='x', access_level=100, is_admin=is_admin)
            db.session.add(user)
            db.session.commit()
            return user.id

    def test_oversized_body_is_rejected_not_saved(self):
        from anetbbs.web.wiki import _WIKI_BODY_MAX_CHARS
        from anetbbs.models import WikiPage
        user_id = self._make_user('wikibodycapuser1')
        client = self._client_as(user_id)

        oversized = 'x' * (_WIKI_BODY_MAX_CHARS + 1)
        resp = client.post('/wiki/toolongpage/edit',
                           data={'title': 'Too Long', 'body': oversized,
                                 'edit_summary': ''},
                           follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'too long', resp.data.lower())
        with self.app.app_context():
            page = WikiPage.query.filter_by(slug='toolongpage').first()
            self.assertIsNone(page, 'an oversized body must not be saved at all')

    def test_body_right_at_the_limit_is_accepted(self):
        from anetbbs.web.wiki import _WIKI_BODY_MAX_CHARS
        from anetbbs.models import WikiPage
        user_id = self._make_user('wikibodycapuser2')
        client = self._client_as(user_id)

        exact = 'y' * _WIKI_BODY_MAX_CHARS
        resp = client.post('/wiki/exactlimitpage/edit',
                           data={'title': 'Exact Limit', 'body': exact,
                                 'edit_summary': ''},
                           follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        with self.app.app_context():
            page = WikiPage.query.filter_by(slug='exactlimitpage').first()
            self.assertIsNotNone(page,
                                 'a body exactly at the cap must be accepted')
            self.assertEqual(len(page.body), _WIKI_BODY_MAX_CHARS)

    def test_ordinary_short_body_is_unaffected(self):
        from anetbbs.models import WikiPage
        user_id = self._make_user('wikibodycapuser3')
        client = self._client_as(user_id)

        resp = client.post('/wiki/normalpage/edit',
                           data={'title': 'Normal Page',
                                 'body': 'Just a normal short wiki page.',
                                 'edit_summary': ''},
                           follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        with self.app.app_context():
            page = WikiPage.query.filter_by(slug='normalpage').first()
            self.assertIsNotNone(page)
            self.assertEqual(page.body, 'Just a normal short wiki page.')

    def test_preview_endpoint_also_rejects_an_oversized_body(self):
        from anetbbs.web.wiki import _WIKI_BODY_MAX_CHARS
        user_id = self._make_user('wikibodycapuser4')
        client = self._client_as(user_id)

        oversized = 'z' * (_WIKI_BODY_MAX_CHARS + 1)
        resp = client.post('/wiki/anyslug/preview', data={'body': oversized})
        self.assertEqual(resp.status_code, 413)
        self.assertIn('too long', resp.get_json()['error'].lower())

    def test_preview_endpoint_still_works_for_a_normal_body(self):
        user_id = self._make_user('wikibodycapuser5')
        client = self._client_as(user_id)

        resp = client.post('/wiki/anyslug/preview',
                           data={'body': '**bold text**'})
        self.assertEqual(resp.status_code, 200)
        self.assertIn('html', resp.get_json())


if __name__ == '__main__':
    unittest.main()
