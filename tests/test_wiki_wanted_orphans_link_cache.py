"""Regression test for a perf issue found in a broader web-UI audit:
wanted()/orphans() (anetbbs/web/wiki.py) used to call
extract_outgoing_links() -- a regex scan that first strips fenced/inline
code -- over EVERY live page's full body, from scratch, on every single
visit to either page. Uncached, so it got slower as the wiki grew, with
no traffic-driven reason to ever skip the work.

Fixed by caching each page's outgoing-link set (WikiPage.outgoing_links_
cache, a JSON list) whenever its body is saved -- in web/wiki.py's
_save_revision() (covers the edit/new/revert/rename routes) and in
wiki/seed.py (covers seeded/synced pages, which set .body directly).
Rows that predate the column self-heal lazily on first view.

This test confirms: (1) wanted()/orphans() still find the right pages
via the real HTTP routes, (2) the cache actually gets populated on save
instead of staying NULL, and (3) editing a page to remove a link updates
the cache (no staleness).
"""
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class WikiWantedOrphansLinkCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.wiki_link_cache_test.db')
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

    def test_save_populates_cache_and_wanted_orphans_stay_correct(self):
        from anetbbs.models import db, User, WikiPage

        with self.app.app_context():
            user = User(username='wikilinkcacheuser', email='wlc@example.com',
                       password_hash='x', access_level=100, is_admin=True)
            db.session.add(user)
            db.session.commit()
            user_id = user.id

        client = self._client_as(user_id)

        # Create "home" (linked to by nothing else, but explicitly
        # exempted from orphans by slug) and a page A that links to a
        # not-yet-created page B and to itself-unrelated page A again.
        resp = client.post('/wiki/home/edit', data={
            'title': 'Home', 'body': 'Welcome. See [[Page A]].',
            'edit_summary': 'init',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        resp = client.post('/wiki/page-a/edit', data={
            'title': 'Page A', 'body': 'Links to [[Page B]] which does not exist yet.',
            'edit_summary': 'init',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            home = WikiPage.query.filter_by(slug='home').first()
            page_a = WikiPage.query.filter_by(slug='page-a').first()
            self.assertIsNotNone(home.outgoing_links_cache)
            self.assertIsNotNone(page_a.outgoing_links_cache)
            self.assertEqual(json.loads(home.outgoing_links_cache), ['page-a'])
            self.assertEqual(json.loads(page_a.outgoing_links_cache), ['page-b'])

        # wanted(): page-b is linked but doesn't exist.
        resp = client.get('/wiki/wanted')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'page-b', resp.data)

        # orphans(): page-a IS linked from home, so it must NOT be
        # listed as an orphan.
        resp = client.get('/wiki/orphans')
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b'Page A', resp.data)

        # Now create page-b and remove the link from page-a -- the
        # cache must update (not go stale), so page-b should now show
        # up as an orphan (nothing links to it) and wanted() should no
        # longer list it.
        resp = client.post('/wiki/page-b/edit', data={
            'title': 'Page B', 'body': 'Standalone page, nothing links out.',
            'edit_summary': 'init',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        resp = client.post('/wiki/page-a/edit', data={
            'title': 'Page A', 'body': 'No longer links anywhere.',
            'edit_summary': 'remove link',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            page_a = WikiPage.query.filter_by(slug='page-a').first()
            self.assertEqual(json.loads(page_a.outgoing_links_cache), [])

        resp = client.get('/wiki/wanted')
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b'page-b', resp.data)

        resp = client.get('/wiki/orphans')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Page B', resp.data)

    def test_null_cache_self_heals_on_view(self):
        """A row saved before this column existed (simulated by forcing
        the cache back to NULL) must still produce correct wanted()/
        orphans() output, and get its cache backfilled in the process."""
        from anetbbs.models import db, User, WikiPage

        with self.app.app_context():
            user = User(username='wikiheal', email='wikiheal@example.com',
                       password_hash='x', access_level=100, is_admin=True)
            db.session.add(user)
            db.session.commit()
            user_id = user.id

        client = self._client_as(user_id)
        resp = client.post('/wiki/heal-test/edit', data={
            'title': 'Heal Test', 'body': 'Links to [[Heal Target]].',
            'edit_summary': 'init',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            page = WikiPage.query.filter_by(slug='heal-test').first()
            page.outgoing_links_cache = None
            db.session.commit()
            self.assertIsNone(
                WikiPage.query.filter_by(slug='heal-test').first().outgoing_links_cache)

        resp = client.get('/wiki/wanted')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'heal-target', resp.data)

        with self.app.app_context():
            page = WikiPage.query.filter_by(slug='heal-test').first()
            self.assertIsNotNone(page.outgoing_links_cache)
            self.assertEqual(json.loads(page.outgoing_links_cache), ['heal-target'])


if __name__ == '__main__':
    unittest.main()
