"""M-2: the FTP username is an identity, never a SQL LIKE pattern.

Before the fix, login used User.username.ilike(<wire username>), so
'%ve' with eve's password logged in. Every later per-command check
re-resolved the account with ilike(self.username) and no is_active filter,
and failed open -- the same session could resolve to an inactive admin and
write to sysop-only areas. Unknown usernames also skipped password hashing,
and the session recheck ignored lock/deactivation.
"""
import os
import tempfile
import unittest
from unittest import mock

import anetbbs.config as cfg_mod


class FtpIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp = tempfile.mkdtemp(prefix='m2-')
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp}/t.db'
        os.environ['FLASK_ENV'] = 'testing'
        from anetbbs.web_app import create_app
        from anetbbs.models import db, User, FileArea, QWKNode
        cls.app = create_app('testing')
        with cls.app.app_context():
            db.create_all()
            steve = User(username='steve', email='s@x.invalid', is_active=False,
                         is_admin=True, is_verified=True)
            steve.set_password('unknown-to-attacker')
            eve = User(username='eve', email='e@x.invalid', is_active=True,
                       is_verified=True)
            eve.set_password('evepass')
            db.session.add_all([steve, eve])
            db.session.add(FileArea(tag='SYSOPONLY', name='Sysop only',
                                    storage_path=cls._tmp, upload_permission='sysop'))
            db.session.add(QWKNode(packet_id='GATE', name='Gate', password='qwkpw',
                                   is_active=True))
            db.session.commit()
            cls.eve_id = eve.id
        d = tempfile.mkdtemp(prefix='m2-ftp-')
        os.makedirs(os.path.join(d, 'qwk-hub'))
        from anetbbs.ftp.server import AnetbbsAuthorizer
        cls.auth = AnetbbsAuthorizer(cls.app, anon_root=d, user_root=d, admin_root=d,
                                     anon_enabled=False, qwk_root=os.path.join(d, 'qwk-hub'))

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_uri

    def _login(self, username, password):
        from pyftpdlib.authorizers import AuthenticationFailed
        h = mock.MagicMock()
        h.remote_ip = None
        with self.app.app_context():
            try:
                self.auth.validate_authentication(username, password, h)
                return h
            except AuthenticationFailed:
                return None

    def test_wildcard_usernames_refused(self):
        for name in ('%ve', '_ve', 'e%', '%', 'ev_'):
            with self.subTest(name=name):
                self.assertIsNone(self._login(name, 'evepass'))

    def test_wildcard_qwk_packet_id_refused(self):
        self.assertIsNone(self._login('G%', 'qwkpw'))
        self.assertIsNotNone(self._login('gate', 'qwkpw'))

    def test_exact_case_insensitive_login_still_works(self):
        h = self._login('EVE', 'evepass')
        self.assertIsNotNone(h)
        self.assertEqual(h._bbs_user_id, self.eve_id)

    def test_unknown_user_still_costs_a_hash(self):
        with mock.patch('werkzeug.security.check_password_hash',
                        wraps=__import__('werkzeug.security', fromlist=['x']).check_password_hash) as m:
            self.assertIsNone(self._login('nobody-here', 'x'))
        self.assertGreaterEqual(m.call_count, 1)

    def _handler(self, user_id, username='whatever'):
        from anetbbs.ftp.server import AnetbbsFTPHandler
        h = AnetbbsFTPHandler.__new__(AnetbbsFTPHandler)
        h.app = self.app
        h.username = username
        h._qwk_node_id = None
        h._bbs_user_id = user_id
        h._parent_area_tag = lambda p: 'SYSOPONLY'
        return h

    def test_permission_checks_resolve_by_session_id_not_wire_name(self):
        # Wire name chosen to LIKE-match the inactive admin: must not matter.
        h = self._handler(self.eve_id, username='%ve')
        self.assertFalse(h._area_write_allowed('/SYSOPONLY/x.zip'))

    def test_unbound_session_fails_closed(self):
        h = self._handler(None)
        self.assertFalse(h._area_write_allowed('/SYSOPONLY/x.zip'))

    def test_recheck_drops_locked_or_deactivated_user(self):
        from anetbbs.models import db, User
        for attr, bad in (('is_locked', True), ('is_active', False), ('is_verified', False)):
            with self.subTest(attr=attr):
                with self.app.app_context():
                    u = db.session.get(User, self.eve_id)
                    good = getattr(u, attr)
                    version = u.auth_version
                h = self._handler(self.eve_id)
                h._closed = False
                h._bbs_auth = (self.eve_id, version)
                h._bbs_transfer_auth = None
                h._auth_recheck = None
                h.data_channel = None
                closed = []
                h.close = lambda: closed.append(True)
                self.assertTrue(h._recheck_auth())
                with self.app.app_context():
                    u = db.session.get(User, self.eve_id)
                    setattr(u, attr, bad)
                    db.session.commit()
                try:
                    self.assertFalse(h._recheck_auth())
                    self.assertTrue(closed)
                finally:
                    with self.app.app_context():
                        u = db.session.get(User, self.eve_id)
                        setattr(u, attr, good)
                        db.session.commit()


class IdentityLookupsAreExact(unittest.TestCase):
    def test_no_ilike_on_wire_or_typed_usernames(self):
        from pathlib import Path
        repo = Path(__file__).resolve().parent.parent
        for rel in ('anetbbs/ftp/server.py', 'anetbbs/web/blocks.py',
                    'anetbbs/web/groups.py', 'anetbbs/features/notify.py'):
            text = (repo / rel).read_text()
            with self.subTest(rel=rel):
                self.assertNotIn('username.ilike(', text)
                self.assertNotIn('packet_id.ilike(', text)


if __name__ == '__main__':
    unittest.main()
