"""Regression test for a real gap found in a security/performance
audit: GameScore.game_id and GameSession's (game_id, status) pair are
both filtered on in real hot paths --

  - web/games.py's /games/scoreboard route loops over EVERY active
    game and runs `GameScore.query.filter_by(game_id=g.id)
    .order_by(score.desc()).limit(10)` for each one, on every request.
  - games/node_manager.py's get_occupied_nodes() runs
    `GameSession.query.filter_by(game_id=..., status='active')` on
    EVERY door-game launch attempt (node allocation).

Neither column was indexed. Fixed the same way NetmailMessage.received_at
and UserSession.last_seen were: index=True / db.Index() on the model,
plus a _ensure_index() backfill in _lightweight_migrate() for installs
whose game_scores/game_sessions tables predate the change, since
create_all() only creates indexes declared on a model when it creates
the table for the first time.

Reuses the same create_app()-based migration test pattern as
test_netmail_received_at_index_migration.py.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class GameScoreSessionIndexMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.game_score_session_index_migration_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'
        from anetbbs.web_app import create_app
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _has_index(self, table, *cols):
        from sqlalchemy import inspect as _inspect
        from anetbbs.models import db
        insp = _inspect(db.engine)
        cols = list(cols)
        for idx in insp.get_indexes(table):
            if idx.get('column_names') == cols:
                return True
        return False

    def test_fresh_install_has_the_game_scores_index(self):
        """create_app() on a brand-new database creates the table with
        the index already declared on the model -- confirms the
        baseline before testing the upgrade/backfill path below."""
        with self.app.app_context():
            self.assertTrue(self._has_index('game_scores', 'game_id'))

    def test_fresh_install_has_the_game_sessions_composite_index(self):
        with self.app.app_context():
            self.assertTrue(self._has_index('game_sessions', 'game_id', 'status'))

    def test_backfills_game_scores_index_on_a_table_that_predates_it(self):
        """Simulates an upgrading install: drop the index (as if the
        table were created before game_id gained index=True), then
        confirm _lightweight_migrate() adds it back."""
        from anetbbs.models import db
        from anetbbs.web_app import _lightweight_migrate
        with self.app.app_context():
            self.assertTrue(self._has_index('game_scores', 'game_id'))
            db.session.execute(db.text(
                'DROP INDEX ix_game_scores_game_id'))
            db.session.commit()
            self.assertFalse(self._has_index('game_scores', 'game_id'))

            _lightweight_migrate(self.app)

            self.assertTrue(self._has_index('game_scores', 'game_id'))

    def test_backfills_game_sessions_composite_index_on_a_table_that_predates_it(self):
        from anetbbs.models import db
        from anetbbs.web_app import _lightweight_migrate
        with self.app.app_context():
            self.assertTrue(self._has_index('game_sessions', 'game_id', 'status'))
            db.session.execute(db.text(
                'DROP INDEX ix_game_sessions_game_id_status'))
            db.session.commit()
            self.assertFalse(self._has_index('game_sessions', 'game_id', 'status'))

            _lightweight_migrate(self.app)

            self.assertTrue(self._has_index('game_sessions', 'game_id', 'status'))

    def test_migration_is_idempotent(self):
        """Running the migration twice in a row (as happens on every
        app boot in production) must not raise, whether or not the
        indexes already exist."""
        from anetbbs.web_app import _lightweight_migrate
        with self.app.app_context():
            _lightweight_migrate(self.app)
            _lightweight_migrate(self.app)
            self.assertTrue(self._has_index('game_scores', 'game_id'))
            self.assertTrue(self._has_index('game_sessions', 'game_id', 'status'))


if __name__ == '__main__':
    unittest.main()
