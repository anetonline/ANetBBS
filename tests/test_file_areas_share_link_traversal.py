"""Regression tests for a real path-traversal gap found in a full
application-wide access-control audit: anetbbs/web/file_areas.py's
create_share() never applied the same normpath+realpath traversal
guard download()/thumbnail() in the same file already use -- a
crafted filename made the existence check an oracle and would have
persisted a SharedFileLink pointing outside the area's storage
directory. fetch_shared() (the actual anonymous download endpoint)
got the same defense-in-depth hardening for any pre-existing rows.
"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class FileAreaShareLinkTraversalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.share_link_traversal_test.db')
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
            user = User(username='sharelinktest', email='slt@example.com',
                       password_hash='x', is_admin=False, access_level=100)
            db.session.add(user)
            db.session.commit()
            cls.user_id = user.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def setUp(self):
        self.work_dir = tempfile.mkdtemp(prefix='share_link_traversal_')
        self.addCleanup(shutil.rmtree, self.work_dir, ignore_errors=True)

    def _client_as(self, user_id):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(user_id)
            sess['_fresh'] = True
        return client

    def _make_area(self, tag, storage_path, min_access_level=0):
        from anetbbs.models import db, FileArea
        with self.app.app_context():
            area = FileArea(tag=tag, name=tag, storage_path=storage_path,
                           is_active=True, min_access_level=min_access_level)
            db.session.add(area)
            db.session.commit()
            return area.id

    def test_traversal_filename_is_rejected_not_shared(self):
        from anetbbs.models import SharedFileLink
        storage_dir = os.path.join(self.work_dir, 'storage')
        outside_dir = os.path.join(self.work_dir, 'outside')
        os.makedirs(storage_dir)
        os.makedirs(outside_dir)
        with open(os.path.join(outside_dir, 'secret.txt'), 'wb') as f:
            f.write(b'outside the jail')

        area_id = self._make_area('SHARETRAV', storage_dir)

        client = self._client_as(self.user_id)
        resp = client.post(
            f'/file-areas/{area_id}/share/../outside/secret.txt',
            data={}, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            self.assertEqual(
                SharedFileLink.query.filter_by(file_area_id=area_id).count(), 0,
                'a traversal filename must never produce a persisted share link')

    def test_legitimate_filename_still_shares_normally(self):
        from anetbbs.models import SharedFileLink
        storage_dir = os.path.join(self.work_dir, 'storage')
        os.makedirs(storage_dir)
        with open(os.path.join(storage_dir, 'real.zip'), 'wb') as f:
            f.write(b'real contents')

        area_id = self._make_area('SHAREOK', storage_dir)

        client = self._client_as(self.user_id)
        resp = client.post(f'/file-areas/{area_id}/share/real.zip',
                           data={}, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            link = SharedFileLink.query.filter_by(file_area_id=area_id).first()
            self.assertIsNotNone(link)
            self.assertEqual(link.filename, 'real.zip')

    def test_fetch_shared_rejects_a_link_with_a_traversal_filename(self):
        """Defense-in-depth: even if a SharedFileLink row somehow has a
        bad filename (e.g. created before this fix), the actual
        download endpoint must not serve outside the storage dir."""
        from anetbbs.models import db, SharedFileLink
        storage_dir = os.path.join(self.work_dir, 'storage')
        outside_dir = os.path.join(self.work_dir, 'outside2')
        os.makedirs(storage_dir)
        os.makedirs(outside_dir)
        with open(os.path.join(outside_dir, 'secret2.txt'), 'wb') as f:
            f.write(b'outside the jail 2')

        area_id = self._make_area('FETCHTRAV', storage_dir)

        with self.app.app_context():
            link = SharedFileLink(
                token='traversaltesttoken1234567890', created_by_id=self.user_id,
                file_area_id=area_id, filename='../outside2/secret2.txt')
            db.session.add(link)
            db.session.commit()

        client = self.app.test_client()  # anonymous -- fetch_shared has no login_required
        resp = client.get('/file-areas/shared/traversaltesttoken1234567890')
        self.assertEqual(resp.status_code, 404)


if __name__ == '__main__':
    unittest.main()
