"""Regression test for a real bug found while fixing "who's online"
for simultaneous connections (see test_who_online_multi_session_*.py):
profile.py::is_user_online() used to fetch ANY one UserSession row for
a user_id with no ordering, and check just that row's last_seen. Fine
when user_id was unique=True (only one row could ever exist); with
multiple rows now possible, an arbitrary/stale row could be picked
over a genuinely active one, reporting an online user as offline.
"""
import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class IsUserOnlineMultiSessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.is_user_online_test.db')
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
            u = User(username='isuseronlinetest', email='iuot@example.com',
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

    def test_a_stale_first_session_plus_a_fresh_second_session_reports_online(self):
        from anetbbs.models import db, User, UserSession
        from anetbbs.web.profile import is_user_online

        with self.app.app_context():
            UserSession.query.filter_by(user_id=self.user_id).delete()
            db.session.commit()
            # Oldest row (e.g. an SSH session from an hour ago) is
            # stale -- inserted FIRST so a naive .first() with no
            # ordering would likely return this one.
            db.session.add(UserSession(
                user_id=self.user_id, session_key='old',
                last_seen=datetime.utcnow() - timedelta(hours=1)))
            db.session.commit()
            # A second, currently-active connection (e.g. the web tab
            # open right now).
            db.session.add(UserSession(
                user_id=self.user_id, session_key='fresh',
                last_seen=datetime.utcnow()))
            db.session.commit()

            user = db.session.get(User, self.user_id)
            self.assertTrue(is_user_online(user),
                           'a genuinely active second session must report '
                           'online even when an older, stale row for the '
                           'same user also exists')

    def test_all_sessions_stale_reports_offline(self):
        from anetbbs.models import db, User, UserSession
        from anetbbs.web.profile import is_user_online

        with self.app.app_context():
            UserSession.query.filter_by(user_id=self.user_id).delete()
            db.session.add(UserSession(
                user_id=self.user_id, session_key='old1',
                last_seen=datetime.utcnow() - timedelta(hours=2)))
            db.session.add(UserSession(
                user_id=self.user_id, session_key='old2',
                last_seen=datetime.utcnow() - timedelta(hours=1)))
            db.session.commit()

            user = db.session.get(User, self.user_id)
            self.assertFalse(is_user_online(user))


if __name__ == '__main__':
    unittest.main()
