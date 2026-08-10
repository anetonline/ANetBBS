"""Regression test for the UserSession.session_key migration --
UserSession.user_id used to be unique=True, a hard one-row-per-user
constraint. Confirmed via schema inspection that this was a separate
SQLite `CREATE UNIQUE INDEX`, not an inline column constraint, so it
can be dropped without a table rebuild. Mirrors
test_activity_log_and_poll_log_migrations.py's pattern.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class WhoOnlineMultiSessionMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.who_online_migration_test.db')
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

    def _user_id_index(self):
        from sqlalchemy import inspect as _inspect
        from anetbbs.models import db
        insp = _inspect(db.engine)
        for idx in insp.get_indexes('user_sessions'):
            if idx['name'] == 'ix_user_sessions_user_id':
                return idx
        return None

    def _has_column(self, table, column):
        from sqlalchemy import inspect as _inspect
        from anetbbs.models import db
        insp = _inspect(db.engine)
        return column in {c['name'] for c in insp.get_columns(table)}

    def test_fresh_install_has_session_key_and_a_non_unique_user_id_index(self):
        with self.app.app_context():
            self.assertTrue(self._has_column('user_sessions', 'session_key'))
            idx = self._user_id_index()
            self.assertIsNotNone(idx)
            self.assertFalse(idx['unique'],
                            'fresh installs must not have a unique constraint '
                            'on user_id -- multiple connections per user must '
                            'be allowed')

    def test_two_rows_for_the_same_user_id_can_coexist(self):
        from anetbbs.models import db, User, UserSession
        with self.app.app_context():
            u = User(username='multisessiontest', email='mst@example.com',
                     is_active=True)
            u.set_password('x')
            db.session.add(u)
            db.session.commit()
            db.session.add(UserSession(user_id=u.id, session_key='web-key'))
            db.session.add(UserSession(user_id=u.id, session_key='ssh-key'))
            db.session.commit()  # would raise IntegrityError before the fix
            self.assertEqual(
                UserSession.query.filter_by(user_id=u.id).count(), 2)

    def test_upgrading_install_drops_the_old_unique_index(self):
        """Simulates a pre-fix database: the OLD unique index still
        exists (as it would on Jerry's live install before this
        migration runs). _lightweight_migrate() must drop it and leave
        a plain index in its place."""
        from anetbbs.models import db, UserSession
        from anetbbs.web_app import _lightweight_migrate
        with self.app.app_context():
            # Other tests in this class (sharing the same class-level DB)
            # may have left rows with a duplicate user_id -- CREATE
            # UNIQUE INDEX below would legitimately fail against real
            # duplicates, which isn't what this test is about (it's
            # only testing the index-migration mechanics).
            UserSession.query.delete()
            db.session.commit()
            db.session.execute(db.text('DROP INDEX ix_user_sessions_user_id'))
            db.session.execute(db.text(
                'CREATE UNIQUE INDEX ix_user_sessions_user_id ON user_sessions (user_id)'))
            db.session.commit()
            idx = self._user_id_index()
            self.assertTrue(idx['unique'], 'test setup sanity check')

            _lightweight_migrate(self.app)

            idx = self._user_id_index()
            self.assertIsNotNone(idx)
            self.assertFalse(idx['unique'],
                            '_lightweight_migrate() must drop the stale UNIQUE '
                            'index and replace it with a plain one')

    def test_migration_is_idempotent(self):
        from anetbbs.web_app import _lightweight_migrate
        with self.app.app_context():
            _lightweight_migrate(self.app)
            _lightweight_migrate(self.app)
            idx = self._user_id_index()
            self.assertIsNotNone(idx)
            self.assertFalse(idx['unique'])


if __name__ == '__main__':
    unittest.main()
