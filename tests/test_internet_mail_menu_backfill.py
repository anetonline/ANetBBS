"""Proves the Internet Mail menu items ('mail'/'mail_send') actually
reach an already-deployed, already-seeded install -- not just a fresh
one. Mirrors tests/test_admin_editable_picker_menus.py's
SeedDefaultMenusBackfillsAnetdrawTests, the real precedent for this
exact scenario: an existing sysop's 'main' BbsMenu predates a newly
added DEFAULT_MENUS item, and seed_default_menus()'s "top up missing
default items on an existing menu" backfill (matched by
(action_type, action_args), not hotkey, so a sysop's own rebinding
survives) must add it on the next startup after upgrade, with no
manual Admin -> Menu Editor step.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class InternetMailMenuBackfillTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.internet_mail_menu_backfill_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def test_backfill_adds_both_mail_items_to_a_pre_existing_main_menu(self):
        from anetbbs.features.menu_engine import seed_default_menus
        from anetbbs.models import db, BbsMenu, BbsMenuItem

        with self.app.app_context():
            menu = BbsMenu.query.filter_by(name='main').first()
            self.assertIsNotNone(menu, "'main' must already be seeded by create_app()")

            # Simulate an install from before this feature existed:
            # remove both items entirely.
            BbsMenuItem.query.filter_by(
                menu_id=menu.id, action_type='mail').delete()
            BbsMenuItem.query.filter_by(
                menu_id=menu.id, action_type='mail_send').delete()
            db.session.commit()
            self.assertIsNone(BbsMenuItem.query.filter_by(
                menu_id=menu.id, action_type='mail').first())

            backfilled = seed_default_menus()
            self.assertGreaterEqual(backfilled, 2)

            mail_item = BbsMenuItem.query.filter_by(
                menu_id=menu.id, action_type='mail').first()
            send_item = BbsMenuItem.query.filter_by(
                menu_id=menu.id, action_type='mail_send').first()
            self.assertIsNotNone(mail_item, "'mail' item was not backfilled")
            self.assertIsNotNone(send_item, "'mail_send' item was not backfilled")
            self.assertEqual(mail_item.label, 'Internet Mail')
            self.assertEqual(send_item.label, 'Send Internet Mail')

    def test_backfill_is_idempotent_no_duplicate_on_second_run(self):
        from anetbbs.features.menu_engine import seed_default_menus
        from anetbbs.models import BbsMenu, BbsMenuItem

        with self.app.app_context():
            menu = BbsMenu.query.filter_by(name='main').first()
            seed_default_menus()  # items already present from the previous test
            seed_default_menus()  # must not add a second copy
            mail_items = BbsMenuItem.query.filter_by(
                menu_id=menu.id, action_type='mail').all()
            send_items = BbsMenuItem.query.filter_by(
                menu_id=menu.id, action_type='mail_send').all()
            self.assertEqual(len(mail_items), 1)
            self.assertEqual(len(send_items), 1)

    def test_sysop_rebound_hotkey_is_left_alone_not_duplicated(self):
        """Backfill matches by (action_type, action_args), not hotkey --
        a sysop who already rebound 'mail' to a different hotkey must
        not get a second, duplicate item."""
        from anetbbs.features.menu_engine import seed_default_menus
        from anetbbs.models import db, BbsMenu, BbsMenuItem

        with self.app.app_context():
            menu = BbsMenu.query.filter_by(name='main').first()
            item = BbsMenuItem.query.filter_by(
                menu_id=menu.id, action_type='mail').first()
            self.assertIsNotNone(item)
            item.hotkey = '2'
            db.session.commit()

            seed_default_menus()

            items = BbsMenuItem.query.filter_by(
                menu_id=menu.id, action_type='mail').all()
            self.assertEqual(len(items), 1,
                             "rebinding the hotkey must not cause a duplicate "
                             "item to be backfilled")
            self.assertEqual(items[0].hotkey, '2',
                             "the sysop's rebound hotkey must be left alone")


if __name__ == '__main__':
    unittest.main()
