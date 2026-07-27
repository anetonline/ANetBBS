"""Regression test: UserManager.create_user() (anetbbs/core/user_manager.py)
unconditionally reported 'email_taken' for ANY IntegrityError on the
final commit, regardless of which unique constraint (username or email)
actually fired. On a genuine race -- two concurrent registrations of the
same username, both passing the pre-check queries above before either
commits -- the username collision surfaced here, and the second
registrant was told an account with their EMAIL already existed, which
was false.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class CreateUserRaceMislabelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.create_user_race_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()

        import anetbbs.core.user_manager as um_mod
        from sqlalchemy.orm import sessionmaker
        with cls.app.app_context():
            test_engine = db.engine
        cls._orig_um_session = um_mod._Session
        um_mod._Session = sessionmaker(bind=test_engine, future=True, expire_on_commit=False)
        cls.um_mod = um_mod

    @classmethod
    def tearDownClass(cls):
        cls.um_mod._Session = cls._orig_um_session
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _simulated_race_commit(self, real_commit_fn, message_substring):
        """Return a function that raises a real IntegrityError (matching
        the shape SQLite actually produces) the FIRST time it's called,
        simulating a race where the conflicting row landed between this
        request's own pre-check queries and its commit."""
        from sqlalchemy.exc import IntegrityError
        calls = {'n': 0}

        def _commit(self_session, *a, **kw):
            calls['n'] += 1
            if calls['n'] == 1:
                raise IntegrityError(
                    'INSERT INTO users ...', {},
                    Exception(f'UNIQUE constraint failed: users.{message_substring}'))
            return real_commit_fn(self_session, *a, **kw)
        return _commit

    def test_username_collision_reports_username_taken_not_email_taken(self):
        from sqlalchemy.orm import Session as _RealSession
        real_commit = _RealSession.commit
        um = self.um_mod.UserManager()

        with patch.object(_RealSession, 'commit',
                          self._simulated_race_commit(real_commit, 'username')):
            result = um.create_user('raceuser', 'correcthorsebatterystaple',
                                    'raceuser@example.com')
        self.assertEqual(result, 'username_taken')

    def test_email_collision_still_reports_email_taken(self):
        from sqlalchemy.orm import Session as _RealSession
        real_commit = _RealSession.commit
        um = self.um_mod.UserManager()

        with patch.object(_RealSession, 'commit',
                          self._simulated_race_commit(real_commit, 'email')):
            result = um.create_user('raceuser2', 'correcthorsebatterystaple',
                                    'raceuser2@example.com')
        self.assertEqual(result, 'email_taken')


if __name__ == '__main__':
    unittest.main()
