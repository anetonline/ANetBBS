"""Regression test for the bundled "Good Time Trivia" door seed
(anetbbs/web_app.py, BUNDLED_DOORS list) -- the 12th door wired up to
ANetBBS's Synchronet JSON-RPC interbbs client support
(anetbbs/games/jsonrpc_client.py + anetbbs/games/sbbs_stubs/json-client.js),
following the same shim contract proven by every prior door this batch.
Real Eric Oulashin (Nightfox) door, source unmodified, bundled at
anetbbs/games/sbbs_doors/gttrivia/, shipping its own gttrivia.ini
pointed at the AUTHOR's own real public score hub
(digitaldistortionbbs.com:10088) rather than StingRay's own server --
confirmed with Jerry to leave it as-is, per his explicit "whatever
server each door's ini already has stays as-is" rule for this batch.

This door found 4 real, previously-unknown compat-shim gaps, two of
which are general (not gttrivia-specific) -- see
test_synchronet_compat_missing_globals.py for their direct regression
tests:
  1. msg_area.sub was completely missing (only .grp existed)
  2. user.is_sysop was completely missing from the user object
  3. bbs.compare_ars was completely missing, including real AGE
     support wired to the actual logged-in user's date_of_birth
  4. door_runner.py's own crash-handler never flushed stdout before
     its blocking keypress-wait read, silently hiding every door
     crash's error message behind an indefinite hang (found because
     THIS door hit gap #3 live and the resulting crash produced zero
     visible output until this was fixed)

Follows the same create_app()/_create_default_data() pattern as
tests/test_thirsty_door_seed.py.
"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_DATA_DIR = Path(__file__).resolve().parents[1] / 'data'
# Real vendored gttrivia door files -- gitignored (third-party door
# source, not redistributed via git; a sysop who wants it downloads
# it separately and drops it at this path, matching BUNDLED_DOORS'
# own must_exist-gated auto-detection). A fresh git clone (CI, a new
# contributor) genuinely doesn't have these files -- skip the whole
# class rather than fail when they're absent.
_DOOR_ROOT = Path(__file__).resolve().parents[1] / 'anetbbs' / 'games' / 'sbbs_doors' / 'gttrivia'


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
                    'requires the real vendored gttrivia door files (gitignored)')
class GttriviaDoorSeedTests(unittest.TestCase):
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
        root = Path(__file__).resolve().parents[1] / 'anetbbs' / 'games' / 'sbbs_doors' / 'gttrivia'
        self.assertTrue((root / 'gttrivia.js').is_file())
        self.assertTrue((root / 'lib.js').is_file())
        self.assertTrue((root / 'gttrivia.ini').is_file())
        self.assertTrue((root / 'gttrivia.asc').is_file())
        self.assertTrue((root / 'qa' / 'general.qa').is_file())
        self.assertTrue((root / 'qa' / 'dirty_minds.qa').is_file())
        self.assertTrue((root / 'qa' / 'star_trek_general.qa').is_file())

    def test_server_side_files_are_not_bundled(self):
        """server/commands.js and server/service.js (real server-side
        JSON-DB module, for hosting scores) are never load()'d by
        gttrivia.js's own client entry point -- matching the same
        exclusion already established for every other door's own
        commands.js/service.js/maintenance.js."""
        root = Path(__file__).resolve().parents[1] / 'anetbbs' / 'games' / 'sbbs_doors' / 'gttrivia'
        self.assertFalse((root / 'server').exists())

    def test_gttrivia_ini_points_at_the_authors_own_real_server(self):
        """Deliberately NOT StingRay's own game.a-net-online.lol --
        Jerry explicitly said to leave each door's already-configured
        server as-is for this batch, and gttrivia's own real default
        is the author's (Eric Oulashin's) public score-sharing hub."""
        root = Path(__file__).resolve().parents[1] / 'anetbbs' / 'games' / 'sbbs_doors' / 'gttrivia'
        content = (root / 'gttrivia.ini').read_text()
        self.assertIn('digitaldistortionbbs.com', content)
        self.assertIn('10088', content)

    def test_seeded_row_has_correct_door_synchronet_config(self):
        app = _fresh_app(str(Path(self._tmp.name) / 'a.db'))
        from anetbbs.models import db, Game
        from anetbbs.web_app import _create_default_data

        with app.app_context():
            db.create_all()
            _create_default_data()
            game = Game.query.filter_by(slug='gttrivia').first()
            self.assertIsNotNone(game, 'row was not seeded -- must_exist check likely failed')
            self.assertEqual(game.game_type, 'door_synchronet')
            self.assertTrue(game.synchronet_script_path.endswith(
                os.path.join('gttrivia', 'gttrivia.js')))
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
            rows = Game.query.filter_by(slug='gttrivia').all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].game_type, 'door_synchronet')


if __name__ == '__main__':
    unittest.main()
