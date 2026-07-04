"""Regression tests for the bundled "A-Net Game Server" door seed
(anetbbs/web_app.py, BUNDLED_DOORS list), added 2026-07-04.

Each ANetBBS install needs its own random rlogin password for this
door -- the remote game server doesn't validate the password against
anything specific, it just needs to be hard for a random stranger to
guess (per the sysop's own explanation: "I just type a bunch of
letters"). Generated fresh only the first time this seed runs for an
install; must never get regenerated/overwritten on later boots (that
would silently break every user who already has the old password
memorized/saved), and two separate installs must get different values
(otherwise "random per BBS" wouldn't actually hold).

Also guards a real bug caught while adding this: the seeding loop
hardcoded max_nodes=1 for every bundled door (fine for single-player
DOSBox/JS doors, wrong for a 20-node remote multiplayer game server) --
fixed to respect a per-door override instead.
"""
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# create_app()'s bootstrap (default-admin seeding, node-slot dirs, etc.)
# writes a handful of small artifacts under the real project's data/
# directory as a side effect, regardless of SQLALCHEMY_DATABASE_URI --
# see tests/test_auto_ban.py for the original discovery of this. This
# file calls create_app() four times (once per test), so the snapshot/
# cleanup happens once at the class level around the whole run rather
# than per-test.
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


class AnetGameServerSeedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._data_dir_before = _snapshot_data_dir()

    @classmethod
    def tearDownClass(cls):
        for entry in _snapshot_data_dir() - cls._data_dir_before:
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_seeded_row_has_a_random_password_tag_and_correct_config(self):
        app = _fresh_app(str(Path(self._tmp.name) / 'a.db'))
        from anetbbs.models import db, Game
        from anetbbs.web_app import _create_default_data

        with app.app_context():
            db.create_all()
            _create_default_data()
            game = Game.query.filter_by(slug='a-net-game-server').first()
            self.assertIsNotNone(game)
            self.assertEqual(game.game_type, 'door_rlogin')
            self.assertEqual(game.executable_path, 'game.a-net-online.lol:513')
            self.assertEqual(game.max_nodes, 20,
                'multiplayer remote door must not be capped at max_nodes=1')
            # BBS tag lives in its own field now, not folded into
            # command_line_args (a space there would silently break the
            # USER_TEMPLATE/PASSWORD/[TERMINAL] split -- see rlogin_bbs_tag's
            # comment on the Game model).
            m = re.match(r'^@USER@ (\S+)$', game.command_line_args or '')
            self.assertIsNotNone(m,
                f'command_line_args not in USER_TEMPLATE PASSWORD shape: '
                f'{game.command_line_args!r}')
            password = m.group(1)
            self.assertGreaterEqual(len(password), 10,
                'password should be a real random token, not a short/empty stub')
            self.assertRegex(game.rlogin_bbs_tag or '', r'^[A-Z]{4}$',
                'BBS tag should be a random 4-letter tag')

    def test_password_and_tag_do_not_change_on_a_second_boot(self):
        app = _fresh_app(str(Path(self._tmp.name) / 'b.db'))
        from anetbbs.models import db, Game
        from anetbbs.web_app import _create_default_data

        with app.app_context():
            db.create_all()
            _create_default_data()
            first = Game.query.filter_by(slug='a-net-game-server').first()
            first_args = first.command_line_args
            first_tag = first.rlogin_bbs_tag

            # Simulate a second app boot re-running the seed.
            _create_default_data()
            second = Game.query.filter_by(slug='a-net-game-server').first()
            self.assertEqual(second.command_line_args, first_args,
                'password must persist across reboots, not regenerate')
            self.assertEqual(second.rlogin_bbs_tag, first_tag,
                'BBS tag must persist across reboots, not regenerate')

    def test_two_separate_installs_get_different_passwords_and_tags(self):
        app_a = _fresh_app(str(Path(self._tmp.name) / 'c.db'))
        from anetbbs.models import db as db_a, Game as Game_a
        from anetbbs.web_app import _create_default_data as seed_a
        with app_a.app_context():
            db_a.create_all()
            seed_a()
            game_a = Game_a.query.filter_by(slug='a-net-game-server').first()
            pw_a, tag_a = game_a.command_line_args, game_a.rlogin_bbs_tag

        app_b = _fresh_app(str(Path(self._tmp.name) / 'd.db'))
        from anetbbs.models import db as db_b, Game as Game_b
        from anetbbs.web_app import _create_default_data as seed_b
        with app_b.app_context():
            db_b.create_all()
            seed_b()
            game_b = Game_b.query.filter_by(slug='a-net-game-server').first()
            pw_b, tag_b = game_b.command_line_args, game_b.rlogin_bbs_tag

        self.assertNotEqual(pw_a, pw_b,
            'two separate installs must not end up with the same password')
        # Tags are only 4 letters (26^4 ~ 457k combos) so a collision is
        # possible in principle -- vanishingly unlikely for one assertion,
        # not worth a retry loop for a test that isn't asserting security.
        self.assertNotEqual(tag_a, tag_b,
            'two separate installs should not end up with the same BBS tag')

    def test_other_bundled_doors_still_default_to_max_nodes_one(self):
        # Regression guard for the setdefault() fix -- confirms it didn't
        # accidentally change behavior for doors that rely on the
        # existing flat default (LORD, DOOM, Duke3D).
        app = _fresh_app(str(Path(self._tmp.name) / 'e.db'))
        from anetbbs.models import db, Game
        from anetbbs.web_app import _create_default_data

        with app.app_context():
            db.create_all()
            _create_default_data()
            lord = Game.query.filter_by(slug='lord').first()
            if lord is not None:
                self.assertEqual(lord.max_nodes, 1)


if __name__ == '__main__':
    unittest.main()
