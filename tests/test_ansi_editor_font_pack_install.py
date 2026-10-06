"""Regression tests for the web ANSI editor's "Download & Install Fonts"
button (POST /admin/ansi/fonts/install, anetbbs/web/ansi_editor.py's
install_fonts()) -- lets a sysop populate TDF_FONTS_DIR from a zip
hosted at TDF_FONTS_PACK_URL with one click, instead of manually
downloading/extracting a font pack by hand. Added alongside the real
~29MB ANetDRAW font pack (1,205 flat .TDF files + a SETS/ subfolder of
43 mega-pack files) being moved OFF the ANetBBS repo/release tarball
entirely and onto a GitHub Release asset this route fetches on demand
-- same reasoning as tools/download_jsdos.sh already uses for the
~5MB js-dos runtime.

Uses a REAL local HTTP server (stdlib http.server, a background
thread) serving a REAL zip built from genuinely valid, parseable .TDF
bytes -- not a mocked requests.get() -- so these tests exercise the
actual download + zip-extraction + flattening + scan_fonts() pipeline
end to end, the same way the real feature was verified live against
the real ~1,241-file pack before this file was written (installed=1241,
list_fonts() afterward saw 5,098 real font entries -- confirms this
pack is well over the "almost 4k" figure once SETS/ is included).

_build_tdf() is the same minimal-but-real synthetic .TDF construction
already proven in test_ansi_editor_font_routes.py -- duplicated here
rather than imported, matching this repo's established convention of
self-contained per-file test fixtures.
"""
import http.server
import io
import json
import os
import struct
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


def _build_tdf(name=b'FONTINSTALLTEST'):
    from anetbbs.features import tdf_fonts
    offs = [0xFFFF] * 94
    data = bytearray()
    offs[ord('H') - 33] = len(data)
    data += bytes([2, 1]) + b'HH' + b'\x00'
    header = (b'\x55\xAA\x00\xFF' + bytes([len(name)]) + name.ljust(12, b' ') +
             b'\x00\x00\x00\x00' + bytes([tdf_fonts.BLOCK, 1]) +
             struct.pack('<H', len(data)))
    offs_bytes = b''.join(struct.pack('<H', o) for o in offs)
    return tdf_fonts.TDF_MAGIC + header + offs_bytes + bytes(data)


def _build_zip(entries):
    """entries: {zip-internal-path: raw bytes}."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        for path, content in entries.items():
            z.writestr(path, content)
    return buf.getvalue()


class _OneShotHTTPHandler(http.server.BaseHTTPRequestHandler):
    """Serves exactly one canned (status, content_type, body) response
    per request, from a dict keyed by path -- set via server.responses
    before starting. Silences the default stderr access log (this
    test's own output stays clean, matching every other test file in
    this suite that touches a real socket)."""
    def log_message(self, *a, **kw):
        pass

    def do_GET(self):
        resp = self.server.responses.get(self.path)
        if resp is None:
            self.send_response(404)
            self.end_headers()
            return
        status, content_type, body = resp
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _TestServer:
    """Real local HTTP server on an ephemeral port, background thread,
    torn down in tearDown -- matches test_mrc_bridge_tcp_listener.py's
    own "real socket, not mocked" bar for anything that talks HTTP."""
    def __init__(self, responses):
        self.httpd = http.server.HTTPServer(('127.0.0.1', 0), _OneShotHTTPHandler)
        self.httpd.responses = responses
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def url(self, path):
        return f'http://127.0.0.1:{self.port}{path}'

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class AnsiEditorFontPackInstallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.ansi_font_install_test.db')
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
            admin = User(username='fontinstalltest', email='fit@example.com',
                        password_hash='x', access_level=100, is_admin=True)
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

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._orig_fonts_dir = self.app.config.get('TDF_FONTS_DIR')
        self._orig_pack_url = self.app.config.get('TDF_FONTS_PACK_URL')
        self.addCleanup(self._restore_config)
        self.app.config['TDF_FONTS_DIR'] = os.path.join(self._tmp.name, 'fonts')
        self.app.config['TDF_FONTS_PACK_URL'] = ''

    def _restore_config(self):
        self.app.config['TDF_FONTS_DIR'] = self._orig_fonts_dir
        self.app.config['TDF_FONTS_PACK_URL'] = self._orig_pack_url

    def _admin_client(self):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True
        return client

    def test_no_pack_url_configured_is_a_clean_400(self):
        client = self._admin_client()
        resp = client.post('/admin/ansi/fonts/install')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('TDF_FONTS_PACK_URL', json.loads(resp.data)['error'])

    def test_real_download_and_extract_flattens_and_installs(self):
        """The core case: a zip shaped like the real ANetDRAW pack --
        a flat top-level .TDF plus one nested under a SETS/-style
        subfolder -- both land directly in TDF_FONTS_DIR (flattened),
        and the font picker sees both afterward."""
        zip_bytes = _build_zip({
            'tdf-fonts/FLAT.TDF': _build_tdf(b'FLATFONT'),
            'tdf-fonts/SETS/NESTED.TDF': _build_tdf(b'NESTEDFONT'),
        })
        server = _TestServer({'/pack.zip': (200, 'application/zip', zip_bytes)})
        self.addCleanup(server.stop)
        self.app.config['TDF_FONTS_PACK_URL'] = server.url('/pack.zip')

        client = self._admin_client()
        resp = client.post('/admin/ansi/fonts/install')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(json.loads(resp.data)['installed'], 2)

        fonts_dir = self.app.config['TDF_FONTS_DIR']
        on_disk = sorted(os.listdir(fonts_dir))
        self.assertEqual(on_disk, ['FLAT.TDF', 'NESTED.TDF'],
                         'both files must be flattened into the same '
                         'top-level directory, matching scan_fonts()\'s '
                         'own non-recursive scan')

        list_resp = client.get('/admin/ansi/fonts')
        names = {e['name'] for e in json.loads(list_resp.data)}
        self.assertEqual(names, {'FLATFONT', 'NESTEDFONT'})

    def test_non_tdf_entries_in_the_zip_are_skipped(self):
        zip_bytes = _build_zip({
            'tdf-fonts/REAL.TDF': _build_tdf(b'REALFONT'),
            'tdf-fonts/README.txt': b'not a font',
        })
        server = _TestServer({'/pack.zip': (200, 'application/zip', zip_bytes)})
        self.addCleanup(server.stop)
        self.app.config['TDF_FONTS_PACK_URL'] = server.url('/pack.zip')

        client = self._admin_client()
        resp = client.post('/admin/ansi/fonts/install')
        self.assertEqual(json.loads(resp.data)['installed'], 1)
        self.assertEqual(os.listdir(self.app.config['TDF_FONTS_DIR']), ['REAL.TDF'])

    def test_bad_zip_is_a_clean_500_not_a_crash(self):
        server = _TestServer({'/pack.zip': (200, 'application/zip', b'not actually a zip file')})
        self.addCleanup(server.stop)
        self.app.config['TDF_FONTS_PACK_URL'] = server.url('/pack.zip')

        client = self._admin_client()
        resp = client.post('/admin/ansi/fonts/install')
        self.assertEqual(resp.status_code, 500)
        self.assertIn('error', json.loads(resp.data))

    def test_zip_bomb_is_refused_before_decompression(self):
        """A real audit finding: install_fonts() read each .tdf member
        via src.read() with no check on its declared uncompressed
        size. A tiny, highly-compressed .zip could expand to hundreds
        of MB in memory the instant it's read -- must now be refused
        as a clean 500, not decompressed."""
        from anetbbs.echomail.zip_safety import MAX_MEMBER_UNCOMPRESSED

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr('tdf-fonts/BOMB.TDF',
                       b'\x00' * (MAX_MEMBER_UNCOMPRESSED + 1024),
                       compresslevel=9)
        bomb = buf.getvalue()
        self.assertLess(len(bomb), 200 * 1024,
                        'the archive itself must stay tiny -- proves the '
                        'check fires from the declared-size header, not '
                        'after actually decompressing')
        server = _TestServer({'/pack.zip': (200, 'application/zip', bomb)})
        self.addCleanup(server.stop)
        self.app.config['TDF_FONTS_PACK_URL'] = server.url('/pack.zip')

        client = self._admin_client()
        resp = client.post('/admin/ansi/fonts/install')
        self.assertEqual(resp.status_code, 500)
        self.assertIn('error', json.loads(resp.data))

    def test_zip_with_no_tdf_files_is_a_clean_500(self):
        zip_bytes = _build_zip({'tdf-fonts/README.txt': b'nothing but this'})
        server = _TestServer({'/pack.zip': (200, 'application/zip', zip_bytes)})
        self.addCleanup(server.stop)
        self.app.config['TDF_FONTS_PACK_URL'] = server.url('/pack.zip')

        client = self._admin_client()
        resp = client.post('/admin/ansi/fonts/install')
        self.assertEqual(resp.status_code, 500)
        self.assertIn('no .TDF fonts', json.loads(resp.data)['error'])

    def test_unreachable_url_is_a_clean_502(self):
        # Nothing is listening on this port -- a real connection failure,
        # not a mocked one.
        self.app.config['TDF_FONTS_PACK_URL'] = 'http://127.0.0.1:1/pack.zip'
        client = self._admin_client()
        resp = client.post('/admin/ansi/fonts/install')
        self.assertEqual(resp.status_code, 502)
        self.assertIn('error', json.loads(resp.data))

    def test_404_from_the_pack_host_is_a_clean_502(self):
        server = _TestServer({})  # no routes registered -> every request 404s
        self.addCleanup(server.stop)
        self.app.config['TDF_FONTS_PACK_URL'] = server.url('/missing.zip')

        client = self._admin_client()
        resp = client.post('/admin/ansi/fonts/install')
        self.assertEqual(resp.status_code, 502)

    def test_re_running_install_overwrites_cleanly_not_duplicated(self):
        zip_bytes = _build_zip({'tdf-fonts/ONE.TDF': _build_tdf(b'ONEFONT')})
        server = _TestServer({'/pack.zip': (200, 'application/zip', zip_bytes)})
        self.addCleanup(server.stop)
        self.app.config['TDF_FONTS_PACK_URL'] = server.url('/pack.zip')

        client = self._admin_client()
        client.post('/admin/ansi/fonts/install')
        resp = client.post('/admin/ansi/fonts/install')
        self.assertEqual(json.loads(resp.data)['installed'], 1)
        self.assertEqual(os.listdir(self.app.config['TDF_FONTS_DIR']), ['ONE.TDF'])


if __name__ == '__main__':
    unittest.main()
