"""Regression tests for duplicate-file detection
(anetbbs/features/file_dedup.py:hash_file(), wired into all four upload
paths). Before this, nothing in any upload path computed a hash of the
uploaded content at all.

files.py has a real per-file DB row (FileUpload.content_hash) to query
against -- tested via the full upload route. file_areas.py's three
routes have no per-file DB row (files are listed by scanning the
directory at request time), so they use a per-area .hashes.json sidecar
cache instead, mirroring the existing .descriptions.json pattern --
tested directly against _check_and_record_dupe(), including that the
cache survives a fresh load (it's a file cache, not in-memory).
"""
import io
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class HashFilePureTests(unittest.TestCase):
    def test_hash_is_deterministic_and_content_sensitive(self):
        from anetbbs.features.file_dedup import hash_file
        with tempfile.NamedTemporaryFile(delete=False) as f1:
            f1.write(b'hello world')
            p1 = f1.name
        with tempfile.NamedTemporaryFile(delete=False) as f2:
            f2.write(b'hello world')
            p2 = f2.name
        with tempfile.NamedTemporaryFile(delete=False) as f3:
            f3.write(b'goodbye world')
            p3 = f3.name
        try:
            h1, h2, h3 = hash_file(p1), hash_file(p2), hash_file(p3)
            self.assertEqual(h1, h2)
            self.assertNotEqual(h1, h3)
            self.assertEqual(len(h1), 64)  # sha256 hex digest
        finally:
            for p in (p1, p2, p3):
                os.remove(p)

    def test_hash_streams_large_file_in_chunks(self):
        from anetbbs.features.file_dedup import hash_file
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b'x' * 200_000)  # bigger than the default 64KB chunk size
            path = f.name
        try:
            h = hash_file(path, chunk_size=1024)
            # Same content hashed in one shot should match a chunked read.
            import hashlib
            expected = hashlib.sha256(b'x' * 200_000).hexdigest()
            self.assertEqual(h, expected)
        finally:
            os.remove(path)


class FileAreasSidecarCacheTests(unittest.TestCase):
    """_check_and_record_dupe / the .hashes.json cache -- no Flask needed,
    these are plain functions operating on a FileArea-shaped object."""

    class _FakeArea:
        def __init__(self, storage_path, tag='TEST'):
            self.storage_path = storage_path
            self.tag = tag

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.area = self._FakeArea(self._tmp.name)

    def _write(self, name, content):
        path = os.path.join(self._tmp.name, name)
        with open(path, 'wb') as f:
            f.write(content)
        return path

    def test_first_upload_no_dupe(self):
        from anetbbs.web.file_areas import _check_and_record_dupe
        dest = self._write('first.zip', b'unique content')
        result = _check_and_record_dupe(self.area, dest, 'first.zip')
        self.assertIsNone(result)

    def test_second_identical_upload_flagged(self):
        from anetbbs.web.file_areas import _check_and_record_dupe
        dest1 = self._write('first.zip', b'same bytes')
        _check_and_record_dupe(self.area, dest1, 'first.zip')

        dest2 = self._write('second.zip', b'same bytes')
        result = _check_and_record_dupe(self.area, dest2, 'second.zip')
        self.assertEqual(result, 'first.zip')

    def test_different_content_not_flagged(self):
        from anetbbs.web.file_areas import _check_and_record_dupe
        dest1 = self._write('first.zip', b'aaa')
        _check_and_record_dupe(self.area, dest1, 'first.zip')

        dest2 = self._write('second.zip', b'bbb')
        result = _check_and_record_dupe(self.area, dest2, 'second.zip')
        self.assertIsNone(result)

    def test_cache_file_survives_fresh_load(self):
        """The cache is a file on disk, not an in-memory dict -- confirm
        a fresh, independent read (simulating a process restart) still
        sees a hash recorded by an earlier call. Uses
        _read_json_sidecar() directly (a security/performance audit
        replaced the old per-cache _load_hash_cache()/_save_hash_cache()
        pair with a shared, lock-protected helper -- see
        _update_json_sidecar's own docstring for the read-modify-write
        race this closed)."""
        from anetbbs.web.file_areas import (_check_and_record_dupe,
                                            _read_json_sidecar, _hash_cache_path)
        dest = self._write('first.zip', b'persisted bytes')
        _check_and_record_dupe(self.area, dest, 'first.zip')

        self.assertTrue(os.path.isfile(_hash_cache_path(self.area)))
        cache = _read_json_sidecar(_hash_cache_path(self.area))
        self.assertEqual(len(cache), 1)
        self.assertIn('first.zip', cache.values())

    def test_hash_cache_filename_excluded_from_dotfile_scan(self):
        """.hashes.json itself must start with '.' so _scan_area()'s
        existing dotfile skip already excludes it from file listings."""
        from anetbbs.web.file_areas import _HASH_CACHE_FILENAME
        self.assertTrue(_HASH_CACHE_FILENAME.startswith('.'))


class FilesUploadDedupTests(unittest.TestCase):
    """Flask-level: the files.py gallery upload route (real FileUpload
    DB row + content_hash column)."""

    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.file_dedup_test.db')
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

    def _client(self):
        from anetbbs.models import db, User
        with self.app.app_context():
            u = User.query.filter_by(username='dedupfilestest').first()
            if not u:
                u = User(username='dedupfilestest', is_admin=False,
                         access_level=10, email='dedupfilestest@example.com')
                u.set_password('x')
                db.session.add(u)
                db.session.commit()
            uid = u.id
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(uid)
            sess['_fresh'] = True
        return client

    def _make_zip_bytes(self, content):
        bio = io.BytesIO()
        with zipfile.ZipFile(bio, 'w') as zf:
            zf.writestr('data.txt', content)
        return bio.getvalue()

    def test_second_identical_upload_gets_warning_flash_and_still_succeeds(self):
        client = self._client()
        payload = self._make_zip_bytes('identical content here')

        resp1 = client.post('/files/upload', data={
            'file': (io.BytesIO(payload), 'first.zip'),
            'file_area_id': '0',
        }, content_type='multipart/form-data', follow_redirects=True)
        self.assertEqual(resp1.status_code, 200)

        resp2 = client.post('/files/upload', data={
            'file': (io.BytesIO(payload), 'second.zip'),
            'file_area_id': '0',
        }, content_type='multipart/form-data', follow_redirects=True)
        self.assertEqual(resp2.status_code, 200)
        self.assertIn(b'identical file already uploaded', resp2.data.lower())

        from anetbbs.models import FileUpload
        with self.app.app_context():
            rows = FileUpload.query.filter(
                FileUpload.original_filename.in_(['first.zip', 'second.zip'])).all()
            self.assertEqual(len(rows), 2, 'the notice should not block the upload')
            self.assertEqual(rows[0].content_hash, rows[1].content_hash)


if __name__ == '__main__':
    unittest.main()
