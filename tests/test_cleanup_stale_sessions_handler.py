"""Test for the events.handlers.cleanup_stale_sessions scheduled-event
handler -- the backstop for connections that never get a clean
disconnect (dropped carrier, killed process, browser closed).
UserSession.user_id lost its unique=True constraint (see
models.UserSession's docstring) so this table is no longer implicitly
bounded to one row per user; without this, an install would
accumulate one permanent row per connection ever made over its
lifetime.
"""
import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class CleanupStaleSessionsHandlerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.cleanup_stale_sessions_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()
            u = User(username='cleanupstaletest', email='cst@example.com',
                    is_active=True)
            u.set_password('x')
            db.session.add(u)
            db.session.commit()
            cls.user_id = u.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def setUp(self):
        from anetbbs.models import db, UserSession
        with self.app.app_context():
            UserSession.query.delete()
            db.session.commit()

    def test_deletes_only_rows_older_than_the_threshold(self):
        from anetbbs.models import db, UserSession
        from anetbbs.events.handlers import cleanup_stale_sessions

        with self.app.app_context():
            db.session.add(UserSession(
                user_id=self.user_id, session_key='ancient',
                last_seen=datetime.utcnow() - timedelta(days=5)))
            db.session.add(UserSession(
                user_id=self.user_id, session_key='recent',
                last_seen=datetime.utcnow() - timedelta(hours=1)))
            db.session.commit()

            ok, msg = cleanup_stale_sessions(self.app, {'stale_days': 1})

            self.assertTrue(ok, msg)
            remaining = UserSession.query.all()
            self.assertEqual(len(remaining), 1)
            self.assertEqual(remaining[0].session_key, 'recent')

    def test_respects_the_stale_days_param(self):
        from anetbbs.models import db, UserSession
        from anetbbs.events.handlers import cleanup_stale_sessions

        with self.app.app_context():
            db.session.add(UserSession(
                user_id=self.user_id, session_key='three-days-old',
                last_seen=datetime.utcnow() - timedelta(days=3)))
            db.session.commit()

            # Threshold of 7 days -- a 3-day-old row must survive.
            ok, msg = cleanup_stale_sessions(self.app, {'stale_days': 7})
            self.assertTrue(ok, msg)
            self.assertEqual(UserSession.query.count(), 1)

            # Threshold of 1 day -- now it must go.
            ok, msg = cleanup_stale_sessions(self.app, {'stale_days': 1})
            self.assertTrue(ok, msg)
            self.assertEqual(UserSession.query.count(), 0)

    def test_defaults_to_one_day_when_no_params_given(self):
        from anetbbs.models import db, UserSession
        from anetbbs.events.handlers import cleanup_stale_sessions

        with self.app.app_context():
            db.session.add(UserSession(
                user_id=self.user_id, session_key='two-days-old',
                last_seen=datetime.utcnow() - timedelta(days=2)))
            db.session.commit()

            ok, msg = cleanup_stale_sessions(self.app, {})
            self.assertTrue(ok, msg)
            self.assertEqual(UserSession.query.count(), 0)

    def test_backfills_duration_seconds_for_a_correlated_caller_log_row(self):
        """Real gap found live: CallerLog.duration_seconds for a web
        login is only ever written by the explicit /logout route -- a
        user who just closes the tab left it NULL/0 forever. This
        handler is the backstop: when it reaps a stale UserSession row
        that's correlated (via caller_log_id) to a CallerLog row still
        missing its duration, it must fill it in from
        last_seen - started_at before deleting the row."""
        from anetbbs.models import db, UserSession, CallerLog
        from anetbbs.events.handlers import cleanup_stale_sessions

        with self.app.app_context():
            started = datetime.utcnow() - timedelta(days=2, minutes=20)
            last_seen = started + timedelta(minutes=17)
            cl = CallerLog(user_id=self.user_id, username='cleanupstaletest',
                           service='web', started_at=started)
            db.session.add(cl)
            db.session.commit()
            db.session.add(UserSession(
                user_id=self.user_id, session_key='abandoned-tab',
                last_seen=last_seen, caller_log_id=cl.id))
            db.session.commit()

            ok, msg = cleanup_stale_sessions(self.app, {'stale_days': 1})

            self.assertTrue(ok, msg)
            refreshed = CallerLog.query.get(cl.id)
            self.assertIsNotNone(refreshed.duration_seconds)
            self.assertEqual(refreshed.duration_seconds, 17 * 60)

    def test_does_not_overwrite_a_duration_already_recorded(self):
        """An explicit /logout already wrote a real duration before this
        row went stale (e.g. the user logged back in on a new tab,
        leaving the old UserSession row to age out normally) -- the
        backstop must never clobber a value a real logout already set."""
        from anetbbs.models import db, UserSession, CallerLog
        from anetbbs.events.handlers import cleanup_stale_sessions

        with self.app.app_context():
            started = datetime.utcnow() - timedelta(days=2, minutes=20)
            cl = CallerLog(user_id=self.user_id, username='cleanupstaletest',
                           service='web', started_at=started,
                           duration_seconds=42)
            db.session.add(cl)
            db.session.commit()
            db.session.add(UserSession(
                user_id=self.user_id, session_key='already-logged-out',
                last_seen=datetime.utcnow() - timedelta(days=2),
                caller_log_id=cl.id))
            db.session.commit()

            ok, msg = cleanup_stale_sessions(self.app, {'stale_days': 1})

            self.assertTrue(ok, msg)
            refreshed = CallerLog.query.get(cl.id)
            self.assertEqual(refreshed.duration_seconds, 42)

    def test_a_recent_row_is_not_backfilled_or_deleted(self):
        """A row inside the stale_days window must be left alone
        entirely -- it may still be a genuinely active session."""
        from anetbbs.models import db, UserSession, CallerLog
        from anetbbs.events.handlers import cleanup_stale_sessions

        with self.app.app_context():
            started = datetime.utcnow() - timedelta(minutes=10)
            cl = CallerLog(user_id=self.user_id, username='cleanupstaletest',
                           service='web', started_at=started)
            db.session.add(cl)
            db.session.commit()
            db.session.add(UserSession(
                user_id=self.user_id, session_key='still-active',
                last_seen=datetime.utcnow() - timedelta(minutes=1),
                caller_log_id=cl.id))
            db.session.commit()

            ok, msg = cleanup_stale_sessions(self.app, {'stale_days': 1})

            self.assertTrue(ok, msg)
            refreshed = CallerLog.query.get(cl.id)
            self.assertIsNone(refreshed.duration_seconds)
            self.assertEqual(UserSession.query.count(), 1)

    def test_one_bad_rows_backfill_does_not_block_deleting_other_stale_rows(self):
        """Regression test for a follow-up audit finding (v1.1.50-present
        sweep, 2026-10-28): the per-row CallerLog backfill loop used to
        share the function's single outer try/except with the actual
        stale-row delete(). If backfilling any ONE row raised, the whole
        function rolled back and returned False -- meaning every OTHER
        stale UserSession row (not just the bad one) survived the
        cleanup tick too, regressing this backstop into the exact
        "row left behind forever" failure it exists to prevent."""
        from unittest.mock import patch
        from anetbbs.models import db, UserSession, CallerLog
        from anetbbs.events.handlers import cleanup_stale_sessions

        with self.app.app_context():
            db.session.add(UserSession(
                user_id=self.user_id, session_key='triggers-backfill-error',
                last_seen=datetime.utcnow() - timedelta(days=2),
                caller_log_id=424242))
            db.session.add(UserSession(
                user_id=self.user_id, session_key='unrelated-stale-row',
                last_seen=datetime.utcnow() - timedelta(days=3)))
            db.session.commit()

            real_get = CallerLog.query.get.__func__ if hasattr(
                CallerLog.query.get, '__func__') else CallerLog.query.get
            query_cls = type(CallerLog.query)

            def flaky_get(self_, ident, *a, **kw):
                if ident == 424242:
                    raise RuntimeError('simulated transient DB error')
                return real_get(self_, ident, *a, **kw)

            with patch.object(query_cls, 'get', flaky_get):
                ok, msg = cleanup_stale_sessions(self.app, {'stale_days': 1})

            self.assertTrue(ok, msg)
            self.assertEqual(UserSession.query.count(), 0,
                             'the unrelated stale row must still be '
                             'deleted even though the other row\'s '
                             'backfill raised')

    def test_is_registered_in_the_scheduler(self):
        from anetbbs.events.handlers import REGISTRY, HANDLER_META, DEFAULT_EVENTS
        self.assertIn('cleanup_stale_sessions', REGISTRY)
        self.assertIn('cleanup_stale_sessions', HANDLER_META)
        self.assertTrue(
            any(ev['handler_key'] == 'cleanup_stale_sessions' for ev in DEFAULT_EVENTS),
            'must be auto-seeded so existing (not just fresh) installs get '
            'the backstop -- ensure_default_events() only inserts rows '
            "whose handler_key isn't already present, so every install "
            'picks this up on next restart')


if __name__ == '__main__':
    unittest.main()
