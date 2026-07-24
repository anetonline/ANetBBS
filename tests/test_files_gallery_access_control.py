"""Regression tests for a real access-control bug found in a full
application-wide audit: anetbbs/web/files.py's public file gallery
(list_files()/download()) never consulted FileArea.min_access_level/
is_sysop_only, unlike the equivalent, already-fixed _visible_to() in
anetbbs/web/file_areas.py -- a file uploaded into a sysop-only or
VIP-gated area was still fully listed and directly downloadable by
anyone, including anonymous visitors.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class FilesGalleryAccessControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.files_gallery_access_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User, FileArea, FileUpload
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        with cls.app.app_context():
            db.create_all()

            low_user = User(username='lowlvlfilegaltest', email='llfg@example.com',
                           password_hash='x', is_admin=False, access_level=10)
            high_user = User(username='hilvlfilegaltest', email='hlfg@example.com',
                            password_hash='x', is_admin=False, access_level=100)
            db.session.add_all([low_user, high_user])
            db.session.commit()
            cls.low_user_id = low_user.id
            cls.high_user_id = high_user.id

            gated_area = FileArea(tag='GALVIPTEST', name='GalleryVipAreaTest',
                                  storage_path='/tmp/nonexistent_gallery_vip_test',
                                  is_active=True, min_access_level=50)
            db.session.add(gated_area)
            db.session.commit()
            cls.gated_area_id = gated_area.id

            gated_upload = FileUpload(
                uploader_id=high_user.id, filename='gatedfile.zip',
                original_filename='GatedSecretFile.zip', file_path='/tmp/x',
                file_size=1, file_area_id=gated_area.id)
            db.session.add(gated_upload)
            db.session.commit()
            cls.gated_upload_id = gated_upload.id

            top_upload = FileUpload(
                uploader_id=high_user.id, filename='topfile.zip',
                original_filename='TopLevelPublicFile.zip', file_path='/tmp/y',
                file_size=1, file_area_id=None)
            db.session.add(top_upload)
            db.session.commit()
            cls.top_upload_id = top_upload.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _client_as(self, user_id):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(user_id)
            sess['_fresh'] = True
        return client

    def test_low_level_user_does_not_see_gated_upload_in_listing(self):
        client = self._client_as(self.low_user_id)
        resp = client.get('/files/')
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b'GatedSecretFile.zip', resp.data)

    def test_high_level_user_sees_gated_upload_in_listing(self):
        client = self._client_as(self.high_user_id)
        resp = client.get('/files/')
        self.assertIn(b'GatedSecretFile.zip', resp.data)

    def test_low_level_user_cannot_download_gated_upload_by_direct_id(self):
        client = self._client_as(self.low_user_id)
        resp = client.get(f'/files/download/{self.gated_upload_id}')
        self.assertEqual(resp.status_code, 403)

    def test_top_level_unscoped_upload_still_visible_to_everyone(self):
        client = self._client_as(self.low_user_id)
        resp = client.get('/files/')
        self.assertIn(b'TopLevelPublicFile.zip', resp.data)


if __name__ == '__main__':
    unittest.main()
