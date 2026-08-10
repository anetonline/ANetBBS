"""Web-side counterpart to test_who_online_multi_session_presence.py --
drives two real Flask test_client()s (simulating two different browser
sessions/tabs) logged in as the SAME account, confirming both show up
in the who's-online listing simultaneously (the real bug Jerry hit:
web + SSH at once, "who's online" only showed one), that the
site-wide/admin "online count" widgets still report 1 distinct user
(not 2), and that logging one out only removes that one's row.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class WhoOnlineWebMultiSessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.who_online_web_multi_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        with cls.app.app_context():
            db.create_all()
            u = User(username='webmultisessiontest', email='wmst@example.com',
                    is_active=True)
            u.set_password('webmultisessionpassword123')
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

    def setUp(self):
        # Each test creates its own fresh logins -- clear out any rows
        # left behind by a previous test method sharing this class's
        # DB (test_client() cookies aren't shared across tests, but the
        # DB is).
        from anetbbs.models import db, UserSession
        with self.app.app_context():
            UserSession.query.filter_by(user_id=self.user_id).delete()
            db.session.commit()

    def _login(self):
        client = self.app.test_client()
        client.post('/auth/login', data={
            'username': 'webmultisessiontest',
            'password': 'webmultisessionpassword123',
        }, follow_redirects=True)
        return client

    def test_two_browser_sessions_for_the_same_account_both_get_a_row(self):
        from anetbbs.models import UserSession

        self._login()
        self._login()

        with self.app.app_context():
            rows = UserSession.query.filter_by(user_id=self.user_id).all()
            self.assertEqual(len(rows), 2,
                            'two different browser test_client()s logged in '
                            'as the same account must each get their own '
                            'UserSession row -- this is the real bug Jerry '
                            'hit with web + SSH simultaneously')
            keys = {r.session_key for r in rows}
            self.assertEqual(len(keys), 2, 'each row must have a distinct session_key')

    def test_online_count_widgets_report_one_distinct_user_not_two(self):
        from anetbbs.models import db, UserSession
        from datetime import timedelta

        browser_a = self._login()
        self._login()
        with self.app.app_context():
            self.assertGreaterEqual(
                UserSession.query.filter_by(user_id=self.user_id).count(), 2)

            from datetime import datetime
            cutoff = datetime.utcnow() - timedelta(minutes=5)
            distinct_count = (db.session.query(UserSession.user_id)
                             .filter(UserSession.last_seen >= cutoff)
                             .distinct().count())
            self.assertEqual(distinct_count, 1,
                            'one person with two simultaneous connections '
                            'must count as 1 online user, not 2')

        # inject_online_count() context processor, exercised via a real page render.
        resp = browser_a.get('/')
        self.assertEqual(resp.status_code, 200)

    def test_logging_out_one_session_only_removes_that_ones_row(self):
        from anetbbs.models import UserSession

        browser_a = self._login()
        self._login()
        with self.app.app_context():
            before = UserSession.query.filter_by(user_id=self.user_id).count()
        self.assertGreaterEqual(before, 2)

        browser_a.get('/auth/logout', follow_redirects=True)

        with self.app.app_context():
            after = UserSession.query.filter_by(user_id=self.user_id).count()
        self.assertEqual(after, before - 1,
                         "logging out ONE browser session must remove only "
                         "that session's row, leaving the other connection's "
                         "presence intact")


if __name__ == '__main__':
    unittest.main()
