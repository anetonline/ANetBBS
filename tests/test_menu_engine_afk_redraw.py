"""Regression test for a real live report: after the AFK screensaver
was dismissed with a keystroke, the caller saw "Welcome back!" and a
bare "Choice:" prompt with no menu content -- had to press ANOTHER key
before the actual menu (banner/items) reappeared.

Root cause: read_key()'s own _AFKInterrupted handling only had `prompt`
(often just a trailing "Choice: ") to redraw with -- the real menu
content was drawn by the CALLER (menu_engine.py's run_menu()) before
ever calling read_key(), and the AFK screensaver's own screen-clear
wiped it. Fixed by factoring run_menu()'s drawing logic into a closure
passed as read_key()'s new on_afk_redraw hook, so a single dismissing
keystroke both wakes the session AND redraws the full menu in one go.

Drives the REAL BBSSession.read_key()/_run_afk_sequence() and the REAL
menu_engine.run_menu() together (not mocked) against a seeded DB menu,
using the same fake reader/writer test-double pattern already
established in test_afk_screensaver.py / test_cursor_style_spinning.py.
"""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

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


class _SlowReader:
    """Exact copy of the test double already established in
    test_afk_screensaver.py -- blocks until `delay` seconds have
    elapsed since the FIRST read() call (an absolute deadline), then
    drains `chunks` with no further delay."""
    def __init__(self, chunks, delay=0.0):
        self._chunks = list(chunks)
        self._delay = delay
        self._deadline = None

    async def read(self, n=1):
        loop = asyncio.get_event_loop()
        if self._deadline is None:
            self._deadline = loop.time() + self._delay
        while loop.time() < self._deadline:
            await asyncio.sleep(min(self._deadline - loop.time(), 0.005))
        if self._chunks:
            return self._chunks.pop(0)
        return b''


class MenuEngineAFKRedrawTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.menu_afk_redraw_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, BbsMenu
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()
            db.session.add(BbsMenu(
                name='afktestmenu', title='AFK Test Menu',
                prompt='Choice: ', min_access=0))
            db.session.commit()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _make_session(self, reader, afk_warning_seconds):
        from anetbbs.core.session import BBSSession
        writer = _FakeWriter()
        session = BBSSession(reader, writer, config={})
        session.user = {'id': 1, 'access_level': 10}
        session.afk_warning_seconds = afk_warning_seconds
        session.idle_timeout = 0
        session.window_size = (80, 24)
        return session, writer

    def test_afk_dismissal_redraws_the_full_menu_not_just_the_prompt(self):
        import anetbbs.core.session as session_mod
        from anetbbs.core.session import CarrierLost
        from anetbbs.features.menu_engine import run_menu

        # 'M' arrives after crossing the AFK threshold and dismisses
        # the warning countdown; reader then goes dry (no further
        # bytes) so run_menu()'s next read raises CarrierLost, ending
        # the loop cleanly for inspection.
        reader = _SlowReader([b'M'], delay=0.05)
        session, writer = self._make_session(reader, afk_warning_seconds=0.02)

        async def _drive():
            with patch.object(session_mod, '_AFK_TICK_SECONDS', 0.01), \
                 patch.object(session_mod, '_AFK_WARNING_COUNTDOWN_SECONDS', 20):
                try:
                    await run_menu(session, start='afktestmenu')
                except CarrierLost:
                    pass

        asyncio.run(_drive())

        out = bytes(writer.written)
        self.assertIn(b'idle a while', out)      # AFK warning stage ran
        self.assertIn(b'Welcome back', out)       # dismissed
        idx = out.index(b'Welcome back')
        # The real menu title must be redrawn AFTER "Welcome back" --
        # not just the bare "Choice: " prompt -- proving the full
        # on_afk_redraw hook fired, not the old prompt-only fallback.
        self.assertIn(b'AFK TEST MENU', out[idx:].upper())


if __name__ == '__main__':
    unittest.main()
