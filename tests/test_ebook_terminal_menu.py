"""Regression tests for the terminal ebook reader's menu wiring
(anetbbs/features/menu_engine.py, anetbbs/models.py), added 2026-07-04.

The reading UI itself (anetbbs/features/bbs_ui.py's show_ebooks() and
friends) was verified manually against a live telnet server -- it's
deeply intertwined with BBSSession's read_key()/read_line()/
read_key_arrow() byte-level I/O, which isn't practical to unit test
without a full session mock. What IS practical and worth covering
here: the DB-level wiring that gets a caller from "logged in" to
"can reach the ebook reader at all" -- the web_enabled/terminal_enabled
toggle defaults, and the menu-seeding backfill that adds the 'K'
hotkey to existing installs on upgrade (the same self-healing pattern
used by BUNDLED_DOORS for door games).
"""
import os
import sys
import shutil
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# create_app()'s bootstrap writes a few small artifacts under the real
# project's data/ directory regardless of SQLALCHEMY_DATABASE_URI --
# see tests/test_auto_ban.py for the original discovery of this.
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
    # menu_engine.py's seed_default_menus() (and several BBSMenuUI
    # methods) open their OWN transient Flask app via bbs_ui._app(),
    # which resolves its config from the FLASK_ENV *environment
    # variable* independently of whatever app create_app('testing')
    # built -- without this, _app() defaults to 'production' config
    # (a totally different, likely nonexistent DB file), so
    # seed_default_menus() would query a database this test never
    # touched and blow up with "no such table: bbs_menus". Setting
    # FLASK_ENV=testing here makes _app() resolve the SAME TestingConfig
    # class (and thus the same scratch DB path) as create_app('testing').
    os.environ['FLASK_ENV'] = 'testing'
    from anetbbs.web_app import create_app
    app = create_app('testing')
    app.config['TESTING'] = True
    return app


class EbookMenuWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._data_dir_before = _snapshot_data_dir()
        import anetbbs.config as cfg_mod
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._orig_flask_env = os.environ.get('FLASK_ENV')

    @classmethod
    def tearDownClass(cls):
        # TestingConfig is a shared class object across the whole test
        # process -- _fresh_app() overwrites it on every call in this
        # file and never restores it, so any test file that runs after
        # this one (in the same pytest/unittest session) would otherwise
        # inherit a now-deleted scratch-file path instead of the default
        # in-memory DB. Same for FLASK_ENV, also set by _fresh_app() and
        # never restored -- a process-wide env var leak, not just a
        # class attribute one.
        import anetbbs.config as cfg_mod
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        if cls._orig_flask_env is None:
            os.environ.pop('FLASK_ENV', None)
        else:
            os.environ['FLASK_ENV'] = cls._orig_flask_env

        for entry in _snapshot_data_dir() - cls._data_dir_before:
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)

    def setUp(self):
        # Scratch DBs go in a tempfile.TemporaryDirectory() (usually
        # tmpfs, RAM-backed) rather than under tests/ (on the external
        # drive this project's source lives on) -- writing SQLite's many
        # small transactional writes directly to spinning/USB storage
        # measured 25x slower in practice (108s vs 4s for an equivalent
        # create_app() call) and made these tests time out.
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_ebooks_game_defaults_to_both_frontends_enabled(self):
        app = _fresh_app(str(Path(self._tmp.name) / 'a.db'))
        from anetbbs.models import Game
        with app.app_context():
            game = Game.query.filter_by(slug='ebooks').first()
            self.assertIsNotNone(game)
            self.assertTrue(game.web_enabled)
            self.assertTrue(game.terminal_enabled)

    def test_other_games_also_default_to_both_enabled(self):
        # web_enabled/terminal_enabled are generic Game columns, not
        # ebooks-specific -- confirm the default doesn't silently
        # disable some OTHER existing game's web or terminal access.
        app = _fresh_app(str(Path(self._tmp.name) / 'b.db'))
        from anetbbs.models import Game
        with app.app_context():
            hangman = Game.query.filter_by(slug='hangman').first()
            self.assertIsNotNone(hangman)
            self.assertTrue(hangman.web_enabled)
            self.assertTrue(hangman.terminal_enabled)

    def test_ebooks_action_type_is_registered(self):
        from anetbbs.features.menu_engine import _ACTIONS
        self.assertIn('ebooks', _ACTIONS)

    def test_k_hotkey_maps_to_ebooks_action_on_fresh_install(self):
        app = _fresh_app(str(Path(self._tmp.name) / 'c.db'))
        from anetbbs.models import BbsMenu, BbsMenuItem
        with app.app_context():
            main = BbsMenu.query.filter_by(name='main').first()
            self.assertIsNotNone(main)
            item = BbsMenuItem.query.filter_by(menu_id=main.id, hotkey='K').first()
            self.assertIsNotNone(item)
            self.assertEqual(item.action_type, 'ebooks')

    def test_backfill_adds_ebooks_item_to_a_pre_existing_install(self):
        # Simulate an install that was seeded BEFORE the ebooks menu item
        # existed: manually delete the row, then confirm re-running the
        # (idempotent) seeder adds it back -- the same upgrade path a
        # real sysop hits when updating to this version.
        app = _fresh_app(str(Path(self._tmp.name) / 'd.db'))
        from anetbbs.models import db, BbsMenu, BbsMenuItem
        from anetbbs.features.menu_engine import seed_default_menus
        with app.app_context():
            main = BbsMenu.query.filter_by(name='main').first()
            item = BbsMenuItem.query.filter_by(menu_id=main.id, hotkey='K').first()
            self.assertIsNotNone(item, 'seed should have created it on fresh install')
            db.session.delete(item)
            db.session.commit()
            self.assertIsNone(
                BbsMenuItem.query.filter_by(menu_id=main.id, hotkey='K').first())

            backfilled = seed_default_menus()
            self.assertGreaterEqual(backfilled, 1)
            restored = BbsMenuItem.query.filter_by(
                menu_id=main.id, action_type='ebooks').first()
            self.assertIsNotNone(restored)
            self.assertEqual(restored.hotkey, 'K')


if __name__ == '__main__':
    unittest.main()
