"""Regression test for a real gap found in a systematic FK/index audit
(a security/performance audit): web/postcards.py's "my postcards"
listing route filters directly on Postcard.created_by_id
(`Postcard.query.filter_by(created_by_id=current_user.id)`), a page any
logged-in user visits routinely -- but the column was unindexed, like
every other FK column on that model except slug.

Fixed the same way NetmailMessage.received_at, UserSession.last_seen,
GameScore.game_id, GameSession(game_id, status), and
EchomailLastRead.last_message_id were: index=True on the model, plus a
_ensure_index() backfill in _lightweight_migrate() for installs whose
postcards table predates the change, since create_all() only creates
indexes declared on a model when it creates the table for the first
time.

Reuses the same create_app()-based migration test pattern as
test_netmail_received_at_index_migration.py and
test_game_score_session_index_migration.py.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class PostcardCreatedByIdIndexMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.postcard_created_by_id_index_migration_test.db')
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

    def test_fresh_install_has_the_postcards_index(self):
        """create_app() on a brand-new database creates the table with
        the index already declared on the model -- confirms the
        baseline before testing the upgrade/backfill path below."""
        with self.app.app_context():
            self.assertTrue(self._has_index('postcards', 'created_by_id'))

    def test_backfills_index_on_a_table_that_predates_it(self):
        """Simulates an upgrading install: drop the index (as if the
        table were created before created_by_id gained index=True),
        then confirm _lightweight_migrate() adds it back."""
        from anetbbs.models import db
        from anetbbs.web_app import _lightweight_migrate
        with self.app.app_context():
            self.assertTrue(self._has_index('postcards', 'created_by_id'))
            db.session.execute(db.text(
                'DROP INDEX ix_postcards_created_by_id'))
            db.session.commit()
            self.assertFalse(self._has_index('postcards', 'created_by_id'))

            _lightweight_migrate(self.app)

            self.assertTrue(self._has_index('postcards', 'created_by_id'))

    def test_migration_is_idempotent(self):
        """Running the migration twice in a row (as happens on every
        app boot in production) must not raise, whether or not the
        index already exists."""
        from anetbbs.web_app import _lightweight_migrate
        with self.app.app_context():
            _lightweight_migrate(self.app)
            _lightweight_migrate(self.app)
            self.assertTrue(self._has_index('postcards', 'created_by_id'))


if __name__ == '__main__':
    unittest.main()
