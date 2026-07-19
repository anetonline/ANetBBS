"""Regression/feature test: file areas previously had to be edited or
deleted one at a time -- no multi-select, no bulk action, and no shortcut
to select every area belonging to a specific FTN network. Adds
POST /admin/file-areas/bulk (mirrors the existing /users/bulk pattern)
plus checkboxes tied to it via HTML's form= attribute so each area can
still keep its own individual edit/delete <form>.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class FileAreaBulkActionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.file_areas_bulk_test.db')
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

    def _admin_client(self):
        from anetbbs.models import db, User
        with self.app.app_context():
            admin = User.query.filter_by(username='fileareasbulktest').first()
            if not admin:
                admin = User(username='fileareasbulktest', is_admin=True,
                            access_level=255,
                            email='fileareasbulktest@example.com')
                admin.set_password('x')
                db.session.add(admin)
                db.session.commit()
            admin_id = admin.id
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(admin_id)
            sess['_fresh'] = True
        return client

    _tag_counter = 0

    def _make_areas(self, n, network_id=None, **kwargs):
        from anetbbs.models import db, FileArea
        ids = []
        with self.app.app_context():
            for _ in range(n):
                FileAreaBulkActionsTests._tag_counter += 1
                fa = FileArea(tag=f'BULK{FileAreaBulkActionsTests._tag_counter}',
                             name='Bulk area',
                             is_active=kwargs.get('is_active', True),
                             is_subscribed=kwargs.get('is_subscribed', True),
                             network_id=network_id)
                db.session.add(fa)
                db.session.commit()
                ids.append(fa.id)
        return ids

    def test_bulk_delete_removes_all_selected(self):
        from anetbbs.models import db, FileArea
        ids = self._make_areas(3)
        client = self._admin_client()
        resp = client.post('/admin/file-areas/bulk',
                           data={'bulk_action': 'delete', 'area_ids': [str(i) for i in ids]},
                           follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        with self.app.app_context():
            remaining = FileArea.query.filter(FileArea.id.in_(ids)).all()
            self.assertEqual(remaining, [])

    def test_bulk_disable_and_enable_selected(self):
        from anetbbs.models import db, FileArea
        ids = self._make_areas(2, is_active=True)
        client = self._admin_client()
        client.post('/admin/file-areas/bulk',
                    data={'bulk_action': 'disable', 'area_ids': [str(i) for i in ids]},
                    follow_redirects=True)
        with self.app.app_context():
            areas = FileArea.query.filter(FileArea.id.in_(ids)).all()
            self.assertTrue(all(not a.is_active for a in areas))

        client.post('/admin/file-areas/bulk',
                    data={'bulk_action': 'enable', 'area_ids': [str(i) for i in ids]},
                    follow_redirects=True)
        with self.app.app_context():
            areas = FileArea.query.filter(FileArea.id.in_(ids)).all()
            self.assertTrue(all(a.is_active for a in areas))

    def test_bulk_action_only_touches_selected_ids_not_every_area(self):
        """A bulk action must never affect areas outside the submitted
        selection, even if they'd match the same filter some other way."""
        from anetbbs.models import db, FileArea
        selected_ids = self._make_areas(2)
        untouched_ids = self._make_areas(2)
        client = self._admin_client()
        client.post('/admin/file-areas/bulk',
                    data={'bulk_action': 'delete', 'area_ids': [str(i) for i in selected_ids]},
                    follow_redirects=True)
        with self.app.app_context():
            self.assertEqual(FileArea.query.filter(FileArea.id.in_(selected_ids)).all(), [])
            self.assertEqual(len(FileArea.query.filter(FileArea.id.in_(untouched_ids)).all()), 2)

    def test_no_selection_flashes_warning_and_does_nothing(self):
        client = self._admin_client()
        resp = client.post('/admin/file-areas/bulk',
                           data={'bulk_action': 'delete'},
                           follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'No file areas selected', resp.data)

    def test_select_by_network_checkbox_data_attribute_present(self):
        """Structural check: each area's bulk-select checkbox must carry
        the network id so the 'select all in network' control can filter
        by it client-side."""
        from anetbbs.models import db, EchomailNetwork
        with self.app.app_context():
            net = EchomailNetwork(name='BulkTestNet', network_type='binkp', is_active=True)
            db.session.add(net)
            db.session.commit()
            net_id = net.id
        self._make_areas(1, network_id=net_id)

        client = self._admin_client()
        resp = client.get('/admin/file-areas')
        html = resp.data.decode()
        self.assertIn(f'data-network-id="{net_id}"', html)
        self.assertIn('id="bulkSelectNetwork"', html)
        self.assertIn('id="bulkSelectAll"', html)


if __name__ == '__main__':
    unittest.main()
