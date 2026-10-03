"""Regression tests for the generalized Admin -> Add-ons page
(anetbbs/web/addons.py) -- GET /admin/addons/ (status listing) and
POST /admin/addons/<id>/install (download + extract), covering both
registered extract modes:

- 'preserve_paths' (enhanced_client): a real .tar.gz shaped like
  tools/build_enhanced_client_addon.sh's own output (a single
  top-level versioned directory wrapping the real file tree) --
  confirms the top-level-dir stripping, the allowed_prefixes
  allowlist (an entry outside it is skipped, never written), and the
  needs_restart hint.
- 'flatten' (tdf_fonts): a real .zip, same shape as
  tests/test_ansi_editor_font_pack_install.py's own fixture, confirming
  the generalized route produces the same flattened result as that
  feature's own dedicated route.

Uses a REAL local HTTP server (stdlib http.server, background thread)
serving real archive bytes -- not a mocked requests.get() -- matching
this repo's established "real socket" bar for anything that talks
HTTP, and the same _OneShotHTTPHandler/_TestServer shape already
proven in test_ansi_editor_font_pack_install.py (duplicated here per
this repo's self-contained-per-file-fixture convention, not imported).
"""
import http.server
import io
import json
import os
import sys
import tarfile
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


def _build_zip(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        for path, content in entries.items():
            z.writestr(path, content)
    return buf.getvalue()


def _build_targz(entries):
    """entries: {tar-internal-path: raw bytes} -- paths are expected to
    already include a shared top-level directory, same shape
    build_enhanced_client_addon.sh's own tar --transform produces."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as t:
        for path, content in entries.items():
            info = tarfile.TarInfo(name=path)
            info.size = len(content)
            t.addfile(info, io.BytesIO(content))
    return buf.getvalue()


class _OneShotHTTPHandler(http.server.BaseHTTPRequestHandler):
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


class AddonsInstallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.addons_install_test.db')
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
            admin = User(username='addonstest', email='addons@example.com',
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
        self._orig = {
            'TDF_FONTS_DIR': self.app.config.get('TDF_FONTS_DIR'),
            'TDF_FONTS_PACK_URL': self.app.config.get('TDF_FONTS_PACK_URL'),
            'ENHANCED_CLIENT_ADDON_URL': self.app.config.get('ENHANCED_CLIENT_ADDON_URL'),
            'INSTALL_DIR': self.app.config.get('INSTALL_DIR'),
        }
        self.addCleanup(self._restore)
        self.app.config['TDF_FONTS_DIR'] = os.path.join(self._tmp.name, 'fonts')
        self.app.config['TDF_FONTS_PACK_URL'] = ''
        self.app.config['ENHANCED_CLIENT_ADDON_URL'] = ''
        # Give enhanced_client's preserve_paths extraction a throwaway
        # install root instead of writing into the real checkout.
        self.app.config['INSTALL_DIR'] = self._tmp.name

    def _restore(self):
        for k, v in self._orig.items():
            self.app.config[k] = v

    def _admin_client(self):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True
        return client

    def test_index_lists_both_addons_unconfigured_by_default(self):
        client = self._admin_client()
        resp = client.get('/admin/addons/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Enhanced Client', resp.data)
        self.assertIn(b'TheDraw Font Pack', resp.data)

    def test_unknown_addon_id_is_a_clean_404(self):
        client = self._admin_client()
        resp = client.post('/admin/addons/nonexistent/install')
        self.assertEqual(resp.status_code, 404)

    def test_no_url_configured_is_a_clean_400(self):
        client = self._admin_client()
        resp = client.post('/admin/addons/enhanced_client/install')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('ENHANCED_CLIENT_ADDON_URL', json.loads(resp.data)['error'])

    def test_enhanced_client_preserve_paths_strips_topdir_and_restarts_hint(self):
        """Real .tar.gz shaped exactly like build_enhanced_client_addon.sh's
        own output -- one shared top-level versioned directory wrapping
        the real file tree. Confirms: the topdir is stripped, allowed
        files land at their real relative path under the install root,
        and the response carries a restart_hint (anetbbs.service)."""
        topdir = 'ANetBBS-EnhancedClient-addon-9.9.9'
        archive = _build_targz({
            f'{topdir}/anetbbs/core/enhanced_server.py': b'# server',
            f'{topdir}/anetbbs/features/enhanced_protocol.py': b'# protocol',
            f'{topdir}/anetbbs/enhanced_client/index.html': b'<html></html>',
            f'{topdir}/anetbbs/enhanced_client/js/app.js': b'// app',
        })
        server = _TestServer({'/pack.tar.gz': (200, 'application/gzip', archive)})
        self.addCleanup(server.stop)
        self.app.config['ENHANCED_CLIENT_ADDON_URL'] = server.url('/pack.tar.gz')

        client = self._admin_client()
        resp = client.post('/admin/addons/enhanced_client/install')
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertEqual(data['installed'], 4)
        self.assertEqual(data['skipped'], 0)
        self.assertIn('anetbbs.service', data['restart_hint'])

        root = self._tmp.name
        self.assertTrue(os.path.exists(
            os.path.join(root, 'anetbbs', 'core', 'enhanced_server.py')))
        self.assertTrue(os.path.exists(
            os.path.join(root, 'anetbbs', 'enhanced_client', 'js', 'app.js')))

        # The index listing now reports it installed.
        index_resp = client.get('/admin/addons/')
        self.assertIn(b'Installed', index_resp.data)

    def test_enhanced_client_skips_anything_outside_the_allowlist(self):
        """A disallowed entry (e.g. something trying to land outside the
        known file set, or at an unexpected path) is skipped and never
        written -- the allowlist is the whole point of preserve_paths
        mode, not an afterthought."""
        topdir = 'ANetBBS-EnhancedClient-addon-9.9.9'
        archive = _build_targz({
            f'{topdir}/anetbbs/core/enhanced_server.py': b'# server',
            f'{topdir}/anetbbs/core/session.py': b'# NOT part of the addon manifest',
            f'{topdir}/evil.sh': b'#!/bin/sh\necho pwned',
        })
        server = _TestServer({'/pack.tar.gz': (200, 'application/gzip', archive)})
        self.addCleanup(server.stop)
        self.app.config['ENHANCED_CLIENT_ADDON_URL'] = server.url('/pack.tar.gz')

        client = self._admin_client()
        resp = client.post('/admin/addons/enhanced_client/install')
        data = json.loads(resp.data)
        self.assertEqual(data['installed'], 1)
        self.assertEqual(data['skipped'], 2)

        root = self._tmp.name
        self.assertTrue(os.path.exists(
            os.path.join(root, 'anetbbs', 'core', 'enhanced_server.py')))
        self.assertFalse(os.path.exists(os.path.join(root, 'anetbbs', 'core', 'session.py')))
        self.assertFalse(os.path.exists(os.path.join(root, 'evil.sh')))

    def test_tdf_fonts_flatten_mode_matches_the_dedicated_route_shape(self):
        zip_bytes = _build_zip({
            'tdf-fonts/FLAT.TDF': b'TDF not a real font but thats not under test here',
            'tdf-fonts/SETS/NESTED.TDF': b'another fake tdf',
            'tdf-fonts/README.txt': b'not a font, must be skipped by suffix',
        })
        server = _TestServer({'/pack.zip': (200, 'application/zip', zip_bytes)})
        self.addCleanup(server.stop)
        self.app.config['TDF_FONTS_PACK_URL'] = server.url('/pack.zip')

        client = self._admin_client()
        resp = client.post('/admin/addons/tdf_fonts/install')
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertEqual(data['installed'], 2)
        self.assertNotIn('restart_hint', data)

        fonts_dir = self.app.config['TDF_FONTS_DIR']
        self.assertEqual(sorted(os.listdir(fonts_dir)), ['FLAT.TDF', 'NESTED.TDF'])

    def test_unreachable_url_is_a_clean_502(self):
        self.app.config['ENHANCED_CLIENT_ADDON_URL'] = 'http://127.0.0.1:1/pack.tar.gz'
        client = self._admin_client()
        resp = client.post('/admin/addons/enhanced_client/install')
        self.assertEqual(resp.status_code, 502)

    def test_bad_archive_is_a_clean_500_not_a_crash(self):
        server = _TestServer({'/pack.tar.gz': (200, 'application/gzip', b'not a real tarball')})
        self.addCleanup(server.stop)
        self.app.config['ENHANCED_CLIENT_ADDON_URL'] = server.url('/pack.tar.gz')
        client = self._admin_client()
        resp = client.post('/admin/addons/enhanced_client/install')
        self.assertEqual(resp.status_code, 500)
        self.assertIn('error', json.loads(resp.data))


if __name__ == '__main__':
    unittest.main()
