"""Regression test for the bundled "Synchronetris" door seed
(anetbbs/web_app.py, BUNDLED_DOORS list) -- the third door wired up to
ANetBBS's Synchronet JSON-RPC interbbs client, and the first genuinely
real-time multiplayer one (its lobby and in-game piece-queue sync are
both built entirely on subscribe()-driven push updates, which is what
motivated building real persistent-connection support into
jsonrpc_client.py/json-client.js). Real door, source unmodified,
bundled at anetbbs/games/sbbs_doors/synchronetris/, shipping its own
server.ini pointed at game.a-net-online.lol:10088.

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


class SynchronetrisDoorSeedTests(unittest.TestCase):
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
        root = Path(__file__).resolve().parents[1] / 'anetbbs' / 'games' / 'sbbs_doors' / 'synchronetris'
        self.assertTrue((root / 'tetris.js').is_file())
        self.assertTrue((root / 'lobby.js').is_file())
        self.assertTrue((root / 'game.js').is_file())
        self.assertTrue((root / 'tetrisobj.js').is_file())
        self.assertTrue((root / 'service.js').is_file())
        self.assertTrue((root / 'server.ini').is_file())

    def test_server_ini_points_at_the_real_hub(self):
        root = Path(__file__).resolve().parents[1] / 'anetbbs' / 'games' / 'sbbs_doors' / 'synchronetris'
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
            game = Game.query.filter_by(slug='synchronetris').first()
            self.assertIsNotNone(game, 'row was not seeded -- must_exist check likely failed')
            self.assertEqual(game.game_type, 'door_synchronet')
            self.assertTrue(game.synchronet_script_path.endswith(
                os.path.join('synchronetris', 'tetris.js')))
            self.assertTrue(os.path.isfile(game.synchronet_script_path),
                            'seeded script path must actually resolve to a real file')
            self.assertTrue(game.is_active)
            self.assertEqual(game.max_nodes, 1)

    def test_seed_is_idempotent_on_a_second_boot(self):
        app = _fresh_app(str(Path(self._tmp.name) / 'b.db'))
        from anetbbs.models import db, Game
        from anetbbs.web_app import _create_default_data

        with app.app_context():
            db.create_all()
            _create_default_data()
            _create_default_data()
            rows = Game.query.filter_by(slug='synchronetris').all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].game_type, 'door_synchronet')


if __name__ == '__main__':
    unittest.main()
