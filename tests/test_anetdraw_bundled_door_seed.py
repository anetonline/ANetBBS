"""Regression test for the ANetDRAW BUNDLED_DOORS seed (anetbbs/
web_app.py) -- unlike RDQ3/ANetCHESS (which deliberately stay separate
downloads, per feedback_keep_door_games_separate_from_anetbbs), Jerry's
explicit call for ANetDRAW specifically was to bundle it the same way
anetsims already is: static Linux binaries shipped inside vendor/games/
anetdraw/, one per architecture (the live server is x86-64, a
Raspberry Pi is aarch64), auto-seeded as an active Game row with no
manual Add Game step. See tests/test_terminal_sysop_menu.py's
AnetdrawAvailableTests for the earlier, external-door-only version of
this (still correct -- a sysop who deletes the bundled row and points
a DIFFERENT slug at their own build still gets the ordinary door path,
just not the auto-seed).

Unlike every other *_door_seed.py test in this repo, this one's
`must_exist` check DOES find a real file checked into this exact
sandbox (vendor/games/anetdraw/anetdraw-x64 and -arm64 were copied in
alongside this feature) -- no skip-if-missing needed.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO_ROOT = Path(__file__).resolve().parents[1]


def _fresh_app(db_path):
    import anetbbs.config as cfg_mod
    if os.path.exists(db_path):
        os.remove(db_path)
    cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path}'
    from anetbbs.web_app import create_app
    app = create_app('testing')
    app.config['TESTING'] = True
    return app


class AnetdrawBinaryNameTests(unittest.TestCase):
    def test_aarch64_picks_arm64_binary(self):
        from anetbbs.web_app import _anetdraw_binary_name
        with patch('platform.machine', return_value='aarch64'):
            self.assertEqual(_anetdraw_binary_name(), 'anetdraw-arm64')

    def test_arm64_alias_also_picks_arm64_binary(self):
        from anetbbs.web_app import _anetdraw_binary_name
        with patch('platform.machine', return_value='arm64'):
            self.assertEqual(_anetdraw_binary_name(), 'anetdraw-arm64')

    def test_x86_64_picks_x64_binary(self):
        from anetbbs.web_app import _anetdraw_binary_name
        with patch('platform.machine', return_value='x86_64'):
            self.assertEqual(_anetdraw_binary_name(), 'anetdraw-x64')

    def test_unrecognized_machine_falls_back_to_x64(self):
        from anetbbs.web_app import _anetdraw_binary_name
        with patch('platform.machine', return_value='riscv64'):
            self.assertEqual(_anetdraw_binary_name(), 'anetdraw-x64')


class AnetdrawBundledDoorSeedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
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
            game = Game.query.filter_by(slug='anetdraw').first()
            self.assertIsNotNone(game, 'row was not seeded -- must_exist check likely failed')
            self.assertEqual(game.game_type, 'door_native')
            self.assertTrue(os.path.isfile(game.executable_path),
                            'seeded executable_path must actually resolve to a real file')
            # bbsdev.drp, not door32.sys -- real gap found live on a Pi
            # (2026-09-30): door32.sys has no screen-size fields, so
            # OpenDoors auto-detects via a DSR query-and-wait, which
            # produced a black screen over ANetBBS's own web terminal
            # (worked fine over a real terminal/raw PTY). bbsdev.drp
            # declares size explicitly, sidestepping this entirely.
            self.assertEqual(game.drop_file_type, 'bbsdev.drp')
            self.assertIn('BBSDEV.DRP', game.drop_file_path)
            # No -D argument needed for bbsdev.drp -- door_runner.py
            # sets the BBSDEV_DRP env var to the real path instead,
            # which OpenDoors reads directly per that format's own spec.
            self.assertNotIn('-D', game.command_line_args)
            self.assertIn('--sysop-level', game.command_line_args)
            self.assertEqual(game.max_nodes, 1)

    def test_row_is_active_and_player_facing(self):
        # Unlike anetbbs-cfg (deliberately hidden, is_active=False),
        # ANetDRAW is a real caller-facing art tool -- it must be
        # active and reachable from the normal games list.
        app = _fresh_app(str(Path(self._tmp.name) / 'b.db'))
        from anetbbs.models import db, Game
        from anetbbs.web_app import _create_default_data

        with app.app_context():
            db.create_all()
            _create_default_data()
            game = Game.query.filter_by(slug='anetdraw').first()
            self.assertTrue(game.is_active)

    def test_seed_is_idempotent_on_a_second_boot(self):
        app = _fresh_app(str(Path(self._tmp.name) / 'c.db'))
        from anetbbs.models import db, Game
        from anetbbs.web_app import _create_default_data

        with app.app_context():
            db.create_all()
            _create_default_data()
            _create_default_data()
            rows = Game.query.filter_by(slug='anetdraw').all()
            self.assertEqual(len(rows), 1)

    def test_sysop_tools_shortcut_appears_once_bundled(self):
        # End-to-end confirmation that Phase 0's terminal shortcut
        # (bbs_ui.py's _anetdraw_available()) now "just works" once
        # ANetDRAW is bundled -- no manual Add Game step needed, which
        # was the whole point of this change (Jerry: "ohh no I did not
        # add it to the Pi, I thought we were bundling it").
        app = _fresh_app(str(Path(self._tmp.name) / 'd.db'))
        from anetbbs.models import db
        from anetbbs.web_app import _create_default_data
        from anetbbs.features.bbs_ui import _anetdraw_available

        with app.app_context():
            db.create_all()
            _create_default_data()
            self.assertTrue(_anetdraw_available())

    def test_binary_files_are_real_and_correct_architecture(self):
        # Guards against a future accidental deletion/corruption of the
        # vendored binaries themselves, not just the seeding logic.
        x64 = REPO_ROOT / 'vendor' / 'games' / 'anetdraw' / 'anetdraw-x64'
        arm64 = REPO_ROOT / 'vendor' / 'games' / 'anetdraw' / 'anetdraw-arm64'
        self.assertTrue(x64.is_file())
        self.assertTrue(arm64.is_file())
        with open(x64, 'rb') as f:
            header = f.read(20)
        self.assertEqual(header[:4], b'\x7fELF')
        self.assertEqual(header[18], 0x3e)  # e_machine: EM_X86_64
        with open(arm64, 'rb') as f:
            header = f.read(20)
        self.assertEqual(header[:4], b'\x7fELF')
        self.assertEqual(header[18], 0xb7)  # e_machine: EM_AARCH64


if __name__ == '__main__':
    unittest.main()
