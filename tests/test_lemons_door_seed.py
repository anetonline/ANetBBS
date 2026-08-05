"""Regression test for the bundled "Lemons" door seed
(anetbbs/web_app.py, BUNDLED_DOORS list) -- the 13th door wired up to
ANetBBS's Synchronet JSON-RPC interbbs client support
(anetbbs/games/jsonrpc_client.py + anetbbs/games/sbbs_stubs/json-client.js),
following the same shim contract proven by every prior door this batch.
Real echicken door (a "Lemmings"-style puzzle game), source unmodified,
bundled at anetbbs/games/sbbs_doors/lemons/, shipping its own server.ini
already pointed at game.a-net-online.lol:10088 (StingRay's real live
Synchronet JSON hub) -- left as-is per Jerry's "whatever server each
door's ini already has stays as-is" rule for this batch.

Zero compat-shim gaps needed fixing for this door -- `Menu`/`Game`/
`Level`/`Help`/`PopUp` all turned out to be the door's own local class
definitions (one per file, matching filenames), and `PopUp` even
brings its own `Frame.prototype.drawBorder` polyfill rather than
depending on anything from the shared shim. (A `PopUp` + `drawBorder`
pair was briefly and mistakenly added to synchronet_compat.py's
frame.js during initial audit, based on a too-shallow read of
lemons.js that stopped at its load() calls -- reverted once the
door's own definitions further down the same file were found; this
seed test doesn't need to guard against that regression specifically
since it would surface as a real test failure in
test_synchronet_compat_missing_globals.py if reintroduced.)

Follows the same create_app()/_create_default_data() pattern as
tests/test_gttrivia_door_seed.py.
"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_DATA_DIR = Path(__file__).resolve().parents[1] / 'data'
# Real vendored lemons door files -- gitignored (third-party door
# source, not redistributed via git; a sysop who wants it downloads
# it separately and drops it at this path, matching BUNDLED_DOORS'
# own must_exist-gated auto-detection). A fresh git clone (CI, a new
# contributor) genuinely doesn't have these files -- skip the whole
# class rather than fail when they're absent.
_DOOR_ROOT = Path(__file__).resolve().parents[1] / 'anetbbs' / 'games' / 'sbbs_doors' / 'lemons'


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


@unittest.skipUnless(_DOOR_ROOT.is_dir(),
                    'requires the real vendored lemons door files (gitignored)')
class LemonsDoorSeedTests(unittest.TestCase):
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

    def test_real_game_files_shipped_in_the_repo(self):
        """Sanity check the actual bundled files exist before testing
        the DB seed that depends on them -- if these are ever missing
        (e.g. an accidental exclusion in build-release.sh's file list),
        the seed silently skips the row rather than erroring, so this
        needs its own direct check."""
        root = Path(__file__).resolve().parents[1] / 'anetbbs' / 'games' / 'sbbs_doors' / 'lemons'
        self.assertTrue((root / 'lemons.js').is_file())
        self.assertTrue((root / 'defs.js').is_file())
        self.assertTrue((root / 'game.js').is_file())
        self.assertTrue((root / 'level.js').is_file())
        self.assertTrue((root / 'menu.js').is_file())
        self.assertTrue((root / 'help.js').is_file())
        self.assertTrue((root / 'dbhelper.js').is_file())
        self.assertTrue((root / 'levels.json').is_file())
        self.assertTrue((root / 'server.ini').is_file())
        self.assertTrue((root / 'lemons.bin').is_file())
        self.assertTrue((root / 'help.bin').is_file())
        self.assertTrue((root / 'sprites' / 'lemon.bin').is_file())
        self.assertTrue((root / 'sprites' / 'lemon.ini').is_file())

    def test_server_side_and_standalone_editor_files_are_not_bundled(self):
        """commands.js/service.js (real server-side JSON-DB module) and
        leveledit.js/leveleditor.js (a separate, standalone level-
        editing tool never load()'d by lemons.js's own client entry
        point) are excluded, matching the established "only bundle
        client-reachable files" rule."""
        root = Path(__file__).resolve().parents[1] / 'anetbbs' / 'games' / 'sbbs_doors' / 'lemons'
        self.assertFalse((root / 'commands.js').exists())
        self.assertFalse((root / 'service.js').exists())
        self.assertFalse((root / 'leveledit.js').exists())
        self.assertFalse((root / 'leveleditor.js').exists())

    def test_server_ini_points_at_the_real_hub(self):
        root = Path(__file__).resolve().parents[1] / 'anetbbs' / 'games' / 'sbbs_doors' / 'lemons'
        content = (root / 'server.ini').read_text()
        self.assertIn('game.a-net-online.lol', content)
        self.assertIn('10088', content)

    def test_seeded_row_has_correct_door_synchronet_config(self):
        app = _fresh_app(str(Path(self._tmp.name) / 'a.db'))
        from anetbbs.models import db, Game
        from anetbbs.web_app import _create_default_data

        with app.app_context():
            db.create_all()
            _create_default_data()
            game = Game.query.filter_by(slug='lemons').first()
            self.assertIsNotNone(game, 'row was not seeded -- must_exist check likely failed')
            self.assertEqual(game.game_type, 'door_synchronet')
            self.assertTrue(game.synchronet_script_path.endswith(
                os.path.join('lemons', 'lemons.js')))
            self.assertTrue(os.path.isfile(game.synchronet_script_path),
                            'seeded script path must actually resolve to a real file')
            # Confirmed working on real Pi3 hardware by Jerry
            # ("they all works :)" -- covering all 5 doors this round).
            self.assertTrue(game.is_active)
            self.assertEqual(game.max_nodes, 1)

    def test_seed_is_idempotent_on_a_second_boot(self):
        """Matches the self-correction behavior every other bundled
        door in this loop already has: running _create_default_data()
        twice must not create a duplicate row or clobber game_type."""
        app = _fresh_app(str(Path(self._tmp.name) / 'b.db'))
        from anetbbs.models import db, Game
        from anetbbs.web_app import _create_default_data

        with app.app_context():
            db.create_all()
            _create_default_data()
            _create_default_data()
            rows = Game.query.filter_by(slug='lemons').all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].game_type, 'door_synchronet')


if __name__ == '__main__':
    unittest.main()
