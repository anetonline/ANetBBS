"""Regression test for a real live bug, found on a real upgraded Pi
install: menu_engine.seed_default_menus()'s "existing install" branch
was only ever designed to top up new ITEMS on an already-existing menu
(e.g. a new hotkey added to 'main' in a later release) -- when a whole
NEW top-level menu name showed up in DEFAULT_MENUS for the first time
(chat_systems/game_center/sysop_tools, added for the admin-editable
picker-menus feature), an upgrading install (which already has 'main',
so takes the "existing install" branch) looked the new name up by
name, found nothing, and silently skipped creating it entirely --
`if not m: continue`.

A fresh install was never affected (it takes the other branch, which
always creates every DEFAULT_MENUS entry from scratch) -- this only
ever showed up on an *upgrade*, confirmed live: 'main' appeared in
Admin -> BBS Menus / anetbbs-cfg as expected after updating, the three
new picker menus never did.

Fixed by extracting _create_menu_from_def() (menu + all its items,
the same construction both branches need) and calling it from the
`if not m:` branch too, instead of `continue`-ing past it.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class SeedDefaultMenusNewMenuBackfillTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.seed_menus_backfill_test.db')
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

    def setUp(self):
        """Simulate a pre-v1.0.79 database: only 'main' exists, exactly
        as any install upgraded from before chat_systems/game_center/
        sysop_tools existed would look -- create_app() above already
        auto-seeded all four via seed_default_menus(), so delete the
        three new ones (and their items) back out before each test."""
        from anetbbs.models import db, BbsMenu, BbsMenuItem
        with self.app.app_context():
            for name in ('chat_systems', 'game_center', 'sysop_tools'):
                m = BbsMenu.query.filter_by(name=name).first()
                if m:
                    BbsMenuItem.query.filter_by(menu_id=m.id).delete()
                    db.session.delete(m)
            db.session.commit()
            main = BbsMenu.query.filter_by(name='main').first()
            self.assertIsNotNone(main, 'main must exist for this to be a '
                                       'realistic "existing install" simulation')
            self.assertIsNone(BbsMenu.query.filter_by(name='chat_systems').first())
            self.assertIsNone(BbsMenu.query.filter_by(name='game_center').first())
            self.assertIsNone(BbsMenu.query.filter_by(name='sysop_tools').first())

    def test_upgrading_install_gets_the_new_menus_created(self):
        from anetbbs.features.menu_engine import seed_default_menus
        from anetbbs.models import BbsMenu, BbsMenuItem
        with self.app.app_context():
            backfilled = seed_default_menus()
            self.assertGreater(backfilled, 0)

            for name in ('chat_systems', 'game_center', 'sysop_tools'):
                m = BbsMenu.query.filter_by(name=name).first()
                self.assertIsNotNone(
                    m, f'{name} must be created on an upgrading install, '
                       'not silently skipped')
                items = BbsMenuItem.query.filter_by(menu_id=m.id).all()
                self.assertGreater(
                    len(items), 0, f'{name} must have its default items too')

    def test_chat_systems_items_match_the_stock_defaults(self):
        from anetbbs.features.menu_engine import seed_default_menus
        from anetbbs.models import BbsMenu, BbsMenuItem
        with self.app.app_context():
            seed_default_menus()
            m = BbsMenu.query.filter_by(name='chat_systems').first()
            action_types = {i.action_type for i in
                            BbsMenuItem.query.filter_by(menu_id=m.id).all()}
            self.assertEqual(
                action_types,
                {'chat_local', 'chat_irc', 'chat_mrc', 'goto'})

    def test_main_is_untouched_and_not_duplicated(self):
        """The pre-existing 'main' menu (and its items) must be left
        exactly alone -- this fix only creates whole MISSING menus, it
        must not touch or duplicate anything already present."""
        from anetbbs.features.menu_engine import seed_default_menus
        from anetbbs.models import BbsMenu, BbsMenuItem
        with self.app.app_context():
            main_before = BbsMenu.query.filter_by(name='main').first()
            item_count_before = BbsMenuItem.query.filter_by(
                menu_id=main_before.id).count()

            seed_default_menus()

            self.assertEqual(BbsMenu.query.filter_by(name='main').count(), 1)
            main_after = BbsMenu.query.filter_by(name='main').first()
            self.assertEqual(main_after.id, main_before.id)
            self.assertEqual(
                BbsMenuItem.query.filter_by(menu_id=main_after.id).count(),
                item_count_before)

    def test_running_seed_twice_does_not_duplicate_the_new_menus(self):
        from anetbbs.features.menu_engine import seed_default_menus
        from anetbbs.models import BbsMenu
        with self.app.app_context():
            seed_default_menus()
            seed_default_menus()
            for name in ('chat_systems', 'game_center', 'sysop_tools'):
                self.assertEqual(
                    BbsMenu.query.filter_by(name=name).count(), 1,
                    f'{name} must not be duplicated by a second seed run')


if __name__ == '__main__':
    unittest.main()
