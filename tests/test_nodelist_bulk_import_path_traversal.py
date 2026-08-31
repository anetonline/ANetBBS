"""Regression test for a real Medium-severity path-traversal finding
from a security/performance audit (2026-08-31): web/nodelist.py's
admin_bulk_import() joined a raw POST form-field-key suffix
("domain__<filename>") directly onto scan_dir with no traversal check.
A filename of "../../../etc/passwd" (or containing any path separator)
made the resolved path escape scan_dir entirely -- and imported
entries become visible to EVERY logged-in user via /nodelist/
afterward, not just the admin who triggered the import. The route
already requires is_admin, but that's not a reason to skip validating
admin-supplied input: a compromised admin session (XSS/CSRF) could
otherwise exfiltrate arbitrary server file contents into a public
listing.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class NodelistBulkImportPathTraversalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.nodelist_traversal_test.db')
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
            admin = User(username='nodelisttraversaladmin',
                         email='nta@example.com', is_admin=True, is_active=True)
            admin.set_password('adminpassword123')
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

    def _client_as(self, user_id):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(user_id)
            sess['_fresh'] = True
        return client

    def test_dotdot_filename_is_refused_before_touching_the_file(self):
        import tempfile
        with tempfile.TemporaryDirectory() as scan_dir:
            secret = os.path.join(os.path.dirname(scan_dir), 'secret_outside.txt')
            with open(secret, 'w') as f:
                f.write('sensitive content')
            try:
                client = self._client_as(self.admin_id)
                resp = client.post('/nodelist/admin/bulk-import', data={
                    'action': 'import',
                    'scan_dir': scan_dir,
                    'domain__../secret_outside.txt': 'fidonet',
                }, follow_redirects=True)
                self.assertEqual(resp.status_code, 200)
                self.assertIn(b'invalid filename', resp.data)
            finally:
                os.unlink(secret)

    def test_absolute_path_filename_is_refused(self):
        import tempfile
        with tempfile.TemporaryDirectory() as scan_dir:
            client = self._client_as(self.admin_id)
            resp = client.post('/nodelist/admin/bulk-import', data={
                'action': 'import',
                'scan_dir': scan_dir,
                'domain__/etc/passwd': 'fidonet',
            }, follow_redirects=True)
            self.assertEqual(resp.status_code, 200)
            self.assertIn(b'invalid filename', resp.data)

    def test_plain_filename_in_scan_dir_still_works(self):
        import tempfile
        with tempfile.TemporaryDirectory() as scan_dir:
            fn = os.path.join(scan_dir, 'fidonet.na')
            with open(fn, 'w') as f:
                f.write(';A not-a-real-nodelist file\n')
            client = self._client_as(self.admin_id)
            resp = client.post('/nodelist/admin/bulk-import', data={
                'action': 'import',
                'scan_dir': scan_dir,
                'domain__fidonet.na': 'fidonet',
            }, follow_redirects=True)
            self.assertEqual(resp.status_code, 200)
            # A plain, in-directory filename must reach the real
            # import_from_path() call, not be rejected as "invalid
            # filename" -- it may still fail parsing (not a real
            # nodelist), but that's a DIFFERENT error than the
            # traversal guard's rejection.
            self.assertNotIn(b'invalid filename', resp.data)


if __name__ == '__main__':
    unittest.main()
