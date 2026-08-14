"""Regression tests for anetbbs.web.file_areas's upload() / manage_upload()
/ smart_upload() -- real gap found in a security/performance audit:
none of these three routes checked upload size at all (unlike
web/files.py's own gallery upload(), which already did), and none had
rate limiting (unlike boards.py's new_post/pm.py's compose). Combined
with no app-wide MAX_CONTENT_LENGTH existing either (also fixed this
audit, see config.py), any authenticated user could fill the server's
disk via any of these three endpoints with no throttling at all.
"""
import io
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class FileAreasUploadSizeAndRateLimitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.file_areas_upload_size_test.db')
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

    def setUp(self):
        # Rate-limit buckets are process-global (features/rate_limit.py's
        # own _buckets dict) -- clear before each test so one test's
        # requests can't bleed into another's limit count.
        from anetbbs.features import rate_limit as rl
        rl._buckets.clear()

    _tag_counter = 0

    def _admin_client(self, storage_root):
        from anetbbs.models import db, User, FileArea
        with self.app.app_context():
            admin = User.query.filter_by(username='fauploadsizetest').first()
            if not admin:
                admin = User(username='fauploadsizetest', is_admin=True,
                            access_level=255,
                            email='fauploadsizetest@example.com')
                admin.set_password('x')
                db.session.add(admin)
                db.session.commit()
            admin_id = admin.id

            FileAreasUploadSizeAndRateLimitTests._tag_counter += 1
            tag = f'UPLOADSIZE{FileAreasUploadSizeAndRateLimitTests._tag_counter}'
            area = FileArea(tag=tag, name='Upload Size Test Area',
                            storage_path=str(storage_root),
                            upload_permission='users', is_active=True)
            db.session.add(area)
            db.session.commit()
            area_id = area.id
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(admin_id)
            sess['_fresh'] = True
        return client, area_id

    def test_upload_rejects_a_file_over_the_configured_max_size(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            client, area_id = self._admin_client(tmpdir)
            self.app.config['UPLOAD_MAX_SIZE'] = 100  # bytes, for a fast test
            try:
                big_data = b'x' * 500
                resp = client.post(
                    f'/file-areas/{area_id}/upload',
                    data={'file': (io.BytesIO(big_data), 'toobig.txt')},
                    content_type='multipart/form-data',
                    follow_redirects=True)
                self.assertEqual(resp.status_code, 200)
                self.assertIn(b'too large', resp.data.lower())
                self.assertEqual(os.listdir(tmpdir), [])
            finally:
                self.app.config.pop('UPLOAD_MAX_SIZE', None)

    def test_upload_still_accepts_a_file_under_the_max_size(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            client, area_id = self._admin_client(tmpdir)
            resp = client.post(
                f'/file-areas/{area_id}/upload',
                data={'file': (io.BytesIO(b'small file content'), 'ok.txt')},
                content_type='multipart/form-data',
                follow_redirects=True)
            self.assertEqual(resp.status_code, 200)
            self.assertIn('ok.txt', os.listdir(tmpdir))

    def test_manage_upload_rejects_a_file_over_the_configured_max_size(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            client, area_id = self._admin_client(tmpdir)
            self.app.config['UPLOAD_MAX_SIZE'] = 100
            try:
                resp = client.post(
                    f'/file-areas/{area_id}/manage/upload',
                    data={'file': (io.BytesIO(b'y' * 500), 'toobig2.txt')},
                    content_type='multipart/form-data',
                    follow_redirects=True)
                self.assertEqual(resp.status_code, 200)
                self.assertIn(b'too large', resp.data.lower())
                self.assertEqual(os.listdir(tmpdir), [])
            finally:
                self.app.config.pop('UPLOAD_MAX_SIZE', None)

    def test_smart_upload_rejects_a_file_over_the_configured_max_size(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            client, area_id = self._admin_client(tmpdir)
            self.app.config['UPLOAD_MAX_SIZE'] = 100
            try:
                resp = client.post(
                    '/file-areas/smart-upload',
                    data={'file': (io.BytesIO(b'z' * 500), 'toobig3.txt'),
                         'area_id': str(area_id)},
                    content_type='multipart/form-data',
                    follow_redirects=True)
                self.assertEqual(resp.status_code, 200)
                self.assertIn(b'too large', resp.data.lower())
                self.assertEqual(os.listdir(tmpdir), [])
            finally:
                self.app.config.pop('UPLOAD_MAX_SIZE', None)

    def test_upload_is_rate_limited_after_the_configured_number_of_requests(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            client, area_id = self._admin_client(tmpdir)
            # The real limit is 20/300s -- drive it past that directly via
            # the rate-limit internals rather than firing 21 real HTTP
            # uploads, matching this project's usual "prove the wiring,
            # don't re-test the rate limiter's own counting logic" split
            # (the counting logic itself has its own dedicated tests).
            from anetbbs.features import rate_limit as rl
            for _ in range(20):
                rl._check('file_area_upload:u' + str(
                    self._admin_user_id()), 20, 300)
            resp = client.post(
                f'/file-areas/{area_id}/upload',
                data={'file': (io.BytesIO(b'one more'), 'onemore.txt')},
                content_type='multipart/form-data')
            self.assertEqual(resp.status_code, 429)

    def _admin_user_id(self):
        from anetbbs.models import User
        with self.app.app_context():
            return User.query.filter_by(username='fauploadsizetest').first().id

    def test_max_content_length_is_configured_app_wide(self):
        """Guards against the exact regression this audit found: no
        app-wide cap existed at all, so Werkzeug would buffer an
        arbitrarily large body before any per-route check (including
        the ones tested above) ever got a chance to run."""
        self.assertIsNotNone(self.app.config.get('MAX_CONTENT_LENGTH'))
        self.assertGreater(self.app.config['MAX_CONTENT_LENGTH'], 0)


if __name__ == '__main__':
    unittest.main()
