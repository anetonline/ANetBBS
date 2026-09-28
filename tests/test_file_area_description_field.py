"""Regression test for a real bug reported live (2026-09-27): the file
area admin web UI used to have a Description field (matches the docs
and the caller-facing web browse view, which shows it) but it had
quietly been dropped from templates/admin/file_areas.html entirely --
the FileArea.description column and the `update` action's handling of
it were both still fully intact, only the template (create form AND
the per-row edit form) never rendered an input for it, and the
`create` action never read/set it at all either.

Fixed by adding a `description` input to both forms in
file_areas.html, and adding `description=` handling to the `create`
action in anetbbs/web/admin.py (the `update` action already had it).
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class FileAreaDescriptionFieldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.file_area_description_test.db')
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
            admin = User(username='fadescadmintest', email='fadt@example.com',
                        password_hash='x', is_admin=True, access_level=100)
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

    def _client_as_admin(self):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True
        return client

    def test_create_sets_description(self):
        from anetbbs.models import FileArea
        client = self._client_as_admin()
        resp = client.post('/admin/file-areas', data={
            'action': 'create', 'tag': 'DESCCREATE', 'name': 'Desc Test',
            'description': 'Music files and MP3s',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        with self.app.app_context():
            fa = FileArea.query.filter_by(tag='DESCCREATE').first()
            self.assertIsNotNone(fa)
            self.assertEqual(fa.description, 'Music files and MP3s')

    def test_create_with_blank_description_stores_none(self):
        from anetbbs.models import FileArea
        client = self._client_as_admin()
        client.post('/admin/file-areas', data={
            'action': 'create', 'tag': 'DESCBLANK', 'name': 'Blank Desc',
            'description': '   ',
        }, follow_redirects=True)
        with self.app.app_context():
            fa = FileArea.query.filter_by(tag='DESCBLANK').first()
            self.assertIsNotNone(fa)
            self.assertIsNone(fa.description)

    def test_update_changes_existing_description(self):
        from anetbbs.models import db, FileArea
        with self.app.app_context():
            fa = FileArea(tag='DESCUPDATE', name='Update Desc Test',
                          description='Old description', is_active=True,
                          is_subscribed=True)
            db.session.add(fa)
            db.session.commit()
            area_id = fa.id

        client = self._client_as_admin()
        resp = client.post('/admin/file-areas', data={
            'action': 'update', 'area_id': str(area_id),
            'name': 'Update Desc Test',
            'description': 'New description text',
            'min_access_level': '10',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        with self.app.app_context():
            fa = FileArea.query.get(area_id)
            self.assertEqual(fa.description, 'New description text')

    def test_admin_page_renders_description_input_for_existing_area(self):
        """The real gap: the per-row edit form must actually render an
        input carrying the area's current description -- not just
        handle it correctly on submit."""
        from anetbbs.models import db, FileArea
        with self.app.app_context():
            fa = FileArea(tag='DESCRENDER', name='Render Desc Test',
                          description='Should appear in the page HTML',
                          is_active=True, is_subscribed=True)
            db.session.add(fa)
            db.session.commit()

        client = self._client_as_admin()
        resp = client.get('/admin/file-areas')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('name="description"', html)
        self.assertIn('Should appear in the page HTML', html)

    def test_create_form_has_description_input(self):
        client = self._client_as_admin()
        resp = client.get('/admin/file-areas')
        html = resp.get_data(as_text=True)
        self.assertGreaterEqual(html.count('name="description"'), 1)


if __name__ == '__main__':
    unittest.main()
