"""Regression tests for a real logging gap found live (broader logging
audit, 2026-10-26): both web download routes that log a 'file_download'
UserActivity row (anetbbs/web/files.py's /files/download/<id>, and
anetbbs/web/file_areas.py's /file-areas/<id>/<filename> -- the latter
previously logged NOTHING at all) must set caller_log_id, the same
flask_session-stashed id auth.py's login route sets and every other
_log_activity() caller already threads through. Without it the row is
written (aggregate stats like "downloads today" are correct) but never
shows up in the admin caller-log drill-down (admin.caller_log_detail) --
exactly the "a lot of time and does not show the activity" complaint
this audit started from.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class WebFileDownloadActivityCorrelationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent /
                         '.web_file_download_activity_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User, FileArea, FileUpload, CallerLog
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        with cls.app.app_context():
            db.create_all()

            user = User(username='dlcorrtest', email='dlcorr@example.com',
                       password_hash='x', is_admin=False, access_level=10)
            db.session.add(user)
            db.session.commit()
            cls.user_id = user.id

            cl = CallerLog(user_id=user.id, username='dlcorrtest', service='web')
            db.session.add(cl)
            db.session.commit()
            cls.caller_log_id = cl.id

            # file_areas.py's download() needs a real file on a real
            # storage_path -- it os.path.isfile()s/getsize()s the real
            # path and send_from_directory()s it.
            import tempfile
            cls.storage_dir = tempfile.mkdtemp()
            with open(os.path.join(cls.storage_dir, 'realfile.txt'), 'wb') as fh:
                fh.write(b'hello world')
            area = FileArea(tag='DLCORRTEST', name='DownloadCorrelationTest',
                           storage_path=cls.storage_dir, is_active=True)
            db.session.add(area)
            db.session.commit()
            cls.area_id = area.id

            # files.py's download() route (a different, older upload
            # feature -- FileUpload rows, not storage_path file areas).
            upload_dir = tempfile.mkdtemp()
            with open(os.path.join(upload_dir, 'upload.bin'), 'wb') as fh:
                fh.write(b'x')
            cls.app.config['UPLOADS_DIR'] = upload_dir
            upload = FileUpload(
                uploader_id=user.id, filename='upload.bin',
                original_filename='upload.bin', file_path='x', file_size=1,
                file_area_id=None)
            db.session.add(upload)
            db.session.commit()
            cls.upload_id = upload.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _client_with_caller_log(self):
        """A logged-in client whose Flask session also carries
        caller_log_id, mirroring what auth.py's real login route
        stashes there."""
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(self.user_id)
            sess['_fresh'] = True
            sess['caller_log_id'] = self.caller_log_id
        return client

    def test_file_areas_download_route_logs_file_download_activity(self):
        """This is THE route every real web file-area download goes
        through -- it previously logged no UserActivity row at all."""
        from anetbbs.models import UserActivity
        client = self._client_with_caller_log()
        resp = client.get(f'/file-areas/{self.area_id}/realfile.txt')
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            activity = UserActivity.query.filter_by(
                user_id=self.user_id, activity_type='file_download').first()
            self.assertIsNotNone(
                activity, 'a file-area download must log a file_download '
                'UserActivity row')
            self.assertEqual(activity.caller_log_id, self.caller_log_id)

    def test_files_download_route_correlates_caller_log_id(self):
        from anetbbs.models import UserActivity
        client = self._client_with_caller_log()
        resp = client.get(f'/files/download/{self.upload_id}')
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            activity = (UserActivity.query
                       .filter_by(user_id=self.user_id,
                                 activity_type='file_download')
                       .order_by(UserActivity.id.desc()).first())
            self.assertIsNotNone(activity)
            self.assertEqual(activity.caller_log_id, self.caller_log_id)


if __name__ == '__main__':
    unittest.main()
