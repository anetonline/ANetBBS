"""Regression test for a real Medium finding from a security/
performance audit (2026-09-02): BbsMenu.is_default is fully wired in
the admin UI (menu_admin.py's edit-form checkbox + list-page badge)
but was never read anywhere at runtime -- core/session.py's start()
always called run_menu(self, start='main') unconditionally, so a
sysop could check "default" on a different menu, see the UI confirm
the change, and every session would still enter at the
literally-named 'main' menu regardless. The identical field on the
sibling PetsciiMenu model (petscii_ui.py) already worked correctly --
this was a real asymmetry, not a design choice.

Fixed by extracting BBSSession._resolve_start_menu(), mirroring
petscii_ui.py's own is_default lookup, and calling it instead of the
hardcoded literal.
"""
import os
import sys
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
    os.environ['FLASK_ENV'] = 'testing'
    from anetbbs.web_app import create_app
    app = create_app('testing')
    app.config['TESTING'] = True
    return app


class ResolveStartMenuTests(unittest.TestCase):
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
        import shutil
        for entry in _snapshot_data_dir() - cls._data_dir_before:
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _fresh_app(str(Path(self._tmp.name) / 'a.db'))
        from anetbbs.models import db
        self._ctx = self.app.app_context()
        self._ctx.push()
        self.addCleanup(self._ctx.pop)
        db.create_all()

    def _make_session(self):
        from anetbbs.core.session import BBSSession
        return object.__new__(BBSSession)

    def _clear_seeded_menus(self):
        # create_app()'s own default-data seed already creates a 'main'
        # BbsMenu row (is_default=True) -- clear it so each test starts
        # from a known, empty state instead of blind-inserting on top
        # of it (same bundled-seed-collision gotcha as the bundled
        # door-game slugs elsewhere in this test suite).
        from anetbbs.models import db, BbsMenu
        BbsMenu.query.delete()
        db.session.commit()

    def test_no_default_menu_set_falls_back_to_main(self):
        self._clear_seeded_menus()
        from anetbbs.models import db, BbsMenu
        db.session.add(BbsMenu(name='welcome', title='Welcome',
                               prompt='Choice: ', min_access=0, is_default=False))
        db.session.commit()
        session = self._make_session()
        self.assertEqual(session._resolve_start_menu(), 'main')

    def test_no_bbsmenu_rows_at_all_falls_back_to_main(self):
        self._clear_seeded_menus()
        session = self._make_session()
        self.assertEqual(session._resolve_start_menu(), 'main')

    def test_a_menu_marked_default_is_used_instead_of_main(self):
        self._clear_seeded_menus()
        from anetbbs.models import db, BbsMenu
        db.session.add(BbsMenu(name='main', title='Main',
                               prompt='Choice: ', min_access=0, is_default=False))
        db.session.add(BbsMenu(name='sysop_custom_welcome', title='Custom Welcome',
                               prompt='Choice: ', min_access=0, is_default=True))
        db.session.commit()
        session = self._make_session()
        self.assertEqual(session._resolve_start_menu(), 'sysop_custom_welcome')


if __name__ == '__main__':
    unittest.main()
