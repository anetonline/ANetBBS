"""Regression test for UserSession.caller_log_id -- added so
events/handlers.py::cleanup_stale_sessions can backfill
CallerLog.duration_seconds for a web login that never hit /logout (see
UserSession's own model comment and test_cleanup_stale_sessions_handler.py
for the behavior this column enables). The nullable FK column itself is
added automatically by _lightweight_migrate()'s generic auto-sweep on an
upgrading install; this test covers the one part that isn't automatic --
the explicit index=True backfill, same reasoning as
test_activity_log_and_poll_log_migrations.py and
test_user_session_last_seen_index_migration.py, reused here.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class UserSessionCallerLogIdMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent /
                         '.user_session_caller_log_id_migration_test.db')
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

    def _has_column(self, table, column):
        from sqlalchemy import inspect as _inspect
        from anetbbs.models import db
        insp = _inspect(db.engine)
        return column in {c['name'] for c in insp.get_columns(table)}

    def _has_index(self, table, column):
        from sqlalchemy import inspect as _inspect
        from anetbbs.models import db
        insp = _inspect(db.engine)
        for idx in insp.get_indexes(table):
            if column in idx.get('column_names', []):
                return True
        return False

    def test_fresh_install_has_the_column_and_index(self):
        with self.app.app_context():
            self.assertTrue(self._has_column('user_sessions', 'caller_log_id'))
            self.assertTrue(self._has_index('user_sessions', 'caller_log_id'))

    def test_backfills_the_index_on_a_table_that_predates_it(self):
        """Simulates an upgrading install: drop the index (column stays,
        as it would on a real upgrade where an earlier boot's generic
        auto-sweep already added it), confirm _lightweight_migrate()
        adds the index back."""
        from anetbbs.models import db
        from anetbbs.web_app import _lightweight_migrate
        with self.app.app_context():
            db.session.execute(db.text(
                'DROP INDEX ix_user_sessions_caller_log_id'))
            db.session.commit()
            self.assertFalse(self._has_index('user_sessions', 'caller_log_id'))

            _lightweight_migrate(self.app)

            self.assertTrue(self._has_index('user_sessions', 'caller_log_id'))

    def test_migration_is_idempotent(self):
        from anetbbs.web_app import _lightweight_migrate
        with self.app.app_context():
            _lightweight_migrate(self.app)
            _lightweight_migrate(self.app)
            self.assertTrue(self._has_index('user_sessions', 'caller_log_id'))


if __name__ == '__main__':
    unittest.main()
