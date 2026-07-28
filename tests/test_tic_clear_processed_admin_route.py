"""Regression test for the "Clear Processed Files" admin action, added
on request: inbound/processed/ (where process_tic() moves successfully
-filed originals, never deleting them) has no cleanup anywhere in the
codebase and just grows forever -- confirmed live with a 41MB file
already sitting in it. This lets a sysop delete anything older than a
chosen number of days, sysop-triggered only (never auto-run), matching
this project's usual "never auto-delete sysop data" caution.
"""
import os
import sys
import shutil
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class TicClearProcessedAdminRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.tic_clear_processed_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        cls.data_dir = tempfile.mkdtemp(prefix='tic_clear_data_')
        cfg_mod.TestingConfig.DATA_DIR = cls.data_dir

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        cls.app.config['DATA_DIR'] = cls.data_dir
        with cls.app.app_context():
            db.create_all()
            u = User(username='tic_clear_admin', is_admin=True,
                     email='tca@example.com')
            u.set_password('x')
            db.session.add(u)
            db.session.commit()
            cls.admin_id = u.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)
        shutil.rmtree(cls.data_dir, ignore_errors=True)

    def _admin_client(self):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True
        return client

    def test_clear_with_no_processed_dir_says_so(self):
        # A fresh data_dir subdir with no processed/ subfolder at all.
        inbound_dir = os.path.join(self.data_dir, 'clear_test_a', 'inbound')
        os.makedirs(inbound_dir, exist_ok=True)
        os.environ['BINKP_INBOUND_DIR'] = inbound_dir
        try:
            client = self._admin_client()
            resp = client.post('/admin/tic-log/clear-processed',
                               data={'days': '30'}, follow_redirects=True)
            self.assertEqual(resp.status_code, 200)
            self.assertIn(b'No processed/ directory found', resp.data)
        finally:
            del os.environ['BINKP_INBOUND_DIR']

    def test_clear_deletes_only_files_older_than_threshold(self):
        inbound_dir = os.path.join(self.data_dir, 'clear_test_b', 'inbound')
        processed_dir = os.path.join(inbound_dir, 'processed')
        os.makedirs(processed_dir, exist_ok=True)
        os.environ['BINKP_INBOUND_DIR'] = inbound_dir
        try:
            old_path = os.path.join(processed_dir, 'ancient.zip')
            recent_path = os.path.join(processed_dir, 'fresh.zip')
            with open(old_path, 'wb') as f:
                f.write(b'old file contents')
            with open(recent_path, 'wb') as f:
                f.write(b'recent file contents')

            # Backdate the "old" file's mtime to 60 days ago; leave the
            # "recent" one at its real just-created mtime.
            sixty_days_ago = time.time() - (60 * 86400)
            os.utime(old_path, (sixty_days_ago, sixty_days_ago))

            client = self._admin_client()
            resp = client.post('/admin/tic-log/clear-processed',
                               data={'days': '30'}, follow_redirects=True)
            self.assertEqual(resp.status_code, 200)
            self.assertIn(b'Deleted 1 file', resp.data)
            self.assertFalse(os.path.exists(old_path),
                             'file older than the threshold should be deleted')
            self.assertTrue(os.path.exists(recent_path),
                            'file newer than the threshold must survive')
        finally:
            del os.environ['BINKP_INBOUND_DIR']

    def test_clear_with_nothing_old_enough_says_so(self):
        inbound_dir = os.path.join(self.data_dir, 'clear_test_c', 'inbound')
        processed_dir = os.path.join(inbound_dir, 'processed')
        os.makedirs(processed_dir, exist_ok=True)
        os.environ['BINKP_INBOUND_DIR'] = inbound_dir
        try:
            with open(os.path.join(processed_dir, 'brand_new.zip'), 'wb') as f:
                f.write(b'just filed')

            client = self._admin_client()
            resp = client.post('/admin/tic-log/clear-processed',
                               data={'days': '30'}, follow_redirects=True)
            self.assertEqual(resp.status_code, 200)
            self.assertIn(b'No files older than', resp.data)
            self.assertTrue(os.path.exists(
                os.path.join(processed_dir, 'brand_new.zip')))
        finally:
            del os.environ['BINKP_INBOUND_DIR']

    def test_invalid_days_value_falls_back_to_default(self):
        """Baseline / guard: a bogus 'days' value must not crash the
        route -- falls back to the 30-day default instead."""
        inbound_dir = os.path.join(self.data_dir, 'clear_test_d', 'inbound')
        processed_dir = os.path.join(inbound_dir, 'processed')
        os.makedirs(processed_dir, exist_ok=True)
        os.environ['BINKP_INBOUND_DIR'] = inbound_dir
        try:
            client = self._admin_client()
            resp = client.post('/admin/tic-log/clear-processed',
                               data={'days': 'not-a-number'}, follow_redirects=True)
            self.assertEqual(resp.status_code, 200)
            self.assertIn(b'No files older than 30 day', resp.data)
        finally:
            del os.environ['BINKP_INBOUND_DIR']


if __name__ == '__main__':
    unittest.main()
