"""Regression test for a real High finding from a security/performance
audit (2026-09-02): banning or locking a user via the admin panel
(admin.py's toggle_ban()/lock_user()) flips the User.is_active/is_locked
DB column but has ZERO effect on that user's ALREADY-connected
telnet/SSH/rlogin session -- core/session.py's self.user is a plain
dict snapshotted once at login and never re-validated, so a banned/
locked user kept full terminal access indefinitely.

The identical bug was already found and fixed for the WEB session path
(web_app.py's load_user(), see tests/test_banned_locked_user_session_
revocation.py) -- Flask-Login re-establishes current_user on every
single request and now rechecks is_active/is_locked there. The
terminal side never got the matching fix.

Fixed by having BBSSession._start_kick_watchdog() -- which already
polls every 5 seconds regardless of activity, to notice a sysop kick --
also re-fetch User.is_active/is_locked on each poll and force-disconnect
on a state flip, the same cadence/mechanism already used for kicks.
Exercises the real method, following the same asyncio.sleep-patching
pattern as tests/test_kick_watchdog_keeps_last_seen_fresh.py.
"""
import os
import sys
import asyncio
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class KickWatchdogRechecksBanAndLockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.kick_watchdog_ban_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import User, db
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()
            good = User(username='goodie', email='goodie@example.com', is_active=True)
            good.set_password('password12345')
            db.session.add(good)
            banned = User(username='baddie', email='baddie@example.com', is_active=False)
            banned.set_password('password12345')
            db.session.add(banned)
            locked = User(username='lockie', email='lockie@example.com',
                          is_active=True, is_locked=True)
            locked.set_password('password12345')
            db.session.add(locked)
            db.session.commit()
            cls.good_id = good.id
            cls.banned_id = banned.id
            cls.locked_id = locked.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _make_node(self, user_id, username):
        from anetbbs.models import db, NodeActivity
        with self.app.app_context():
            now = datetime.utcnow()
            row = NodeActivity(slot=1, user_id=user_id, username=username,
                               protocol='ssh', peer='1.2.3.4:1', page='boards',
                               action='Reading msg #1',
                               started_at=now, last_seen=now)
            db.session.add(row)
            db.session.commit()
            return row.id

    def _bare_session(self, node_activity_id, user_id):
        from anetbbs.core.session import BBSSession

        class _FakeWriter:
            def close(self):
                pass

        fake = object.__new__(BBSSession)
        fake._node_activity_id = node_activity_id
        fake.user = {'id': user_id}
        fake.writer = _FakeWriter()
        return fake

    def _run_one_poll(self, fake, written):
        """Let the watchdog loop run exactly ONE real iteration, then
        cancel -- avoids an infinite tight busy-loop for the
        never-kicked case (same call-counting pattern as
        test_kick_watchdog_keeps_last_seen_fresh.py's own test)."""
        async def _fake_write(text):
            written.append(text)
        fake.write = _fake_write

        async def _drive():
            call_count = {'n': 0}

            async def _fast_sleep(_secs):
                call_count['n'] += 1
                if call_count['n'] >= 2:
                    raise asyncio.CancelledError()

            with patch('anetbbs.core.session.asyncio.sleep', _fast_sleep), \
                 patch('anetbbs.features.bbs_ui._app', lambda: self.app):
                fake._start_kick_watchdog()
                task = fake._kick_task
                try:
                    await asyncio.wait_for(task, timeout=5)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
        asyncio.run(_drive())

    def test_banned_users_terminal_session_is_disconnected(self):
        nid = self._make_node(self.banned_id, 'baddie')
        fake = self._bare_session(nid, self.banned_id)
        written = []
        self._run_one_poll(fake, written)

        self.assertTrue(any('Disconnected' in line for line in written),
                        f'expected a disconnect message, got: {written}')
        from anetbbs.models import NodeActivity
        with self.app.app_context():
            self.assertIsNone(NodeActivity.query.get(nid),
                              'banned user session should be torn down like a kick')

    def test_locked_users_terminal_session_is_disconnected(self):
        nid = self._make_node(self.locked_id, 'lockie')
        fake = self._bare_session(nid, self.locked_id)
        written = []
        self._run_one_poll(fake, written)

        self.assertTrue(any('Disconnected' in line for line in written),
                        f'expected a disconnect message, got: {written}')
        from anetbbs.models import NodeActivity
        with self.app.app_context():
            self.assertIsNone(NodeActivity.query.get(nid))

    def test_normal_active_user_is_left_alone(self):
        nid = self._make_node(self.good_id, 'goodie')
        fake = self._bare_session(nid, self.good_id)
        written = []
        self._run_one_poll(fake, written)

        self.assertFalse(any('Disconnected' in line for line in written),
                         f'a normal active user must not be disconnected: {written}')
        from anetbbs.models import NodeActivity
        with self.app.app_context():
            self.assertIsNotNone(NodeActivity.query.get(nid),
                                 'active user session must survive the poll')


if __name__ == '__main__':
    unittest.main()
