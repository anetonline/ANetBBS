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
