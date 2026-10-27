"""Regression test for core.activity_log.web_log_activity() -- the new
shared helper added during a broader logging audit (2026-10-26) for web
blueprints other than web/auth.py (which already had its own private
_log_activity()) to log a UserActivity row correctly correlated to the
active login's CallerLog row, without each blueprint reinventing the
direct UserActivity(...) insert pattern that kept missing caller_log_id
(see test_web_file_download_activity_correlation.py for a real instance
of that bug). Drives a real Flask request/session via a throwaway test
route rather than mocking flask.request/flask.session, since the whole
point of this helper is reading those correctly.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class WebLogActivityHelperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent /
                         '.web_log_activity_helper_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User, CallerLog
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True

        @cls.app.route('/__test_web_log_activity__')
        def _probe():
            from anetbbs.core.activity_log import web_log_activity
            from flask import session as flask_session
            web_log_activity(flask_session.get('_probe_user_id'),
                             'probe_event', 'probe details')
            return 'ok'

        with cls.app.app_context():
            db.create_all()
            u = User(username='weblogactivitytest',
                    email='wlat@example.com', password_hash='x')
            db.session.add(u)
            db.session.commit()
            cls.user_id = u.id
            cl = CallerLog(user_id=u.id, username='weblogactivitytest',
                           service='web')
            db.session.add(cl)
            db.session.commit()
            cls.caller_log_id = cl.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def test_logs_with_caller_log_id_from_flask_session_and_real_ip(self):
        from anetbbs.models import UserActivity

        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_probe_user_id'] = self.user_id
            sess['caller_log_id'] = self.caller_log_id
        resp = client.get('/__test_web_log_activity__',
                          environ_overrides={'REMOTE_ADDR': '203.0.113.9'})
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            activity = (UserActivity.query
                       .filter_by(user_id=self.user_id,
                                 activity_type='probe_event')
                       .order_by(UserActivity.id.desc()).first())
            self.assertIsNotNone(activity)
            self.assertEqual(activity.details, 'probe details')
            self.assertEqual(activity.service, 'web')
            self.assertEqual(activity.caller_log_id, self.caller_log_id)
            self.assertEqual(activity.ip_address, '203.0.113.9')

    def test_explicit_caller_log_id_overrides_the_session_one(self):
        from anetbbs.models import UserActivity

        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_probe_user_id'] = self.user_id
            sess['caller_log_id'] = self.caller_log_id

        from anetbbs.core.activity_log import web_log_activity
        with self.app.test_request_context('/__test_web_log_activity__'):
            web_log_activity(self.user_id, 'probe_event_explicit', None,
                             caller_log_id=999999)

        with self.app.app_context():
            activity = (UserActivity.query
                       .filter_by(user_id=self.user_id,
                                 activity_type='probe_event_explicit')
                       .first())
            self.assertIsNotNone(activity)
            self.assertEqual(activity.caller_log_id, 999999)


if __name__ == '__main__':
    unittest.main()
