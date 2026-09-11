"""Regression test for a real Critical bug found in a security/
performance audit: anetbbs/web/feeds.py's echomail_rss() (the public,
anonymous-readable /feed/echomail.xml feed) rendered NETMAIL-tagged
EchoArea messages -- QWK-routed 1-on-1 PRIVATE mail, not broadcast
echomail, see web/echomail.py's _owns_netmail_echomail() docstring for
the full mechanism -- with only the area's own min_access_level/
is_sysop_only check, never the per-user ownership check every other
read path for these messages enforces (area listing, thread view,
read(), read_markdown(), netmail_inbox()). Since a NETMAIL area is
typically created with permissive defaults (not sysop-only, default
min_access_level), any visitor able to pass that default level could
read the subject/body/sender of every user's private netmail
system-wide via the public XML feed, with zero ownership scoping.

Fixed by excluding NETMAIL-tagged areas from the feed entirely.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class FeedsEchomailNetmailLeakTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.feeds_netmail_leak_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User, EchomailNetwork, EchoArea, EchomailMessage
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        with cls.app.app_context():
            db.create_all()

            user = User(username='feedleaktestuser', email='flt@example.com',
                       password_hash='x', is_admin=False, access_level=100)
            db.session.add(user)
            db.session.commit()
            cls.user_id = user.id

            net = EchomailNetwork(name='FeedLeakTestNet', network_type='qwk',
                                  is_active=True)
            db.session.add(net)
            db.session.commit()

            # NETMAIL area — created the same way echomail.py's
            # netmail_compose() creates one: no explicit min_access_level/
            # is_sysop_only override, so it gets the permissive model
            # defaults (min_access_level=10, not sysop-only).
            netmail_area = EchoArea(network_id=net.id, tag='NETMAIL',
                                    name='Netmail (Private)',
                                    is_active=True, is_subscribed=True)
            # Ordinary broadcast area for the control case.
            broadcast_area = EchoArea(network_id=net.id, tag='GENERAL',
                                      name='General Chat', min_access_level=0,
                                      is_active=True, is_subscribed=True)
            db.session.add_all([netmail_area, broadcast_area])
            db.session.commit()

            private_msg = EchomailMessage(
                area_id=netmail_area.id, network_id=net.id,
                from_name='Alice', to_name='Bob',
                subject='SuperSecretPrivateSubject',
                body='This is private 1-on-1 netmail content nobody else should see.',
                direction='inbound')
            public_msg = EchomailMessage(
                area_id=broadcast_area.id, network_id=net.id,
                from_name='Carol', to_name='All',
                subject='PubliclyBroadcastSubject',
                body='This is ordinary broadcast echomail.',
                direction='inbound')
            db.session.add_all([private_msg, public_msg])
            db.session.commit()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _client_as(self, user_id=None):
        client = self.app.test_client()
        if user_id is not None:
            with client.session_transaction() as sess:
                sess['_user_id'] = str(user_id)
                sess['_fresh'] = True
        return client

    def test_public_echomail_feed_does_not_leak_netmail_content(self):
        client = self._client_as(self.user_id)
        resp = client.get('/feed/echomail.xml')
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b'SuperSecretPrivateSubject', resp.data,
                         'private NETMAIL-area content must never appear '
                         'in the public echomail RSS feed')

    def test_public_echomail_feed_still_shows_broadcast_content(self):
        client = self._client_as(self.user_id)
        resp = client.get('/feed/echomail.xml')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'PubliclyBroadcastSubject', resp.data,
                     'ordinary broadcast echomail must still appear in the feed')

    def test_anonymous_request_does_not_leak_netmail_content_either(self):
        client = self._client_as()
        resp = client.get('/feed/echomail.xml')
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b'SuperSecretPrivateSubject', resp.data)


if __name__ == '__main__':
    unittest.main()
