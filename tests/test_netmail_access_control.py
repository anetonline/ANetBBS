"""Regression tests for a real, severe access-control bug reported live:
a regular user could see -- and DELETE -- netmail addressed to the
sysop (or to the AreaFix/FileFix bots) in their own web netmail
inbox/sent view, purely because they were logged in, with zero
connection to the message.

Root cause: anetbbs/web/netmail.py's _user_addresses() added every
active EchomailNetwork's own `our_address` to EVERY user's list of
"addresses that belong to them" unconditionally -- not just admins.
Since _user_owns() (gating read/delete/reply) and the inbox()/sent()
list queries all call this function, any netmail addressed to (or
from) the BBS's own raw hub address -- exactly what an unresolved
"to the sysop" netmail, or any AreaFix/FileFix bot netmail, looks like
-- matched for literally every logged-in user.

Fixed by only including the network's own our_address for admins.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class NetmailAccessControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.netmail_access_control_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User, EchomailNetwork
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        with cls.app.app_context():
            db.create_all()

            admin = User(username='sysopadmintest', email='sat@example.com',
                        password_hash='x', is_admin=True, access_level=100)
            regular = User(username='regularusertest', email='rut2@example.com',
                          password_hash='x', is_admin=False, access_level=100)
            db.session.add_all([admin, regular])
            db.session.commit()
            cls.admin_id = admin.id
            cls.regular_id = regular.id

            net = EchomailNetwork(name='NetmailAccessNet', network_type='binkp',
                                  our_address='6:6/1', is_active=True)
            db.session.add(net)
            db.session.commit()
            cls.net_id = net.id
            cls.our_address = net.our_address

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

    def _make_unresolved_netmail(self, subject):
        """Netmail addressed to the bare hub address, no to_user_id --
        the exact shape of the live-reported bug: a new/unrecognized
        node's netmail to "the sysop" that never resolved to a real
        local user via resolve_netmail_recipient()."""
        from anetbbs.models import db, NetmailMessage
        with self.app.app_context():
            nm = NetmailMessage(
                network_id=self.net_id, from_address='6:6/2', from_name='NewNode',
                to_address=self.our_address, to_name='Sysop', subject=subject,
                body='x', direction='inbound', status='received',
                to_user_id=None)
            db.session.add(nm)
            db.session.commit()
            return nm.id

    def _make_areafix_bot_reply(self, subject):
        """Outbound AreaFix reply netmail -- from_address is the
        network's own our_address, same class of leak the user also
        reported ("I am rx the areafix messages")."""
        from anetbbs.models import db, NetmailMessage
        with self.app.app_context():
            nm = NetmailMessage(
                network_id=self.net_id, from_address=self.our_address,
                from_name='Areafix', to_address='6:6/2', to_name='NewNode',
                subject=subject, body='x', direction='outbound', status='queued')
            db.session.add(nm)
            db.session.commit()
            return nm.id

    # ---- inbox() list view ----

    def test_regular_user_inbox_does_not_show_sysop_catch_all_netmail(self):
        self._make_unresolved_netmail('Sysop-only inbox leak test')
        client = self._client_as(self.regular_id)
        resp = client.get('/netmail/')
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b'Sysop-only inbox leak test', resp.data)

    def test_admin_inbox_still_shows_sysop_catch_all_netmail(self):
        """Sanity check: the fix must not break the legitimate case --
        an admin should still see netmail addressed to the bare hub
        address (that's the whole point of the catch-all)."""
        self._make_unresolved_netmail('Admin should still see this')
        client = self._client_as(self.admin_id)
        resp = client.get('/netmail/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Admin should still see this', resp.data)

    # ---- sent() list view (AreaFix/FileFix bot traffic) ----

    def test_regular_user_sent_does_not_show_areafix_bot_traffic(self):
        self._make_areafix_bot_reply('AreaFix bot leak test')
        client = self._client_as(self.regular_id)
        resp = client.get('/netmail/sent')
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b'AreaFix bot leak test', resp.data)

    def test_admin_sent_hides_areafix_bot_traffic(self):
        """Real live complaint (2026-07-24): every AreaFix/FileFix command
        a downstream node sent landed in the sysop's personal Netmail
        Inbox/Sent as if it were 1-on-1 human mail, purely because the
        admin catch-all address matches the hub's own bare address that
        robot netmail is addressed to/from. This got far more visible
        after the v1.0b2.201 dedup-exemption fix stopped silently
        dropping repeat AreaFix commands. AreafixLog already gives
        admins a proper dedicated view of every request+response, so
        robot netmail is now excluded from the personal inbox/sent
        views entirely (previously this test asserted the opposite --
        superseded by direct sysop feedback)."""
        self._make_areafix_bot_reply('Admin should not see bot traffic here')
        client = self._client_as(self.admin_id)
        resp = client.get('/netmail/sent')
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b'Admin should not see bot traffic here', resp.data)

    def test_admin_inbox_hides_areafix_bot_request(self):
        """Same fix, inbound direction: a downstream node's inbound
        AreaFix/FileFix command (to_name='AreaFix') must not clutter
        the personal inbox either, even for the admin catch-all."""
        from anetbbs.models import db, NetmailMessage
        with self.app.app_context():
            nm = NetmailMessage(
                network_id=self.net_id, from_address='6:6/2', from_name='Craig',
                to_address=self.our_address, to_name='AreaFix',
                subject='Admin should not see this areafix request',
                body='+ALL\n', direction='inbound', status='received',
                to_user_id=None)
            db.session.add(nm)
            db.session.commit()

        client = self._client_as(self.admin_id)
        resp = client.get('/netmail/')
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b'Admin should not see this areafix request', resp.data)

    def test_regular_user_inbox_hides_filefix_bot_request_too(self):
        from anetbbs.models import db, NetmailMessage
        with self.app.app_context():
            nm = NetmailMessage(
                network_id=self.net_id, from_address='6:6/2', from_name='Craig',
                to_address='', to_name='FileFix',
                subject='Filefix request not shown',
                body='+ALL\n', direction='inbound', status='received',
                to_user_id=self.regular_id)
            db.session.add(nm)
            db.session.commit()

        client = self._client_as(self.regular_id)
        resp = client.get('/netmail/')
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b'Filefix request not shown', resp.data,
                         'robot netmail must stay hidden from the personal '
                         'inbox even when explicitly linked via to_user_id')

    # ---- read() direct access ----

    def test_regular_user_cannot_read_sysop_catch_all_netmail_by_direct_id(self):
        nm_id = self._make_unresolved_netmail('Direct read attempt')
        client = self._client_as(self.regular_id)
        resp = client.get(f'/netmail/{nm_id}')
        self.assertEqual(resp.status_code, 403,
                         'a regular user must not be able to read a netmail '
                         'they have no connection to just by guessing/knowing its id')

    def test_admin_can_read_sysop_catch_all_netmail_by_direct_id(self):
        nm_id = self._make_unresolved_netmail('Admin direct read')
        client = self._client_as(self.admin_id)
        resp = client.get(f'/netmail/{nm_id}')
        self.assertEqual(resp.status_code, 200)

    # ---- delete() -- the escalation the sysop specifically flagged ----

    def test_regular_user_cannot_delete_sysop_catch_all_netmail(self):
        from anetbbs.models import NetmailMessage
        nm_id = self._make_unresolved_netmail('Delete attempt by regular user')
        client = self._client_as(self.regular_id)
        resp = client.post(f'/netmail/{nm_id}/delete', follow_redirects=True)
        self.assertEqual(resp.status_code, 403)

        with self.app.app_context():
            nm = NetmailMessage.query.get(nm_id)
            self.assertFalse(nm.deleted_by_recipient,
                             'a regular user deleting a netmail they have no '
                             'connection to must not actually mark it deleted')

    def test_admin_can_still_delete_sysop_catch_all_netmail(self):
        from anetbbs.models import NetmailMessage
        nm_id = self._make_unresolved_netmail('Admin delete test')
        client = self._client_as(self.admin_id)
        client.post(f'/netmail/{nm_id}/delete', follow_redirects=True)

        with self.app.app_context():
            nm = NetmailMessage.query.get(nm_id)
            self.assertTrue(nm.deleted_by_recipient)

    # ---- legitimate matches must still work ----

    def test_regular_user_still_sees_netmail_addressed_to_their_own_username(self):
        from anetbbs.models import db, NetmailMessage
        with self.app.app_context():
            nm = NetmailMessage(
                network_id=self.net_id, from_address='6:6/2', from_name='Someone',
                to_address='', to_name='regularusertest', subject='For me specifically',
                body='x', direction='inbound', status='received', to_user_id=None)
            db.session.add(nm)
            db.session.commit()

        client = self._client_as(self.regular_id)
        resp = client.get('/netmail/')
        self.assertIn(b'For me specifically', resp.data)

    def test_regular_user_still_sees_netmail_via_own_aka(self):
        from anetbbs.models import db, NetmailMessage, UserAka
        with self.app.app_context():
            db.session.add(UserAka(user_id=self.regular_id, address='6:6/99'))
            nm = NetmailMessage(
                network_id=self.net_id, from_address='6:6/2', from_name='Someone',
                to_address='6:6/99', to_name='RegularUserAka', subject='Via my own AKA',
                body='x', direction='inbound', status='received', to_user_id=None)
            db.session.add(nm)
            db.session.commit()

        client = self._client_as(self.regular_id)
        resp = client.get('/netmail/')
        self.assertIn(b'Via my own AKA', resp.data)

    def test_regular_user_still_sees_netmail_via_to_user_id_link(self):
        from anetbbs.models import db, NetmailMessage
        with self.app.app_context():
            nm = NetmailMessage(
                network_id=self.net_id, from_address='6:6/2', from_name='Someone',
                to_address=self.our_address, to_name='Whatever', subject='Linked to me directly',
                body='x', direction='inbound', status='received',
                to_user_id=self.regular_id)
            db.session.add(nm)
            db.session.commit()

        client = self._client_as(self.regular_id)
        resp = client.get('/netmail/')
        self.assertIn(b'Linked to me directly', resp.data,
                      'an explicit to_user_id link must still work even '
                      'though the to_address happens to be the bare hub '
                      'address -- this is the CORRECT, resolved case')


if __name__ == '__main__':
    unittest.main()
