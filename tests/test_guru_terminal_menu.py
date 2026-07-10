"""Regression tests for the Ask Anet guru door's menu wiring
(anetbbs/features/menu_engine.py), added 2026-07-10.

Mirrors tests/test_ebook_terminal_menu.py's pattern -- covers DB-level
wiring only (action registration, hotkey placement, upgrade backfill),
not the terminal UI itself, which isn't practical to unit test without a
full session mock.
"""
import os
import shutil
import tempfile
import unittest
from pathlib import Path
import sys

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
    os.environ['FLASK_ENV'] = 'testing'
    from anetbbs.web_app import create_app
    app = create_app('testing')
    app.config['TESTING'] = True
    return app


class GuruMenuWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._data_dir_before = _snapshot_data_dir()
        import anetbbs.config as cfg_mod
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._orig_flask_env = os.environ.get('FLASK_ENV')

    @classmethod
    def tearDownClass(cls):
        import anetbbs.config as cfg_mod
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        if cls._orig_flask_env is None:
            os.environ.pop('FLASK_ENV', None)
        else:
            os.environ['FLASK_ENV'] = cls._orig_flask_env
        for entry in _snapshot_data_dir() - cls._data_dir_before:
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_guru_action_type_is_registered(self):
        from anetbbs.features.menu_engine import _ACTIONS
        self.assertIn('guru', _ACTIONS)

    def test_l_hotkey_maps_to_guru_action_on_fresh_install(self):
        app = _fresh_app(str(Path(self._tmp.name) / 'a.db'))
        from anetbbs.models import BbsMenu, BbsMenuItem
        with app.app_context():
            main = BbsMenu.query.filter_by(name='main').first()
            self.assertIsNotNone(main)
            item = BbsMenuItem.query.filter_by(menu_id=main.id, hotkey='L').first()
            self.assertIsNotNone(item)
            self.assertEqual(item.action_type, 'guru')

    def test_backfill_adds_guru_item_to_a_pre_existing_install(self):
        app = _fresh_app(str(Path(self._tmp.name) / 'b.db'))
        from anetbbs.models import db, BbsMenu, BbsMenuItem
        from anetbbs.features.menu_engine import seed_default_menus
        with app.app_context():
            main = BbsMenu.query.filter_by(name='main').first()
            item = BbsMenuItem.query.filter_by(menu_id=main.id, hotkey='L').first()
            self.assertIsNotNone(item, 'seed should have created it on fresh install')
            db.session.delete(item)
            db.session.commit()
            self.assertIsNone(
                BbsMenuItem.query.filter_by(menu_id=main.id, hotkey='L').first())

            backfilled = seed_default_menus()
            self.assertGreaterEqual(backfilled, 1)
            restored = BbsMenuItem.query.filter_by(
                menu_id=main.id, action_type='guru').first()
            self.assertIsNotNone(restored)
            self.assertEqual(restored.hotkey, 'L')

    def test_guru_action_type_in_menu_admin_whitelist(self):
        from anetbbs.web.menu_admin import ACTION_TYPES
        self.assertIn('guru', dict(ACTION_TYPES))


if __name__ == '__main__':
    unittest.main()
