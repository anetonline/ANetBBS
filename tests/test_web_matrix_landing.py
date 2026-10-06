"""Regression tests for the stock web pre-login "Matrix" landing page
(web/matrix_landing.py's render_stock_web_matrix(), wired into
main.py's index()), promoted from a sysop's own private
mods/core/web_landing.py to a real built-in feature gated behind
Config.WEB_MATRIX_ENABLED (Admin -> Settings, off by default) --
requested live 2026-09-29.

Covers: off by default (no behavior change for existing installs),
shown only to logged-out visitors, the once-per-session skip cookie,
and that a full data/mods/core/web_landing.py override still wins
outright over the toggle when both exist (same precedence every other
override in this app already has).
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class WebMatrixLandingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent
                          / '.web_matrix_landing_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()
            u = User(username='webmatrixtest', email='wmxt@example.com',
                    is_active=True)
            u.set_password('correcthorsebatterystaple')
            db.session.add(u)
            db.session.commit()
            cls.user_id = u.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def setUp(self):
        self._orig_toggle = self.app.config.get('WEB_MATRIX_ENABLED')
        self.addCleanup(
            lambda: self.app.config.__setitem__('WEB_MATRIX_ENABLED', self._orig_toggle))

    def test_toggle_off_by_default(self):
        self.assertFalse(cfg_mod.Config.WEB_MATRIX_ENABLED)

    def test_not_shown_when_toggle_off(self):
        self.app.config['WEB_MATRIX_ENABLED'] = False
        resp = self.app.test_client().get('/')
        self.assertNotIn(b'Connect in the Browser', resp.data)

    def test_shown_to_logged_out_visitor_when_toggle_on(self):
        self.app.config['WEB_MATRIX_ENABLED'] = True
        resp = self.app.test_client().get('/')
        self.assertIn(b'Connect in the Browser', resp.data)

    def test_not_shown_to_logged_in_visitor(self):
        self.app.config['WEB_MATRIX_ENABLED'] = True
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(self.user_id)
            sess['_fresh'] = True
        resp = client.get('/')
        self.assertNotIn(b'Connect in the Browser', resp.data)

    def test_skip_cookie_hides_it_on_next_visit(self):
        self.app.config['WEB_MATRIX_ENABLED'] = True
        client = self.app.test_client()
        first = client.get('/')
        self.assertIn(b'Connect in the Browser', first.data)
        import re
        m = re.search(rb'href="(/\?mtx=skip)"', first.data)
        self.assertIsNotNone(m)
        redirect_resp = client.get(m.group(1).decode(), follow_redirects=False)
        self.assertEqual(redirect_resp.status_code, 302)
        second = client.get('/')
        self.assertNotIn(b'Connect in the Browser', second.data)

    def test_host_header_is_escaped_not_reflected_raw(self):
        """BBS_DOMAIN unset falls back to the request's own Host header
        for the "connect directly" hint -- an attacker-supplied Host
        header must come back HTML-escaped, not injected raw into the
        page (a real audit finding: unescaped reflection into
        `<code>{connect_host}</code>`).

        Calls render_stock_web_matrix() directly with a fake request
        object rather than going through a real HTTP request -- current
        Werkzeug already rejects a Host header containing '<'/'>' at
        the WSGI layer (its own, separate hardening), which would mask
        whether *this* code's own escaping is doing its job."""
        self.app.config['WEB_MATRIX_ENABLED'] = True
        self.app.config['BBS_DOMAIN'] = ''
        from anetbbs.web.matrix_landing import render_stock_web_matrix
        from unittest.mock import MagicMock

        fake_request = MagicMock()
        fake_request.cookies.get.return_value = None
        fake_request.args.get.return_value = None
        fake_request.host = '<script>alert(1)</script>:8080'
        with self.app.test_request_context('/'):
            html = render_stock_web_matrix(fake_request)
        self.assertNotIn('<script>alert(1)</script>', html)
        self.assertIn('&lt;script&gt;', html)

    def test_mod_override_wins_over_toggle_when_both_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            mods_core = Path(tmp) / 'mods' / 'core'
            mods_core.mkdir(parents=True)
            (mods_core / 'web_landing.py').write_text(
                "def render_web_landing(request):\n"
                "    return 'CUSTOM MOD LANDING PAGE', 200\n"
            )
            orig_data_dir = cfg_mod.Config.DATA_DIR
            cfg_mod.Config.DATA_DIR = tmp
            self.app.config['WEB_MATRIX_ENABLED'] = True
            try:
                resp = self.app.test_client().get('/')
                self.assertIn(b'CUSTOM MOD LANDING PAGE', resp.data)
                self.assertNotIn(b'Connect in the Browser', resp.data,
                                 'the stock matrix must not also render '
                                 'once a full mod override exists')
            finally:
                cfg_mod.Config.DATA_DIR = orig_data_dir


if __name__ == '__main__':
    unittest.main()
