"""Regression test for the two schema additions in the weekend fixes
batch (Jerry's caller-log/activity-log drill-down + per-node echomail
poll-log filter): UserActivity.caller_log_id and
EchomailPollLog.node_id. Both are nullable FK columns the generic
auto-sweep in _lightweight_migrate() would add on its own, but neither
gets its explicit index without the _ensure_index() backfill added
alongside them -- create_all() only creates indexes declared on a
model when it creates the table for the first time (same reasoning as
test_user_session_last_seen_index_migration.py, reused here).
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class ActivityLogAndPollLogMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.activity_poll_log_migration_test.db')
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

    def _has_index(self, table, column):
        from sqlalchemy import inspect as _inspect
        from anetbbs.models import db
        insp = _inspect(db.engine)
        for idx in insp.get_indexes(table):
            if column in idx.get('column_names', []):
                return True
        return False

    def _has_column(self, table, column):
        from sqlalchemy import inspect as _inspect
        from anetbbs.models import db
        insp = _inspect(db.engine)
        return column in {c['name'] for c in insp.get_columns(table)}

    def test_fresh_install_has_both_columns_and_indexes(self):
        with self.app.app_context():
            self.assertTrue(self._has_column('user_activities', 'caller_log_id'))
            self.assertTrue(self._has_index('user_activities', 'caller_log_id'))
            self.assertTrue(self._has_column('echomail_poll_logs', 'node_id'))
            self.assertTrue(self._has_index('echomail_poll_logs', 'node_id'))

    def test_backfills_indexes_on_tables_that_predate_them(self):
        """Simulates an upgrading install: drop both indexes (columns
        stay, as they would on a real upgrade where the auto-sweep
        already added them in an earlier boot), confirm
        _lightweight_migrate() adds the indexes back."""
        from anetbbs.models import db
        from anetbbs.web_app import _lightweight_migrate
        with self.app.app_context():
            db.session.execute(db.text(
                'DROP INDEX ix_user_activities_caller_log_id'))
            db.session.execute(db.text(
                'DROP INDEX ix_echomail_poll_logs_node_id'))
            db.session.commit()
            self.assertFalse(self._has_index('user_activities', 'caller_log_id'))
            self.assertFalse(self._has_index('echomail_poll_logs', 'node_id'))

            _lightweight_migrate(self.app)

            self.assertTrue(self._has_index('user_activities', 'caller_log_id'))
            self.assertTrue(self._has_index('echomail_poll_logs', 'node_id'))

    def test_migration_is_idempotent(self):
        from anetbbs.web_app import _lightweight_migrate
        with self.app.app_context():
            _lightweight_migrate(self.app)
            _lightweight_migrate(self.app)
            self.assertTrue(self._has_index('user_activities', 'caller_log_id'))
            self.assertTrue(self._has_index('echomail_poll_logs', 'node_id'))


if __name__ == '__main__':
    unittest.main()
