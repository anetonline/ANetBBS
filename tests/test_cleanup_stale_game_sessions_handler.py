"""Test for the events.handlers.cleanup_stale_game_sessions scheduled-
event handler -- real gap found in a security/performance audit:
anetbbs/games/node_manager.py's own cleanup_stale_sessions() (a
GameSession-row backstop -- releases the node slot of any door session
a crashed/killed process left stuck at status='active' forever) already
existed but nothing anywhere ever called it, so a door crash
permanently held a node slot other players could never reclaim on a
long-running install. Wired into the scheduled-event registry the same
way the pre-existing UserSession backstop (cleanup_stale_sessions,
tests/test_cleanup_stale_sessions_handler.py) already is.
"""
import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class CleanupStaleGameSessionsHandlerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.cleanup_stale_game_sessions_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User, Game
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()
            u = User(username='cleanupstalegametest', email='csgt@example.com',
                    is_active=True)
            u.set_password('x')
            db.session.add(u)
            g = Game(name='Test Door', slug='test-door-cleanup',
                     game_type='door_native', max_nodes=4)
            db.session.add(g)
            db.session.commit()
            cls.user_id = u.id
            cls.game_id = g.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def setUp(self):
        from anetbbs.models import db, GameSession
        from anetbbs.games import node_manager
        with self.app.app_context():
            GameSession.query.delete()
            db.session.commit()
        node_manager._active.clear()

    def test_closes_only_sessions_older_than_the_timeout_and_releases_the_node(self):
        from anetbbs.models import db, GameSession
        from anetbbs.games import node_manager
        from anetbbs.events.handlers import cleanup_stale_game_sessions

        with self.app.app_context():
            stale = GameSession(game_id=self.game_id, user_id=self.user_id,
                                node_number=1, status='active',
                                started_at=datetime.utcnow() - timedelta(hours=2))
            fresh = GameSession(game_id=self.game_id, user_id=self.user_id,
                                node_number=2, status='active',
                                started_at=datetime.utcnow())
            db.session.add_all([stale, fresh])
            db.session.commit()
            stale_id, fresh_id = stale.id, fresh.id

            node_manager._active[(self.game_id, 1)] = stale_id
            node_manager._active[(self.game_id, 2)] = fresh_id

            ok, msg = cleanup_stale_game_sessions(self.app, {'timeout_seconds': 3600})
            self.assertTrue(ok, msg)

            self.assertEqual(GameSession.query.get(stale_id).status, 'timeout')
            self.assertEqual(GameSession.query.get(fresh_id).status, 'active')
            # The stale session's node slot must be released so a new
            # player can claim it; the fresh session's slot must not be.
            self.assertNotIn((self.game_id, 1), node_manager._active)
            self.assertIn((self.game_id, 2), node_manager._active)

    def test_respects_the_timeout_seconds_param(self):
        from anetbbs.models import db, GameSession
        from anetbbs.events.handlers import cleanup_stale_game_sessions

        with self.app.app_context():
            gs = GameSession(game_id=self.game_id, user_id=self.user_id,
                             node_number=3, status='active',
                             started_at=datetime.utcnow() - timedelta(minutes=30))
            db.session.add(gs)
            db.session.commit()
            gs_id = gs.id

            # 1-hour threshold -- a 30-minute-old session must survive.
            ok, msg = cleanup_stale_game_sessions(self.app, {'timeout_seconds': 3600})
            self.assertTrue(ok, msg)
            self.assertEqual(GameSession.query.get(gs_id).status, 'active')

            # 10-minute threshold -- now it must be closed.
            ok, msg = cleanup_stale_game_sessions(self.app, {'timeout_seconds': 600})
            self.assertTrue(ok, msg)
            self.assertEqual(GameSession.query.get(gs_id).status, 'timeout')

    def test_defaults_to_one_hour_when_no_params_given(self):
        from anetbbs.models import db, GameSession
        from anetbbs.events.handlers import cleanup_stale_game_sessions

        with self.app.app_context():
            gs = GameSession(game_id=self.game_id, user_id=self.user_id,
                             node_number=4, status='active',
                             started_at=datetime.utcnow() - timedelta(hours=2))
            db.session.add(gs)
            db.session.commit()
            gs_id = gs.id

            ok, msg = cleanup_stale_game_sessions(self.app, {})
            self.assertTrue(ok, msg)
            self.assertEqual(GameSession.query.get(gs_id).status, 'timeout')

    def test_is_registered_in_the_scheduler(self):
        from anetbbs.events.handlers import REGISTRY, HANDLER_META, DEFAULT_EVENTS
        self.assertIn('cleanup_stale_game_sessions', REGISTRY)
        self.assertIn('cleanup_stale_game_sessions', HANDLER_META)
        self.assertTrue(
            any(ev['handler_key'] == 'cleanup_stale_game_sessions' for ev in DEFAULT_EVENTS),
            'must be auto-seeded so existing (not just fresh) installs get '
            'the backstop -- ensure_default_events() only inserts rows '
            "whose handler_key isn't already present, so every install "
            'picks this up on next restart')


if __name__ == '__main__':
    unittest.main()
