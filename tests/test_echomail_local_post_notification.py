"""Regression test for a real gap found live: two local ANetBBS accounts
replying to each other directly in a shared echo area (no external
network transfer involved at all) never triggered the echomail_reply
notification -- the notify_reply.py hook (anetbbs/echomail/notify_reply.py's
maybe_notify_recipient(), wired up earlier this session) was only ever
called from the THREE inbound-network-import paths (poller.py's
_import_message, binkp_server.py's real-time listener, qwk_hub_ftp.py's
process_rep_upload), all of which fire only when a message arrives FROM
an external network. A local user composing/replying directly through
ANetBBS's own web or terminal composer is a fourth, previously-unhooked
write path into EchomailMessage.

Reported live: sysop logged in as one account, posted a reply in
"ANotherNetwork" addressed TO: another of their own accounts, then
logged in as that second account and got no notification at all, even
though the message itself was saved and visible in the area correctly.

Covers the web compose route (anetbbs/web/echomail.py's compose()) --
the terminal composer (anetbbs/features/bbs_ui.py's _compose_echomail())
got the identical fix but isn't covered here (driving the terminal
composer end-to-end needs the full ANEdit editor mocked, which is out
of proportion for what's otherwise the same three-line hook already
proven correct by the web test and the existing notify_reply.py unit
tests).
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class EchomailLocalPostNotificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.echomail_local_post_notif_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User, EchomailNetwork, EchoArea
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        with cls.app.app_context():
            db.create_all()

            sender = User(username='jerryreed', email='jerryreed@example.com',
                         password_hash='x', access_level=100)
            db.session.add(sender)
            recipient = User(username='StingRay', email='stingray@example.com',
                             password_hash='x', access_level=100)
            db.session.add(recipient)
            db.session.commit()
            cls.sender_id = sender.id
            cls.recipient_id = recipient.id

            net = EchomailNetwork(name='ANotherNetwork', network_type='qwk')
            db.session.add(net)
            db.session.flush()
            area = EchoArea(network_id=net.id, tag='ANET_TEST', name='ANet Test Area',
                            is_active=True, is_subscribed=True, min_access_level=10)
            db.session.add(area)
            db.session.commit()
            cls.area_id = area.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _client_as(self, user_id):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(user_id)
            sess['_fresh'] = True
        return client

    def test_local_reply_addressed_to_a_real_user_creates_notification(self):
        from anetbbs.models import Notification, EchomailMessage
        client = self._client_as(self.sender_id)
        resp = client.post(f'/echomail/{self.area_id}/compose', data={
            'area_id': str(self.area_id),
            'to_name': 'StingRay',
            'subject': 'Test reply',
            'body': 'Hello StingRay, this is a local reply.',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            msg = EchomailMessage.query.filter_by(subject='Test reply').first()
            self.assertIsNotNone(msg, 'the message itself must still be saved correctly')
            self.assertEqual(msg.to_name, 'StingRay')

            notif = Notification.query.filter_by(
                user_id=self.recipient_id, kind='echomail_reply').first()
            self.assertIsNotNone(
                notif, 'a local post/reply addressed to a real user must notify '
                'them, same as an inbound network message would')
            self.assertIn('jerryreed', notif.title)
            self.assertEqual(notif.target_url, f'/echomail/{self.area_id}/{msg.id}')

    def test_local_post_addressed_to_all_creates_no_notification(self):
        from anetbbs.models import Notification
        client = self._client_as(self.sender_id)
        resp = client.post(f'/echomail/{self.area_id}/compose', data={
            'area_id': str(self.area_id),
            'to_name': 'All',
            'subject': 'General post',
            'body': 'Nothing addressed to anyone specific.',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        with self.app.app_context():
            self.assertEqual(
                Notification.query.filter_by(kind='echomail_reply').count(), 0)


if __name__ == '__main__':
    unittest.main()
