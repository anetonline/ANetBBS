"""Regression tests for call_core_web_override() -- the sync
counterpart to call_core_override() (test_core_mods_override.py),
added for a new web-side mods/core/ override point requested live
2026-09-28: a sysop-droppable pre-login "matrix"/landing page for
unauthenticated web visitors (data/mods/core/web_landing.py's
render_web_landing(request)), following the same fall-back-to-stock-
on-any-failure contract the four terminal-session override points
already have.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod
from anetbbs.core.mods_override import call_core_web_override


class CallCoreWebOverrideTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_dir = Path(self._tmp.name)
        (self.data_dir / 'mods' / 'core').mkdir(parents=True)

        class _FakeApp:
            config = {'DATA_DIR': str(self.data_dir)}

        self._patcher = patch('anetbbs.features.bbs_ui._app', return_value=_FakeApp())
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def _write_override(self, name, code):
        (self.data_dir / 'mods' / 'core' / f'{name}.py').write_text(code)

    def _stock(self):
        return 'STOCK'

    def test_no_override_file_calls_stock(self):
        result = call_core_web_override('web_landing', 'render_web_landing', self._stock)
        self.assertEqual(result, 'STOCK')

    def test_valid_override_wins_over_stock(self):
        self._write_override('web_landing', (
            "def render_web_landing(request):\n"
            "    return 'OVERRIDE:' + request\n"
        ))
        result = call_core_web_override(
            'web_landing', 'render_web_landing', self._stock, 'fake-request')
        self.assertEqual(result, 'OVERRIDE:fake-request')

    def test_override_returning_none_is_passed_through_not_replaced(self):
        # A mod that wants to fall through to the normal page (e.g.
        # "only show once per session") returns None -- the loader
        # must hand that back as-is, not silently call stock instead.
        self._write_override('web_landing', (
            "def render_web_landing(request):\n"
            "    return None\n"
        ))
        result = call_core_web_override(
            'web_landing', 'render_web_landing', self._stock, 'fake-request')
        self.assertIsNone(result)

    def test_missing_function_falls_back(self):
        self._write_override('web_landing', "x = 1\n")
        result = call_core_web_override('web_landing', 'render_web_landing', self._stock)
        self.assertEqual(result, 'STOCK')

    def test_syntax_error_falls_back(self):
        self._write_override('web_landing', "def broken(:\n")
        result = call_core_web_override('web_landing', 'render_web_landing', self._stock)
        self.assertEqual(result, 'STOCK')

    def test_runtime_exception_falls_back(self):
        self._write_override('web_landing', (
            "def render_web_landing(*a):\n"
            "    raise RuntimeError('boom')\n"
        ))
        result = call_core_web_override('web_landing', 'render_web_landing', self._stock)
        self.assertEqual(result, 'STOCK')

    def test_stock_not_invoked_when_override_succeeds(self):
        self._write_override('web_landing', (
            "def render_web_landing(*a):\n"
            "    return 'OVERRIDE'\n"
        ))
        called = {'stock': False}

        def _stock():
            called['stock'] = True
            return 'STOCK'

        result = call_core_web_override('web_landing', 'render_web_landing', _stock)
        self.assertEqual(result, 'OVERRIDE')
        self.assertFalse(called['stock'])


class HomePageWebLandingWiringTests(unittest.TestCase):
    """Confirms main.py's index() route actually wires the hook up
    correctly: shown to a logged-out visitor when an override exists,
    never shown once logged in, and a totally normal home page when no
    override file exists at all."""

    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent
                          / '.web_landing_wiring_test.db')
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
            user = User(username='webmodstest', email='wmt@example.com',
                       password_hash='x', access_level=10)
            db.session.add(user)
            db.session.commit()
            cls.user_id = user.id

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
        self.mods_core = Path(self._tmp.name) / 'mods' / 'core'
        self.mods_core.mkdir(parents=True)
        # bbs_ui._app() (which mods_override.py's _override_path() goes
        # through) re-syncs app.config from the Config CLASS on every
        # call, even on a cache hit -- so overriding app.config directly
        # on an already-built app instance gets silently clobbered right
        # back. Patch the class attribute itself, matching how other
        # tests repoint class-level config (e.g. SQLALCHEMY_DATABASE_URI).
        self._orig_data_dir = cfg_mod.Config.DATA_DIR
        cfg_mod.Config.DATA_DIR = self._tmp.name
        self.addCleanup(setattr, cfg_mod.Config, 'DATA_DIR', self._orig_data_dir)

    def _write_override(self, code):
        (self.mods_core / 'web_landing.py').write_text(code)

    def test_no_override_shows_normal_home_page(self):
        resp = self.app.test_client().get('/')
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b'GATEKEEPER MATRIX TEST MARKER', resp.data)

    def test_override_shown_to_logged_out_visitor(self):
        self._write_override(
            "def render_web_landing(request):\n"
            "    return 'GATEKEEPER MATRIX TEST MARKER', 200\n"
        )
        resp = self.app.test_client().get('/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'GATEKEEPER MATRIX TEST MARKER', resp.data)

    def test_override_skipped_for_logged_in_user(self):
        self._write_override(
            "def render_web_landing(request):\n"
            "    return 'GATEKEEPER MATRIX TEST MARKER', 200\n"
        )
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(self.user_id)
            sess['_fresh'] = True
        resp = client.get('/')
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b'GATEKEEPER MATRIX TEST MARKER', resp.data)

    def test_override_returning_none_falls_through_to_normal_home_page(self):
        self._write_override(
            "def render_web_landing(request):\n"
            "    return None\n"
        )
        resp = self.app.test_client().get('/')
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b'GATEKEEPER MATRIX TEST MARKER', resp.data)

    def test_broken_override_degrades_to_normal_home_page(self):
        self._write_override("def render_web_landing(:\n")
        resp = self.app.test_client().get('/')
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b'GATEKEEPER MATRIX TEST MARKER', resp.data)


if __name__ == '__main__':
    unittest.main()
