"""Regression test: web/file_areas.py's smart_upload() route (upload +
auto-detect/pick the target file area by tag) never checked
FILE_MOD_QUEUE_ENABLED at all, unlike its sibling per-area upload()
route, which routes non-admin uploads into FileQueueEntry quarantine
before anything reaches disk or gets TIC-hatched to network peers.
Any user with upload_permission to an area could use smart_upload()
instead of the per-area form to get a file live and out to the network
with ZERO sysop review, even with moderation explicitly turned on --
found in a full file-areas audit.
"""
import io
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class SmartUploadModerationQueueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.smart_upload_modq_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        import tempfile
        cls._storage_dir = tempfile.mkdtemp()

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
        import shutil
        shutil.rmtree(cls._storage_dir, ignore_errors=True)

    def _admin_client(self, username):
        from anetbbs.models import db, User
        with self.app.app_context():
            u = User.query.filter_by(username=username).first()
            if not u:
                u = User(username=username, is_admin=True,
                        email=f'{username}@example.com')
                u.set_password('x')
                db.session.add(u)
                db.session.commit()
            uid = u.id
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(uid)
            sess['_fresh'] = True
        return client

    def _user_client(self, username):
        from anetbbs.models import db, User
        with self.app.app_context():
            u = User.query.filter_by(username=username).first()
            if not u:
                u = User(username=username, is_admin=False, access_level=10)
                u.email = f'{username}@example.com'
                u.set_password('x')
                db.session.add(u)
                db.session.commit()
            uid = u.id
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(uid)
            sess['_fresh'] = True
        return client

    def _make_area(self, tag, suffix):
        from anetbbs.models import db, FileArea
        area = FileArea(tag=tag, name=f'Area {suffix}', is_active=True,
                        storage_path=os.path.join(self._storage_dir, suffix),
                        upload_permission='users', min_access_level=0)
        db.session.add(area)
        db.session.commit()
        return area

    def test_moderation_enabled_smart_upload_is_quarantined_not_live(self):
        from anetbbs.models import FileArea, FileQueueEntry
        with self.app.app_context():
            area = self._make_area('SMARTMOD1', 'mod1')
            area_id = area.id

        self.app.config['FILE_MOD_QUEUE_ENABLED'] = True
        client = self._user_client('smartmoduser1')
        resp = client.post('/file-areas/smart-upload', data={
            'file': (io.BytesIO(b'file content here'), 'test.zip'),
            'area_id': str(area_id),
        }, content_type='multipart/form-data', follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'pending sysop approval', resp.data)

        with self.app.app_context():
            area = FileArea.query.get(area_id)
            # Must NOT be live on disk in the area's storage path.
            self.assertFalse(
                os.path.exists(os.path.join(area.storage_path, 'test.zip')),
                'a moderated upload must not reach the area storage path '
                'directly -- it must sit in quarantine first')
            entry = FileQueueEntry.query.filter_by(file_area_id=area_id).first()
            self.assertIsNotNone(entry, 'a FileQueueEntry must be created')
            self.assertEqual(entry.status, 'pending')
            self.assertTrue(os.path.exists(entry.quarantine_path))

    def test_moderation_disabled_smart_upload_still_goes_live_directly(self):
        """Baseline / guard against a too-broad fix."""
        from anetbbs.models import FileArea
        with self.app.app_context():
            area = self._make_area('SMARTMOD2', 'mod2')
            area_id = area.id

        self.app.config['FILE_MOD_QUEUE_ENABLED'] = False
        client = self._user_client('smartmoduser2')
        resp = client.post('/file-areas/smart-upload', data={
            'file': (io.BytesIO(b'file content here'), 'test2.zip'),
            'area_id': str(area_id),
        }, content_type='multipart/form-data', follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            area = FileArea.query.get(area_id)
            self.assertTrue(
                os.path.exists(os.path.join(area.storage_path, 'test2.zip')),
                'with moderation disabled, the file must land directly in '
                'the area storage path as before')

    def test_moderation_enabled_admin_upload_bypasses_queue(self):
        """Admins were already exempt on the per-area upload() route --
        smart_upload() must preserve that, not accidentally start
        quarantining admin uploads too."""
        from anetbbs.models import FileArea
        with self.app.app_context():
            area = self._make_area('SMARTMOD3', 'mod3')
            area_id = area.id

        self.app.config['FILE_MOD_QUEUE_ENABLED'] = True
        client = self._admin_client('smartmodadmin')
        resp = client.post('/file-areas/smart-upload', data={
            'file': (io.BytesIO(b'file content here'), 'test3.zip'),
            'area_id': str(area_id),
        }, content_type='multipart/form-data', follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            area = FileArea.query.get(area_id)
            self.assertTrue(
                os.path.exists(os.path.join(area.storage_path, 'test3.zip')),
                'an admin upload must go live directly, same as the '
                'per-area upload() route')


if __name__ == '__main__':
    unittest.main()
