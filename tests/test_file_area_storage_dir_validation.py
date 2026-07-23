"""Regression tests for a real request: a sysop typing a Storage Path
for a file area that doesn't exist on disk yet used to be saved
silently -- uploads to that area would then fail at upload time with
no earlier warning pointing back to the typo/unmade directory.

Fixed in anetbbs/web/admin.py:
  - file_areas_admin() (single area create/edit) -- a new "Create
    directory if missing" checkbox; unchecked + missing path rejects
    the save (create) or leaves storage_path unchanged (edit, other
    fields still apply) with a warning; checked creates it (mkdir -p).
  - file_areas_bulk_import() -- an optional "storage path base
    directory" field computes <base>/<TAG>/ per imported area, with
    the same create-if-missing checkbox applied to the whole batch.
"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class FileAreaStorageDirValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.file_area_storage_dir_test.db')
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
            admin = User(username='fileareaadmintest', email='faat@example.com',
                        password_hash='x', is_admin=True, access_level=100)
            db.session.add(admin)
            db.session.commit()
            cls.admin_id = admin.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def setUp(self):
        self.work_dir = tempfile.mkdtemp(prefix='filearea_storage_test_')
        self.addCleanup(shutil.rmtree, self.work_dir, ignore_errors=True)

    def _client_as_admin(self):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True
        return client

    # ---- single-area create ----

    def test_create_with_missing_dir_and_box_unchecked_is_rejected(self):
        from anetbbs.models import FileArea
        missing = os.path.join(self.work_dir, 'does_not_exist_yet')

        client = self._client_as_admin()
        resp = client.post('/admin/file-areas', data={
            'action': 'create', 'tag': 'MISSINGDIRTEST', 'name': 'Test',
            'storage_path': missing,
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'does not exist', resp.data)

        with self.app.app_context():
            self.assertIsNone(FileArea.query.filter_by(tag='MISSINGDIRTEST').first(),
                             'the area must not be created with an unconfirmed '
                             'missing directory')
        self.assertFalse(os.path.isdir(missing))

    def test_create_with_missing_dir_and_box_checked_creates_it(self):
        from anetbbs.models import FileArea
        missing = os.path.join(self.work_dir, 'auto_create_me')

        client = self._client_as_admin()
        resp = client.post('/admin/file-areas', data={
            'action': 'create', 'tag': 'AUTOCREATETEST', 'name': 'Test',
            'storage_path': missing, 'create_storage_dir': 'on',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        self.assertTrue(os.path.isdir(missing))
        with self.app.app_context():
            fa = FileArea.query.filter_by(tag='AUTOCREATETEST').first()
            self.assertIsNotNone(fa)
            self.assertEqual(fa.storage_path, missing)

    def test_create_with_already_existing_dir_succeeds_without_checkbox(self):
        from anetbbs.models import FileArea
        existing = os.path.join(self.work_dir, 'already_here')
        os.makedirs(existing)

        client = self._client_as_admin()
        client.post('/admin/file-areas', data={
            'action': 'create', 'tag': 'EXISTINGDIRTEST', 'name': 'Test',
            'storage_path': existing,
        }, follow_redirects=True)

        with self.app.app_context():
            fa = FileArea.query.filter_by(tag='EXISTINGDIRTEST').first()
            self.assertIsNotNone(fa)
            self.assertEqual(fa.storage_path, existing)

    def test_create_with_blank_storage_path_succeeds(self):
        """Sanity check: no path at all (queue-only area) is always fine,
        must not trigger the missing-directory check."""
        from anetbbs.models import FileArea

        client = self._client_as_admin()
        client.post('/admin/file-areas', data={
            'action': 'create', 'tag': 'BLANKPATHTEST', 'name': 'Test',
            'storage_path': '',
        }, follow_redirects=True)

        with self.app.app_context():
            fa = FileArea.query.filter_by(tag='BLANKPATHTEST').first()
            self.assertIsNotNone(fa)
            self.assertIsNone(fa.storage_path)

    # ---- single-area update ----

    def test_update_with_missing_dir_unchecked_leaves_storage_path_unchanged(self):
        from anetbbs.models import db, FileArea
        with self.app.app_context():
            fa = FileArea(tag='UPDATEMISSINGTEST', name='Old Name',
                          storage_path=None, is_active=True)
            db.session.add(fa)
            db.session.commit()
            area_id = fa.id

        missing = os.path.join(self.work_dir, 'update_missing_dir')
        client = self._client_as_admin()
        resp = client.post('/admin/file-areas', data={
            'action': 'update', 'area_id': str(area_id),
            'name': 'New Name', 'storage_path': missing,
            'upload_permission': 'users',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'does not exist', resp.data)

        with self.app.app_context():
            refreshed = FileArea.query.get(area_id)
            self.assertIsNone(refreshed.storage_path,
                             'storage_path must stay unchanged when the new '
                             'value points at a non-existent, unconfirmed dir')
            self.assertEqual(refreshed.name, 'New Name',
                             'other fields on the same submit must still apply')

    def test_update_with_missing_dir_checked_creates_and_applies(self):
        from anetbbs.models import db, FileArea
        with self.app.app_context():
            fa = FileArea(tag='UPDATECREATETEST', name='Old Name',
                          storage_path=None, is_active=True)
            db.session.add(fa)
            db.session.commit()
            area_id = fa.id

        missing = os.path.join(self.work_dir, 'update_create_dir')
        client = self._client_as_admin()
        client.post('/admin/file-areas', data={
            'action': 'update', 'area_id': str(area_id),
            'name': 'New Name', 'storage_path': missing,
            'create_storage_dir': 'on', 'upload_permission': 'users',
        }, follow_redirects=True)

        self.assertTrue(os.path.isdir(missing))
        with self.app.app_context():
            refreshed = FileArea.query.get(area_id)
            self.assertEqual(refreshed.storage_path, missing)

    def test_update_leaving_storage_path_unchanged_does_not_reflag(self):
        """Submitting the SAME (already-valid) storage_path again must
        not spuriously trip the missing-directory check."""
        from anetbbs.models import db, FileArea
        existing = os.path.join(self.work_dir, 'unchanged_dir')
        os.makedirs(existing)
        with self.app.app_context():
            fa = FileArea(tag='UNCHANGEDPATHTEST', name='Old Name',
                          storage_path=existing, is_active=True)
            db.session.add(fa)
            db.session.commit()
            area_id = fa.id

        client = self._client_as_admin()
        resp = client.post('/admin/file-areas', data={
            'action': 'update', 'area_id': str(area_id),
            'name': 'New Name', 'storage_path': existing,
            'upload_permission': 'users',
        }, follow_redirects=True)
        self.assertNotIn(b'does not exist', resp.data)

        with self.app.app_context():
            refreshed = FileArea.query.get(area_id)
            self.assertEqual(refreshed.storage_path, existing)
            self.assertEqual(refreshed.name, 'New Name')

    # ---- bulk import ----

    def test_bulk_import_with_base_dir_and_create_checked_makes_per_tag_dirs(self):
        from anetbbs.models import FileArea
        base = os.path.join(self.work_dir, 'bulk_base')

        client = self._client_as_admin()
        resp = client.post('/admin/file-areas/bulk_import', data={
            'echolist': 'BULKTAG1   Bulk Area One\nBULKTAG2   Bulk Area Two\n',
            'storage_base': base,
            'create_storage_dirs': 'on',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        self.assertTrue(os.path.isdir(os.path.join(base, 'BULKTAG1')))
        self.assertTrue(os.path.isdir(os.path.join(base, 'BULKTAG2')))
        with self.app.app_context():
            a1 = FileArea.query.filter_by(tag='BULKTAG1').first()
            a2 = FileArea.query.filter_by(tag='BULKTAG2').first()
            self.assertEqual(a1.storage_path, os.path.join(base, 'BULKTAG1'))
            self.assertEqual(a2.storage_path, os.path.join(base, 'BULKTAG2'))

    def test_bulk_import_with_base_dir_and_create_unchecked_imports_without_path(self):
        """Areas still get created (bulk import isn't all-or-nothing over
        a directory issue) but without a storage_path, and the sysop is
        told which ones via the flash message."""
        from anetbbs.models import FileArea
        base = os.path.join(self.work_dir, 'bulk_base_no_create')

        client = self._client_as_admin()
        resp = client.post('/admin/file-areas/bulk_import', data={
            'echolist': 'BULKNOCREATE1   Bulk No Create\n',
            'storage_base': base,
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'WITHOUT a storage', resp.data)

        self.assertFalse(os.path.isdir(os.path.join(base, 'BULKNOCREATE1')))
        with self.app.app_context():
            fa = FileArea.query.filter_by(tag='BULKNOCREATE1').first()
            self.assertIsNotNone(fa, 'the area must still be created')
            self.assertIsNone(fa.storage_path)

    def test_bulk_import_with_no_base_dir_behaves_as_before(self):
        from anetbbs.models import FileArea

        client = self._client_as_admin()
        client.post('/admin/file-areas/bulk_import', data={
            'echolist': 'BULKNOBASE1   No Base Dir\n',
        }, follow_redirects=True)

        with self.app.app_context():
            fa = FileArea.query.filter_by(tag='BULKNOBASE1').first()
            self.assertIsNotNone(fa)
            self.assertIsNone(fa.storage_path)


if __name__ == '__main__':
    unittest.main()
