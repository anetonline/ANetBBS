"""Route-level regression tests for the web ANSI editor's TheDraw font
picker routes (/admin/ansi/fonts, /admin/ansi/fonts/render) added
toward ANetDRAW feature parity. Pure tdf_fonts.py logic is covered
separately in tests/test_tdf_fonts.py; this file confirms the routes
are wired up correctly against a real (small, synthetic) .tdf file.
"""
import json
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


def _build_tdf():
    from anetbbs.features import tdf_fonts
    offs = [0xFFFF] * 94
    data = bytearray()
    # 'H' -> 2x1 glyph "HH"
    offs[ord('H') - 33] = len(data)
    data += bytes([2, 1]) + b'HH' + b'\x00'
    name = b'ROUTETEST'.ljust(12, b' ')
    header = (b'\x55\xAA\x00\xFF' + bytes([len(b'ROUTETEST')]) + name +
             b'\x00\x00\x00\x00' + bytes([tdf_fonts.BLOCK, 1]) +
             struct.pack('<H', len(data)))
    offs_bytes = b''.join(struct.pack('<H', o) for o in offs)
    return tdf_fonts.TDF_MAGIC + header + offs_bytes + bytes(data)


class AnsiEditorFontRoutesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.ansi_font_routes_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        cls._fonts_tmp = tempfile.TemporaryDirectory()
        (Path(cls._fonts_tmp.name) / 'route.tdf').write_bytes(_build_tdf())

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        cls.app.config['TDF_FONTS_DIR'] = cls._fonts_tmp.name
        with cls.app.app_context():
            db.create_all()
            admin = User(username='ansifonttest', email='aft@example.com',
                        password_hash='x', access_level=100, is_admin=True)
            db.session.add(admin)
            db.session.commit()
            cls.admin_id = admin.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        cls._fonts_tmp.cleanup()
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

    def test_list_fonts_returns_the_test_font(self):
        client = self._admin_client()
        resp = client.get('/admin/ansi/fonts')
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['name'], 'ROUTETEST')

    def test_list_fonts_empty_when_no_dir_configured(self):
        client = self._admin_client()
        # Temporarily point at an empty setting for this one request.
        self.app.config['TDF_FONTS_DIR'] = ''
        try:
            resp = client.get('/admin/ansi/fonts')
            self.assertEqual(json.loads(resp.data), [])
        finally:
            self.app.config['TDF_FONTS_DIR'] = self._fonts_tmp.name

    def test_render_font_preview_returns_real_cells(self):
        client = self._admin_client()
        resp = client.get('/admin/ansi/fonts/render?index=0&text=H&fg=9&bg=0')
        self.assertEqual(resp.status_code, 200)
        result = json.loads(resp.data)
        self.assertEqual(result['width'], 2)
        self.assertEqual(result['height'], 1)
        self.assertEqual(result['cells'][0]['c'], 'H')
        self.assertEqual(result['cells'][0]['fg'], 9)

    def test_render_font_preview_missing_text_is_a_400(self):
        client = self._admin_client()
        resp = client.get('/admin/ansi/fonts/render?index=0&text=')
        self.assertEqual(resp.status_code, 400)

    def test_render_font_preview_out_of_range_index_is_404(self):
        client = self._admin_client()
        resp = client.get('/admin/ansi/fonts/render?index=999&text=H')
        self.assertEqual(resp.status_code, 404)

    def test_render_font_preview_character_not_in_font_returns_no_cells(self):
        client = self._admin_client()
        resp = client.get('/admin/ansi/fonts/render?index=0&text=Z')
        self.assertEqual(resp.status_code, 200)
        result = json.loads(resp.data)
        self.assertIn('error', result)

    def test_fg_bg_are_clamped_to_valid_ranges(self):
        client = self._admin_client()
        resp = client.get('/admin/ansi/fonts/render?index=0&text=H&fg=999&bg=-5')
        self.assertEqual(resp.status_code, 200)
        result = json.loads(resp.data)
        self.assertLessEqual(result['cells'][0]['fg'], 15)
        self.assertGreaterEqual(result['cells'][0]['bg'], 0)


if __name__ == '__main__':
    unittest.main()
