"""Regression tests for the wiki seed sync fix (real bug found live,
2026-07-25): seed_initial_pages() was idempotent-only (only inserts a
page whose slug doesn't already exist), so once a page existed, no
later content fix made to anetbbs/wiki/seed.py's SEED list ever
reached an already-seeded install -- a sysop found stale paths/
service names/ports on their live wiki that had already been fixed in
this repo's SEED content long ago, across several past docs/wiki
accuracy passes.

Fix: sync_unedited=True (now used from web_app.py's startup path)
refreshes only pages the sysop has never personally edited via the
wiki UI, and only when content actually differs -- see
_page_never_manually_edited()'s own docstring for why a human edit is
reliably distinguishable (web/wiki.py's edit() route is
@login_required and always stamps a real author_id; seed/auto-sync
revisions always use author_id=None).
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod

_TEST_SEED = [
    ('quotasynca', 'Sync Test A', 'Original body A'),
    ('quotasyncb', 'Sync Test B', 'Original body B'),
]


class WikiSeedSyncTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.wiki_seed_sync_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _clear_wiki(self):
        from anetbbs.models import db, WikiPage, WikiRevision
        WikiRevision.query.delete()
        WikiPage.query.delete()
        db.session.commit()

    def test_default_call_is_idempotent_no_overwrite(self):
        """Baseline: unchanged default behavior (force=False,
        sync_unedited=False) must still never touch an existing page,
        even if SEED content has since changed."""
        from anetbbs.models import WikiPage
        from anetbbs.wiki.seed import seed_initial_pages

        with self.app.app_context():
            self._clear_wiki()
            with patch('anetbbs.wiki.seed.SEED', _TEST_SEED):
                added = seed_initial_pages()
            self.assertEqual(added, 2)

            changed_seed = [('quotasynca', 'Sync Test A', 'CHANGED body A')] + _TEST_SEED[1:]
            with patch('anetbbs.wiki.seed.SEED', changed_seed):
                added2 = seed_initial_pages()
            self.assertEqual(added2, 0)

            page = WikiPage.query.filter_by(slug='quotasynca').first()
            self.assertEqual(page.body, 'Original body A',
                             'default call must never overwrite existing content')

    def test_sync_unedited_refreshes_untouched_page(self):
        from anetbbs.models import WikiPage
        from anetbbs.wiki.seed import seed_initial_pages

        with self.app.app_context():
            self._clear_wiki()
            with patch('anetbbs.wiki.seed.SEED', _TEST_SEED):
                seed_initial_pages()

            changed_seed = [('quotasynca', 'Sync Test A', 'CHANGED body A')] + _TEST_SEED[1:]
            with patch('anetbbs.wiki.seed.SEED', changed_seed):
                added = seed_initial_pages(sync_unedited=True)
            self.assertEqual(added, 1, 'only the changed, never-edited page should sync')

            page = WikiPage.query.filter_by(slug='quotasynca').first()
            self.assertEqual(page.body, 'CHANGED body A')

    def test_sync_unedited_never_touches_a_page_a_sysop_edited(self):
        """The core safety guarantee: a real human edit (author_id
        set, matching web/wiki.py's _save_revision(..., current_user))
        must permanently opt a page out of auto-sync."""
        from anetbbs.models import db, WikiPage, WikiRevision, User
        from anetbbs.wiki.seed import seed_initial_pages

        with self.app.app_context():
            self._clear_wiki()
            with patch('anetbbs.wiki.seed.SEED', _TEST_SEED):
                seed_initial_pages()

            sysop = User(username='wikisyncsysop', email='wss@example.com',
                        password_hash='x', is_admin=True, access_level=100)
            db.session.add(sysop)
            db.session.commit()

            page = WikiPage.query.filter_by(slug='quotasynca').first()
            page.body = 'Sysop customized this page'
            db.session.add(WikiRevision(
                page_id=page.id, rev_num=2, title=page.title,
                body='Sysop customized this page',
                edit_summary='sysop edit', author_id=sysop.id))
            db.session.commit()

            changed_seed = [('quotasynca', 'Sync Test A', 'CHANGED body A')] + _TEST_SEED[1:]
            with patch('anetbbs.wiki.seed.SEED', changed_seed):
                added = seed_initial_pages(sync_unedited=True)
            self.assertEqual(added, 0,
                             'a sysop-edited page must never be auto-synced')

            refreshed = WikiPage.query.filter_by(slug='quotasynca').first()
            self.assertEqual(refreshed.body, 'Sysop customized this page')

    def test_sync_unedited_is_noop_when_content_already_matches(self):
        from anetbbs.models import WikiRevision
        from anetbbs.wiki.seed import seed_initial_pages

        with self.app.app_context():
            self._clear_wiki()
            with patch('anetbbs.wiki.seed.SEED', _TEST_SEED):
                seed_initial_pages()

            with patch('anetbbs.wiki.seed.SEED', _TEST_SEED):
                added = seed_initial_pages(sync_unedited=True)
            self.assertEqual(added, 0)

            from anetbbs.models import WikiPage
            page = WikiPage.query.filter_by(slug='quotasynca').first()
            rev_count = WikiRevision.query.filter_by(page_id=page.id).count()
            self.assertEqual(rev_count, 1,
                             'no-op sync must not create a pointless revision')

    def test_force_still_overwrites_even_an_edited_page(self):
        """force=True keeps its original, stronger (unsafe-for-
        automation) semantics -- unchanged behavior, still available."""
        from anetbbs.models import db, WikiPage, WikiRevision, User
        from anetbbs.wiki.seed import seed_initial_pages

        with self.app.app_context():
            self._clear_wiki()
            with patch('anetbbs.wiki.seed.SEED', _TEST_SEED):
                seed_initial_pages()

            sysop = User(username='wikiforcesysop', email='wfs@example.com',
                        password_hash='x', is_admin=True, access_level=100)
            db.session.add(sysop)
            db.session.commit()
            page = WikiPage.query.filter_by(slug='quotasynca').first()
            db.session.add(WikiRevision(
                page_id=page.id, rev_num=2, title=page.title,
                body='Sysop customized this page', edit_summary='sysop edit',
                author_id=sysop.id))
            db.session.commit()

            changed_seed = [('quotasynca', 'Sync Test A', 'FORCED body A')] + _TEST_SEED[1:]
            with patch('anetbbs.wiki.seed.SEED', changed_seed):
                seed_initial_pages(force=True)

            refreshed = WikiPage.query.filter_by(slug='quotasynca').first()
            self.assertEqual(refreshed.body, 'FORCED body A')


if __name__ == '__main__':
    unittest.main()
