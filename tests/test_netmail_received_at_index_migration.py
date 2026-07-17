"""Regression test for a real live incident: NetmailMessage.received_at
gained index=True in v1.0b2.145 (the content-based netmail dedup
fallback added in v1.0b2.143 filters on this column to bound its
lookback window), but SQLAlchemy's create_all() only creates indexes
declared on a model when it's creating the TABLE for the first time --
it does not retroactively add a newly-declared index to a table that
already exists on an upgraded install. Every already-installed sysop's
database needed an explicit backfill (_ensure_index() in web_app.py,
mirroring the existing _ensure_column() pattern) to actually get the
index. Without it, the dedup query kept doing an unindexed scan after
every upgrade -- and under eventlet (this app's web process
monkey-patches threading but not sqlite3), a slow query blocks the
entire process for every user on every page, which is exactly what a
real live report described: the whole web UI freezing for minutes at a
time, worsening as more never-deduped rows piled up in the table.

Reuses the create_app()-based migration test pattern from
test_hub_identity_migration.py.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class NetmailReceivedAtIndexMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.netmail_index_migration_test.db')
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

    def _has_received_at_index(self):
        from sqlalchemy import inspect as _inspect
        from anetbbs.models import db
        insp = _inspect(db.engine)
        for idx in insp.get_indexes('netmail_messages'):
            if 'received_at' in idx.get('column_names', []):
                return True
        return False

    def test_fresh_install_has_the_index(self):
        """create_app() on a brand-new database creates the table with
        the index already declared on the model -- confirms the
        baseline before testing the upgrade/backfill path below."""
        with self.app.app_context():
            self.assertTrue(self._has_received_at_index())

    def test_backfills_index_on_a_table_that_predates_it(self):
        """Simulates an upgrading install: drop the index (as if the
        table were created before received_at gained index=True), then
        confirm _lightweight_migrate() adds it back."""
        from anetbbs.models import db
        from anetbbs.web_app import _lightweight_migrate
        with self.app.app_context():
            self.assertTrue(self._has_received_at_index())
            db.session.execute(db.text(
                'DROP INDEX ix_netmail_messages_received_at'))
            db.session.commit()
            self.assertFalse(self._has_received_at_index())

            _lightweight_migrate(self.app)

            self.assertTrue(self._has_received_at_index())

    def test_migration_is_idempotent(self):
        """Running the migration twice in a row (as happens on every
        app boot in production) must not raise, whether or not the
        index already exists."""
        from anetbbs.web_app import _lightweight_migrate
        with self.app.app_context():
            _lightweight_migrate(self.app)
            _lightweight_migrate(self.app)
            self.assertTrue(self._has_received_at_index())


if __name__ == '__main__':
    unittest.main()
