"""Regression test for a real Low finding from a security/performance
audit (2026-09-02): anetbbs/web/ansi_editor.py's save() route -- unlike
its sibling create()/import_ans() routes, which both clamp width/height
to [20,132]/[5,50] -- took the posted grid's width/height with no
bound at all. An unbounded value there would make render_ansi_text()'s
nested row/col loop attempt a huge iteration on the next save/preview.
Admin-only, so low severity, but the same clamp costs nothing to apply
consistently.
"""
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class AnsiEditorSaveSizeClampTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.ansi_editor_clamp_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User, AnsiArt
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        with cls.app.app_context():
            db.create_all()
            admin = User(username='ansiclamptest', email='act@example.com',
                        password_hash='x', access_level=100, is_admin=True)
            db.session.add(admin)
            art = AnsiArt(name='Clamp Test', slug='clamp-test',
                          width=80, height=25,
                          grid_json=json.dumps({'width': 80, 'height': 25, 'cells': []}))
            db.session.add(art)
            db.session.commit()
            cls.admin_id = admin.id
            cls.art_id = art.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _admin_client(self):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True
        return client

    def test_oversized_grid_dimensions_are_clamped_on_save(self):
        client = self._admin_client()
        grid = {'width': 999999, 'height': 999999,
               'cells': [{'c': ' ', 'fg': 15, 'bg': 1}]}
        resp = client.post(f'/admin/ansi/{self.art_id}/save',
                           json={'grid': grid, 'name': 'Clamp Test'})
        self.assertEqual(resp.status_code, 200)

        from anetbbs.models import AnsiArt
        with self.app.app_context():
            art = AnsiArt.query.get(self.art_id)
            self.assertLessEqual(art.width, 132)
            self.assertLessEqual(art.height, 50)

    def test_undersized_grid_dimensions_are_clamped_on_save(self):
        client = self._admin_client()
        grid = {'width': 1, 'height': 0,
               'cells': [{'c': ' ', 'fg': 15, 'bg': 1}]}
        resp = client.post(f'/admin/ansi/{self.art_id}/save',
                           json={'grid': grid, 'name': 'Clamp Test'})
        self.assertEqual(resp.status_code, 200)

        from anetbbs.models import AnsiArt
        with self.app.app_context():
            art = AnsiArt.query.get(self.art_id)
            self.assertGreaterEqual(art.width, 20)
            self.assertGreaterEqual(art.height, 5)

    def test_normal_grid_dimensions_are_unaffected(self):
        client = self._admin_client()
        grid = {'width': 100, 'height': 30,
               'cells': [{'c': ' ', 'fg': 15, 'bg': 1}]}
        resp = client.post(f'/admin/ansi/{self.art_id}/save',
                           json={'grid': grid, 'name': 'Clamp Test'})
        self.assertEqual(resp.status_code, 200)

        from anetbbs.models import AnsiArt
        with self.app.app_context():
            art = AnsiArt.query.get(self.art_id)
            self.assertEqual(art.width, 100)
            self.assertEqual(art.height, 30)


if __name__ == '__main__':
    unittest.main()
