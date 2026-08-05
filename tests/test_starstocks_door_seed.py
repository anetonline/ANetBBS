"""Regression test for the bundled "Star Stocks" door seed
(anetbbs/web_app.py, BUNDLED_DOORS list) -- the 14th door wired up to
ANetBBS's Synchronet JSON-RPC interbbs client support
(anetbbs/games/jsonrpc_client.py + anetbbs/games/sbbs_stubs/json-client.js),
following the same shim contract proven by every prior door this batch.
Real Matt Johnson (mcmlxxix) door (a galactic-investment strategy game
-- same author as Fat Fish/Dice Warz ][/Maze Race/Uber Blox), source
unmodified, bundled at anetbbs/games/sbbs_doors/starstocks/, shipping
its own server.ini already pointed at game.a-net-online.lol:10088
(StingRay's real live Synchronet JSON hub) -- left as-is per Jerry's
"whatever server each door's ini already has stays as-is" rule for
this batch (the door's own install-xtrn.ini default is actually the
author's own bbs.thebrokenbubble.com).

Found one real, previously-unknown compat-shim gap: `console.clearline()`
(distinct from the already-supported `cleartoeol()` -- clears the WHOLE
current line, not just cursor-to-end) was completely missing, and is
called in processSelection(), the core "build an outpost on a star"
gameplay flow, not an edge case. Fixed generically in
synchronet_compat.py (any door calling it, not just this one) -- see
that file's own console.clearline definition and the matching
clearline() bare-global alias.

Follows the same create_app()/_create_default_data() pattern as
tests/test_lemons_door_seed.py.
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


class StarstocksDoorSeedTests(unittest.TestCase):
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
        root = Path(__file__).resolve().parents[1] / 'anetbbs' / 'games' / 'sbbs_doors' / 'starstocks'
        self.assertTrue((root / 'stars.js').is_file())
        self.assertTrue((root / 'game.js').is_file())
        self.assertTrue((root / 'stars.cfg').is_file())
        self.assertTrue((root / 'server.ini').is_file())
        self.assertTrue((root / 'starstocks.ans').is_file())
        self.assertTrue((root / 'exit.bin').is_file())

    def test_server_ini_points_at_the_real_hub(self):
        root = Path(__file__).resolve().parents[1] / 'anetbbs' / 'games' / 'sbbs_doors' / 'starstocks'
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
            game = Game.query.filter_by(slug='starstocks').first()
            self.assertIsNotNone(game, 'row was not seeded -- must_exist check likely failed')
            self.assertEqual(game.game_type, 'door_synchronet')
            self.assertTrue(game.synchronet_script_path.endswith(
                os.path.join('starstocks', 'stars.js')))
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
            rows = Game.query.filter_by(slug='starstocks').all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].game_type, 'door_synchronet')


if __name__ == '__main__':
    unittest.main()
