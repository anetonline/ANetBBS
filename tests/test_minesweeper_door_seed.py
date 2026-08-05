"""Regression test for the bundled "Minesweeper" door seed
(anetbbs/web_app.py, BUNDLED_DOORS list) -- the 17th Synchronet door
bundled this batch, but a DIFFERENT family from the 16 JSON-RPC doors
before it: this is Digital Man's (Rob Swindell's) own real, official
Synchronet Minesweeper, which doesn't touch the JSON-RPC interbbs client
at all. Its only InterBBS feature (posting wins to a shared "syncdata"
DOVE-Net/FidoNet message area via a real MsgBase) auto-detects and
gracefully self-disables on a stock ANetBBS install (msg_area.sub is
{}), so there's no server.ini/game.a-net-online.lol config to check here
-- unlike every other door_seed test in this batch.

Full audit found and fixed 4 real, general compat-shim bugs in
synchronet_compat.py (see the BUNDLED_DOORS comment for this door in
anetbbs/web_app.py and the new tests in
tests/test_synchronet_compat_missing_globals.py for the details):
BG_HIGH/BLINK missing from the registered-globals list, format()
missing the '%u' conversion + zero-pad width flags, and file_getname()/
file_exists()/directory() each silently shadowed by a strictly-worse
duplicate definition later in the file.

Not yet confirmed on real Pi3 hardware -- bundled with
'_active_default': False, matching every other door's own rollout
convention pending that confirmation.
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


class MinesweeperDoorSeedTests(unittest.TestCase):
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
        the DB seed that depends on them -- if these are ever missing,
        the seed silently skips the row rather than erroring, so this
        needs its own direct check."""
        root = Path(__file__).resolve().parents[1] / 'anetbbs' / 'games' / 'sbbs_doors' / 'minesweeper'
        self.assertTrue((root / 'minesweeper.js').is_file())
        self.assertTrue((root / 'minesweeper.hlp').is_file())
        self.assertTrue((root / 'welcome.bin').is_file())
        self.assertTrue((root / 'mine.bin').is_file())
        self.assertTrue((root / 'winner.bin').is_file())
        self.assertTrue((root / 'loser.bin').is_file())
        for n in (1, 2, 3, 4):
            self.assertTrue((root / f'boom{n}.bin').is_file())
        self.assertTrue((root / 'graphics.ppm').is_file())
        self.assertTrue((root / 'selmask.pbm').is_file())
        # Deliberately NOT shipped: install-xtrn.ini, readme.txt (not
        # needed at runtime), losers.jsonl (sample/dummy data, not a
        # real seed this repo should carry).
        self.assertFalse((root / 'install-xtrn.ini').exists())
        self.assertFalse((root / 'readme.txt').exists())
        self.assertFalse((root / 'losers.jsonl').exists())

    def test_seeded_row_has_correct_door_synchronet_config(self):
        app = _fresh_app(str(Path(self._tmp.name) / 'a.db'))
        from anetbbs.models import db, Game
        from anetbbs.web_app import _create_default_data

        with app.app_context():
            db.create_all()
            _create_default_data()
            game = Game.query.filter_by(slug='sbbs-minesweeper').first()
            self.assertIsNotNone(game, 'row was not seeded -- must_exist check likely failed')
            self.assertEqual(game.game_type, 'door_synchronet')
            self.assertTrue(game.synchronet_script_path.endswith(
                os.path.join('minesweeper', 'minesweeper.js')))
            self.assertTrue(os.path.isfile(game.synchronet_script_path),
                            'seeded script path must actually resolve to a real file')
            # Not yet confirmed on real Pi3 hardware -- off by default
            # pending that confirmation.
            self.assertFalse(game.is_active)
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
            rows = Game.query.filter_by(slug='sbbs-minesweeper').all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].game_type, 'door_synchronet')


if __name__ == '__main__':
    unittest.main()
