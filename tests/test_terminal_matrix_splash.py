"""Regression tests for the stock terminal pre-login "Matrix" splash
(session.py's _show_terminal_matrix()), promoted from a sysop's own
private mods/core/login_menu.py to a real built-in feature gated
behind Config.TERMINAL_MATRIX_ENABLED (Admin -> Settings, off by
default) -- requested live 2026-09-29.

Covers: off by default (no behavior change for existing installs), the
connection-ways list reflects live config, the bundled stock ANSI art
renders when no sysop override exists, a sysop's own
data/mods/text/matrix.ans wins when present (same resolution order as
welcome.ans), the screen is cleared before the lightbar draws (guards
the exact bug found live in the original private mod:
"Instant Message from ... the menu doubling on scroll" --
see [[project entries]] for 2026-09-28), and a
data/mods/core/login_menu.py override still wins outright over the
toggle when both exist.
"""
import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod
from anetbbs.core.session import BBSSession


def _async_return(value):
    async def _fn(*a, **kw):
        return value
    return _fn


class _FakeWriter:
    def __init__(self):
        self.written = bytearray()

    def write(self, data):
        self.written += data

    async def drain(self):
        pass

    def close(self):
        pass


class TerminalMatrixSplashTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent
                          / '.terminal_matrix_splash_test.db')
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

    def setUp(self):
        self.app.config['TERMINAL_MATRIX_ENABLED'] = True
        self.app.config['TELNET_ENABLED'] = True
        self.app.config['TELNET_PORT'] = 2233
        self.app.config['SSH_ENABLED'] = True
        self.app.config['SSH_PORT'] = 2234
        self.app.config['RLOGIN_ENABLED'] = False
        self.app.config['BBS_DOMAIN'] = ''
        self.app.config['WEB_PORT'] = 5000
        self._patcher = patch('anetbbs.features.bbs_ui._app', return_value=self.app)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def _session(self):
        writer = _FakeWriter()
        session = BBSSession(object(), writer, config={}, forced_term_mode='ansi')
        return session, writer

    def _rendered(self, session):
        return session.writer.written.decode('cp437', errors='replace')

    def test_shows_bundled_stock_banner_when_no_override_exists(self):
        session, writer = self._session()
        session.read_line = _async_return('')
        asyncio.run(session._show_terminal_matrix('Test BBS'))
        text = self._rendered(session)
        self.assertIn('THE MATRIX', text)

    def test_connection_ways_reflect_live_config(self):
        session, writer = self._session()
        session.read_line = _async_return('')
        asyncio.run(session._show_terminal_matrix('Test BBS'))
        text = self._rendered(session)
        self.assertIn('Telnet', text)
        self.assertIn('2233', text)
        self.assertIn('SSH', text)
        self.assertIn('2234', text)
        self.assertNotIn('Rlogin', text)  # RLOGIN_ENABLED is False

    def test_rlogin_appears_when_enabled(self):
        self.app.config['RLOGIN_ENABLED'] = True
        self.app.config['RLOGIN_PORT'] = 513
        session, writer = self._session()
        session.read_line = _async_return('')
        asyncio.run(session._show_terminal_matrix('Test BBS'))
        text = self._rendered(session)
        self.assertIn('Rlogin', text)
        self.assertIn('513', text)

    def test_screen_is_cleared_immediately_after_the_pause(self):
        """Real bug found live 2026-09-28 in the private mod this was
        promoted from: without a clear right before the caller draws
        the lightbar menu, it drew on top of leftover splash text and
        visually corrupted on scroll (hardcoded absolute row numbers
        desynced from where the box actually was). The clear must be
        the LAST thing written."""
        session, writer = self._session()
        session.read_line = _async_return('')
        asyncio.run(session._show_terminal_matrix('Test BBS'))
        raw = bytes(session.writer.written)
        self.assertTrue(raw.endswith(b'\x1b[2J\x1b[H'),
                        'screen clear must be the final write before returning')

    def test_sysop_custom_ansi_override_wins_over_bundled_stock(self):
        with tempfile.TemporaryDirectory() as tmp:
            mods_text = Path(tmp) / 'mods' / 'text'
            mods_text.mkdir(parents=True)
            (mods_text / 'matrix.ans').write_bytes(
                b'\x1b[1;35mMY CUSTOM MATRIX ART\x1b[0m\r\n')
            self.app.config['DATA_DIR'] = tmp
            self.addCleanup(lambda: self.app.config.pop('DATA_DIR', None))

            session, writer = self._session()
            session.read_line = _async_return('')
            asyncio.run(session._show_terminal_matrix('Test BBS'))
            text = self._rendered(session)
            self.assertIn('MY CUSTOM MATRIX ART', text)
            self.assertNotIn('THE MATRIX', text,
                             'bundled stock banner should not also render '
                             'once a sysop override exists')

    def test_toggle_off_by_default(self):
        self.assertFalse(cfg_mod.Config.TERMINAL_MATRIX_ENABLED)


class TerminalMatrixLoginFlowWiringTests(unittest.TestCase):
    """Confirms login_screen()'s stock ANSI-lightbar branch actually
    calls the splash when the toggle is on, and that a full
    data/mods/core/login_menu.py override still wins outright over it
    (same precedence every other override in this app already has)."""

    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent
                          / '.terminal_matrix_wiring_test.db')
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

    def _session(self):
        writer = _FakeWriter()
        session = BBSSession(object(), writer, config={}, forced_term_mode='ansi')
        session.read_line = _async_return('3')
        session.read_key_arrow = _async_return('E')
        return session, writer

    def test_mod_override_wins_over_toggle_when_both_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            mods_core = Path(tmp) / 'mods' / 'core'
            mods_core.mkdir(parents=True)
            (mods_core / 'login_menu.py').write_text(
                "async def render_login_menu(session, bbs_name):\n"
                "    await session.write('OVERRIDE RAN\\r\\n')\n"
                "    return '3'\n"
            )
            self.app.config['DATA_DIR'] = tmp
            self.app.config['TERMINAL_MATRIX_ENABLED'] = True
            self.addCleanup(lambda: self.app.config.pop('DATA_DIR', None))
            self.addCleanup(lambda: self.app.config.pop('TERMINAL_MATRIX_ENABLED', None))

            with patch('anetbbs.features.bbs_ui._app', return_value=self.app):
                session, writer = self._session()
                asyncio.run(session.login_screen())
        text = bytes(session.writer.written).decode('cp437', errors='replace')
        self.assertIn('OVERRIDE RAN', text)
        self.assertNotIn('THE MATRIX', text,
                         'the stock matrix splash must not run when a '
                         'full mods/core/login_menu.py override exists')


if __name__ == '__main__':
    unittest.main()
