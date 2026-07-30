"""Regression tests for the v1.0.0 GA cutover's changelog split:
docs/CHANGELOG.md had grown past 6,000 lines covering every internal
beta build back to v1.0a1.1. Jerry asked whether it's common practice
to archive that history into its own file when cutting a stable
release rather than let it keep growing forever -- it is, so the
beta-era entries moved to docs/CHANGELOG-beta.md, leaving
docs/CHANGELOG.md to start fresh from v1.0.0 onward.

anetbbs/web/docs.py's `/docs/<slug>` route special-cases the
'CHANGELOG' slug for entry-count pagination (see _split_changelog) --
this needed generalizing to also cover the new 'CHANGELOG-beta' slug,
since the archive is just as long as the original was and would hit
the exact same slow-single-page-render problem the pagination feature
was originally built to fix.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class ChangelogBetaArchiveSplitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.changelog_beta_split_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        with cls.app.app_context():
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def test_current_changelog_loads_and_shows_v1_0_0(self):
        client = self.app.test_client()
        resp = client.get('/docs/CHANGELOG')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn('v1.0.0', body)

    def test_current_changelog_links_to_the_archive(self):
        client = self.app.test_client()
        resp = client.get('/docs/CHANGELOG')
        body = resp.get_data(as_text=True)
        self.assertIn('CHANGELOG-beta', body)

    def test_archive_loads_and_is_paginated(self):
        client = self.app.test_client()
        resp = client.get('/docs/CHANGELOG-beta')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        # Old beta entries live here now, not in the trimmed current file.
        self.assertIn('v1.0b2.239', body)
        # A single page shouldn't dump all ~200+ version entries at once.
        page2 = client.get('/docs/CHANGELOG-beta?page=2')
        self.assertEqual(page2.status_code, 200)
        self.assertNotEqual(resp.get_data(), page2.get_data())

    def test_docs_index_lists_both_changelog_files(self):
        client = self.app.test_client()
        resp = client.get('/docs/')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn('/docs/CHANGELOG"', body)
        self.assertIn('/docs/CHANGELOG-beta"', body)


if __name__ == '__main__':
    unittest.main()
