"""Regression tests for a broader logging audit (2026-10-26): most web
blueprints never wrote any UserActivity row at all for real user
actions -- only web/auth.py's login/register/logout/password-reset did.
anetbbs/web/echomail.py now logs via the shared
anetbbs.core.activity_log.web_log_activity() helper at these call sites:

  - area()            -> 'echomail_area_viewed' (coarse: once per area
                          opened, not once per message read inside it)
  - compose()          -> 'echomail_post' (new message) or
                          'echomail_reply' (reply_to_id given)
  - delete_message()   -> 'echomail_delete'
  - netmail_compose()  -> 'netmail_sent'

Each test drives the real route through a real Flask test client and
asserts the resulting UserActivity row's activity_type, details, and --
critically -- caller_log_id, which must match the id auth.py's login
route stashes in flask_session (session['caller_log_id']). Without that
correlation the row exists but never surfaces in the admin caller-log
drill-down, same bug class closed for file downloads in
test_web_file_download_activity_correlation.py.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class WebEchomailActivityLoggingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent /
                         '.web_echomail_activity_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import (db, User, EchomailNetwork, EchoArea,
                                     EchomailMessage, CallerLog)
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        with cls.app.app_context():
            db.create_all()

            # Admin so area/network access checks are a non-issue --
            # this audit is about logging, not access control (already
            # covered by other test files).
            user = User(username='echoacttest', email='echoacttest@example.com',
                       password_hash='x', access_level=100, is_admin=True)
            db.session.add(user)
            db.session.commit()
            cls.user_id = user.id

            cl = CallerLog(user_id=user.id, username='echoacttest', service='web')
            db.session.add(cl)
            db.session.commit()
            cls.caller_log_id = cl.id

            net = EchomailNetwork(name='ActivityTestNet', network_type='binkp',
                                  is_active=True, our_address='1:1/1')
            db.session.add(net)
            db.session.flush()
            cls.net_id = net.id

            area = EchoArea(network_id=net.id, tag='ACTTEST', name='Activity Test Area',
                           is_active=True, is_subscribed=True,
                           is_sysop_only=False, min_access_level=0)
            db.session.add(area)
            db.session.commit()
            cls.area_id = area.id

            # A pre-existing message for the delete() and reply() tests.
            seed = EchomailMessage(
                area_id=area.id, network_id=net.id, msg_id='seed-msg-1',
                from_name='Someone', to_name='All', subject='Seed Subject',
                body='Seed body.', direction='inbound')
            db.session.add(seed)
            db.session.commit()
            cls.seed_msg_id = seed.id

            qwk_net = EchomailNetwork(name='ActivityTestQWK', network_type='qwk',
                                      is_active=True)
            db.session.add(qwk_net)
            db.session.commit()
            cls.qwk_net_id = qwk_net.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _client(self):
        """A logged-in client whose Flask session also carries
        caller_log_id, mirroring what auth.py's real login route
        stashes there."""
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(self.user_id)
            sess['_fresh'] = True
            sess['caller_log_id'] = self.caller_log_id
        return client

    def test_area_view_logs_coarse_area_viewed_event(self):
        from anetbbs.models import UserActivity
        client = self._client()
        resp = client.get(f'/echomail/{self.area_id}')
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            activity = (UserActivity.query
                       .filter_by(user_id=self.user_id,
                                 activity_type='echomail_area_viewed')
                       .order_by(UserActivity.id.desc()).first())
            self.assertIsNotNone(
                activity, 'opening an echomail area must log an '
                'echomail_area_viewed UserActivity row')
            self.assertEqual(activity.caller_log_id, self.caller_log_id)
            self.assertIn('Activity Test Area', activity.details)

    def test_compose_new_message_logs_echomail_post(self):
        from anetbbs.models import UserActivity
        client = self._client()
        resp = client.post(f'/echomail/{self.area_id}/compose', data={
            'area_id': str(self.area_id),
            'to_name': 'All',
            'subject': 'Brand New Composed Subject',
            'body': 'Hello world, this is a new message.',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            activity = (UserActivity.query
                       .filter_by(user_id=self.user_id,
                                 activity_type='echomail_post')
                       .order_by(UserActivity.id.desc()).first())
            self.assertIsNotNone(
                activity, 'composing a new echomail message must log an '
                'echomail_post UserActivity row')
            self.assertEqual(activity.caller_log_id, self.caller_log_id)
            self.assertIn('Brand New Composed Subject', activity.details)

    def test_compose_reply_logs_echomail_reply(self):
        from anetbbs.models import UserActivity
        client = self._client()
        resp = client.post(
            f'/echomail/{self.area_id}/reply/{self.seed_msg_id}', data={
                'area_id': str(self.area_id),
                'to_name': 'Someone',
                'subject': 'Re: Seed Subject',
                'body': 'Replying to the seed message.',
            }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            activity = (UserActivity.query
                       .filter_by(user_id=self.user_id,
                                 activity_type='echomail_reply')
                       .order_by(UserActivity.id.desc()).first())
            self.assertIsNotNone(
                activity, 'replying to an echomail message must log an '
                'echomail_reply UserActivity row, not echomail_post')
            self.assertEqual(activity.caller_log_id, self.caller_log_id)
            self.assertIn('Re: Seed Subject', activity.details)

    def test_delete_message_logs_echomail_delete(self):
        from anetbbs.models import db, EchomailMessage, UserActivity

        with self.app.app_context():
            msg = EchomailMessage(
                area_id=self.area_id, network_id=self.net_id,
                msg_id='delete-target-1', from_name='Someone', to_name='All',
                subject='Message To Be Deleted', body='Delete me.',
                direction='inbound')
            db.session.add(msg)
            db.session.commit()
            msg_id = msg.id

        client = self._client()
        resp = client.post(f'/echomail/{self.area_id}/{msg_id}/delete',
                           follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            self.assertIsNone(db.session.get(EchomailMessage, msg_id))
            activity = (UserActivity.query
                       .filter_by(user_id=self.user_id,
                                 activity_type='echomail_delete')
                       .order_by(UserActivity.id.desc()).first())
            self.assertIsNotNone(
                activity, 'deleting an echomail message must log an '
                'echomail_delete UserActivity row')
            self.assertEqual(activity.caller_log_id, self.caller_log_id)
            self.assertIn('Message To Be Deleted', activity.details)

    def test_netmail_compose_logs_netmail_sent(self):
        from anetbbs.models import UserActivity
        client = self._client()
        resp = client.post('/echomail/netmail/compose', data={
            'network_id': str(self.qwk_net_id),
            'to_name': 'RemoteUser',
            'to_address': '',
            'subject': 'Private Netmail Subject',
            'body': 'A private 1-on-1 message.',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            activity = (UserActivity.query
                       .filter_by(user_id=self.user_id,
                                 activity_type='netmail_sent')
                       .order_by(UserActivity.id.desc()).first())
            self.assertIsNotNone(
                activity, 'sending netmail must log a netmail_sent '
                'UserActivity row')
            self.assertEqual(activity.caller_log_id, self.caller_log_id)
            self.assertIn('Private Netmail Subject', activity.details)


if __name__ == '__main__':
    unittest.main()
