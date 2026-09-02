"""Regression test for a real High finding from a security/performance
audit (2026-09-02): a `goto` menu item whose action_args names a
BbsMenu that doesn't exist (a sysop deleted/renamed a submenu that's
still linked from elsewhere, or typo'd one when saving) used to write
"Menu '<name>' not found." and `return` from run_menu() -- which
unwinds all the way out through core/session.py's start(), running
logoff modules and disconnecting the user's ENTIRE session, just from
pressing one broken hotkey.

petscii_ui.py's own menu loop already fails safe into its hardcoded
default menu for the identical bug shape ("Broken 'goto' target, or the
default menu got deleted mid-session -- fail safe into the always-
working hardcoded menu rather than dead-ending the session"); the
ANSI/main menu engine never got the same fix. Fixed by falling back to
BBSMenuUI.show_main() instead of returning an error.

Drives the REAL menu_engine.run_menu() against a seeded DB menu, using
the same fake reader/writer test-double pattern already established in
test_menu_engine_afk_redraw.py.
"""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class _FakeWriter:
    def __init__(self, peer=('1.2.3.4', 1234)):
        self._peer = peer
        self.written = bytearray()
        self._closing = False

    def get_extra_info(self, key):
        return self._peer if key == 'peername' else None

    def write(self, data):
        self.written += data

    async def drain(self):
        pass

    def is_closing(self):
        return self._closing

    def close(self):
        self._closing = True

    async def wait_closed(self):
        pass


class _InstantReader:
    """Feeds `chunks` with no delay, then goes dry."""
    def __init__(self, chunks):
        self._chunks = list(chunks)

    async def read(self, n=1):
        if self._chunks:
            return self._chunks.pop(0)
        return b''


class MenuEngineGotoMissingMenuFailsafeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.menu_goto_missing_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, BbsMenu, BbsMenuItem
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()
            menu = BbsMenu(name='startmenu', title='Start Menu',
                           prompt='Choice: ', min_access=0)
            db.session.add(menu)
            db.session.flush()
            db.session.add(BbsMenuItem(
                menu_id=menu.id, hotkey='G', label='Go somewhere broken',
                action_type='goto', action_args='doesnotexist',
                is_visible=True, min_access=0, sort_order=0))
            db.session.commit()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _make_session(self, reader):
        from anetbbs.core.session import BBSSession
        writer = _FakeWriter()
        session = BBSSession(reader, writer, config={})
        session.user = {'id': 1, 'access_level': 10}
        session.window_size = (80, 24)
        return session, writer

    def test_goto_to_a_missing_menu_falls_back_instead_of_ending_the_session(self):
        from anetbbs.features import menu_engine

        reader = _InstantReader([b'G'])
        session, writer = self._make_session(reader)

        with patch.object(menu_engine.BBSMenuUI, 'show_main',
                          AsyncMock()) as mock_show_main:
            # Must complete normally (return), not raise -- the old bug
            # was already "safe" in the sense of not crashing, but it
            # unwound the whole call stack; this proves run_menu() now
            # returns via the fallback path, same shape either way, so
            # the meaningful assertion is on WHAT happened, below.
            asyncio.run(menu_engine.run_menu(session, start='startmenu'))

        mock_show_main.assert_awaited_once()
        out = bytes(writer.written)
        self.assertNotIn(b"not found", out,
                         'must not show the old dead-end error message')


if __name__ == '__main__':
    unittest.main()
