"""Regression test for the "Rescan Inbound Now" admin action, added on
request: there was previously no way to manually trigger a TIC inbound
scan outside of an actual BinkP session completing -- a sysop
diagnosing a stuck TIC (or verifying a fix) had no button, only SSH.
"""
import os
import sys
import shutil
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class TicManualRescanAdminRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.tic_manual_rescan_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        cls.data_dir = tempfile.mkdtemp(prefix='tic_rescan_data_')
        cfg_mod.TestingConfig.DATA_DIR = cls.data_dir

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        cls.app.config['DATA_DIR'] = cls.data_dir
        with cls.app.app_context():
            db.create_all()
            u = User(username='tic_rescan_admin', is_admin=True,
                     email='tra@example.com')
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

    def test_rescan_with_no_inbound_dir_flashes_and_redirects(self):
        # Force the precondition regardless of test execution order --
        # other tests in this class create inbound_dir under the same
        # shared class-level DATA_DIR.
        inbound_dir = os.path.join(self.data_dir, 'binkp', 'inbound')
        shutil.rmtree(inbound_dir, ignore_errors=True)
        client = self._admin_client()
        resp = client.post('/admin/tic-log/rescan', follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'does not exist', resp.data)

    def test_rescan_processes_a_real_pending_tic(self):
        from anetbbs.models import db, FileArea, TicFile

        inbound_dir = os.path.join(self.data_dir, 'binkp', 'inbound')
        os.makedirs(inbound_dir, exist_ok=True)
        storage_dir = os.path.join(self.data_dir, 'rescan_storage')
        os.makedirs(storage_dir, exist_ok=True)

        with self.app.app_context():
            area = FileArea(tag='RESCANTEST', name='Rescan Test',
                            storage_path=storage_dir, is_active=True,
                            is_subscribed=True)
            db.session.add(area)
            db.session.commit()

        with open(os.path.join(inbound_dir, 'rescanfile.zip'), 'wb') as f:
            f.write(b'contents')
        with open(os.path.join(inbound_dir, 'rescanfile.tic'), 'w',
                 encoding='cp437') as f:
            f.write('File rescanfile.zip\nArea RESCANTEST\nDesc x\n')

        client = self._admin_client()
        resp = client.post('/admin/tic-log/rescan', follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'processed 1', resp.data)

        with self.app.app_context():
            tic = TicFile.query.filter_by(filename='rescanfile.zip').first()
            self.assertIsNotNone(tic)
            self.assertEqual(tic.status, 'filed')

    def test_rescan_with_no_pending_files_says_so(self):
        inbound_dir = os.path.join(self.data_dir, 'binkp', 'inbound')
        os.makedirs(inbound_dir, exist_ok=True)
        # No .tic files present (from the previous test, inbound_dir now
        # only has the moved-to-processed/ leftovers, if any -- either
        # way, no *new* .tic sitting at the top level).
        client = self._admin_client()
        resp = client.post('/admin/tic-log/rescan', follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'no unprocessed', resp.data)


if __name__ == '__main__':
    unittest.main()
