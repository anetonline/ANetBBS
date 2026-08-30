"""Regression test for a real live bug (2026-08-30): an idle-but-still-
connected terminal session would silently vanish from every presence
surface (web NodeSpy, the in-BBS Node Monitor, anetbbs-monitor) after 5
minutes, even though it was fully connected the whole time.

Root cause: NodeActivity.last_seen was only ever bumped by
core/session.py's _heartbeat_node(), itself only called from active
menu-navigation/door-game/MRC/AFK-transition call sites. A session that
just sits on one screen doing nothing never touches any of those, so
last_seen goes stale and every consumer's 5-minute online-cutoff query
(see tests/test_node_monitor_cli.py's own liveness-cutoff test) filters
the row out.

Fix: BBSSession._start_kick_watchdog() already polls its own
NodeActivity row every 5 seconds regardless of what the user is doing
(to notice a sysop kick) -- this test verifies it ALSO bumps last_seen
on every poll when no kick is pending, so a connected-but-idle session
never goes stale. Exercises the real method, following the same
asyncio.sleep-patching pattern as
tests/test_presence_alerts.py's own watchdog test.
"""
import os
import sys
import asyncio
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class KickWatchdogKeepsLastSeenFreshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.kick_watchdog_test.db')
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
            u = User(username='alice', email='alice@example.com', is_active=True)
            u.set_password('password12345')
            db.session.add(u)
            db.session.commit()
            cls.alice_id = u.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _make_idle_node(self, stale_minutes):
        """A NodeActivity row that hasn't been touched by
        _heartbeat_node() in `stale_minutes` -- simulates a session
        sitting idle on one screen, not navigating."""
        from anetbbs.models import db, NodeActivity
        with self.app.app_context():
            old = datetime.utcnow() - timedelta(minutes=stale_minutes)
            row = NodeActivity(slot=1, user_id=self.alice_id, username='alice',
                                protocol='ssh', peer='1.2.3.4:1', page='boards',
                                action='Reading msg #1',
                                started_at=old, last_seen=old)
            db.session.add(row)
            db.session.commit()
            return row.id

    def test_idle_session_stays_out_of_stale_cutoff_across_repeated_polls(self):
        """The bug, reproduced end-to-end: a session already PAST the
        5-minute cutoff (as if it had been idle at one screen the whole
        time) must be pulled back inside it by the very next watchdog
        poll -- proving the poll itself refreshes last_seen, not just
        that the row happened to still be fresh when the test asserts
        (mocking asyncio.sleep means no real wall-clock time passes
        between polls, so starting inside the cutoff would pass this
        assertion regardless of whether the fix does anything)."""
        nid = self._make_idle_node(stale_minutes=10)

        fake = self._bare_session(nid)

        async def _drive():
            call_count = {'n': 0}

            async def _fast_sleep(_secs):
                # asyncio.sleep(5) is the FIRST statement in the watchdog's
                # loop body -- it must return normally once to let that
                # iteration's DB update run, then cancel on the next call
                # (the top of the following iteration) rather than
                # short-circuiting before any poll ever executes.
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

        from anetbbs.models import NodeActivity
        from anetbbs.monitor.app import fetch_live_nodes
        with self.app.app_context():
            refreshed = NodeActivity.query.get(nid)
            self.assertLess(
                datetime.utcnow() - refreshed.last_seen, timedelta(seconds=30),
                'watchdog poll must bump last_seen to "now" even when the '
                'session is just idling, not being kicked')
            nodes = fetch_live_nodes()
            self.assertIn(1, nodes,
                          'session was still connected (never kicked/closed) but '
                          'dropped out of the online cutoff purely from sitting '
                          'idle -- last_seen was never refreshed by the watchdog')

    def test_pending_kick_still_wins_over_the_last_seen_refresh(self):
        """A kick request must still be honored even though every poll
        now also touches last_seen -- the two must not conflict."""
        nid = self._make_idle_node(stale_minutes=1)
        from anetbbs.models import db, NodeActivity
        with self.app.app_context():
            row = NodeActivity.query.get(nid)
            row.kick_requested = True
            row.kick_reason = 'be right back'
            db.session.commit()

        fake = self._bare_session(nid)
        written = []

        async def _fake_write(text):
            written.append(text)
        fake.write = _fake_write

        async def _drive():
            async def _fast_sleep(_secs):
                return
            with patch('anetbbs.core.session.asyncio.sleep', _fast_sleep), \
                 patch('anetbbs.features.bbs_ui._app', lambda: self.app):
                fake._start_kick_watchdog()
                task = fake._kick_task
                try:
                    await asyncio.wait_for(task, timeout=5)
                except asyncio.TimeoutError:
                    pass

        asyncio.run(_drive())

        self.assertTrue(any('Disconnected by sysop' in line or 'be right back' in line
                            for line in written),
                        f'expected the kick disconnect message, got: {written}')
        with self.app.app_context():
            self.assertIsNone(NodeActivity.query.get(nid),
                              'kicked row should be deleted, same as before this fix')

    def _bare_session(self, node_activity_id):
        from anetbbs.core.session import BBSSession

        class _FakeWriter:
            def close(self):
                pass

        fake = object.__new__(BBSSession)
        fake._node_activity_id = node_activity_id
        fake.writer = _FakeWriter()

        async def _noop_write(_text):
            pass
        fake.write = _noop_write
        return fake


if __name__ == '__main__':
    unittest.main()
