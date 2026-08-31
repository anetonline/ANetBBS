"""Regression test for a real Low-severity finding from a security/
performance audit (2026-08-31): several fail-open except blocks in
file_areas.py swallowed exceptions with no logging at all -- unlike
the file's own established convention elsewhere (the ratio-check gap,
fixed in an earlier audit round, has this exact same "fails open but
logs" comment). A real bug in any of these would silently stop
tracking ratio counters, stop scanning uploads for malware/corrupt
archives, or hide a broken thumbnail pipeline with zero trace.

Fixed:
  - download()'s and upload()'s ratio-counter bump (added in an
    earlier fix this same audit round) now log on failure.
  - smart_upload()'s own virus-scan/archive-integrity blocks (a
    near-duplicate of upload()'s, which already logged) now log too.
  - thumbnail()'s catch-all now logs at debug level (not a visible
    level by default, since this path is also hit routinely by the
    -- expected, not a bug -- oversized-image rejection for any large
    legitimate photo).
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class FileAreasSilentExceptionLoggingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.file_areas_silent_exc_test.db')
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
            user = User(username='silentexctester', email='set@example.com',
                       password_hash='x', is_admin=False, access_level=100)
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

    def setUp(self):
        self.work_dir = tempfile.mkdtemp(prefix='silent_exc_')
        self.addCleanup(__import__('shutil').rmtree, self.work_dir, ignore_errors=True)

    def _client_as(self, user_id):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(user_id)
            sess['_fresh'] = True
        return client

    def _make_area(self, tag, storage_path):
        from anetbbs.models import db, FileArea
        with self.app.app_context():
            area = FileArea(tag=tag, name=tag, storage_path=storage_path,
                           is_active=True, min_access_level=0)
            db.session.add(area)
            db.session.commit()
            return area.id

    def test_download_ratio_bump_crash_is_logged(self):
        from anetbbs.web import file_areas

        storage_dir = os.path.join(self.work_dir, 'storage')
        os.makedirs(storage_dir)
        with open(os.path.join(storage_dir, 'file.txt'), 'wb') as f:
            f.write(b'content')
        area_id = self._make_area('SILENTDL', storage_dir)

        client = self._client_as(self.user_id)
        with patch.object(file_areas, '_bump_file_ratio',
                         side_effect=RuntimeError('boom')), \
             self.assertLogs('anetbbs.web_app', level='ERROR') as cm:
            resp = client.get(f'/file-areas/{area_id}/file.txt')

        self.assertEqual(resp.status_code, 200,
                         'the download itself must still succeed (fail open)')
        self.assertTrue(
            any('ratio-counter bump crashed' in m for m in cm.output),
            f'expected a logged crash message, got: {cm.output}')

    def test_upload_ratio_bump_crash_is_logged(self):
        from anetbbs.web import file_areas

        storage_dir = os.path.join(self.work_dir, 'storage')
        os.makedirs(storage_dir)
        area_id = self._make_area('SILENTUL', storage_dir)

        client = self._client_as(self.user_id)
        data = {'file': (__import__('io').BytesIO(b'hello world'), 'up.txt')}
        with patch.object(file_areas, '_bump_file_ratio',
                         side_effect=RuntimeError('boom')), \
             self.assertLogs('anetbbs.web_app', level='ERROR') as cm:
            resp = client.post(f'/file-areas/{area_id}/upload',
                              data=data, content_type='multipart/form-data')

        self.assertLess(resp.status_code, 500,
                        'the upload itself must still succeed (fail open)')
        self.assertTrue(
            any('ratio-counter bump crashed' in m for m in cm.output),
            f'expected a logged crash message, got: {cm.output}')

    def test_thumbnail_failure_is_logged_at_debug_level(self):
        storage_dir = os.path.join(self.work_dir, 'storage')
        os.makedirs(storage_dir)
        with open(os.path.join(storage_dir, 'broken.png'), 'wb') as f:
            f.write(b'not a real png at all')
        area_id = self._make_area('SILENTTHUMB', storage_dir)

        client = self._client_as(self.user_id)
        with self.assertLogs('anetbbs.web_app', level='DEBUG') as cm:
            resp = client.get(f'/file-areas/{area_id}/thumb/broken.png')

        self.assertEqual(resp.status_code, 200,
                         'must still fall back to serving the raw file')
        self.assertTrue(
            any('thumbnail generation failed' in m for m in cm.output),
            f'expected a logged debug message, got: {cm.output}')


if __name__ == '__main__':
    unittest.main()
