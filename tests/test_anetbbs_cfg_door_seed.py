"""Regression test for the 'anetbbs-cfg' BUNDLED_DOORS seed
(anetbbs/web_app.py) -- registers the standalone anetbbs-cfg curses
config tool as an ordinary door_native Game row purely so the terminal
Sysop Menu can launch it through door_runner.py's already-hardened PTY
bridging (launch_door_game / play_door_game_telnet) instead of
reimplementing PTY fork/exec + I/O pumping from scratch. See
tests/test_terminal_sysop_menu.py for the SSH-only menu-gating tests.

Unlike every other *_door_seed.py test in this repo, the file this
entry's `must_exist` check looks for isn't a vendored third-party
asset checked into the repo -- it's the REAL installed console-script
(setup.py's `anetbbs-cfg=anetbbs.cfg.app:main` entry point), which
only exists once `pip install -e .` (or non-editable) has actually
run. install.sh's real production install always does this
(`pip install --prefer-binary -e "$INSTALL_DIR"`), but a bare
`sys.path.insert`-based test environment that never ran a real pip
install of this package won't have it -- skip in that case, same
"can't test what genuinely isn't there" reasoning every other
door_seed test already uses for its own gitignored/optional assets.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_CFG_SCRIPT = Path(os.path.dirname(sys.executable)) / 'anetbbs-cfg'


def _fresh_app(db_path):
    import anetbbs.config as cfg_mod
    if os.path.exists(db_path):
        os.remove(db_path)
    cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path}'
    from anetbbs.web_app import create_app
    app = create_app('testing')
    app.config['TESTING'] = True
    return app


@unittest.skipUnless(_CFG_SCRIPT.is_file(),
                     'requires a real `pip install -e .`/`pip install .` of '
                     'this package (anetbbs-cfg console script not found in '
                     'this venv) -- matches install.sh\'s real production flow')
class AnetbbsCfgDoorSeedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # _fresh_app() mutates TestingConfig.SQLALCHEMY_DATABASE_URI, a
        # module-level class attribute shared by every test file in the
        # same pytest process -- save+restore it, matching every other
        # *_door_seed.py test's own setUpClass/tearDownClass pattern.
        # Real bug caught live: without this, a later test file's own
        # create_app('testing') call inherited whatever tempfile path
        # this file's LAST test happened to set, which setUp's own
        # TemporaryDirectory cleanup had already deleted by then --
        # broke an unrelated, unmodified test class (test_cfg_sections_
        # data_v2.py) with 30 failures, only when run as part of the
        # full suite (each file passes fine standalone).
        import anetbbs.config as cfg_mod
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI

    @classmethod
    def tearDownClass(cls):
        import anetbbs.config as cfg_mod
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_seeded_row_has_correct_door_native_config(self):
        app = _fresh_app(str(Path(self._tmp.name) / 'a.db'))
        from anetbbs.models import db, Game
        from anetbbs.web_app import _create_default_data

        with app.app_context():
            db.create_all()
            _create_default_data()
            game = Game.query.filter_by(slug='anetbbs-cfg').first()
            self.assertIsNotNone(game, 'row was not seeded -- must_exist check likely failed')
            self.assertEqual(game.game_type, 'door_native')
            self.assertTrue(os.path.isfile(game.executable_path),
                            'seeded executable_path must actually resolve to a real file')
            self.assertEqual(game.max_nodes, 1)

    def test_row_is_hidden_from_the_normal_games_list(self):
        """The whole point of registering this as a Game row is so the
        Sysop Menu can reuse door_runner's launch machinery directly --
        it must NEVER show up as a selectable "game" to any player
        (or even an admin browsing the normal games list), since
        is_active=False is what the terminal/web games-list queries
        filter on (see anetbbs/features/games.py)."""
        app = _fresh_app(str(Path(self._tmp.name) / 'b.db'))
        from anetbbs.models import db, Game
        from anetbbs.web_app import _create_default_data

        with app.app_context():
            db.create_all()
            _create_default_data()
            game = Game.query.filter_by(slug='anetbbs-cfg').first()
            self.assertFalse(game.is_active)
            self.assertFalse(game.web_enabled)

    def test_seed_is_idempotent_on_a_second_boot(self):
        app = _fresh_app(str(Path(self._tmp.name) / 'c.db'))
        from anetbbs.models import db, Game
        from anetbbs.web_app import _create_default_data

        with app.app_context():
            db.create_all()
            _create_default_data()
            _create_default_data()
            rows = Game.query.filter_by(slug='anetbbs-cfg').all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].game_type, 'door_native')


if __name__ == '__main__':
    unittest.main()
