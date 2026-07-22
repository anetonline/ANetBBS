"""Route-level tests for the PETSCII custom-menu admin screens
(/admin/petscii-menus/) -- covers CRUD for PetsciiMenu/PetsciiMenuItem.
The interpreter itself (goto/dispatch/fallback) is covered by
tests/test_petscii_custom_menus.py.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class PetsciiMenuAdminRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.petscii_menu_admin_route_test.db')
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

    def _admin_client(self, username='petmenu_admin'):
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

    def test_create_menu_then_add_item_then_see_it_in_edit_view(self):
        client = self._admin_client()
        r = client.post('/admin/petscii-menus/new', data={
            'name': 'main', 'title': 'Main Menu', 'prompt': 'Choice: ',
            'min_access': '0', 'is_default': 'y',
        }, follow_redirects=True)
        self.assertEqual(r.status_code, 200)

        with self.app.app_context():
            from anetbbs.models import PetsciiMenu
            menu = PetsciiMenu.query.filter_by(name='main').first()
            self.assertIsNotNone(menu)
            self.assertTrue(menu.is_default)
            menu_id = menu.id

        r2 = client.post(f'/admin/petscii-menus/{menu_id}/items/new', data={
            'hotkey': 'B', 'label': 'Boards', 'action_type': 'boards',
            'action_args': '', 'min_access': '0', 'sort_order': '1',
        }, follow_redirects=True)
        self.assertEqual(r2.status_code, 200)
        self.assertIn(b'Boards', r2.data)

    def test_duplicate_name_rejected(self):
        client = self._admin_client()
        client.post('/admin/petscii-menus/new', data={
            'name': 'dupe', 'title': 'First', 'prompt': 'Choice: ',
        }, follow_redirects=True)
        r = client.post('/admin/petscii-menus/new', data={
            'name': 'dupe', 'title': 'Second', 'prompt': 'Choice: ',
        }, follow_redirects=True)
        self.assertIn(b'already exists', r.data)
        with self.app.app_context():
            from anetbbs.models import PetsciiMenu
            self.assertEqual(PetsciiMenu.query.filter_by(name='dupe').count(), 1)

    def test_spawn_submenu_creates_linked_menu_with_back_item(self):
        client = self._admin_client()
        client.post('/admin/petscii-menus/new', data={
            'name': 'parent', 'title': 'Parent', 'prompt': 'Choice: ',
        })
        with self.app.app_context():
            from anetbbs.models import PetsciiMenu
            parent_id = PetsciiMenu.query.filter_by(name='parent').first().id

        r = client.post(f'/admin/petscii-menus/{parent_id}/spawn-submenu', data={
            'name': 'child', 'title': 'Child Menu', 'hotkey': 'C', 'label': 'Child',
        }, follow_redirects=True)
        self.assertEqual(r.status_code, 200)

        with self.app.app_context():
            from anetbbs.models import PetsciiMenu, PetsciiMenuItem
            child = PetsciiMenu.query.filter_by(name='child').first()
            self.assertIsNotNone(child)
            back_item = PetsciiMenuItem.query.filter_by(
                menu_id=child.id, hotkey='Q').first()
            self.assertIsNotNone(back_item)
            self.assertEqual(back_item.action_type, 'goto')
            self.assertEqual(back_item.action_args, 'parent')
            link_item = PetsciiMenuItem.query.filter_by(
                menu_id=parent_id, hotkey='C').first()
            self.assertIsNotNone(link_item)
            self.assertEqual(link_item.action_args, 'child')

    def test_delete_menu_removes_it_and_its_items(self):
        client = self._admin_client()
        client.post('/admin/petscii-menus/new', data={
            'name': 'todelete', 'title': 'Gone Soon', 'prompt': 'Choice: ',
        })
        with self.app.app_context():
            from anetbbs.models import db, PetsciiMenu, PetsciiMenuItem
            m = PetsciiMenu.query.filter_by(name='todelete').first()
            db.session.add(PetsciiMenuItem(
                menu_id=m.id, hotkey='Q', label='Logoff', action_type='logoff'))
            db.session.commit()
            menu_id = m.id

        r = client.post(f'/admin/petscii-menus/{menu_id}/delete', follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        with self.app.app_context():
            from anetbbs.models import PetsciiMenu, PetsciiMenuItem
            self.assertIsNone(PetsciiMenu.query.get(menu_id))
            self.assertEqual(PetsciiMenuItem.query.filter_by(menu_id=menu_id).count(), 0)

    def test_list_page_renders_with_no_menus(self):
        client = self._admin_client(username='fresh_admin')
        r = client.get('/admin/petscii-menus/')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'PETSCII', r.data)


if __name__ == '__main__':
    unittest.main()
