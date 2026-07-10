"""Regression tests for the Ask Anet guru door's retrieval layer
(anetbbs/guru/fts.py, aliases.py, search.py), added 2026-07-10.

Covers: the FTS5 index gets created and populated at startup, search
finds real wiki pages by direct word and via the alias table, soft-deleted
pages are excluded, and the sync triggers actually fire on wiki edits.
"""
import os
import shutil
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_DATA_DIR = Path(__file__).resolve().parents[1] / 'data'


def _snapshot_data_dir():
    if not _DATA_DIR.is_dir():
        return set()
    return set(_DATA_DIR.iterdir())


def _fresh_app(db_path):
    import anetbbs.config as cfg_mod
    if os.path.exists(db_path):
        os.remove(db_path)
    cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path}'
    os.environ['FLASK_ENV'] = 'testing'
    from anetbbs.web_app import create_app
    app = create_app('testing')
    app.config['TESTING'] = True
    return app


class GuruSearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._data_dir_before = _snapshot_data_dir()
        import anetbbs.config as cfg_mod
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._orig_flask_env = os.environ.get('FLASK_ENV')

    @classmethod
    def tearDownClass(cls):
        import anetbbs.config as cfg_mod
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        if cls._orig_flask_env is None:
            os.environ.pop('FLASK_ENV', None)
        else:
            os.environ['FLASK_ENV'] = cls._orig_flask_env
        for entry in _snapshot_data_dir() - cls._data_dir_before:
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _app(self, name):
        return _fresh_app(str(Path(self._tmp.name) / name))

    def test_fts_index_created_and_populated(self):
        app = self._app('a.db')
        from anetbbs.models import db, WikiPage
        with app.app_context():
            total = WikiPage.query.filter_by(is_deleted=False).count()
            self.assertGreater(total, 0)
            indexed = db.session.execute(
                db.text('SELECT count(*) FROM wiki_pages_fts')).scalar()
            self.assertEqual(indexed, total)

    def test_rebuild_actually_indexes_pre_existing_rows_on_upgrade(self):
        # Regression test for a real bug found live on the Pi3 test
        # server: simulate an "upgrading install" where wiki_pages
        # already had rows *before* the guru FTS5 table/triggers ever
        # existed (i.e. every real ANetBBS install prior to this
        # feature shipping). Drop the index build create_app() already
        # did, then re-run ensure_fts_index() exactly as a fresh
        # process startup would -- this is the exact scenario where the
        # old row-count-comparison heuristic permanently failed to
        # trigger a real rebuild (count(*) on an external-content FTS5
        # table passes through to the content table's count regardless
        # of whether the search index was ever populated), even though
        # non-MATCH reads of the table looked completely normal.
        app = self._app('rebuild.db')
        from anetbbs.models import db
        from anetbbs.guru.fts import ensure_fts_index
        from anetbbs.guru.search import search
        with app.app_context():
            db.session.execute(db.text('DROP TABLE wiki_pages_fts'))
            db.session.execute(db.text('DROP TRIGGER wiki_pages_fts_ai'))
            db.session.execute(db.text('DROP TRIGGER wiki_pages_fts_ad'))
            db.session.execute(db.text('DROP TRIGGER wiki_pages_fts_au'))
            db.session.commit()

            ensure_fts_index(db.engine, db.text, logger=None)

            results = search('where can I view netmail')
        self.assertTrue(any(r['slug'] == 'netmail' for r in results))

    def test_search_finds_netmail_page_by_direct_word(self):
        app = self._app('b.db')
        from anetbbs.guru.search import search
        with app.app_context():
            results = search('where can I view netmail')
        self.assertTrue(any(r['slug'] == 'netmail' for r in results))

    def test_search_finds_notifications_via_alias(self):
        app = self._app('c.db')
        from anetbbs.guru.search import search
        with app.app_context():
            results = search('where do I see my notifications')
        self.assertTrue(any(r['slug'] == 'notifications' for r in results))

    def test_search_finds_chat_via_alias(self):
        app = self._app('d.db')
        from anetbbs.guru.search import search
        with app.app_context():
            results = search('how do I chat with people')
        self.assertTrue(any(r['slug'] == 'chat' for r in results))

    def test_search_empty_question_returns_empty_list(self):
        app = self._app('e.db')
        from anetbbs.guru.search import search
        with app.app_context():
            self.assertEqual(search(''), [])
            self.assertEqual(search('   '), [])

    def test_search_excludes_soft_deleted_pages(self):
        app = self._app('f.db')
        from anetbbs.models import db, WikiPage
        from anetbbs.guru.search import search
        with app.app_context():
            page = WikiPage.query.filter_by(slug='netmail').first()
            self.assertIsNotNone(page)
            page.is_deleted = True
            db.session.commit()
            results = search('where can I view netmail')
        self.assertFalse(any(r['slug'] == 'netmail' for r in results))

    def test_index_resyncs_after_wiki_edit(self):
        app = self._app('g.db')
        from anetbbs.models import db, WikiPage
        from anetbbs.guru.search import search
        with app.app_context():
            page = WikiPage.query.filter_by(slug='netmail').first()
            self.assertIsNotNone(page)
            page.body = 'zzqqxxyybrandnewtoken only, nothing else relevant here'
            db.session.commit()
            hits_new = search('zzqqxxyybrandnewtoken')
            self.assertTrue(any(r['slug'] == 'netmail' for r in hits_new))

    def test_view_count_bump_does_not_touch_index(self):
        app = self._app('h.db')
        from anetbbs.models import db, WikiPage
        with app.app_context():
            before = db.session.execute(db.text(
                "SELECT wiki_pages_fts.body FROM wiki_pages_fts JOIN wiki_pages wp "
                "ON wp.id = wiki_pages_fts.rowid WHERE wp.slug = 'netmail'"
            )).scalar()
            page = WikiPage.query.filter_by(slug='netmail').first()
            page.view_count += 1
            db.session.commit()
            after = db.session.execute(db.text(
                "SELECT wiki_pages_fts.body FROM wiki_pages_fts JOIN wiki_pages wp "
                "ON wp.id = wiki_pages_fts.rowid WHERE wp.slug = 'netmail'"
            )).scalar()
        self.assertEqual(before, after)


if __name__ == '__main__':
    unittest.main()
