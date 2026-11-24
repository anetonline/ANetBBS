"""H-2: netmail compose must not let a user pick an FTN origin address they
don't hold, or inject kludge lines.

Before the fix, web/netmail.py used the posted `from_aka` verbatim as the
message's from_address (and in MSGID/REPLYADDR/REPLYTO/INTL), and the
packet writer copied stored kludges to the wire unfiltered, so
`2:999/999\\r\\x01FAKEKLUDGE: x` produced a forged kludge line.
"""
import json
import os
import tempfile
import unittest

import anetbbs.config as cfg_mod


class NetmailOriginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp = tempfile.mkdtemp(prefix='h2-')
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp}/t.db'
        os.environ['FLASK_ENV'] = 'testing'
        from anetbbs.web_app import create_app
        from anetbbs.models import db, User, EchomailNetwork, UserAka
        cls.app = create_app('testing')
        cls.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        with cls.app.app_context():
            db.create_all()
            u = User(username='h2user', email='h2@example.test', password_hash='x',
                     access_level=10, is_admin=False, is_verified=True, is_active=True)
            adm = User(username='h2sysop', email='h2s@example.test', password_hash='x',
                       access_level=255, is_admin=True, is_verified=True, is_active=True)
            db.session.add_all([u, adm])
            net = EchomailNetwork(name='H2Net', network_type='binkp',
                                  our_address='1:100/1', hub_address='1:100/0',
                                  is_active=True)
            db.session.add(net)
            db.session.commit()
            db.session.add(UserAka(user_id=u.id, address='1:100/1.7'))
            db.session.commit()
            cls.uid, cls.aid = u.id, adm.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_uri

    def _client(self, uid):
        c = self.app.test_client()
        with self.app.app_context():
            from anetbbs.models import User
            ident = User.query.get(uid).get_id()
        with c.session_transaction() as s:
            s['_user_id'] = ident
            s['_fresh'] = True
        return c

    def _post(self, uid, **fields):
        data = {'to_address': '1:100/5', 'to_name': 'Bob', 'subject': 's', 'body': 'b'}
        data.update(fields)
        return self._client(uid).post('/netmail/compose', data=data)

    def _latest(self):
        from anetbbs.models import NetmailMessage
        with self.app.app_context():
            m = NetmailMessage.query.order_by(NetmailMessage.id.desc()).first()
            return (m.id, m.from_address, m.kludges) if m else (None, None, None)

    def test_unowned_from_aka_refused(self):
        before = self._latest()[0]
        r = self._post(self.uid, from_aka='2:999/999')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self._latest()[0], before, 'no row may be stored')

    def test_network_address_refused_for_non_admin(self):
        before = self._latest()[0]
        r = self._post(self.uid, from_aka='1:100/1')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self._latest()[0], before)

    def test_control_characters_refused(self):
        before = self._latest()[0]
        for field, value in (('from_aka', '1:100/1.7\r\x01FAKE: x'),
                             ('to_name', 'Bob\r\x01FAKE: x'),
                             ('subject', 'hi\x01FAKE')):
            with self.subTest(field=field):
                r = self._post(self.uid, **{field: value})
                self.assertIn(r.status_code, (200, 400))
                self.assertEqual(self._latest()[0], before)

    def test_own_aka_accepted(self):
        r = self._post(self.uid, from_aka='1:100/1.7')
        self.assertEqual(r.status_code, 302, r.get_data(as_text=True)[:300])
        _id, from_addr, _k = self._latest()
        self.assertEqual(from_addr, '1:100/1.7')

    def test_admin_may_use_network_address(self):
        r = self._post(self.aid, from_aka='1:100/1')
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self._latest()[1], '1:100/1')


class PacketWriterDropsForgedKludges(unittest.TestCase):
    def test_build_message_drops_multiline_kludges(self):
        from anetbbs.echomail.kludges import build_message
        out = build_message('body', kludges=[
            'MSGID: 1:1/1 1234',
            'REPLYADDR: 2:999/999\r\x01FAKEKLUDGE: injected',
            'X: a\nb', 'Y: \x01z', 'Z: \x00'])
        self.assertIn('\x01MSGID: 1:1/1 1234\r', out)
        self.assertNotIn('FAKEKLUDGE', out)
        for line in out.split('\r'):
            self.assertNotIn('\n', line)
            self.assertLessEqual(line.count('\x01'), 1)
            if '\x01' in line:
                self.assertTrue(line.startswith('\x01'), repr(line))

    def test_preexisting_bad_row_cannot_inject(self):
        """A message already stored with a forged kludge (e.g. before this
        fix) must not put a forged line into a built packet."""
        from anetbbs.echomail.kludges import build_message
        stored = json.dumps(['REPLYTO: 1:1/1\r\x01INJECTED: yes Bob'])
        from anetbbs.echomail.kludges import kludges_from_json
        out = build_message('b', kludges=kludges_from_json(stored))
        self.assertNotIn('INJECTED', out)


if __name__ == '__main__':
    unittest.main()
