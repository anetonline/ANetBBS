"""Regression test for a real live gap in NodeSpy/anetbbs-monitor's
"Doing"/Action column (found live 2026-08-28, reported by Jerry:
"I am logged in and sitting at the game menu... it says entered BBS").

Root cause: GameManager (anetbbs/features/games.py) is its own bespoke
read_line-driven loop system, entirely separate from menu_engine.py's
generic `_ACTIONS` dispatch -- and only GameManager._launch() (starting
an actual door) ever called `session._heartbeat_node(...)`. Browsing
the Game Center itself (the top menu, the door list, an as_submenu
category, or playing the built-in Number Guessing game) never touched
NodeActivity at all, so a sysop watching any of the three NodeSpy-style
views (web panel, in-BBS Node Monitor, anetbbs-monitor) saw "Doing"
frozen at whatever it was before the user entered Game Center.

Covers all four call sites added to games.py: show_menu(),
show_door_menu(), _show_category_submenu(), and play_number_guess().
"""
import asyncio
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_DATA_DIR = Path(__file__).resolve().parents[1] / 'data'


def _snapshot_data_dir():
    if not _DATA_DIR.is_dir():
        return set()
    return set(_DATA_DIR.iterdir())


def _fresh_app(db_path):
    import anetbbs.config as cfg_mod
    if os.path.exists(db_path):
        os.remove(db_path)
    cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path}'
    from anetbbs.web_app import create_app
    app = create_app('testing')
    app.config['TESTING'] = True
    return app


class _FakeWriter:
    def __init__(self, sink):
        self._sink = sink

    def write(self, data):
        self._sink.append(data.decode('latin-1', errors='replace'))

    async def drain(self):
        pass


class _FakeSession:
    """Like test_door_games_menu_layout.py's _FakeSession, plus a
    _heartbeat_node recorder -- this test is about WHETHER/WITH-WHAT
    _heartbeat_node gets called, not the real DB write (covered by
    tests/test_terminal_node_monitor.py and tests/test_node_monitor_cli.py
    for the write side itself)."""
    def __init__(self, responses, width=80):
        self.user = {'id': 1, 'username': 'tester'}
        self._responses = list(responses)
        self.written = []
        self.window_size = (width, 24)
        self.term_mode = 'ansi'
        self.writer = _FakeWriter(self.written)
        self.heartbeats = []

    async def write(self, text):
        self.written.append(text)

    async def read_line(self, prompt=''):
        if prompt:
            await self.write(prompt)
        if not self._responses:
            raise AssertionError(
                f'_FakeSession.read_line() called with prompt={prompt!r} but '
                'the scripted response queue is empty')
        return self._responses.pop(0)

    def _heartbeat_node(self, page=None, action=None, screen=None):
        self.heartbeats.append((page, action))


class GameCenterHeartbeatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._data_dir_before = _snapshot_data_dir()
        import anetbbs.config as cfg_mod
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI

    @classmethod
    def tearDownClass(cls):
        import anetbbs.config as cfg_mod
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for entry in _snapshot_data_dir() - cls._data_dir_before:
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _fresh_app(str(Path(self._tmp.name) / 'a.db'))
        from anetbbs.models import db
        from anetbbs.web_app import _create_default_data
        self._ctx = self.app.app_context()
        self._ctx.push()
        self.addCleanup(self._ctx.pop)
        db.create_all()
        _create_default_data()
        # show_door_menu()/_show_category_submenu() build their OWN
        # throwaway Flask app internally -- same FLASK_ENV requirement
        # as test_door_games_menu_layout.py's setUp.
        self._orig_flask_env = os.environ.get('FLASK_ENV')
        os.environ['FLASK_ENV'] = 'testing'
        self.addCleanup(self._restore_flask_env)

    def _restore_flask_env(self):
        if self._orig_flask_env is None:
            os.environ.pop('FLASK_ENV', None)
        else:
            os.environ['FLASK_ENV'] = self._orig_flask_env

    def test_top_level_game_center_menu_heartbeats(self):
        from anetbbs.features.games import GameManager
        session = _FakeSession(['Q'])
        asyncio.run(GameManager(session).show_menu())
        self.assertIn(('games', 'menu: Game Center'), session.heartbeats)

    def test_door_list_menu_heartbeats(self):
        from anetbbs.features.games import GameManager
        session = _FakeSession(['Q'])
        asyncio.run(GameManager(session).show_door_menu())
        self.assertIn(('games:doors', 'menu: Door Games'), session.heartbeats)

    def test_category_submenu_heartbeats_with_the_category_name(self):
        from anetbbs.models import db, Game, GameCategory
        db.session.add(GameCategory(name='Synthetic Category',
                                    slug='synthetic-category', as_submenu=True))
        db.session.add(Game(name='Synthetic Sub Game', slug='synthetic-sub-game',
                            category='synthetic-category', game_type='door_native',
                            is_active=True))
        db.session.commit()
        from anetbbs.features.games import GameManager
        session = _FakeSession(['B'])
        asyncio.run(GameManager(session)._show_category_submenu(
            'synthetic-category', 'Synthetic Category',
            [{'id': 1, 'name': 'Synthetic Sub Game', 'game_type': 'door_native'}]))
        self.assertIn(('games:doors', 'menu: Synthetic Category'), session.heartbeats)

    def test_number_guess_heartbeats_on_entry(self):
        from anetbbs.features.games import GameManager
        session = _FakeSession(['999'])  # deliberately wrong guess, then give up
        # play_number_guess() loops until a correct guess or the fake
        # session's response queue runs dry (raising AssertionError from
        # read_line) -- either way the heartbeat on entry already fired
        # by then, which is what this test checks.
        with self.assertRaises(AssertionError):
            asyncio.run(GameManager(session).play_number_guess())
        self.assertIn(('games:number-guess', 'playing: Number Guessing'),
                      session.heartbeats)


if __name__ == '__main__':
    unittest.main()
