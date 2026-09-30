"""Route-level regression tests for the web ANSI editor's Phase 3
export/import routes added toward ANetDRAW feature parity -- PNG, BIN,
XBin, PCBoard, Renegade/Mystic, Synchronet downloads, and the .BIN/.XBin
upload route. Pure rendering-function correctness is covered separately
in tests/test_ansi_editor_file_formats.py; this file confirms the
routes are actually wired up and, in particular, that a malformed
upload can't 500 the import route.
"""
import io
import json
import os
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class AnsiEditorExportRoutesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.ansi_export_routes_test.db')
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
            admin = User(username='ansiexporttest', email='aet@example.com',
                        password_hash='x', access_level=100, is_admin=True)
            db.session.add(admin)
            grid = {'width': 4, 'height': 2, 'cells': [
                {'c': ch, 'fg': 4, 'bg': 0} for ch in 'TESTBYTE'[:8]]}
            art = AnsiArt(name='Export Test', slug='export-test', width=4, height=2,
                          grid_json=json.dumps(grid))
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

    def test_raw_png_downloads_a_real_png(self):
        client = self._admin_client()
        resp = client.get(f'/admin/ansi/{self.art_id}/raw.png')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data.startswith(b'\x89PNG'))

    def test_raw_bin_downloads_correct_byte_count(self):
        client = self._admin_client()
        resp = client.get(f'/admin/ansi/{self.art_id}/raw.bin')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data), 4 * 2 * 2)  # width*height*2

    def test_raw_xbin_downloads_with_correct_header(self):
        client = self._admin_client()
        resp = client.get(f'/admin/ansi/{self.art_id}/raw.xb')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data.startswith(b'XBIN\x1a'))
        width, height = struct.unpack('<HH', resp.data[5:9])
        self.assertEqual((width, height), (4, 2))

    def test_raw_pcboard_downloads_with_at_x_codes(self):
        client = self._admin_client()
        resp = client.get(f'/admin/ansi/{self.art_id}/raw.pcb')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'@X', resp.data)

    def test_raw_pipe_downloads_with_pipe_codes(self):
        client = self._admin_client()
        resp = client.get(f'/admin/ansi/{self.art_id}/raw.ren')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'|', resp.data)

    def test_raw_synchronet_downloads_with_ctrl_a_codes(self):
        client = self._admin_client()
        resp = client.get(f'/admin/ansi/{self.art_id}/raw.syn')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'\x01', resp.data)

    def test_import_binfile_get_renders_form(self):
        client = self._admin_client()
        resp = client.get('/admin/ansi/import-binfile')
        self.assertEqual(resp.status_code, 200)

    def test_import_valid_bin_file_creates_art(self):
        from anetbbs.web.ansi_editor import render_bin
        from anetbbs.models import AnsiArt
        # import_binfile() clamps width to a minimum of 20 (matching
        # create()/import_ans()'s own [20,132] clamp elsewhere in this
        # file) -- use a 20-char fixture so the real, intentional
        # clamp doesn't fight this test's own assumption.
        text = 'ABCDEFGHIJKLMNOPQRST'
        data = render_bin({'width': 20, 'height': 1,
                           'cells': [{'c': ch, 'fg': 4, 'bg': 0} for ch in text]})
        client = self._admin_client()
        resp = client.post('/admin/ansi/import-binfile', data={
            'bin_file': (io.BytesIO(data), 'test.bin'),
            'width': '20', 'name': 'BinImportTest',
        }, content_type='multipart/form-data', follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        with self.app.app_context():
            art = AnsiArt.query.filter_by(name='BinImportTest').first()
            self.assertIsNotNone(art)
            self.assertEqual(''.join(c['c'] for c in json.loads(art.grid_json)['cells']), text)

    def test_import_valid_xbin_file_creates_art(self):
        from anetbbs.web.ansi_editor import render_xbin
        from anetbbs.models import AnsiArt
        data = render_xbin({'width': 3, 'height': 1,
                            'cells': [{'c': ch, 'fg': 4, 'bg': 0} for ch in 'XYZ']})
        client = self._admin_client()
        resp = client.post('/admin/ansi/import-binfile', data={
            'bin_file': (io.BytesIO(data), 'test.xb'),
            'name': 'XbinImportTest',
        }, content_type='multipart/form-data', follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        with self.app.app_context():
            art = AnsiArt.query.filter_by(name='XbinImportTest').first()
            self.assertIsNotNone(art)
            self.assertEqual(''.join(c['c'] for c in json.loads(art.grid_json)['cells']), 'XYZ')

    def test_import_truncated_xbin_does_not_500(self):
        # Real robustness gap found and fixed: struct.error (raised by
        # a truncated header) isn't a ValueError or IndexError, so an
        # earlier version of the except clause would have let this
        # crash the route as an unhandled 500 instead of a clean flash
        # + redirect.
        client = self._admin_client()
        garbage = b'XBIN\x1a\x01\x02'  # magic present, header cut short
        resp = client.post('/admin/ansi/import-binfile', data={
            'bin_file': (io.BytesIO(garbage), 'broken.xb'),
            'name': 'ShouldNotCrash',
        }, content_type='multipart/form-data', follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        from anetbbs.models import AnsiArt
        with self.app.app_context():
            self.assertIsNone(AnsiArt.query.filter_by(name='ShouldNotCrash').first())

    def test_import_no_file_uploaded_does_not_crash(self):
        client = self._admin_client()
        resp = client.post('/admin/ansi/import-binfile', data={'name': 'x'},
                           content_type='multipart/form-data', follow_redirects=True)
        self.assertEqual(resp.status_code, 200)


if __name__ == '__main__':
    unittest.main()
