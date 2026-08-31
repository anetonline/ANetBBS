"""Regression test for a real High-severity IDOR finding from a
security/performance audit (2026-08-31): web/saved.py's add() route
only ever access-checked kind='post' bookmarking (added in an earlier
audit pass, whose own comment explicitly said echomail/netmail/pm were
"not covered by this pass") -- so any logged-in user could bookmark
ANY echomail message (bypassing sysop-only/min-access-level area
gating) or ANY netmail message (another user's private point-to-point
mail) just by guessing/incrementing a target_id, and have its
subject+sender permanently displayed on their own /saved/ page even
though opening the real message would correctly 403.

Also covers a real correctness bug found alongside it: kind='pm'
resolved against the WRONG model (Message, i.e. bulletins, instead of
PrivateMessage, the actual private-message table) with no ownership
check either.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class SavedMessageIdorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.saved_idor_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import (db, User, EchomailNetwork, EchoArea,
                                    EchomailMessage, NetmailMessage,
                                    PrivateMessage)
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        with cls.app.app_context():
            db.create_all()

            lowlevel = User(username='savedidorlow', email='sil@example.com',
                            is_active=True, access_level=10)
            lowlevel.set_password('lowpassword123')
            other = User(username='savedidorother', email='sio@example.com',
                        is_active=True, access_level=10)
            other.set_password('otherpassword123')
            db.session.add_all([lowlevel, other])
            db.session.commit()
            cls.low_id = lowlevel.id
            cls.other_id = other.id

            net = EchomailNetwork(name='TestNet', network_type='binkp')
            db.session.add(net)
            db.session.commit()

            # Sysop-only echomail area, and a message inside it.
            sysop_area = EchoArea(network_id=net.id, tag='SYSOP.ONLY',
                                  name='Sysop Only', is_sysop_only=True,
                                  min_access_level=999)
            db.session.add(sysop_area)
            db.session.commit()
            echomsg = EchomailMessage(
                area_id=sysop_area.id, network_id=net.id,
                from_name='Sysop', to_name='All', subject='Secret Sysop Topic',
                body='confidential')
            db.session.add(echomsg)
            db.session.commit()
            cls.echomsg_id = echomsg.id

            # Private netmail addressed to `other`, not `lowlevel`.
            netmail = NetmailMessage(
                network_id=net.id, from_user_id=other.id, to_user_id=other.id,
                from_name='Other', to_name='Other', subject='Private business',
                body='sensitive')
            db.session.add(netmail)
            db.session.commit()
            cls.netmail_id = netmail.id

            # Private message between `other` and a third party, not `lowlevel`.
            pm = PrivateMessage(sender_id=other.id, recipient_id=other.id,
                                subject='Just between us', body='shh')
            db.session.add(pm)
            db.session.commit()
            cls.pm_id = pm.id

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

    def _saved_count_for(self, user_id, kind, target_id):
        from anetbbs.models import SavedMessage
        with self.app.app_context():
            return SavedMessage.query.filter_by(
                user_id=user_id, kind=kind, target_id=target_id).count()

    def test_low_access_user_cannot_bookmark_sysop_only_echomail(self):
        client = self._client_as(self.low_id)
        client.post('/saved/add', data={'kind': 'echomail',
                                        'target_id': self.echomsg_id})
        self.assertEqual(
            self._saved_count_for(self.low_id, 'echomail', self.echomsg_id), 0,
            'a below-access-level user must not be able to bookmark a '
            'sysop-only echomail message')

    def test_user_cannot_bookmark_someone_elses_netmail(self):
        client = self._client_as(self.low_id)
        client.post('/saved/add', data={'kind': 'netmail',
                                        'target_id': self.netmail_id})
        self.assertEqual(
            self._saved_count_for(self.low_id, 'netmail', self.netmail_id), 0,
            'a user must not be able to bookmark another user\'s private netmail')

    def test_user_cannot_bookmark_someone_elses_pm(self):
        client = self._client_as(self.low_id)
        client.post('/saved/add', data={'kind': 'pm',
                                        'target_id': self.pm_id})
        self.assertEqual(
            self._saved_count_for(self.low_id, 'pm', self.pm_id), 0,
            'a user must not be able to bookmark another user\'s private message')

    def test_owner_can_still_bookmark_their_own_netmail(self):
        client = self._client_as(self.other_id)
        client.post('/saved/add', data={'kind': 'netmail',
                                        'target_id': self.netmail_id})
        self.assertEqual(
            self._saved_count_for(self.other_id, 'netmail', self.netmail_id), 1,
            'the actual owner must still be able to bookmark their own netmail')

    def test_owner_can_still_bookmark_their_own_pm(self):
        client = self._client_as(self.other_id)
        client.post('/saved/add', data={'kind': 'pm', 'target_id': self.pm_id})
        self.assertEqual(
            self._saved_count_for(self.other_id, 'pm', self.pm_id), 1,
            'the actual owner must still be able to bookmark their own PM')

    def test_pm_resolves_against_the_real_privatemessage_table(self):
        """The wrong-model bug: kind='pm' used to resolve against
        Message (bulletins) instead of PrivateMessage."""
        from anetbbs.web.saved import _resolve
        from anetbbs.models import PrivateMessage
        with self.app.app_context():
            target = _resolve('pm', self.pm_id)
            self.assertIsInstance(target, PrivateMessage)
            self.assertEqual(target.subject, 'Just between us')


if __name__ == '__main__':
    unittest.main()
