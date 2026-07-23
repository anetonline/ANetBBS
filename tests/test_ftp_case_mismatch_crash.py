"""Regression test for a real live crash: an FTP login crashed the
whole session with an unhandled KeyError.

    File ".../pyftpdlib/handlers/ftp/control.py", line 1832, in ftp_PASS
        home = self.authorizer.get_home_dir(self.username)
    File ".../anetbbs/ftp/server.py", line 215, in get_home_dir
        return super().get_home_dir(username)
    File ".../pyftpdlib/authorizers.py", line 165, in get_home_dir
        return self.user_table[username]["home"]
    KeyError: 'gatekeeper'

Root cause: pyftpdlib's ftp_USER stores the client's raw wire username
verbatim (`self.username = line`, confirmed via pyftpdlib source --
no case normalization at all), but validate_authentication() used to
register the DummyAuthorizer's user_table entry under a DIFFERENT
case -- User.username (the DB's canonical stored case) for BBS users,
or QWKNode.packet_id.upper() for QWK nodes. The DB lookups themselves
are case-insensitive (ilike), so a client typing "gatekeeper" against
a stored username/packet_id of "GateKeeper"/"GATEKEEPER" authenticated
successfully -- but the later get_home_dir(self.username) lookup is a
plain case-SENSITIVE dict lookup keyed by the raw wire username, which
didn't match what was registered. Fix: always register under the
exact `username` parameter (what the client actually typed), never a
normalized form, so both lookups always agree.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class FtpCaseMismatchCrashTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.ftp_case_mismatch_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _make_authorizer(self, data_dir):
        from anetbbs.ftp.server import AnetbbsAuthorizer
        qwk_root = os.path.join(data_dir, 'qwk-hub')
        os.makedirs(qwk_root, exist_ok=True)
        return AnetbbsAuthorizer(
            self.app, anon_root=data_dir, user_root=data_dir,
            admin_root=data_dir, anon_enabled=False, qwk_root=qwk_root)

    def test_qwk_node_login_with_lowercase_username_does_not_crash(self):
        """The exact reported scenario: a QWK node registered with an
        uppercase packet_id, client logs in typing it lowercase."""
        import tempfile
        from anetbbs.models import db, QWKNode

        with tempfile.TemporaryDirectory() as data_dir:
            with self.app.app_context():
                node = QWKNode(packet_id='GATEKEEPER', name='GateKeeper',
                              password='hunter2', is_active=True)
                db.session.add(node)
                db.session.commit()

            authorizer = self._make_authorizer(data_dir)
            handler = MagicMock()

            with self.app.app_context():
                # Client typed lowercase -- exactly what the live log showed.
                authorizer.validate_authentication('gatekeeper', 'hunter2', handler)

                # This is the exact call that crashed live -- must not raise.
                home = authorizer.get_home_dir('gatekeeper')
                self.assertTrue(home)

    def test_bbs_user_login_with_different_case_does_not_crash(self):
        """Same class of bug, BBS-user branch: DB stores one case, client
        types another."""
        import tempfile
        from anetbbs.models import db, User

        with tempfile.TemporaryDirectory() as data_dir:
            with self.app.app_context():
                u = User(username='StingRay', email='sr@example.com',
                         is_active=True)
                u.set_password('correcthorse')
                db.session.add(u)
                db.session.commit()

            authorizer = self._make_authorizer(data_dir)
            handler = MagicMock()

            with self.app.app_context():
                # Client typed all-lowercase against a mixed-case DB username.
                authorizer.validate_authentication('stingray', 'correcthorse', handler)
                home = authorizer.get_home_dir('stingray')
                self.assertTrue(home)

    def test_mixed_case_qwk_login_registers_under_the_raw_wire_username(self):
        """Directly asserts the fix's mechanism: the registered key must
        equal what the client typed, not a normalized form."""
        import tempfile
        from anetbbs.models import db, QWKNode

        with tempfile.TemporaryDirectory() as data_dir:
            with self.app.app_context():
                node = QWKNode(packet_id='MixedCase', name='Mixed',
                              password='x', is_active=True)
                db.session.add(node)
                db.session.commit()

            authorizer = self._make_authorizer(data_dir)
            handler = MagicMock()

            with self.app.app_context():
                authorizer.validate_authentication('MIXEDCASE', 'x', handler)
                self.assertTrue(authorizer.has_user('MIXEDCASE'))
                self.assertFalse(authorizer.has_user('mixedcase'))


if __name__ == '__main__':
    unittest.main()
