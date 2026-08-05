"""Regression test for the bundled "Synkroban" door seed
(anetbbs/web_app.py, BUNDLED_DOORS list) -- the 6th door wired up to
ANetBBS's Synchronet JSON-RPC interbbs client support
(anetbbs/games/jsonrpc_client.py + anetbbs/games/sbbs_stubs/json-client.js),
following the same shim contract proven by Chicken Delivery/Bubble
Boggle/Synchronetris/Jeopardized/Gooble Gooble. Real ART @ FATCATS BBS
door (a Sokoban warehouse-puzzle clone), source unmodified, bundled at
anetbbs/games/sbbs_doors/synkroban/, shipping its own server.ini
pointed at game.a-net-online.lol:10088 (StingRay's real live Synchronet
JSON hub).

Follows the same create_app()/_create_default_data() pattern as
tests/test_bublbogl_door_seed.py.
"""
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


class SynkrobanDoorSeedTests(unittest.TestCase):
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
        root = Path(__file__).resolve().parents[1] / 'anetbbs' / 'games' / 'sbbs_doors' / 'synkroban'
        self.assertTrue((root / 'synkroban.js').is_file())
        self.assertTrue((root / 'server.ini').is_file())
        self.assertTrue((root / 'levels').is_dir())
        self.assertTrue((root / 'levels' / 'Arfonzo.txt').is_file())
        self.assertTrue((root / 'levels' / 'Microban.txt').is_file())

    def test_server_ini_points_at_the_real_hub(self):
        root = Path(__file__).resolve().parents[1] / 'anetbbs' / 'games' / 'sbbs_doors' / 'synkroban'
        content = (root / 'server.ini').read_text()
        self.assertIn('game.a-net-online.lol', content)
        self.assertIn('10088', content)

    def test_hardcoded_install_path_is_not_present_in_the_bundled_source(self):
        """The real vendor source hardcodes the author's own absolute
        install path ("/sbbs/xtrn/synkroban/") for level loading --
        fixed via a load-time door-patch in _applyKnownDoorFixes, NOT
        a file edit, so the BUNDLED file itself is expected to still
        contain this literal (byte-identical to upstream, per this
        door's copyright notice restricting file modification). This
        test documents that expectation directly, so a future
        "helpful" hand-edit of the bundled file doesn't silently make
        the door-patch's string-substitution fix a no-op without
        anyone noticing."""
        root = Path(__file__).resolve().parents[1] / 'anetbbs' / 'games' / 'sbbs_doors' / 'synkroban'
        content = (root / 'synkroban.js').read_text()
        self.assertIn('/sbbs/xtrn/synkroban/', content)

    def test_seeded_row_has_correct_door_synchronet_config(self):
        app = _fresh_app(str(Path(self._tmp.name) / 'a.db'))
        from anetbbs.models import db, Game
        from anetbbs.web_app import _create_default_data

        with app.app_context():
            db.create_all()
            _create_default_data()
            game = Game.query.filter_by(slug='synkroban').first()
            self.assertIsNotNone(game, 'row was not seeded -- must_exist check likely failed')
            self.assertEqual(game.game_type, 'door_synchronet')
            self.assertTrue(game.synchronet_script_path.endswith(
                os.path.join('synkroban', 'synkroban.js')))
            self.assertTrue(os.path.isfile(game.synchronet_script_path),
                            'seeded script path must actually resolve to a real file')
            # Confirmed working on real Pi3 hardware by Jerry
            # ("both games worked!!!") alongside Gooble Gooble, v1.0.32.
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
            rows = Game.query.filter_by(slug='synkroban').all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].game_type, 'door_synchronet')


if __name__ == '__main__':
    unittest.main()
