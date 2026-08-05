"""Regression tests for Game.msgbase_area_id -- the admin-configurable
InterBBS DOVE-Net score-sharing setting (Jerry's own ask: "in the door
setting for minesweeper, we should have a setting for interbbs dove-net
score sharing"). Covers the real admin form end to end through a real
Flask test client: the dropdown lists real EchoArea rows, saving a game
persists the chosen area's FK, and clearing it back to "-- None --"
correctly nulls the column out (door_runner.py's
_write_msgbase_ini_override() treats NULL as "feature off").
"""
import os
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod

_DATA_DIR = Path(__file__).resolve().parents[1] / 'data'


def _snapshot_data_dir():
    if not _DATA_DIR.is_dir():
        return set()
    return set(_DATA_DIR.iterdir())


class GamesAdminMsgBaseAreaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._data_dir_before = _snapshot_data_dir()
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.games_admin_msgbase_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'

        from anetbbs.web_app import create_app
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False

        with cls.app.app_context():
            from anetbbs.models import db, User
            from werkzeug.security import generate_password_hash
            user = User.query.filter_by(username='admin').first()
            if not user:
                user = User(username='admin', email='admin@example.com',
                            password_hash=generate_password_hash('testpass123'),
                            access_level=100, is_admin=True)
                db.session.add(user)
            else:
                user.password_hash = generate_password_hash('testpass123')
            db.session.commit()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)
        for entry in _snapshot_data_dir() - cls._data_dir_before:
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)

    def setUp(self):
        with self.app.app_context():
            from anetbbs.models import (db, Game, EchoArea, EchomailNetwork,
                                        EchomailMessage)
            EchomailMessage.query.delete()
            Game.query.delete()
            EchoArea.query.delete()
            EchomailNetwork.query.delete()
            db.session.commit()
            net = EchomailNetwork(name='DOVE-Net', network_type='binkp',
                                  our_address='1:2/3')
            db.session.add(net)
            db.session.commit()
            area = EchoArea(network_id=net.id, tag='SYNCDATA', name='Synchronet Data')
            db.session.add(area)
            db.session.commit()
            self.area_id = area.id
        self.client = self.app.test_client()
        self.client.post('/auth/login',
                         data={'username': 'admin', 'password': 'testpass123'},
                         follow_redirects=True)

    def _base_form_data(self, **overrides):
        data = {
            'name': 'Minesweeper', 'slug': 'sbbs-minesweeper',
            'category': 'other', 'min_access_level': '0',
            'game_type': 'door_synchronet', 'max_nodes': '1',
            'sort_order': '0', 'is_active': 'on', 'web_enabled': 'on',
            'terminal_enabled': 'on', 'share_scores_interbbs': 'on',
            'drop_file_type': 'none',
            'synchronet_script_path': '/opt/anetbbs/.../minesweeper.js',
            'synchronet_exec_dir': '/opt/anetbbs/.../minesweeper',
        }
        data.update(overrides)
        return data

    def test_add_form_lists_the_real_echo_area_in_the_dropdown(self):
        resp = self.client.get('/admin/games/add')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Synchronet Data', resp.data)
        self.assertIn('None'.encode(), resp.data)

    def test_dropdown_groups_areas_by_network_not_one_flat_alphabetical_list(self):
        """Real gap Jerry hit live: with FidoNet's own dozens of areas
        seeded alongside DOVE-Net's single "Synchronet Data" area, a
        flat alphabetical-by-area-name dropdown buried the one area a
        sysop actually wants among a wall of unrelated FidoNet areas.
        Confirms the dropdown is real <optgroup>-per-network markup --
        each network name appears as its own optgroup label, and DOVE-
        Net's area is nested under the DOVE-Net optgroup specifically,
        not just present somewhere in the page."""
        with self.app.app_context():
            from anetbbs.models import db, EchomailNetwork, EchoArea
            fido = EchomailNetwork(name='FidoNet', network_type='binkp',
                                   our_address='1:1/1')
            db.session.add(fido)
            db.session.commit()
            # A handful of real-shaped FidoNet areas that would otherwise
            # alphabetically interleave with "Synchronet Data" in a flat
            # by-area-name sort.
            for tag, name in [('FIDONET.GEN', 'FidoNet General'),
                              ('SYNC.SUPPORT', 'Synchronet Support'),
                              ('SYSOP.CHAT', 'Sysop Chat')]:
                db.session.add(EchoArea(network_id=fido.id, tag=tag, name=name))
            db.session.commit()

        resp = self.client.get('/admin/games/add')
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode()
        self.assertIn('<optgroup label="DOVE-Net">', html)
        self.assertIn('<optgroup label="FidoNet">', html)
        # "Synchronet Data" must appear inside the DOVE-Net optgroup
        # block specifically, not merely somewhere on the page (which
        # would also be true of the old flat/buried layout).
        dove_block = html.split('<optgroup label="DOVE-Net">', 1)[1].split('</optgroup>', 1)[0]
        self.assertIn('Synchronet Data', dove_block)
        fido_block = html.split('<optgroup label="FidoNet">', 1)[1].split('</optgroup>', 1)[0]
        self.assertNotIn('Synchronet Data', fido_block)
        self.assertIn('FidoNet General', fido_block)

    def test_saving_a_game_with_an_area_selected_persists_the_fk(self):
        resp = self.client.post('/admin/games/add', data=self._base_form_data(
            msgbase_area_id=str(self.area_id)), follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        with self.app.app_context():
            from anetbbs.models import Game
            game = Game.query.filter_by(slug='sbbs-minesweeper').first()
            self.assertIsNotNone(game)
            self.assertEqual(game.msgbase_area_id, self.area_id)

    def test_saving_a_game_with_no_area_selected_leaves_it_null(self):
        resp = self.client.post('/admin/games/add', data=self._base_form_data(
            msgbase_area_id='0'), follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        with self.app.app_context():
            from anetbbs.models import Game
            game = Game.query.filter_by(slug='sbbs-minesweeper').first()
            self.assertIsNotNone(game)
            self.assertIsNone(game.msgbase_area_id)

    def test_editing_back_to_none_clears_a_previously_set_area(self):
        with self.app.app_context():
            from anetbbs.models import db, Game
            game = Game(name='Minesweeper', slug='sbbs-minesweeper',
                       category='other', game_type='door_synchronet',
                       msgbase_area_id=self.area_id)
            db.session.add(game)
            db.session.commit()
            game_id = game.id

        resp = self.client.post(f'/admin/games/{game_id}/edit', data=self._base_form_data(
            msgbase_area_id='0'), follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        with self.app.app_context():
            from anetbbs.models import Game
            self.assertIsNone(Game.query.get(game_id).msgbase_area_id)


if __name__ == '__main__':
    unittest.main()
