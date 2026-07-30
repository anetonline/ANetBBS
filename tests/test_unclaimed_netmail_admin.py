"""Regression tests for a real gap found in a full echomail-subsystem
audit: inbound netmail whose to_name/to_address doesn't resolve to a
local user (routing.py's resolve_netmail_recipient()) is stored with
to_user_id=NULL, but every read path (web/netmail.py's inbox/sent/
drafts) filters by the logged-in user's own identity -- there was no
admin-facing view of these at all, unlike the analogous BadAreaLog
mechanism for unrecognized echomail areas. Fixed with a new
/admin/echomail/unclaimed_netmail review queue + manual assign action.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class UnclaimedNetmailAdminTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.unclaimed_netmail_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User, EchomailNetwork, NetmailMessage
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        with cls.app.app_context():
            db.create_all()

            admin = User(username='netmailadmin', email='nma@example.com',
                        password_hash='x', is_admin=True, access_level=100)
            target = User(username='realuser', email='ru@example.com',
                          password_hash='x', access_level=100)
            db.session.add_all([admin, target])
            db.session.commit()
            cls.admin_id = admin.id
            cls.target_user_id = target.id

            net = EchomailNetwork(name='UnclaimedNetmailNet', network_type='binkp',
                                  our_address='5:5/1')
            db.session.add(net)
            db.session.commit()
            cls.net_id = net.id

            bot_traffic = NetmailMessage(
                network_id=net.id, from_address='5:5/2', to_address='5:5/1',
                from_name='SomePeer', to_name='areafix', subject='areafix',
                body='+SOMETAG\n', direction='inbound', status='received',
                to_user_id=None)
            db.session.add(bot_traffic)

            claimed = NetmailMessage(
                network_id=net.id, from_address='5:5/2', to_address='5:5/1',
                from_name='SomePeer', to_name='realuser', subject='Already claimed',
                body='x', direction='inbound', status='received',
                to_user_id=target.id)
            db.session.add(claimed)

            db.session.commit()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _client_as_admin(self):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True
        return client

    def _make_unclaimed(self, subject):
        from anetbbs.models import db, NetmailMessage
        with self.app.app_context():
            unclaimed = NetmailMessage(
                network_id=self.net_id, from_address='5:5/2', to_address='5:5/1',
                from_name='SomePeer', to_name='TypoName', subject=subject,
                body='x', direction='inbound', status='received',
                to_user_id=None)
            db.session.add(unclaimed)
            db.session.commit()
            return unclaimed.id

    def test_unclaimed_netmail_page_lists_unresolved_but_not_bot_or_claimed(self):
        self._make_unclaimed('Hello there')
        client = self._client_as_admin()
        resp = client.get('/admin/echomail/unclaimed_netmail')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn('Hello there', body)
        self.assertNotIn('+SOMETAG', body,
                         'AreaFix bot traffic must not clutter this queue -- '
                         'it is already tracked via AreafixLog')
        self.assertNotIn('Already claimed', body,
                         'a netmail with to_user_id already set is not unclaimed')

    def test_assign_sets_to_user_id_and_removes_from_queue(self):
        from anetbbs.models import NetmailMessage
        unclaimed_id = self._make_unclaimed('Assign me')
        client = self._client_as_admin()
        resp = client.post(
            f'/admin/echomail/unclaimed_netmail/{unclaimed_id}/assign',
            data={'user_id': str(self.target_user_id)},
            follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            nm = NetmailMessage.query.get(unclaimed_id)
            self.assertEqual(nm.to_user_id, self.target_user_id)

        resp2 = client.get('/admin/echomail/unclaimed_netmail')
        self.assertNotIn('Assign me', resp2.get_data(as_text=True))

    def test_assign_creates_a_notification_for_the_target_user(self):
        from anetbbs.models import Notification
        unclaimed_id = self._make_unclaimed('Notify me')
        client = self._client_as_admin()
        client.post(
            f'/admin/echomail/unclaimed_netmail/{unclaimed_id}/assign',
            data={'user_id': str(self.target_user_id)})

        with self.app.app_context():
            notif = Notification.query.filter_by(
                user_id=self.target_user_id, kind='netmail').first()
            self.assertIsNotNone(notif,
                                 'assigning unclaimed netmail must notify the '
                                 'recipient the same way automatic resolution does')

    def test_areafix_response_from_bot_addressed_to_sysop_is_excluded(self):
        """Real gap found live: a peer's AreaFix robot replying FROM
        'AREAFIX' with an automated 'AREAFIX response' confirmation,
        generically addressed TO 'Sysop' (not a real local username),
        used to slip through the old to_name-only bot filter and pile
        up unbounded (50+ found on a real install). Must be excluded
        by checking from_name too, the same as the to_name check
        already does for the reverse direction."""
        from anetbbs.models import db, NetmailMessage
        with self.app.app_context():
            bot_reply = NetmailMessage(
                network_id=self.net_id, from_address='5:5/2', to_address='5:5/1',
                from_name='AREAFIX', to_name='Sysop', subject='AREAFIX response',
                body='+SOMETAG', direction='inbound', status='received',
                to_user_id=None)
            db.session.add(bot_reply)
            db.session.commit()

        client = self._client_as_admin()
        resp = client.get('/admin/echomail/unclaimed_netmail')
        self.assertNotIn('AREAFIX response', resp.get_data(as_text=True),
                         'an AreaFix bot reply must not clutter this queue '
                         'just because it is addressed to a generic name')

    def test_clear_all_deletes_only_unclaimed_rows(self):
        from anetbbs.models import NetmailMessage
        id1 = self._make_unclaimed('Clear me 1')
        id2 = self._make_unclaimed('Clear me 2')
        client = self._client_as_admin()
        resp = client.post('/admin/echomail/unclaimed_netmail/clear_all',
                           follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            self.assertIsNone(NetmailMessage.query.get(id1))
            self.assertIsNone(NetmailMessage.query.get(id2))
            # bot traffic and the already-claimed message from setUpClass
            # must survive -- clear_all only touches what the queue shows.
            claimed_still_there = NetmailMessage.query.filter_by(
                subject='Already claimed').first()
            self.assertIsNotNone(claimed_still_there)

    def test_clear_all_requires_admin(self):
        from anetbbs.models import db, User
        with self.app.app_context():
            plain = User(username='notanadmin', email='na@example.com',
                        password_hash='x', access_level=100)
            db.session.add(plain)
            db.session.commit()
            plain_id = plain.id

        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(plain_id)
            sess['_fresh'] = True
        resp = client.post('/admin/echomail/unclaimed_netmail/clear_all')
        self.assertNotEqual(resp.status_code, 200)


if __name__ == '__main__':
    unittest.main()
