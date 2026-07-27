"""Route-level tests for the new TIC Out (Hatch) log admin page
(admin.hatch_log) -- a sysop asked for a "tic out log for hub
management" since the Hub admin TIC tab only ever showed two aggregate
counters (Pending/Failed) with no way to see what's actually queued,
for which peer, or why a delivery attempt failed. Mirrors the existing
admin.tic_log (inbound) route/template pattern.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class HatchLogRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.hatch_log_route_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _admin_client(self, username='hatchlog_admin'):
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

    def _make_item(self, suffix, status='pending', retry_count=0, error_message=None):
        from anetbbs.models import db, FileArea, HatchQueue
        farea = FileArea(tag=f'HATCHLOG{suffix}', name='x', is_active=True)
        db.session.add(farea)
        db.session.commit()
        item = HatchQueue(
            file_area_id=farea.id, peer_address='1:1/99',
            binary_path=f'/tmp/{suffix}.zip', filename=f'{suffix}.zip',
            description='x', crc32='x', size_bytes=1,
            seenby='[]', path='[]', status=status,
            retry_count=retry_count, error_message=error_message,
        )
        db.session.add(item)
        db.session.commit()
        return item

    def test_empty_log_shows_placeholder(self):
        client = self._admin_client('empty_admin')
        r = client.get('/admin/hatch-log')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'No outbound TIC activity yet', r.data)

    def test_pending_and_failed_items_are_listed_with_details(self):
        with self.app.app_context():
            self._make_item('Pending', status='pending')
            self._make_item('Failed', status='failed', retry_count=20,
                            error_message="peer didn't ack binary")
        client = self._admin_client('listing_admin')
        r = client.get('/admin/hatch-log')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Pending.zip', r.data)
        self.assertIn(b'Failed.zip', r.data)
        self.assertIn(b'peer didn&#39;t ack binary', r.data)

    def test_status_filter_narrows_results(self):
        with self.app.app_context():
            self._make_item('OnlyPending', status='pending')
            self._make_item('OnlySent', status='sent')
        client = self._admin_client('filter_admin')
        r = client.get('/admin/hatch-log?status=sent')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'OnlySent.zip', r.data)
        self.assertNotIn(b'OnlyPending.zip', r.data)

    def test_requires_admin_login(self):
        client = self.app.test_client()
        r = client.get('/admin/hatch-log', follow_redirects=False)
        self.assertIn(r.status_code, (302, 401, 403))


if __name__ == '__main__':
    unittest.main()
