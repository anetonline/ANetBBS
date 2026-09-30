"""Regression tests for the web ANSI editor's file-browser (Open from
disk / Save to disk) added toward ANetDRAW feature parity -- new routes
in anetbbs/web/ansi_editor.py (browse, save_to_disk, open_from_disk)
backed by a new ANSI_EDITOR_BROWSE_DIRS config setting.

Covers the one real security concern called out in the plan for this
feature: path-traversal safety on the configured browse root, both at
the pure-function level (_safe_join) and through the real routes (a
crafted filename must not escape the configured directory).
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class SafeJoinTests(unittest.TestCase):
    """Pure function, no app/DB needed."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = os.path.realpath(self._tmp.name)

    def test_plain_filename_resolves_inside_root(self):
        from anetbbs.web.ansi_editor import _safe_join
        result = _safe_join(self.root, 'matrix.ans')
        self.assertEqual(result, os.path.join(self.root, 'matrix.ans'))

    def test_dotdot_traversal_is_rejected(self):
        from anetbbs.web.ansi_editor import _safe_join
        result = _safe_join(self.root, '../../../../etc/passwd')
        self.assertIsNone(result)

    def test_absolute_path_is_treated_as_relative_not_a_full_escape(self):
        # os.path.join would normally discard root_abs entirely when
        # the second argument looks absolute -- _safe_join must strip
        # the leading slash first so this can't happen.
        from anetbbs.web.ansi_editor import _safe_join
        result = _safe_join(self.root, '/etc/passwd')
        self.assertIsNotNone(result)
        self.assertTrue(result.startswith(self.root + os.sep))

    def test_null_byte_is_rejected(self):
        from anetbbs.web.ansi_editor import _safe_join
        result = _safe_join(self.root, 'ok.ans\x00.txt')
        self.assertIsNone(result)

    def test_empty_filename_is_rejected(self):
        from anetbbs.web.ansi_editor import _safe_join
        self.assertIsNone(_safe_join(self.root, ''))

    def test_subdirectory_traversal_via_symlink_style_dotdot_is_rejected(self):
        from anetbbs.web.ansi_editor import _safe_join
        result = _safe_join(self.root, 'sub/../../outside.ans')
        self.assertIsNone(result)


class BrowseRootsParsingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _app_with_config(self, value):
        # ANSI_EDITOR_BROWSE_DIRS (like DOWNLOADS_DIR/FILE_BULLETINS_DIR
        # elsewhere in this codebase) is computed from os.environ ONCE
        # at Config class-body execution time (module import), so
        # setting os.environ afterward has no effect -- override
        # app.config directly on the created app instance instead,
        # matching test_downloads_sha256_sidecar_chaining.py's own
        # established pattern for this exact class of setting.
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = 'sqlite://'
        os.environ['FLASK_ENV'] = 'testing'
        from anetbbs.web_app import create_app
        app = create_app('testing')
        app.config['ANSI_EDITOR_BROWSE_DIRS'] = value
        return app

    def test_parses_label_path_pairs_and_creates_dirs(self):
        p1 = os.path.join(self._tmp.name, 'a')
        p2 = os.path.join(self._tmp.name, 'b', 'c')
        app = self._app_with_config(f'text:{p1};mods/txt:{p2}')
        from anetbbs.web.ansi_editor import _browse_roots
        with app.app_context():
            roots = _browse_roots()
        labels = [r[0] for r in roots]
        self.assertIn('text', labels)
        self.assertIn('mods/txt', labels)
        self.assertTrue(os.path.isdir(p1))
        self.assertTrue(os.path.isdir(p2))

    def test_malformed_entries_are_skipped_not_fatal(self):
        app = self._app_with_config('not-a-valid-entry;;text:' + self._tmp.name)
        from anetbbs.web.ansi_editor import _browse_roots
        with app.app_context():
            roots = _browse_roots()
        self.assertEqual(len(roots), 1)
        self.assertEqual(roots[0][0], 'text')


class DiskBrowserRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.ansi_disk_browser_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        cls._browse_tmp = tempfile.TemporaryDirectory()
        cls._browse_dir = os.path.join(cls._browse_tmp.name, 'text')

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User, AnsiArt
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        # See BrowseRootsParsingTests._app_with_config's comment --
        # this must be set on app.config directly, not os.environ.
        cls.app.config['ANSI_EDITOR_BROWSE_DIRS'] = f'text:{cls._browse_dir}'
        with cls.app.app_context():
            db.create_all()
            admin = User(username='ansidisktest', email='adt@example.com',
                        password_hash='x', access_level=100, is_admin=True)
            db.session.add(admin)
            art = AnsiArt(
                name='Disk Test Art', slug='disk-test-art', width=80, height=25,
                grid_json=json.dumps({'width': 80, 'height': 25, 'cells': [
                    {'c': 'X', 'fg': 15, 'bg': 1}]}),
                ansi_text='TEST ANSI CONTENT')
            db.session.add(art)
            db.session.commit()
            cls.admin_id = admin.id
            cls.art_id = art.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        cls._browse_tmp.cleanup()
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _admin_client(self):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True
        return client

    def test_save_to_disk_writes_a_real_file(self):
        client = self._admin_client()
        resp = client.post(f'/admin/ansi/{self.art_id}/save-to-disk',
                           data={'root': 'text', 'filename': 'roundtrip.ans'},
                           follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        dest = os.path.join(self._browse_dir, 'roundtrip.ans')
        self.assertTrue(os.path.isfile(dest))
        with open(dest, encoding='cp437') as f:
            self.assertEqual(f.read(), 'TEST ANSI CONTENT')

    def test_save_to_disk_adds_ans_extension_if_missing(self):
        client = self._admin_client()
        client.post(f'/admin/ansi/{self.art_id}/save-to-disk',
                   data={'root': 'text', 'filename': 'noext'})
        self.assertTrue(os.path.isfile(os.path.join(self._browse_dir, 'noext.ans')))

    def test_save_to_disk_rejects_traversal_filename(self):
        client = self._admin_client()
        outside_target = os.path.join(self._browse_tmp.name, 'escaped.ans')
        client.post(f'/admin/ansi/{self.art_id}/save-to-disk',
                   data={'root': 'text', 'filename': '../escaped.ans'})
        self.assertFalse(os.path.isfile(outside_target))

    def test_open_from_disk_creates_new_art_with_matching_content(self):
        # Write a real .ans directly (bypassing the route) to open it.
        src = os.path.join(self._browse_dir, 'preexisting.ans')
        with open(src, 'w', encoding='cp437') as f:
            f.write('hello from disk')

        client = self._admin_client()
        resp = client.post('/admin/ansi/open-from-disk',
                           data={'root': 'text', 'filename': 'preexisting.ans'},
                           follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        from anetbbs.models import AnsiArt
        with self.app.app_context():
            art = AnsiArt.query.filter_by(name='preexisting').first()
            self.assertIsNotNone(art)
            self.assertIn('hello from disk', art.ansi_text)

    def test_open_from_disk_rejects_nonexistent_file(self):
        client = self._admin_client()
        resp = client.post('/admin/ansi/open-from-disk',
                           data={'root': 'text', 'filename': 'does-not-exist.ans'},
                           follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        from anetbbs.models import AnsiArt
        with self.app.app_context():
            self.assertIsNone(AnsiArt.query.filter_by(name='does-not-exist').first())

    def test_browse_page_lists_saved_files(self):
        client = self._admin_client()
        client.post(f'/admin/ansi/{self.art_id}/save-to-disk',
                   data={'root': 'text', 'filename': 'listed.ans'})
        resp = client.get('/admin/ansi/browse')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'listed.ans', resp.data)


if __name__ == '__main__':
    unittest.main()
