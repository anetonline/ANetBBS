"""Regression tests for the web Game Center lobby's door-menu-sections
feature (GameCategory.as_submenu) -- mirrors the terminal menu's own
drill-down behavior (see tests/test_door_games_menu_layout.py) but via
the existing ?category=slug filter mechanism the lobby already had:
a category flagged as_submenu=True collapses to a single "section
card" linking to ?category=slug instead of listing its games inline;
visiting that filtered URL shows the full game grid as normal (the
flag only affects the top-level, all-categories view -- one level
deep, same design as the terminal menu).
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class GamesLobbySubmenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.games_lobby_submenu_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'

        from anetbbs.web_app import create_app
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def setUp(self):
        with self.app.app_context():
            from anetbbs.models import db, Game, GameCategory
            Game.query.delete()
            GameCategory.query.delete()
            db.session.commit()
            arcade = GameCategory(name='Arcade', slug='arcade', sort_order=1, as_submenu=True)
            puzzle = GameCategory(name='Puzzle', slug='puzzle', sort_order=2, as_submenu=False)
            db.session.add_all([arcade, puzzle])
            db.session.commit()
            db.session.add_all([
                Game(name='Chicken Delivery', slug='chicken-delivery', category='arcade',
                    game_type='door_synchronet', is_active=True, web_enabled=True),
                Game(name='Bubble Boggle', slug='bubble-boggle', category='arcade',
                    game_type='door_synchronet', is_active=True, web_enabled=True),
                Game(name='Synkroban', slug='synkroban', category='puzzle',
                    game_type='door_synchronet', is_active=True, web_enabled=True),
            ])
            db.session.commit()
        self.client = self.app.test_client()

    def test_top_level_lobby_collapses_submenu_category_to_a_section_card(self):
        resp = self.client.get('/games/')
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode()
        self.assertIn('Arcade', html)
        # The section-card link, not the individual game cards.
        self.assertIn('?category=arcade', html)
        self.assertNotIn('Chicken Delivery', html)
        self.assertNotIn('Bubble Boggle', html)
        # Puzzle is NOT as_submenu -- stays inline as before.
        self.assertIn('Synkroban', html)

    def test_filtered_view_of_a_submenu_category_shows_its_games_inline(self):
        resp = self.client.get('/games/?category=arcade')
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode()
        self.assertIn('Chicken Delivery', html)
        self.assertIn('Bubble Boggle', html)
        # Puzzle's game must not leak into the arcade-filtered view.
        self.assertNotIn('Synkroban', html)


if __name__ == '__main__':
    unittest.main()
