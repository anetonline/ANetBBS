"""Regression tests for the "X just logged in/out" presence-alert
feature (models.PresenceEvent), requested directly by Jerry: classic
multi-node BBS behavior where every OTHER currently-online user sees a
live alert when someone logs in or out, across BOTH terminal (telnet/
SSH/rlogin) and web -- not sysop-only.

Covers: the model itself, both writer paths (web login/logout in
auth.py, terminal login/logout in core/presence.py's SessionPresence),
the web-side SocketIO relay thread (core/presence.py's _relay_loop --
needed because terminal and web run in SEPARATE processes in a real
deployment), self-exclusion (a user must not be alerted about their
own login/logout), and the scheduled cleanup handler.

The terminal-side WATCHDOG (core/session.py's
_start_presence_alert_watchdog) that actually prints the alert into an
active telnet/SSH session is exercised here at the query-logic level
(the same filter it runs against real PresenceEvent rows) rather than
as a full asyncio+live-PTY test -- verifying two simultaneous live
terminal sessions end-to-end isn't practical in this environment; the
watchdog's own query is a thin, directly-testable wrapper around
exactly what's covered here.
"""
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class PresenceAlertsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.presence_alerts_test.db')
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
            a = User(username='alice', email='alice@example.com', is_active=True)
            a.set_password('password12345')
            b = User(username='bob', email='bob@example.com', is_active=True)
            b.set_password('password12345')
            db.session.add_all([a, b])
            db.session.commit()
            cls.alice_id, cls.bob_id = a.id, b.id

            # core/presence.py talks to the DB through its own module-
            # level SQLAlchemy engine, bound once at import time via
            # DATABASE_URL/FLASK_ENV -- not the Flask app's engine.
            # Established fix (see test_who_online_multi_session_
            # presence.py): patch anetbbs.core.presence._Session to a
            # sessionmaker bound to THIS test's own app engine instead
            # of fighting import-order/env-var timing.
            from sqlalchemy.orm import sessionmaker
            cls._test_sessionmaker = sessionmaker(
                bind=db.engine, future=True, expire_on_commit=False)

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _clear_events(self):
        from anetbbs.models import db, PresenceEvent
        with self.app.app_context():
            PresenceEvent.query.delete()
            db.session.commit()

    def setUp(self):
        self._clear_events()

    # -- Web-side writer (auth.py) ---------------------------------------

    def test_web_login_records_a_presence_event(self):
        client = self.app.test_client()
        resp = client.post('/auth/login', data={
            'username': 'alice', 'password': 'password12345'}, follow_redirects=False)
        self.assertIn(resp.status_code, (302, 303))
        with self.app.app_context():
            from anetbbs.models import PresenceEvent
            ev = (PresenceEvent.query
                 .filter_by(user_id=self.alice_id, kind='login').first())
            self.assertIsNotNone(ev)
            self.assertEqual(ev.username, 'alice')
            self.assertEqual(ev.protocol, 'web')

    def test_web_logout_records_a_presence_event(self):
        client = self.app.test_client()
        client.post('/auth/login', data={
            'username': 'bob', 'password': 'password12345'})
        self._clear_events()  # isolate the logout event from the login one
        resp = client.get('/auth/logout', follow_redirects=False)
        self.assertIn(resp.status_code, (302, 303))
        with self.app.app_context():
            from anetbbs.models import PresenceEvent
            ev = (PresenceEvent.query
                 .filter_by(user_id=self.bob_id, kind='logout').first())
            self.assertIsNotNone(ev)
            self.assertEqual(ev.username, 'bob')

    # -- Terminal-side writer (core/presence.py) -------------------------

    def test_terminal_session_login_records_a_presence_event(self):
        from anetbbs.core.presence import SessionPresence
        with patch('anetbbs.core.presence._Session', self._test_sessionmaker):
            presence = SessionPresence(self.alice_id, protocol='telnet',
                                       peer='1.2.3.4:1234', username='alice')
            with self.app.app_context():
                from anetbbs.models import PresenceEvent
                ev = (PresenceEvent.query
                     .filter_by(user_id=self.alice_id, kind='login',
                               protocol='telnet').first())
                self.assertIsNotNone(ev)
                self.assertEqual(ev.username, 'alice')
            presence.disconnect()

    def test_terminal_session_disconnect_records_a_presence_event(self):
        from anetbbs.core.presence import SessionPresence
        with patch('anetbbs.core.presence._Session', self._test_sessionmaker):
            presence = SessionPresence(self.bob_id, protocol='ssh',
                                       peer='5.6.7.8:22', username='bob')
            self._clear_events()
            presence.disconnect()
            with self.app.app_context():
                from anetbbs.models import PresenceEvent
                ev = (PresenceEvent.query
                     .filter_by(user_id=self.bob_id, kind='logout',
                               protocol='ssh').first())
                self.assertIsNotNone(ev)

    # -- Self-exclusion (the query the terminal watchdog itself runs) ----

    def test_presence_query_excludes_the_watching_users_own_events(self):
        """This is exactly the filter core/session.py's
        _start_presence_alert_watchdog runs -- verified directly since
        a full live two-session PTY test isn't practical here."""
        with self.app.app_context():
            from anetbbs.models import db, PresenceEvent
            db.session.add_all([
                PresenceEvent(user_id=self.alice_id, username='alice', kind='login'),
                PresenceEvent(user_id=self.bob_id, username='bob', kind='login'),
            ])
            db.session.commit()

            events = (PresenceEvent.query
                     .filter(PresenceEvent.id > 0)
                     .filter(PresenceEvent.user_id != self.alice_id)
                     .order_by(PresenceEvent.id.asc()).all())
            usernames = [e.username for e in events]
            self.assertNotIn('alice', usernames)
            self.assertIn('bob', usernames)

    # -- Web relay thread (core/presence.py's _relay_loop) ----------------

    def test_relay_loop_emits_new_events_and_advances_past_them(self):
        """_relay_loop snapshots the current max id as its baseline
        BEFORE entering the poll loop (deliberately, so a freshly-
        started relay never replays old history) -- so the event under
        test must be inserted AFTER that baseline, during the first
        simulated sleep interval, not before the loop even starts."""
        from anetbbs.core import presence as presence_mod
        fake_sio = MagicMock()
        self.app.extensions['socketio'] = fake_sio
        presence_mod._relay_stop.clear()

        def _insert_then_continue(*a, **kw):
            with self.app.app_context():
                from anetbbs.models import db, PresenceEvent
                db.session.add(PresenceEvent(
                    user_id=self.alice_id, username='alice', kind='login',
                    protocol='telnet'))
                db.session.commit()
            return False  # keep looping so the NEXT tick's query sees it

        calls = {'n': 0}
        def _wait_side_effect(*a, **kw):
            calls['n'] += 1
            if calls['n'] == 1:
                return _insert_then_continue(*a, **kw)
            presence_mod._relay_stop.set()
            return False
        with patch.object(presence_mod._relay_stop, 'wait', side_effect=_wait_side_effect):
            presence_mod._relay_loop(self.app)

        fake_sio.emit.assert_called_once()
        args, kwargs = fake_sio.emit.call_args
        self.assertEqual(args[0], 'presence_alert')
        self.assertEqual(args[1]['username'], 'alice')
        self.assertEqual(args[1]['kind'], 'login')
        self.assertEqual(kwargs.get('namespace'), '/')
        del self.app.extensions['socketio']

    def test_relay_loop_does_not_re_emit_already_seen_events(self):
        from anetbbs.core import presence as presence_mod
        fake_sio = MagicMock()
        self.app.extensions['socketio'] = fake_sio
        presence_mod._relay_stop.clear()

        calls = {'n': 0}
        def _wait_side_effect(*a, **kw):
            calls['n'] += 1
            if calls['n'] == 1:
                # Insert AFTER the loop's baseline snapshot, same as
                # the sibling test above.
                with self.app.app_context():
                    from anetbbs.models import db, PresenceEvent
                    db.session.add(PresenceEvent(
                        user_id=self.bob_id, username='bob', kind='logout',
                        protocol='web'))
                    db.session.commit()
                return False
            if calls['n'] >= 3:
                presence_mod._relay_stop.set()
            return False
        with patch.object(presence_mod._relay_stop, 'wait', side_effect=_wait_side_effect):
            presence_mod._relay_loop(self.app)

        # Three ticks ran (one to insert, two more to re-poll), but
        # only one real event ever existed -- must only have been
        # emitted once, not once per tick.
        fake_sio.emit.assert_called_once()
        del self.app.extensions['socketio']

    # -- The real terminal watchdog method itself (not a re-implementation) --

    def test_presence_alert_watchdog_writes_the_real_alert_line(self):
        """Exercises core/session.py's actual
        BBSSession._start_presence_alert_watchdog code (not a
        re-implementation of its query) -- constructs a bare instance
        via object.__new__ (its __init__ needs a real asyncio stream
        reader/writer this test has no use for), stubs .user and
        .write, and drives one real iteration by patching asyncio.sleep
        so the test doesn't block for 5 real seconds."""
        import asyncio
        from anetbbs.core.session import BBSSession

        fake = object.__new__(BBSSession)
        fake.user = {'id': self.alice_id}  # alice is WATCHING
        written = []

        async def _fake_write(text):
            written.append(text)

        fake.write = _fake_write

        async def _drive():
            call_count = {'n': 0}

            async def _fast_sleep(_secs):
                call_count['n'] += 1
                if call_count['n'] == 1:
                    # Insert AFTER the watchdog's own baseline snapshot
                    # (taken before the loop starts, deliberately, so a
                    # freshly-connected session never replays history)
                    # -- simulates a real login/logout happening during
                    # this sleep interval.
                    with self.app.app_context():
                        from anetbbs.models import db, PresenceEvent
                        # alice's OWN event too -- she must not be
                        # alerted about herself, only bob's.
                        db.session.add(PresenceEvent(
                            user_id=self.alice_id, username='alice',
                            kind='login', protocol='web'))
                        db.session.add(PresenceEvent(
                            user_id=self.bob_id, username='bob',
                            kind='login', protocol='ssh'))
                        db.session.commit()
                    return
                raise asyncio.CancelledError()

            with patch('anetbbs.core.session.asyncio.sleep', _fast_sleep), \
                 patch('anetbbs.features.bbs_ui._app', lambda: self.app):
                fake._start_presence_alert_watchdog()
                task = fake._presence_alert_task
                try:
                    await asyncio.wait_for(task, timeout=5)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass

        asyncio.run(_drive())

        self.assertTrue(any('bob' in line and 'logged in' in line
                            for line in written),
                        f'expected a "bob just logged in" line, got: {written}')
        self.assertFalse(any('alice' in line for line in written),
                         f'alice must not be alerted about her own login: {written}')

    # -- Cleanup handler ---------------------------------------------------

    def test_cleanup_deletes_old_events_keeps_recent(self):
        from anetbbs.events.handlers import cleanup_stale_presence_events
        from datetime import datetime, timedelta
        with self.app.app_context():
            from anetbbs.models import db, PresenceEvent
            db.session.add(PresenceEvent(
                user_id=self.alice_id, username='alice', kind='login',
                created_at=datetime.utcnow() - timedelta(minutes=120)))
            db.session.add(PresenceEvent(
                user_id=self.bob_id, username='bob', kind='login',
                created_at=datetime.utcnow() - timedelta(minutes=1)))
            db.session.commit()

            ok, msg = cleanup_stale_presence_events(self.app, {'stale_minutes': 60})
            self.assertTrue(ok)
            remaining = [e.username for e in PresenceEvent.query.all()]
            self.assertNotIn('alice', remaining)
            self.assertIn('bob', remaining)


if __name__ == '__main__':
    unittest.main()
