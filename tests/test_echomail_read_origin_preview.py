"""Regression test for a real live source of confusion: a sysop tested
the v1.0b2.139 origin-line address fix by composing a new message and
viewing it in the web UI, saw no address in the "* Origin:" line, and
concluded the fix wasn't working. It was -- the web message-read page
was just showing the raw STORED origin_line, which never includes the
address (that gets appended separately, per network, at actual BinkP
send time in anetbbs/echomail/binkp.py's _build_ftn_packet). The
preview didn't match what would actually go out over the wire.

Fixed by having the read route compute the same address-append logic
_build_ftn_packet uses, for OUTBOUND messages only (inbound messages'
origin_line already came from the sender fully formed).
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_DATA_DIR = Path(__file__).resolve().parents[1] / 'data'


def _snapshot_data_dir():
    if not _DATA_DIR.is_dir():
        return set()
    return set(_DATA_DIR.iterdir())


class EchomailReadOriginPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._data_dir_before = _snapshot_data_dir()

        import anetbbs.config as cfg_mod
        cls._dbfile = str(Path(__file__).resolve().parent / '.echomail_read_origin_test.db')
        if os.path.exists(cls._dbfile):
            os.remove(cls._dbfile)
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._dbfile}'

        from anetbbs.web_app import create_app
        from anetbbs.models import (db, User, EchomailNetwork, EchoArea,
                                    EchomailMessage)

        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False

        with cls.app.app_context():
            db.create_all()
            admin = User(username='sysop', email='sysop@example.com', is_admin=True)
            admin.set_password('password123')
            db.session.add(admin)

            net = EchomailNetwork(name='TQWNet', network_type='binkp',
                                  our_address='1200:1/3', is_active=True)
            db.session.add(net)
            db.session.flush()

            area = EchoArea(network_id=net.id, tag='TESTAREA', name='Test Area',
                            is_active=True, is_subscribed=True)
            db.session.add(area)
            db.session.flush()

            cls.outbound_msg = EchomailMessage(
                area_id=area.id, network_id=net.id, from_name='sysop',
                to_name='All', subject='Happy Friday!!', body='TGIF',
                tear_line='--- ANetBBS',
                origin_line='ANetBBS - A Modern BBS System',
                direction='outbound')
            db.session.add(cls.outbound_msg)

            cls.inbound_msg = EchomailMessage(
                area_id=area.id, network_id=net.id, from_name='RemoteUser',
                to_name='All', subject='Hi', body='hello',
                origin_line='RemoteBBS (1:999/1)',
                direction='inbound')
            db.session.add(cls.inbound_msg)
            db.session.commit()
            cls.outbound_msg_id = cls.outbound_msg.id
            cls.inbound_msg_id = cls.inbound_msg.id
            cls.area_id = area.id

    @classmethod
    def tearDownClass(cls):
        import anetbbs.config as cfg_mod
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._dbfile + suffix
            if os.path.exists(path):
                os.remove(path)
        import shutil
        for entry in _snapshot_data_dir() - cls._data_dir_before:
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)

    def _logged_in_client(self):
        client = self.app.test_client()
        client.post('/auth/login', data={'username': 'sysop',
                                         'password': 'password123'})
        return client

    def test_outbound_message_preview_includes_the_network_address(self):
        client = self._logged_in_client()
        resp = client.get(f'/echomail/{self.area_id}/{self.outbound_msg_id}')
        body = resp.get_data(as_text=True)
        self.assertIn('ANetBBS - A Modern BBS System (1200:1/3)', body,
                      'the read page must preview the address that will '
                      'actually be sent, not the bare stored origin_line')

    def test_inbound_message_shows_its_own_origin_line_unmodified(self):
        client = self._logged_in_client()
        resp = client.get(f'/echomail/{self.area_id}/{self.inbound_msg_id}')
        body = resp.get_data(as_text=True)
        self.assertIn('RemoteBBS (1:999/1)', body)
        # Must not double-append -- the sender's own address is already there.
        self.assertNotIn('(1:999/1) (1200:1/3)', body)


if __name__ == '__main__':
    unittest.main()
