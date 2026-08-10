"""Regression test for the activity-log drill-down (Jerry: "you should
be able to click on that name [in the caller log] for that session and
see exactly what they did"). UserActivity gained a caller_log_id column
so events can be grouped per login; this test drives a real web
login/logout and confirms:

  1. The login route's CallerLog row and its 'login' UserActivity row
     share the same caller_log_id (the login route now creates the
     CallerLog row and stashes its id in flask_session BEFORE calling
     _log_activity(), instead of after).
  2. The logout route's 'logout' UserActivity row uses the SAME
     caller_log_id (correlating the whole session, not just login).
  3. CallerLog.duration_seconds -- declared on the model and shown in
     two admin templates but never actually written anywhere before
     this fix -- is now a real positive number after logout.
"""
import os
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class ActivityLogSessionCorrelationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.activity_correlation_test.db')
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
            u = User(username='correlationtest', email='ct@example.com',
                     is_active=True)
            u.set_password('correlationpassword123')
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

    def test_login_and_logout_events_share_one_caller_log_id_and_duration_is_recorded(self):
        from anetbbs.models import db, CallerLog, UserActivity

        client = self.app.test_client()
        resp = client.post('/auth/login', data={
            'username': 'correlationtest', 'password': 'correlationpassword123',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with client.session_transaction() as sess:
            cl_id = sess.get('caller_log_id')
        self.assertIsNotNone(cl_id, 'login must stash caller_log_id in flask_session')

        with self.app.app_context():
            login_event = (UserActivity.query
                          .filter_by(user_id=self.user_id, activity_type='login')
                          .order_by(UserActivity.id.desc()).first())
            self.assertIsNotNone(login_event)
            self.assertEqual(login_event.caller_log_id, cl_id,
                             "login's UserActivity row must correlate to the "
                             "CallerLog row created in the same request")

        time.sleep(1.1)  # duration_seconds must be > 0, not just non-null
        resp = client.get('/auth/logout', follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            cl = db.session.get(CallerLog, cl_id)
            self.assertIsNotNone(cl.duration_seconds)
            self.assertGreater(cl.duration_seconds, 0,
                              'CallerLog.duration_seconds must actually be '
                              'written on logout, not stay stuck at 0/None')

            logout_event = (UserActivity.query
                           .filter_by(user_id=self.user_id, activity_type='logout')
                           .order_by(UserActivity.id.desc()).first())
            self.assertIsNotNone(logout_event)
            self.assertEqual(logout_event.caller_log_id, cl_id,
                             'logout must correlate to the SAME caller_log_id '
                             'as login, not a new/different one')


if __name__ == '__main__':
    unittest.main()
