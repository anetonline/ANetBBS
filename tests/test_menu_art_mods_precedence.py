"""Regression test for a real live report: a sysop's file-based ANSI
art override for a data-driven BbsMenu (in particular chat_systems,
one of the new admin-editable picker menus) had no effect when placed
in data/mods/text/menus/ -- the documented, update-safe location.

Root cause: menu_engine.run_menu()'s file-based art lookup only ever
checked data/text/menus/ (the older location) -- data/mods/text/menus/
was never checked at all for database-driven menus, unlike every other
mods/ override in this project (see ansi_ui.py's load_menu_ansi() and
docs/35-mods-directory.md, both of which check mods/ first). Fixed by
checking data/mods/text/menus/ first, falling back to data/text/menus/,
matching that same precedence.

Drives the real menu_engine.run_menu() loop against a seeded DB,
matching tests/test_menu_engine_stale_access_level.py's harness.
"""
import asyncio
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class _FakeWriter:
    def __init__(self, peer=('1.2.3.4', 1234)):
        self._peer = peer
        self.written = bytearray()
        self._closing = False

    def get_extra_info(self, key):
        return self._peer if key == 'peername' else None

    def write(self, data):
        self.written += data

    async def drain(self):
        pass

    def is_closing(self):
        return self._closing

    def close(self):
        self._closing = True

    async def wait_closed(self):
        pass


class _InstantReader:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    async def read(self, n=1):
        if self._chunks:
            return self._chunks.pop(0)
        return b''


class MenuArtModsPrecedenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.menu_art_mods_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import User
        from anetbbs.models import db as _db
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            user = User(username='menuarttester', email='mat@example.com',
                       password_hash='x', access_level=10, is_admin=False)
            _db.session.add(user)
            _db.session.commit()
            cls.user_id = user.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._mods_menus_dir = Path(self._tmp.name) / 'mods' / 'text' / 'menus'
        self._plain_menus_dir = Path(self._tmp.name) / 'text' / 'menus'
        self._mods_menus_dir.mkdir(parents=True)
        self._plain_menus_dir.mkdir(parents=True)
        # run_menu()'s art lookup resolves DATA_DIR through
        # bbs_ui._app() -- a separate, singleton Flask app (not the
        # `self.app` create_app('testing') returned) that re-syncs its
        # config from the TestingConfig *class* on every call (see
        # _app()'s own docstring). Setting self.app.config['DATA_DIR']
        # has no effect on it -- has to be the class attribute, same
        # idiom test_chat_mrc_toggle.py's _set_mrc_enabled() already
        # uses for MRC_BRIDGE_ENABLED.
        cfg_mod.TestingConfig.DATA_DIR = self._tmp.name
        self.addCleanup(lambda: delattr(cfg_mod.TestingConfig, 'DATA_DIR'))

    def _make_session(self, reader, term_mode='ansi'):
        # term_mode is a read-only property derived from window_size
        # (>=132 cols -> 'wide') -- set window_size, not term_mode.
        from anetbbs.core.session import BBSSession
        writer = _FakeWriter()
        session = BBSSession(reader, writer, config={})
        session.user = {'id': self.user_id, 'access_level': 10, 'is_admin': False}
        session.window_size = (132, 37) if term_mode == 'wide' else (80, 24)
        return session, writer

    def _run(self, session):
        from anetbbs.features import menu_engine
        from anetbbs.core.session import CarrierLost
        try:
            asyncio.run(menu_engine.run_menu(session, start='chat_systems'))
        except CarrierLost:
            pass

    def test_mods_dir_art_is_used_when_present(self):
        (self._mods_menus_dir / 'chat_systems.ans').write_text('MODS ART HERE')
        session, writer = self._make_session(_InstantReader([b'Q']))
        self._run(session)
        out = bytes(writer.written).decode('latin-1', errors='replace')
        self.assertIn('MODS ART HERE', out)

    def test_mods_dir_wins_over_older_plain_text_menus_dir(self):
        (self._mods_menus_dir / 'chat_systems.ans').write_text('MODS WINS')
        (self._plain_menus_dir / 'chat_systems.ans').write_text('OLD LOCATION LOSES')
        session, writer = self._make_session(_InstantReader([b'Q']))
        self._run(session)
        out = bytes(writer.written).decode('latin-1', errors='replace')
        self.assertIn('MODS WINS', out)
        self.assertNotIn('OLD LOCATION LOSES', out)

    def test_older_plain_text_menus_dir_still_works_alone(self):
        """Backward compatibility: a sysop who set this up before mods/
        support existed for database-driven menus must not lose it."""
        (self._plain_menus_dir / 'chat_systems.ans').write_text('OLD LOCATION STILL WORKS')
        session, writer = self._make_session(_InstantReader([b'Q']))
        self._run(session)
        out = bytes(writer.written).decode('latin-1', errors='replace')
        self.assertIn('OLD LOCATION STILL WORKS', out)

    def test_wide_variant_preferred_in_mods_dir_for_wide_terminal(self):
        (self._mods_menus_dir / 'chat_systems132.ans').write_text('MODS WIDE ART')
        (self._mods_menus_dir / 'chat_systems.ans').write_text('mods narrow, should not be used')
        session, writer = self._make_session(_InstantReader([b'Q']), term_mode='wide')
        self._run(session)
        out = bytes(writer.written).decode('latin-1', errors='replace')
        self.assertIn('MODS WIDE ART', out)
        self.assertNotIn('mods narrow', out)

    def test_no_file_anywhere_falls_back_to_db_ansi_screen_field(self):
        from anetbbs.models import db, BbsMenu
        with self.app.app_context():
            m = BbsMenu.query.filter_by(name='chat_systems').first()
            m.ansi_screen = 'DB FALLBACK ART'
            db.session.commit()
        session, writer = self._make_session(_InstantReader([b'Q']))
        self._run(session)
        out = bytes(writer.written).decode('latin-1', errors='replace')
        self.assertIn('DB FALLBACK ART', out)


if __name__ == '__main__':
    unittest.main()
