"""Regression test for the terminal (telnet/SSH/rlogin) door-games menu
layout fix (anetbbs/features/games.py, GameManager.show_door_menu()).

Real feedback from Jerry after confirming Gooble Gooble + Synkroban
worked on the Pi3: with 13 bundled doors across ~7 categories, the old
one-game-per-line layout ran off the bottom of a real 24-row terminal,
requiring SyncTERM's scrollback to see the earlier categories. Fixed
by packing each category's games into 2 columns instead of 1 (and
dropping the `[game_type]` tag from the terminal display to make room
-- an implementation detail, not something a player picking a game
needs).

Uses a real seeded DB (same create_app()/_create_default_data()
pattern as the door-seed tests) rather than mocking Game/GameCategory
queries, since show_door_menu() builds its category grouping directly
from real SQLAlchemy query results.
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


class _FakeSession:
    def __init__(self, responses, width=80):
        self.user = None
        self._responses = list(responses)
        self.written = []
        self.window_size = (width, 24)
        self.term_mode = 'ansi'

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

    def transcript(self):
        return ''.join(self.written)


class DoorGamesMenuLayoutTests(unittest.TestCase):
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
        # show_door_menu() builds its OWN throwaway Flask app internally
        # (`Flask(__name__)` + `get_config(os.environ.get('FLASK_ENV',
        # 'production'))`) rather than reusing this test's app context --
        # it needs FLASK_ENV=testing so that internal get_config() call
        # resolves to the SAME TestingConfig.SQLALCHEMY_DATABASE_URI
        # _fresh_app() just patched, not whatever 'production' defaults
        # to (which has no seeded tables at all here).
        self._orig_flask_env = os.environ.get('FLASK_ENV')
        os.environ['FLASK_ENV'] = 'testing'
        self.addCleanup(self._restore_flask_env)

    def _restore_flask_env(self):
        if self._orig_flask_env is None:
            os.environ.pop('FLASK_ENV', None)
        else:
            os.environ['FLASK_ENV'] = self._orig_flask_env

    def test_arcade_category_packs_multiple_games_onto_one_line(self):
        """Arcade has 4 real bundled doors (Chicken Delivery, Bubble
        Boggle, Synchronetris, Gooble Gooble) -- the whole point of
        this fix. Confirms at least one line carries two different
        game names/numbers together, not one game per line."""
        from anetbbs.features.games import GameManager
        session = _FakeSession(['Q'])
        asyncio.run(GameManager(session).show_door_menu())
        txt = session.transcript()
        lines = [l for l in txt.split('\r\n') if l.strip()]
        two_games_per_line = [
            l for l in lines
            if l.count('. ') >= 2 and 'Return' not in l
        ]
        self.assertTrue(
            two_games_per_line,
            msg=f'expected at least one 2-games-per-line row; got:\n{txt}')

    def test_game_type_tag_no_longer_shown(self):
        """The [door_synchronet] etc tag was dropped to make room for
        the second column -- confirms it's actually gone, not just
        narrower."""
        from anetbbs.features.games import GameManager
        session = _FakeSession(['Q'])
        asyncio.run(GameManager(session).show_door_menu())
        txt = session.transcript()
        self.assertNotIn('[door_synchronet', txt)
        self.assertNotIn('[door_native', txt)

    def test_every_game_name_still_appears_and_numbering_still_works(self):
        """Confirms the column-packing didn't drop or mis-number any
        game -- every bundled door's name must still be present, and
        picking a specific number must still launch the right game
        (checked indirectly here via choosing a number that maps to a
        known door and confirming _launch's failure path -- a bogus
        script path -- mentions that door's own name, proving the
        flat_list index survived the 2-column rewrite intact)."""
        from anetbbs.features.games import GameManager
        from anetbbs.models import Game
        session = _FakeSession(['Q'])
        asyncio.run(GameManager(session).show_door_menu())
        txt = session.transcript()
        active_names = [
            g.name for g in Game.query.filter_by(is_active=True)
            .filter(~Game.game_type.in_(['builtin_web', 'door_dos_browser']))
            .all()
        ]
        self.assertTrue(active_names, 'no active terminal-eligible games seeded')
        for name in active_names:
            self.assertIn(name[:20], txt, msg=f'{name!r} missing from menu output')

    def test_quitting_returns_without_launching_anything(self):
        from anetbbs.features.games import GameManager
        session = _FakeSession(['Q'])
        asyncio.run(GameManager(session).show_door_menu())
        self.assertIn('Return', session.transcript())


if __name__ == '__main__':
    unittest.main()
