"""Regression tests for Internet Mail's Phase 1 schema: the new
InternetMail/MailConfig tables (free via db.create_all(), no
_ensure_column needed) and the two new User columns (email_enabled/
email_local_part), which DO need _ensure_column entries in
_lightweight_migrate() since they're added to an existing table.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class InternetMailSchemaMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.internet_mail_migration_test.db')
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

    def _user_columns(self):
        from sqlalchemy import inspect as _inspect
        from anetbbs.models import db
        insp = _inspect(db.engine)
        return {c['name'] for c in insp.get_columns('users')}

    def _has_table(self, name):
        from sqlalchemy import inspect as _inspect
        from anetbbs.models import db
        insp = _inspect(db.engine)
        return name in insp.get_table_names()

    def _user_indexes(self):
        from sqlalchemy import inspect as _inspect
        from anetbbs.models import db
        insp = _inspect(db.engine)
        return {i['name'] for i in insp.get_indexes('users')}

    def test_fresh_install_has_internet_mail_and_mail_config_tables(self):
        with self.app.app_context():
            self.assertTrue(self._has_table('internet_mail'))
            self.assertTrue(self._has_table('mail_config'))

    def test_fresh_install_has_user_email_columns(self):
        with self.app.app_context():
            cols = self._user_columns()
            self.assertIn('email_enabled', cols)
            self.assertIn('email_local_part', cols)

    def test_backfills_user_email_columns_on_an_upgrading_install(self):
        from anetbbs.models import db
        from anetbbs.web_app import _lightweight_migrate
        with self.app.app_context():
            # SQLite refuses to drop a column while an index on it still
            # exists ("error in index ... after drop column") -- drop
            # the index first, same as the existing index-migration
            # tests (e.g. test_user_session_last_seen_index_migration.py).
            db.session.execute(db.text('DROP INDEX ix_users_email_enabled'))
            db.session.execute(db.text('DROP INDEX ix_users_email_local_part'))
            db.session.execute(db.text('ALTER TABLE users DROP COLUMN email_enabled'))
            db.session.execute(db.text('ALTER TABLE users DROP COLUMN email_local_part'))
            db.session.commit()
            self.assertNotIn('email_enabled', self._user_columns())

            _lightweight_migrate(self.app)

            cols = self._user_columns()
            self.assertIn('email_enabled', cols)
            self.assertIn('email_local_part', cols)
            indexes = self._user_indexes()
            self.assertIn('ix_users_email_enabled', indexes)
            self.assertIn('ix_users_email_local_part', indexes)

    def test_migration_is_idempotent(self):
        from anetbbs.web_app import _lightweight_migrate
        with self.app.app_context():
            _lightweight_migrate(self.app)
            _lightweight_migrate(self.app)
            self.assertIn('email_enabled', self._user_columns())

    def test_mail_config_singleton_creates_disabled_row_if_absent(self):
        from anetbbs.models import MailConfig
        with self.app.app_context():
            cfg = MailConfig.get()
            self.assertFalse(cfg.enabled)
            self.assertEqual(cfg.min_access_level, 100)
            # Idempotent -- calling .get() again returns the same row,
            # doesn't create a second one.
            cfg2 = MailConfig.get()
            self.assertEqual(cfg.id, cfg2.id)
            self.assertEqual(MailConfig.query.count(), 1)


if __name__ == '__main__':
    unittest.main()
