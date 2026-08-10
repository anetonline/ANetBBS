"""Regression test for a real live bug: "who's on" always showed a
telnet/SSH/rlogin user as being "at main," no matter what they were
actually doing (in a door game, chatting in MRC/IRC, reading echomail,
browsing files). Root cause: SessionPresence.set_page() -- the exact
method designed for this -- was called exactly once, hardcoded to
'main', right before the menu loop in BBSSession.start(). Nothing
downstream (menu_engine.py, games.py, chat.py) had a reference to the
SessionPresence instance to call set_page() again, because it was a
bare local variable in start(), never stored as self.presence.

Fixed by storing self.presence on the session and calling set_page()
from menu_engine.py's central action dispatch (every top-level menu
action) plus games.py's GameManager._launch() (specific door name) and
mrc_chat.py's _connect_and_chat() (specific room) for finer detail on
the two long-running activities Jerry called out by name.

This covers the menu-dispatch call site (drives the real BBSSession +
menu_engine.run_menu() together, same pattern as
test_menu_engine_afk_redraw.py) and the games.py call site (direct
unit test of GameManager._launch()).
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


class _OneShotReader:
    """Yields `chunks` once each, then returns EOF (b'') forever --
    enough to dispatch exactly one hotkey and then let run_menu()'s
    next read raise CarrierLost so the loop ends for inspection."""
    def __init__(self, chunks):
        self._chunks = list(chunks)

    async def read(self, n=1):
        if self._chunks:
            return self._chunks.pop(0)
        return b''


class _FakePresence:
    """Records every set_page() call instead of touching a real DB --
    this test is about WHETHER/WITH-WHAT set_page() gets called, not
    SessionPresence's own DB-write behavior (covered implicitly by it
    being the same class used live)."""
    def __init__(self):
        self.protocol = 'telnet'
        self.pages = []

    def set_page(self, page):
        self.pages.append(page)

    def heartbeat(self):
        pass

    def disconnect(self):
        pass


class MenuDispatchPresenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.whos_on_presence_test.db')
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
            menu = BbsMenu(name='presencetestmenu', title='Presence Test Menu',
                           prompt='Choice: ', min_access=0)
            db.session.add(menu)
            db.session.flush()
            db.session.add(BbsMenuItem(menu_id=menu.id, hotkey='W', label='Who',
                                       action_type='who', sort_order=0))
            db.session.commit()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def test_dispatching_a_menu_action_updates_presence_beyond_main(self):
        from anetbbs.core.session import BBSSession, CarrierLost
        from anetbbs.features.menu_engine import run_menu

        reader = _OneShotReader([b'W'])
        writer = _FakeWriter()
        session = BBSSession(reader, writer, config={})
        session.user = {'id': 1, 'access_level': 10}
        session.window_size = (80, 24)
        session.presence = _FakePresence()

        # Avoid actually touching the DB inside _act_who's show_online() --
        # dispatch itself (the code under test) runs for real either way.
        async def _noop_action(ui, args):
            return None

        async def _drive():
            with patch.dict('anetbbs.features.menu_engine._ACTIONS',
                           {'who': _noop_action}):
                try:
                    await run_menu(session, start='presencetestmenu')
                except CarrierLost:
                    pass

        asyncio.run(_drive())

        self.assertIn('who', session.presence.pages,
                      "menu_engine.py's dispatch must call "
                      "session.presence.set_page() with the action type, "
                      "not leave presence frozen at 'main'")


class GameLaunchPresenceTests(unittest.TestCase):
    def test_launching_a_builtin_door_sets_and_restores_presence(self):
        from anetbbs.features.games import GameManager

        class _FakeSession:
            def __init__(self):
                self.user = {'id': 1, 'username': 'tester'}
                self.presence = _FakePresence()
                self.written = []

            async def write(self, text):
                self.written.append(text)

            async def read_line(self, prompt=''):
                return ''

        session = _FakeSession()
        gm = GameManager(session)

        async def _fake_launch(sess, username):
            # Presence must already reflect the specific game by the
            # time the launcher actually runs.
            assert session.presence.pages[-1] == 'games:Lord', session.presence.pages

        fake_module = type(sys)('fake_builtin_game_module')
        fake_module.launch = _fake_launch
        sys.modules['fake_builtin_game_module'] = fake_module
        try:
            asyncio.run(gm._launch({
                'id': 1, 'name': 'Lord', 'game_type': 'builtin_python',
                'web_game_module': 'fake_builtin_game_module',
            }))
        finally:
            del sys.modules['fake_builtin_game_module']

        self.assertEqual(session.presence.pages, ['games:Lord', 'games'],
                         'presence must be set to the specific game on entry '
                         'and reset to the coarser "games" page on exit')


if __name__ == '__main__':
    unittest.main()
