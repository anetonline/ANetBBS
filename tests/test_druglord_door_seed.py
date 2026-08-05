"""Regression test for the bundled "DrugLord" door seed
(anetbbs/web_app.py, BUNDLED_DOORS list) -- the 15th door wired up to
ANetBBS's Synchronet JSON-RPC interbbs client support
(anetbbs/games/jsonrpc_client.py + anetbbs/games/sbbs_stubs/json-client.js),
following the same shim contract proven by every prior door this batch.
Real "art" (Fatcats BBS) door (a "Dope Wars"-style economic sim), source
unmodified, bundled at anetbbs/games/sbbs_doors/druglord/, shipping its
own server.ini already pointed at romulusbbs.com:10088 -- a real,
thriving third-party multi-BBS community hub (matches the door's own
hardcoded druglord_config default exactly), NOT StingRay's own server.
Left as-is per Jerry's "whatever server each door's ini already has
stays as-is" rule for this batch.

Zero compat-shim gaps needed fixing -- every one of this door's own
null/undefined checks uses loose `==`/`!=` (correctly catches both null
AND undefined in one comparison), sidestepping the null-vs-undefined
bug class several earlier doors this batch hit via `===`. Confirmed
live end-to-end against the real remote server: intro story, splash
art, the real gameplay screen (drug prices/cash/debt/pockets), and a
clean quit -> confirmation -> score-submission flow, zero errors.

Research note: this door's own .js files contain extended-ASCII bytes
that make plain `grep` silently treat them as binary (zero matches, no
error) -- `grep -a` is required when auditing this door's source, or
real content gets missed entirely.

Follows the same create_app()/_create_default_data() pattern as
tests/test_starstocks_door_seed.py.
"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_DATA_DIR = Path(__file__).resolve().parents[1] / 'data'
# Real vendored druglord door files -- gitignored (third-party door
# source, not redistributed via git; a sysop who wants it downloads
# it separately and drops it at this path, matching BUNDLED_DOORS'
# own must_exist-gated auto-detection). A fresh git clone (CI, a new
# contributor) genuinely doesn't have these files -- skip the whole
# class rather than fail when they're absent.
_DOOR_ROOT = Path(__file__).resolve().parents[1] / 'anetbbs' / 'games' / 'sbbs_doors' / 'druglord'


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
                    'requires the real vendored druglord door files (gitignored)')
class DruglordDoorSeedTests(unittest.TestCase):
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
        root = Path(__file__).resolve().parents[1] / 'anetbbs' / 'games' / 'sbbs_doors' / 'druglord'
        self.assertTrue((root / 'druglord.js').is_file())
        self.assertTrue((root / 'ANSI.js').is_file())
        self.assertTrue((root / 'atm.js').is_file())
        self.assertTrue((root / 'Drug.js').is_file())
        self.assertTrue((root / 'event.js').is_file())
        self.assertTrue((root / 'Location.js').is_file())
        self.assertTrue((root / 'pocket.js').is_file())
        self.assertTrue((root / 'druglord.ans').is_file())
        self.assertTrue((root / 'highscore.ans').is_file())
        self.assertTrue((root / 'winners.ans').is_file())
        self.assertTrue((root / 'server.ini').is_file())

    def test_unreferenced_logos_ans_is_not_bundled(self):
        """logos.ans is present in the real source but never actually
        referenced by any console.printfile() call in the door's own
        code -- unreachable, so excluded, matching the established
        "only bundle client-reachable files" rule."""
        root = Path(__file__).resolve().parents[1] / 'anetbbs' / 'games' / 'sbbs_doors' / 'druglord'
        self.assertFalse((root / 'logos.ans').exists())

    def test_server_ini_points_at_the_real_hub(self):
        root = Path(__file__).resolve().parents[1] / 'anetbbs' / 'games' / 'sbbs_doors' / 'druglord'
        content = (root / 'server.ini').read_text()
        self.assertIn('romulusbbs.com', content)
        self.assertIn('10088', content)

    def test_seeded_row_has_correct_door_synchronet_config(self):
        app = _fresh_app(str(Path(self._tmp.name) / 'a.db'))
        from anetbbs.models import db, Game
        from anetbbs.web_app import _create_default_data

        with app.app_context():
            db.create_all()
            _create_default_data()
            game = Game.query.filter_by(slug='druglord').first()
            self.assertIsNotNone(game, 'row was not seeded -- must_exist check likely failed')
            self.assertEqual(game.game_type, 'door_synchronet')
            self.assertTrue(game.synchronet_script_path.endswith(
                os.path.join('druglord', 'druglord.js')))
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
            rows = Game.query.filter_by(slug='druglord').all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].game_type, 'door_synchronet')


if __name__ == '__main__':
    unittest.main()
