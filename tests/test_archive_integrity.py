"""Regression tests for anetbbs/features/archive_meta.py:test_archive_integrity()
-- previously nothing validated an uploaded archive wasn't corrupt, only
extracted a description from it. Mirrors virus_scan.py's fail-open
philosophy: only a *confirmed* bad archive gets ok=False; anything we
can't actually check (missing optional library, unrecognized format,
a crash mid-test) fails open.

The pure-function tests need no Flask/DB (archive_meta.py has no such
import at module level) and run directly in this sandbox. The upload-path
test needs the full Flask app.
"""
import os
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class ArchiveIntegrityPureTests(unittest.TestCase):
    """No Flask/DB needed."""

    def test_clean_zip_passes(self):
        from anetbbs.features.archive_meta import test_archive_integrity
        with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as f:
            path = f.name
        try:
            with zipfile.ZipFile(path, 'w') as zf:
                zf.writestr('hello.txt', 'hello world' * 100)
            result = test_archive_integrity(path)
            self.assertTrue(result.ok)
        finally:
            os.remove(path)

    def test_corrupt_zip_fails(self):
        from anetbbs.features.archive_meta import test_archive_integrity
        with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as f:
            path = f.name
        try:
            with zipfile.ZipFile(path, 'w') as zf:
                zf.writestr('hello.txt', 'hello world' * 100)
            # Flip a byte inside the compressed file data (well past the
            # local file header) without touching the central directory,
            # so it still opens as a zip but testzip() finds a bad CRC.
            with open(path, 'r+b') as f:
                data = bytearray(f.read())
                # First local file header is at offset 0; header is
                # 30 bytes + filename length before the compressed data
                # starts. Corrupt a byte well into the payload.
                data[60] ^= 0xFF
                f.seek(0)
                f.write(bytes(data))
            result = test_archive_integrity(path)
            self.assertFalse(result.ok)
        finally:
            os.remove(path)

    def test_clean_tar_passes(self):
        from anetbbs.features.archive_meta import test_archive_integrity
        with tempfile.NamedTemporaryFile(suffix='.tar', delete=False) as f:
            path = f.name
        try:
            with tarfile.open(path, 'w') as tf:
                data = b'hello world' * 100
                with tempfile.NamedTemporaryFile() as src:
                    src.write(data)
                    src.flush()
                    tf.add(src.name, arcname='hello.txt')
            result = test_archive_integrity(path)
            self.assertTrue(result.ok)
        finally:
            os.remove(path)

    def test_missing_file_fails_open(self):
        from anetbbs.features.archive_meta import test_archive_integrity
        result = test_archive_integrity('/tmp/does-not-exist-anywhere.zip')
        self.assertTrue(result.ok)

    def test_non_archive_file_fails_open(self):
        from anetbbs.features.archive_meta import test_archive_integrity
        with tempfile.NamedTemporaryFile(suffix='.txt', delete=False) as f:
            f.write(b'just a plain text file, not an archive')
            path = f.name
        try:
            result = test_archive_integrity(path)
            self.assertTrue(result.ok)
        finally:
            os.remove(path)

    def test_rar_without_library_installed_fails_open(self):
        """rarfile is a soft dependency, not installed in this checkout --
        confirm a .rar upload isn't blocked just because we can't test it."""
        from anetbbs.features.archive_meta import test_archive_integrity
        with tempfile.NamedTemporaryFile(suffix='.rar', delete=False) as f:
            f.write(b'Rar!\x1a\x07\x01\x00' + b'\x00' * 100)  # not a real rar, just the magic
            path = f.name
        try:
            result = test_archive_integrity(path)
            self.assertTrue(result.ok)
        finally:
            os.remove(path)


class ArchiveIntegrityUploadPathTests(unittest.TestCase):
    """Flask-level: confirm the reject-and-redirect path in files.upload()."""

    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.archive_integrity_test.db')
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
            u = User.query.filter_by(username='archivetest').first()
            if not u:
                u = User(username='archivetest', is_admin=False,
                         access_level=10, email='archivetest@example.com')
                u.set_password('x')
                db.session.add(u)
                db.session.commit()
            uid = u.id
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(uid)
            sess['_fresh'] = True
        return client

    def test_corrupt_zip_upload_rejected(self):
        buf = bytearray()
        import io
        bio = io.BytesIO()
        with zipfile.ZipFile(bio, 'w') as zf:
            zf.writestr('hello.txt', 'hello world' * 100)
        data = bytearray(bio.getvalue())
        data[60] ^= 0xFF  # corrupt payload, keep central directory intact

        client = self._client()
        resp = client.post('/files/upload', data={
            'file': (io.BytesIO(bytes(data)), 'test.zip'),
            'file_area_id': '0',
        }, content_type='multipart/form-data', follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'corrupt archive', resp.data.lower())

        from anetbbs.models import FileUpload
        with self.app.app_context():
            self.assertIsNone(
                FileUpload.query.filter_by(original_filename='test.zip').first(),
                'corrupt upload should not have created a FileUpload row')


if __name__ == '__main__':
    unittest.main()
