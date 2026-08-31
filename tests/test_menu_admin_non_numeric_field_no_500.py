"""Regression test for a real Low-severity finding from a security/
performance audit (2026-08-31): every min_access/sort_order field in
menu_admin.py and petscii_menu_admin.py used
`int(request.form.get(...) or 0)` -- a non-numeric submitted value (a
sysop typo, a malformed autofill) raised an unhandled ValueError and
500'd the whole admin form instead of just rejecting/defaulting the
bad value.

Fixed by switching to Werkzeug's own `request.form.get(key, default,
type=int)` coercion, which already catches the conversion failure
internally and falls back to the given default -- a drop-in fix at
every call site, no custom helper needed.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class MenuAdminNonNumericFieldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.menu_admin_nonnum_test.db')
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
            admin = User(username='menuadminnonnum', email='man@example.com',
                        is_admin=True, is_active=True)
            admin.set_password('menuadminnonnumpass123')
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

    def test_ansi_menu_new_with_non_numeric_min_access_does_not_500(self):
        from anetbbs.models import BbsMenu
        client = self._client_as(self.admin_id)
        resp = client.post('/admin/bbs-menus/new', data={
            'name': 'nonnumtest', 'title': 'Test',
            'min_access': 'not-a-number',
        }, follow_redirects=False)
        self.assertLess(resp.status_code, 500,
                        f'a non-numeric min_access must not 500 -- got '
                        f'{resp.status_code}')
        with self.app.app_context():
            m = BbsMenu.query.filter_by(name='nonnumtest').first()
            self.assertIsNotNone(m)
            self.assertEqual(m.min_access, 0,
                             'a non-numeric value must fall back to the '
                             'default (0), not crash')

    def test_ansi_menu_item_new_with_non_numeric_sort_order_does_not_500(self):
        from anetbbs.models import BbsMenu, BbsMenuItem
        with self.app.app_context():
            from anetbbs.models import db
            menu = BbsMenu(name='itemhost', title='Item Host')
            db.session.add(menu)
            db.session.commit()
            menu_id = menu.id

        client = self._client_as(self.admin_id)
        resp = client.post(f'/admin/bbs-menus/{menu_id}/items/new', data={
            'hotkey': 'X', 'label': 'Test Item',
            'action_type': 'goto', 'action_args': 'main',
            'min_access': 'garbage', 'sort_order': 'also-garbage',
        }, follow_redirects=False)
        self.assertLess(resp.status_code, 500,
                        f'non-numeric fields must not 500 -- got '
                        f'{resp.status_code}')
        with self.app.app_context():
            item = BbsMenuItem.query.filter_by(menu_id=menu_id, hotkey='X').first()
            self.assertIsNotNone(item)
            self.assertEqual(item.min_access, 0)
            self.assertEqual(item.sort_order, 0)

    def test_petscii_menu_new_with_non_numeric_min_access_does_not_500(self):
        from anetbbs.models import PetsciiMenu
        client = self._client_as(self.admin_id)
        resp = client.post('/admin/petscii-menus/new', data={
            'name': 'petsciinonnum', 'title': 'PETSCII Test',
            'min_access': 'nope',
        }, follow_redirects=False)
        self.assertLess(resp.status_code, 500,
                        f'a non-numeric min_access must not 500 -- got '
                        f'{resp.status_code}')
        with self.app.app_context():
            m = PetsciiMenu.query.filter_by(name='petsciinonnum').first()
            self.assertIsNotNone(m)
            self.assertEqual(m.min_access, 0)


if __name__ == '__main__':
    unittest.main()
