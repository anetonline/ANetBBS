"""Regression test for a real Medium finding from a security/performance
audit (2026-09-10): menu_engine.run_menu() used to compute `access`
(the effective access_level used to gate every menu and menu item) ONCE
from session.user before its while loop even started -- session.user is
a plain dict snapshotted at login and never re-validated for the rest
of the session (the same staleness shape already fixed for is_active/
is_locked via core/session.py's _start_kick_watchdog). An admin raising
(or lowering) a user's access_level mid-session had zero effect on an
already-connected terminal session's menu gating until the user
reconnected -- unlike the web session path, where flask_login's
user_loader re-fetches the User row fresh on every single request.

Fixed by refreshing session.user['access_level']/['is_admin'] from the
live User row on every trip through run_menu()'s while loop, on the
same per-iteration DB round-trip the loop already makes for the menu
lookup itself.

Drives the REAL menu_engine.run_menu() against a seeded DB: a start
menu with a 'goto' item pointing at a submenu gated at min_access=100,
while session.user's own in-memory dict still says access_level=10 (as
if it were snapshotted at login before the DB-side promotion). Before
the fix, the goto lands on "Access denied." and the session ends
immediately. After the fix, the just-promoted access_level is picked up
on this same loop iteration and the submenu renders normally.
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
    """Feeds `chunks` with no delay, then goes dry (returns b'' forever)."""
    def __init__(self, chunks):
        self._chunks = list(chunks)

    async def read(self, n=1):
        if self._chunks:
            return self._chunks.pop(0)
        return b''


class MenuEngineStaleAccessLevelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.menu_stale_access_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, BbsMenu, BbsMenuItem, User
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()
            # DB-side truth: this user has ALREADY been promoted to
            # access_level=100 by an admin, mid-session.
            user = User(username='promoted', email='promoted@example.com',
                       password_hash='x', access_level=100, is_admin=False)
            db.session.add(user)

            start_menu = BbsMenu(name='startmenu', title='Start Menu',
                                 prompt='Choice: ', min_access=0)
            db.session.add(start_menu)
            secret_menu = BbsMenu(name='secretmenu', title='Secret Menu',
                                  prompt='Choice: ', min_access=100)
            db.session.add(secret_menu)
            db.session.flush()
            db.session.add(BbsMenuItem(
                menu_id=start_menu.id, hotkey='G', label='Go to secret',
                action_type='goto', action_args='secretmenu',
                is_visible=True, min_access=0, sort_order=0))
            db.session.commit()
            cls.user_id = user.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _make_session(self, reader):
        from anetbbs.core.session import BBSSession
        writer = _FakeWriter()
        session = BBSSession(reader, writer, config={})
        # Stale snapshot -- as if this dict were captured at login,
        # BEFORE the admin's DB-side promotion to access_level=100 above.
        session.user = {'id': self.user_id, 'access_level': 10,
                        'is_admin': False}
        session.window_size = (80, 24)
        return session, writer

    def test_promoted_access_level_takes_effect_without_reconnect(self):
        from anetbbs.features import menu_engine
        from anetbbs.core.session import CarrierLost

        reader = _InstantReader([b'G'])
        session, writer = self._make_session(reader)

        try:
            asyncio.run(menu_engine.run_menu(session, start='startmenu'))
        except CarrierLost:
            # Expected once the dry reader runs out on the NEXT prompt
            # inside secretmenu -- we only care what was rendered before
            # that point.
            pass

        out = bytes(writer.written)
        self.assertNotIn(b'Access denied', out,
                         'stale access_level=10 snapshot wrongly blocked '
                         'a user the DB already promoted to 100')
        self.assertIn(b'SECRET MENU', out.upper(),
                      'refreshed access_level should have let the goto '
                      'reach secretmenu (min_access=100) on this same '
                      'session, with no reconnect required')
        # And the in-memory snapshot itself should now reflect the DB.
        self.assertEqual(session.user['access_level'], 100)


if __name__ == '__main__':
    unittest.main()
