"""Regression tests for a real access-control bug found in a full
application-wide audit (same class as the just-fixed web/netmail.py
bug): anetbbs/web/echomail.py's netmail_inbox() showed the last 100
messages from EVERY network's NETMAIL-tagged echo area (QWK-routed
1-on-1 private mail, a separate mechanism from the real NetmailMessage
table web/netmail.py uses) to ANY logged-in user, with zero recipient
filtering at all -- the route's own old docstring admitted this
("Per-user filtering by recipient name is left for a future
revision"). The direct-by-ID read() route also had no ownership check
for these messages, AND was separately missing the area
min_access_level/is_sysop_only check every sibling route in the file
already has.

Fixed with _owns_netmail_echomail(): admins see everything (the
historical sysop-review catch-all), regular users only see messages
addressed to/from their own username/display_name/AKA.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class EchomailNetmailAccessControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.echomail_netmail_access_test.db')
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

            admin = User(username='qwknmadmintest', email='qat@example.com',
                        password_hash='x', is_admin=True, access_level=100)
            alice = User(username='alicenmtest', email='ant@example.com',
                        password_hash='x', is_admin=False, access_level=100)
            bob = User(username='bobnmtest', email='bnt@example.com',
                      password_hash='x', is_admin=False, access_level=100)
            db.session.add_all([admin, alice, bob])
            db.session.commit()
            cls.admin_id = admin.id
            cls.alice_id = alice.id
            cls.bob_id = bob.id

            net = EchomailNetwork(name='QwkNetmailAccessNet', network_type='qwk',
                                  is_active=True)
            db.session.add(net)
            db.session.commit()
            cls.net_id = net.id

            area = EchoArea(network_id=net.id, tag='NETMAIL', name='Netmail',
                            is_active=True, is_subscribed=True, min_access_level=0)
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

    def _make_msg(self, to_name, from_name, subject, to_address='', from_address=''):
        from anetbbs.models import db, EchomailMessage
        with self.app.app_context():
            msg = EchomailMessage(
                area_id=self.area_id, network_id=self.net_id,
                from_name=from_name, from_address=from_address,
                to_name=to_name, to_address=to_address,
                subject=subject, body='x', direction='inbound')
            db.session.add(msg)
            db.session.commit()
            return msg.id

    # ---- netmail_inbox() list view ----

    def test_regular_user_does_not_see_netmail_addressed_to_someone_else(self):
        self._make_msg(to_name='bobnmtest', from_name='SomePeer',
                       subject='Private to Bob only')
        client = self._client_as(self.alice_id)
        resp = client.get('/echomail/netmail')
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b'Private to Bob only', resp.data)

    def test_regular_user_sees_netmail_addressed_to_them(self):
        self._make_msg(to_name='alicenmtest', from_name='SomePeer',
                       subject='For Alice specifically')
        client = self._client_as(self.alice_id)
        resp = client.get('/echomail/netmail')
        self.assertIn(b'For Alice specifically', resp.data)

    def test_regular_user_sees_netmail_they_sent(self):
        self._make_msg(to_name='SomePeer', from_name='alicenmtest',
                       subject='Alice sent this out')
        client = self._client_as(self.alice_id)
        resp = client.get('/echomail/netmail')
        self.assertIn(b'Alice sent this out', resp.data)

    def test_admin_still_sees_everything(self):
        self._make_msg(to_name='bobnmtest', from_name='SomePeer',
                       subject='Admin catch-all visibility check')
        client = self._client_as(self.admin_id)
        resp = client.get('/echomail/netmail')
        self.assertIn(b'Admin catch-all visibility check', resp.data)

    def test_user_own_aka_address_match_works(self):
        from anetbbs.models import db, UserAka
        with self.app.app_context():
            db.session.add(UserAka(user_id=self.bob_id, address='9:9/42'))
            db.session.commit()
        self._make_msg(to_name='Whoever', from_name='SomePeer',
                       subject='Via Bobs own AKA address', to_address='9:9/42')
        client = self._client_as(self.bob_id)
        resp = client.get('/echomail/netmail')
        self.assertIn(b'Via Bobs own AKA address', resp.data)

    def test_blank_to_name_does_not_match_a_user_with_no_display_name(self):
        """Real edge case caught while fixing: an empty to_name/from_name
        must never match an empty display_name -- that would let a user
        without a display_name see every blank-addressed message."""
        self._make_msg(to_name='', from_name='', subject='Blank fields test')
        client = self._client_as(self.bob_id)
        resp = client.get('/echomail/netmail')
        self.assertNotIn(b'Blank fields test', resp.data)

    # ---- read() direct access ----

    def test_regular_user_cannot_read_someone_elses_netmail_by_direct_id(self):
        msg_id = self._make_msg(to_name='bobnmtest', from_name='SomePeer',
                                subject='Direct read attempt')
        client = self._client_as(self.alice_id)
        resp = client.get(f'/echomail/{self.area_id}/{msg_id}')
        self.assertEqual(resp.status_code, 403)

    def test_recipient_can_read_their_own_netmail_by_direct_id(self):
        msg_id = self._make_msg(to_name='bobnmtest', from_name='SomePeer',
                                subject='Bob can read this')
        client = self._client_as(self.bob_id)
        resp = client.get(f'/echomail/{self.area_id}/{msg_id}')
        self.assertEqual(resp.status_code, 200)

    def test_admin_can_read_any_netmail_by_direct_id(self):
        msg_id = self._make_msg(to_name='bobnmtest', from_name='SomePeer',
                                subject='Admin direct read')
        client = self._client_as(self.admin_id)
        resp = client.get(f'/echomail/{self.area_id}/{msg_id}')
        self.assertEqual(resp.status_code, 200)

    # ---- read() area access gate (separate bug, same route) ----

    def test_read_enforces_sysop_only_area_gate(self):
        """The other half of the read() fix: every sibling route already
        called _check_area_access(); read() itself never did."""
        from anetbbs.models import db, EchoArea, EchomailMessage
        with self.app.app_context():
            restricted = EchoArea(network_id=self.net_id, tag='RESTRICTED_TEST',
                                  name='Restricted', is_active=True,
                                  is_subscribed=True, is_sysop_only=True)
            db.session.add(restricted)
            db.session.commit()
            msg = EchomailMessage(
                area_id=restricted.id, network_id=self.net_id,
                from_name='X', to_name='All', subject='Restricted content',
                body='x', direction='inbound')
            db.session.add(msg)
            db.session.commit()
            restricted_area_id = restricted.id
            msg_id = msg.id

        client = self._client_as(self.alice_id)
        resp = client.get(f'/echomail/{restricted_area_id}/{msg_id}')
        self.assertEqual(resp.status_code, 403,
                         'a regular user must not be able to read a '
                         'sysop-only area message by guessing its ID')


if __name__ == '__main__':
    unittest.main()
