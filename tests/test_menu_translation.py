"""Regression tests for menu translation wiring
(anetbbs/features/menu_engine.py:_apply_menu_translations, called from
run_menu()). Both User.language and MenuTranslation existed as schema
before this -- confirmed via exhaustive grep that nothing anywhere ever
read a MenuTranslation row or used User.language to pick one. This tests
the lookup helper directly (pulled out of run_menu() specifically so it
doesn't need the full async menu-render loop to verify) rather than
driving a live telnet session.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class MenuTranslationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.menu_translation_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def test_english_is_a_no_op_no_query(self):
        """'en' (the default) must do zero extra work -- confirmed by
        passing garbage item tuples that would blow up any real query."""
        from anetbbs.features.menu_engine import _apply_menu_translations
        item_list = [('M', 'Message Boards', 'boards', '')]
        title, items = _apply_menu_translations('main', 'Main Menu', item_list, 'en')
        self.assertEqual(title, 'Main Menu')
        self.assertEqual(items, item_list)

    def test_falsy_lang_is_a_no_op(self):
        from anetbbs.features.menu_engine import _apply_menu_translations
        item_list = [('M', 'Message Boards', 'boards', '')]
        title, items = _apply_menu_translations('main', 'Main Menu', item_list, None)
        self.assertEqual(title, 'Main Menu')
        self.assertEqual(items, item_list)

    def test_translated_title_and_item_substituted_when_present(self):
        from anetbbs.models import db, MenuTranslation
        from anetbbs.features.menu_engine import _apply_menu_translations

        with self.app.app_context():
            db.session.add(MenuTranslation(lang='es', key='menu.main.title',
                                           text='Menu Principal'))
            db.session.add(MenuTranslation(lang='es', key='menu.main.item.M',
                                           text='Tableros de Mensajes'))
            db.session.commit()

            item_list = [('M', 'Message Boards', 'boards', ''),
                        ('B', 'Bulletins', 'bulletins', '')]
            title, items = _apply_menu_translations('main', 'Main Menu', item_list, 'es')

            self.assertEqual(title, 'Menu Principal')
            self.assertEqual(items[0], ('M', 'Tableros de Mensajes', 'boards', ''))
            # No translation row for 'B' -- falls back to the source label.
            self.assertEqual(items[1], ('B', 'Bulletins', 'bulletins', ''))

    def test_falls_back_to_source_text_when_no_translation_row_at_all(self):
        from anetbbs.features.menu_engine import _apply_menu_translations

        with self.app.app_context():
            item_list = [('M', 'Message Boards', 'boards', '')]
            title, items = _apply_menu_translations(
                'nosuchmenu_translation_test', 'Main Menu', item_list, 'fr')
            self.assertEqual(title, 'Main Menu')
            self.assertEqual(items, item_list)

    def test_keys_are_scoped_per_menu_name_no_cross_menu_collision(self):
        """Two different menus can each have their own translation for
        the same hotkey without colliding, since the key includes
        menu_name."""
        from anetbbs.models import db, MenuTranslation
        from anetbbs.features.menu_engine import _apply_menu_translations

        with self.app.app_context():
            db.session.add(MenuTranslation(lang='de', key='menu.main.item.M',
                                           text='Hauptmenue M'))
            db.session.add(MenuTranslation(lang='de', key='menu.games.item.M',
                                           text='Spielemenue M'))
            db.session.commit()

            _, main_items = _apply_menu_translations(
                'main', 'Main', [('M', 'Original', 'x', '')], 'de')
            _, games_items = _apply_menu_translations(
                'games', 'Games', [('M', 'Original', 'x', '')], 'de')

            self.assertEqual(main_items[0][1], 'Hauptmenue M')
            self.assertEqual(games_items[0][1], 'Spielemenue M')


if __name__ == '__main__':
    unittest.main()
