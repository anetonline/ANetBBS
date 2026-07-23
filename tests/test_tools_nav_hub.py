"""Regression tests for the Tools navbar reorganization: a real
screenshot showed the "Tools" dropdown had grown to 24 flat items
("it was sooooo long it was crazy... it is kinda long now too"),
mirroring a complaint the Admin dropdown already had (see
ADMIN_HUB_SECTIONS / admin.hub() in anetbbs/web/admin.py). Applied the
identical fix: category landing pages instead of one flat list.

anetbbs/web/main.py's TOOLS_HUB_SECTIONS / tools_hub() replaces the
old flat dropdown with 5 category links (Community, Network Directory,
Content, My Stuff, Info & Help), each rendering a card grid at
/tools/<section>. Per-item auth gating from the old dropdown (17 of
24 items were only shown to logged-in users) is preserved via each
tool tuple's trailing auth_required flag, filtered in the route.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class ToolsNavHubTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.tools_nav_hub_test.db')
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
            user = User(username='tester', email='tester@example.com',
                       password_hash='x', access_level=100)
            db.session.add(user)
            db.session.commit()
            cls.user_id = user.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _client_as(self, user_id=None):
        client = self.app.test_client()
        if user_id is not None:
            with client.session_transaction() as sess:
                sess['_user_id'] = str(user_id)
                sess['_fresh'] = True
        return client

    def test_all_five_sections_load_for_guest(self):
        client = self._client_as()
        for section in ('community', 'directory', 'content', 'personal', 'info'):
            resp = client.get(f'/tools/{section}')
            self.assertEqual(resp.status_code, 200, section)

    def test_all_five_sections_load_for_authenticated_user(self):
        client = self._client_as(self.user_id)
        for section in ('community', 'directory', 'content', 'personal', 'info'):
            resp = client.get(f'/tools/{section}')
            self.assertEqual(resp.status_code, 200, section)

    def test_unknown_section_404s(self):
        client = self._client_as()
        resp = client.get('/tools/bogus')
        self.assertEqual(resp.status_code, 404)

    def test_guest_only_sees_the_seven_previously_guest_visible_tools(self):
        """Old dropdown: 7 items lived outside the
        `{% if current_user.is_authenticated %}` block (Nodelist,
        Statistics, Tour, Documentation, Wiki, Ask Anet, About) -- the
        rest (17) were auth-only. Same split must hold post-reorg."""
        client = self._client_as()
        counts = {}
        for section in ('community', 'directory', 'content', 'personal', 'info'):
            resp = client.get(f'/tools/{section}')
            counts[section] = resp.get_data(as_text=True).count('card-title')
        self.assertEqual(counts['community'], 0)
        self.assertEqual(counts['content'], 0)
        self.assertEqual(counts['personal'], 0)
        self.assertEqual(counts['directory'], 2)   # Nodelist, Statistics
        self.assertEqual(counts['info'], 5)        # Tour, Docs, Wiki, Ask Anet, About
        self.assertIn('Log in to see tools here',
                      client.get('/tools/community').get_data(as_text=True))

    def test_authenticated_user_sees_all_24_tools_across_sections(self):
        client = self._client_as(self.user_id)
        total = 0
        for section in ('community', 'directory', 'content', 'personal', 'info'):
            resp = client.get(f'/tools/{section}')
            total += resp.get_data(as_text=True).count('card-title')
        # 23 without network join (REGISTRY_MODE_ENABLED off by default
        # in tests) -- Join Our Network only appears when the hub's
        # network-join form is actually turned on, bringing it to 24.
        self.assertEqual(total, 23)

    def test_bbs_history_card_resolves_its_slug_kwarg(self):
        """BBS History is the one tool needing a url_for() kwarg
        (site_pages.view, slug='history') -- the old flat-tuple hub.html
        pattern (borrowed from admin/hub.html) doesn't support kwargs at
        all, so this exercises the extended (endpoint, ..., kwargs)
        tuple shape that main.py's TOOLS_HUB_SECTIONS uses instead."""
        client = self._client_as(self.user_id)
        resp = client.get('/tools/content')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('/page/history', resp.get_data(as_text=True))

    def test_navbar_renders_five_category_links_not_the_old_flat_list(self):
        client = self._client_as(self.user_id)
        resp = client.get('/')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        for section in ('community', 'directory', 'content', 'personal', 'info'):
            self.assertIn(f'/tools/{section}', body)


if __name__ == '__main__':
    unittest.main()
