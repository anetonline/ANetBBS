"""Regression test for a gap found in a broader web-blueprint logging
audit (2026-10-26): web/pm.py never wrote any UserActivity row at all,
so a private message sent through the web UI left no trace in the
admin "caller log" session drill-down (admin.caller_log_detail), even
though the message itself was delivered correctly.

Composing/sending a PM is the one clearly discrete, meaningful action
in this blueprint -- see pm.py's compose() route. Viewing the inbox or
reading a message thread is deliberately NOT logged here (paginated,
frequent, low-signal -- see the module docstring in
anetbbs/core/activity_log.py and the audit's own granularity guidance).

Uses the shared web_log_activity() helper (anetbbs/core/activity_log.py)
so the row also carries caller_log_id, correlating it to the sender's
login session -- the exact field web/files.py and web/file_areas.py
forgot in an earlier, already-fixed bug (see
test_web_file_download_activity_correlation.py).
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class WebPmActivityLoggingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent /
                         '.web_pm_activity_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User, CallerLog
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        with cls.app.app_context():
            db.create_all()

            sender = User(username='pmsender', email='pmsender@example.com',
                          password_hash='x', is_admin=False, access_level=10)
            recipient = User(username='pmrecipient', email='pmrecip@example.com',
                             password_hash='x', is_admin=False, access_level=10)
            db.session.add_all([sender, recipient])
            db.session.commit()
            cls.sender_id = sender.id
            cls.recipient_id = recipient.id

            cl = CallerLog(user_id=sender.id, username='pmsender', service='web')
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

    def _sender_client(self):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(self.sender_id)
            sess['_fresh'] = True
            sess['caller_log_id'] = self.caller_log_id
        return client

    def test_compose_logs_pm_sent_activity_with_caller_log_id(self):
        from anetbbs.models import UserActivity
        client = self._sender_client()
        resp = client.post('/messages/compose', data={
            'recipient': 'pmrecipient',
            'subject': 'Hello there',
            'content': 'Just testing the PM send path.',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            activity = (UserActivity.query
                       .filter_by(user_id=self.sender_id, activity_type='pm_sent')
                       .order_by(UserActivity.id.desc()).first())
            self.assertIsNotNone(
                activity, 'sending a PM must log a pm_sent UserActivity row')
            self.assertIn('pmrecipient', activity.details)
            self.assertIn('Hello there', activity.details)
            self.assertEqual(activity.caller_log_id, self.caller_log_id)
            self.assertEqual(activity.service, 'web')


if __name__ == '__main__':
    unittest.main()
