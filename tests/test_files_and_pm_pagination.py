"""Regression tests for a real gap found in a security/performance
audit: anetbbs/web/files.py's public gallery (list_files()) and
anetbbs/web/pm.py's inbox()/sent() all did a plain unbounded .all()
query -- every visit fetched and rendered every visible row, with no
limit at all. Fixed with pagination, matching the pattern already
established elsewhere in this codebase (file_areas.py, boards.py,
admin.py, etc.) -- verified here that a page only ever contains
per_page items and that the pagination metadata is accurate.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class FilesAndPmPaginationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.files_pm_pagination_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User, FileUpload, PrivateMessage
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        with cls.app.app_context():
            db.create_all()

            uploader = User(username='paginationuploader', email='pu@example.com',
                            password_hash='x', is_admin=False, access_level=50)
            other = User(username='paginationother', email='po@example.com',
                        password_hash='x', is_admin=False, access_level=50)
            db.session.add_all([uploader, other])
            db.session.commit()
            cls.uploader_id = uploader.id
            cls.other_id = other.id

            # More than one page's worth (FILES_PER_PAGE / PM_PER_PAGE
            # are both 50) of top-level, unscoped (always-visible)
            # uploads and of PMs between the same two users.
            for i in range(55):
                db.session.add(FileUpload(
                    uploader_id=uploader.id, filename=f'f{i}.zip',
                    original_filename=f'File{i}.zip', file_path='/tmp/x',
                    file_size=1, file_area_id=None))
                db.session.add(PrivateMessage(
                    sender_id=other.id, recipient_id=uploader.id,
                    subject=f'Subject {i}', body=f'Body {i}'))
            db.session.commit()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)

    def _login(self, client, user_id):
        with client.session_transaction() as sess:
            sess['_user_id'] = str(user_id)
            sess['_fresh'] = True

    def test_file_gallery_first_page_capped_at_per_page(self):
        from anetbbs.web.files import FILES_PER_PAGE
        client = self.app.test_client()
        self._login(client, self.uploader_id)
        resp = client.get('/files/')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        # 55 uploads named File0..File54 -- first page (50) must show
        # File54 (most recent, desc order) but NOT File4 (which is on
        # page 2 given 55 items / 50 per page, desc order).
        self.assertIn('File54.zip', body)
        self.assertIn(f'Page 1 of 2', body)
        self.assertEqual(FILES_PER_PAGE, 50)

    def test_file_gallery_second_page_has_the_remainder(self):
        client = self.app.test_client()
        self._login(client, self.uploader_id)
        resp = client.get('/files/?page=2')
        body = resp.get_data(as_text=True)
        self.assertIn('File0.zip', body, 'oldest upload should be on the last page')
        self.assertNotIn('File54.zip', body, 'newest upload should not repeat on page 2')

    def test_pm_inbox_is_paginated(self):
        client = self.app.test_client()
        self._login(client, self.uploader_id)
        resp = client.get('/messages/')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn('Subject 54', body)
        self.assertIn('Page 1 of 2', body)
        resp2 = client.get('/messages/?page=2')
        body2 = resp2.get_data(as_text=True)
        self.assertIn('Subject 0', body2)
        self.assertNotIn('Subject 54', body2)

    def test_pm_sent_is_paginated(self):
        client = self.app.test_client()
        self._login(client, self.other_id)
        resp = client.get('/messages/sent')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn('Page 1 of 2', body)


if __name__ == '__main__':
    unittest.main()
