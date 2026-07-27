"""Regression tests for a full FTP server security audit
(anetbbs/ftp/server.py) -- found and fixed:

1. validate_authentication() only checked User.is_active, never
   is_locked or is_verified -- unlike every other login surface (web
   auth.py, terminal user_manager.py) -- so a locked-out or
   not-yet-approved (NUV) account could still fully authenticate and
   read/write every non-sysop file area over FTP.
2. Zero brute-force protection -- no AutoBanConfig/IpBan/rate-limit
   integration at all, unlike web and terminal logins. Fixed with the
   same models via a local _check_ip_and_rate_limit(), its own
   'ftp_login:<ip>' bucket.
3. Regular users got a flat 'elradfmw' permission over the whole
   session home dir, so upload_permission='none'/'sysop' areas (meant
   to be sysop-curated/read-only for regular users) could still be
   deleted from, renamed within, or have directories created/removed --
   on_file_received only ever gated STOR. Fixed with a
   _area_write_allowed() pre-check on DELE/RNFR/RNTO/MKD/RMD.
4. FTP uploads never went through the ClamAV scan the web upload
   routes all use -- on_file_received wrote straight to a FileUpload
   row with zero AV check. Fixed by calling scan_path() before
   recording the upload, same reject-and-log pattern as the
   upload_permission check right above it.

See also tests/test_network_join_qwk_packet_id_traversal.py for the
CRITICAL finding from the same audit (unsanitized packet_id from the
public network-join form flowing into this server's per-node home dir
path).
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class FtpSecurityAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.ftp_security_audit_test.db')
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

    def setUp(self):
        from anetbbs.features.rate_limit import _buckets
        _buckets.clear()

    def _make_authorizer(self, data_dir):
        from anetbbs.ftp.server import AnetbbsAuthorizer
        qwk_root = os.path.join(data_dir, 'qwk-hub')
        os.makedirs(qwk_root, exist_ok=True)
        return AnetbbsAuthorizer(
            self.app, anon_root=data_dir, user_root=data_dir,
            admin_root=data_dir, anon_enabled=False, qwk_root=qwk_root)

    def _make_handler(self, username, fs_root, qwk_node_id=None):
        from anetbbs.ftp.server import AnetbbsFTPHandler
        h = object.__new__(AnetbbsFTPHandler)
        h.app = self.app
        h.username = username
        h._qwk_node_id = qwk_node_id
        h.respond = MagicMock()
        h.fs = MagicMock(root=fs_root)
        return h

    # -- 1. is_locked / is_verified gates ---------------------------------

    def test_locked_user_login_rejected(self):
        from anetbbs.models import db, User
        from pyftpdlib.authorizers import AuthenticationFailed
        with self.app.app_context():
            u = User(username='ftplockeduser', email='fl@example.com',
                     is_active=True, is_locked=True)
            u.set_password('correct')
            db.session.add(u)
            db.session.commit()

        with tempfile.TemporaryDirectory() as data_dir:
            authorizer = self._make_authorizer(data_dir)
            handler = MagicMock()
            handler.remote_ip = '198.51.100.10'
            with self.app.app_context():
                with self.assertRaises(AuthenticationFailed):
                    authorizer.validate_authentication(
                        'ftplockeduser', 'correct', handler)

    def test_unverified_nonadmin_login_rejected(self):
        from anetbbs.models import db, User
        from pyftpdlib.authorizers import AuthenticationFailed
        with self.app.app_context():
            u = User(username='ftpunverifieduser', email='fu@example.com',
                     is_active=True, is_verified=False)
            u.set_password('correct')
            db.session.add(u)
            db.session.commit()

        with tempfile.TemporaryDirectory() as data_dir:
            authorizer = self._make_authorizer(data_dir)
            handler = MagicMock()
            handler.remote_ip = '198.51.100.11'
            with self.app.app_context():
                with self.assertRaises(AuthenticationFailed):
                    authorizer.validate_authentication(
                        'ftpunverifieduser', 'correct', handler)

    def test_unverified_admin_login_still_allowed(self):
        """Same admin bypass as web/auth.py's login()."""
        from anetbbs.models import db, User
        with self.app.app_context():
            u = User(username='ftpunverifiedadmin', email='fua@example.com',
                     is_active=True, is_verified=False, is_admin=True)
            u.set_password('correct')
            db.session.add(u)
            db.session.commit()

        with tempfile.TemporaryDirectory() as data_dir:
            authorizer = self._make_authorizer(data_dir)
            handler = MagicMock()
            handler.remote_ip = '198.51.100.12'
            with self.app.app_context():
                authorizer.validate_authentication(
                    'ftpunverifiedadmin', 'correct', handler)
                self.assertTrue(authorizer.has_user('ftpunverifiedadmin'))

    def test_active_verified_unlocked_user_login_still_works(self):
        """Baseline -- must not have broken the ordinary case."""
        from anetbbs.models import db, User
        with self.app.app_context():
            u = User(username='ftpnormaluser', email='fn@example.com',
                     is_active=True)
            u.set_password('correct')
            db.session.add(u)
            db.session.commit()

        with tempfile.TemporaryDirectory() as data_dir:
            authorizer = self._make_authorizer(data_dir)
            handler = MagicMock()
            handler.remote_ip = '198.51.100.13'
            with self.app.app_context():
                authorizer.validate_authentication(
                    'ftpnormaluser', 'correct', handler)
                self.assertTrue(authorizer.has_user('ftpnormaluser'))

    # -- 2. brute-force / rate-limit ---------------------------------------

    def test_rate_limit_blocks_after_threshold_and_bans_real_ip(self):
        from anetbbs.models import db, User, AutoBanConfig, IpBan
        from pyftpdlib.authorizers import AuthenticationFailed
        with self.app.app_context():
            cfg = AutoBanConfig.get()
            cfg.enabled = True
            cfg.attempt_limit = 2
            cfg.window_seconds = 300
            cfg.ban_duration_hours = 1
            db.session.commit()

            u = User(username='ftprluser', email='frl@example.com', is_active=True)
            u.set_password('correct')
            db.session.add(u)
            db.session.commit()

        with tempfile.TemporaryDirectory() as data_dir:
            authorizer = self._make_authorizer(data_dir)
            handler = MagicMock()
            handler.remote_ip = '203.0.113.201'

            with self.app.app_context():
                # First two attempts (even with correct credentials) consume
                # the rate-limit bucket, matching terminal's
                # _check_ip_and_rate_limit -- every attempt counts, not just
                # failures.
                authorizer.validate_authentication('ftprluser', 'correct', handler)
                authorizer.validate_authentication('ftprluser', 'correct', handler)
                with self.assertRaises(AuthenticationFailed):
                    authorizer.validate_authentication('ftprluser', 'correct', handler)

                ban = IpBan.query.filter_by(cidr='203.0.113.201').first()
                self.assertIsNotNone(ban, 'IP must be auto-banned after tripping the limit')

    def test_whitelisted_ip_bypasses_rate_limit(self):
        from anetbbs.models import db, User, AutoBanConfig, IpWhitelist
        with self.app.app_context():
            cfg = AutoBanConfig.get()
            cfg.enabled = True
            cfg.attempt_limit = 1
            cfg.window_seconds = 300
            db.session.commit()
            db.session.add(IpWhitelist(cidr='203.0.113.202'))
            db.session.commit()

            u = User(username='ftpwluser', email='fwl@example.com', is_active=True)
            u.set_password('correct')
            db.session.add(u)
            db.session.commit()

        with tempfile.TemporaryDirectory() as data_dir:
            authorizer = self._make_authorizer(data_dir)
            handler = MagicMock()
            handler.remote_ip = '203.0.113.202'
            with self.app.app_context():
                # attempt_limit=1 would normally block the 2nd attempt --
                # whitelisting must bypass that entirely.
                authorizer.validate_authentication('ftpwluser', 'correct', handler)
                authorizer.validate_authentication('ftpwluser', 'correct', handler)
                authorizer.validate_authentication('ftpwluser', 'correct', handler)

    # -- 3. area-level write enforcement (DELE/RNFR/RNTO/MKD/RMD) ---------

    def _make_area(self, tag, permission, fs_root):
        from anetbbs.models import db, FileArea
        storage = os.path.join(fs_root, tag)
        os.makedirs(storage, exist_ok=True)
        area = FileArea(tag=tag, name=f'Area {tag}', is_active=True,
                        storage_path=storage, upload_permission=permission,
                        min_access_level=0)
        db.session.add(area)
        db.session.commit()
        return area

    def test_regular_user_blocked_from_deleting_in_sysop_only_area(self):
        from anetbbs.models import db, User
        with tempfile.TemporaryDirectory() as fs_root:
            with self.app.app_context():
                self._make_area('SYSOPWRITE', 'sysop', fs_root)
                u = User(username='ftpwriteuser1', email='fw1@example.com',
                        is_active=True, is_admin=False)
                u.set_password('x')
                db.session.add(u)
                db.session.commit()

            handler = self._make_handler(
                'ftpwriteuser1', fs_root=os.path.join(fs_root))
            target = os.path.join(fs_root, 'SYSOPWRITE', 'somefile.txt')

            with patch('pyftpdlib.handlers.FTPHandler.ftp_DELE') as mock_super:
                handler.ftp_DELE(target)
            mock_super.assert_not_called()
            handler.respond.assert_called_once()
            self.assertIn('550', handler.respond.call_args[0][0])

    def test_regular_user_allowed_to_delete_in_users_permission_area(self):
        from anetbbs.models import db, User
        with tempfile.TemporaryDirectory() as fs_root:
            with self.app.app_context():
                self._make_area('USERWRITE', 'users', fs_root)
                u = User(username='ftpwriteuser2', email='fw2@example.com',
                        is_active=True, is_admin=False)
                u.set_password('x')
                db.session.add(u)
                db.session.commit()

            handler = self._make_handler('ftpwriteuser2', fs_root=fs_root)
            target = os.path.join(fs_root, 'USERWRITE', 'somefile.txt')

            with patch('pyftpdlib.handlers.FTPHandler.ftp_DELE',
                      return_value=target) as mock_super:
                result = handler.ftp_DELE(target)
            mock_super.assert_called_once_with(target)
            self.assertEqual(result, target)
            handler.respond.assert_not_called()

    def test_admin_bypasses_area_write_restriction(self):
        from anetbbs.models import db, User
        with tempfile.TemporaryDirectory() as fs_root:
            with self.app.app_context():
                self._make_area('SYSOPWRITE2', 'sysop', fs_root)
                u = User(username='ftpwriteadmin', email='fwa@example.com',
                        is_active=True, is_admin=True)
                u.set_password('x')
                db.session.add(u)
                db.session.commit()

            handler = self._make_handler('ftpwriteadmin', fs_root=fs_root)
            target = os.path.join(fs_root, 'SYSOPWRITE2', 'somefile.txt')

            with patch('pyftpdlib.handlers.FTPHandler.ftp_MKD',
                      return_value=target) as mock_super:
                handler.ftp_MKD(target)
            mock_super.assert_called_once_with(target)

    def test_qwk_node_session_exempt_from_area_write_check(self):
        """QWK sessions manage their own per-node hub dir outside any
        FileArea storage_path -- must never be blocked by this guard."""
        with tempfile.TemporaryDirectory() as fs_root:
            handler = self._make_handler(
                'SOMEQWKNODE', fs_root=fs_root, qwk_node_id=99)
            target = os.path.join(fs_root, 'ANET.REP')
            with patch('pyftpdlib.handlers.FTPHandler.ftp_DELE',
                      return_value=target) as mock_super:
                handler.ftp_DELE(target)
            mock_super.assert_called_once_with(target)

    def test_rnfr_rnto_and_rmd_also_enforced(self):
        from anetbbs.models import db, User
        with tempfile.TemporaryDirectory() as fs_root:
            with self.app.app_context():
                self._make_area('SYSOPWRITE3', 'none', fs_root)
                u = User(username='ftpwriteuser3', email='fw3@example.com',
                        is_active=True, is_admin=False)
                u.set_password('x')
                db.session.add(u)
                db.session.commit()

            handler = self._make_handler('ftpwriteuser3', fs_root=fs_root)
            target = os.path.join(fs_root, 'SYSOPWRITE3', 'x.txt')

            for method_name in ('ftp_RNFR', 'ftp_RNTO', 'ftp_RMD'):
                handler.respond.reset_mock()
                with patch(f'pyftpdlib.handlers.FTPHandler.{method_name}') as mock_super:
                    getattr(handler, method_name)(target)
                mock_super.assert_not_called()
                handler.respond.assert_called_once()
                self.assertIn('550', handler.respond.call_args[0][0])

    # -- 4. ClamAV scan on FTP upload --------------------------------------

    def _make_upload_file(self, fs_root, area_tag, name, content=b'hello'):
        area_dir = os.path.join(fs_root, area_tag)
        os.makedirs(area_dir, exist_ok=True)
        path = os.path.join(area_dir, name)
        with open(path, 'wb') as f:
            f.write(content)
        return path

    def test_infected_upload_removed_and_never_recorded(self):
        from anetbbs.models import db, User, FileArea, FileUpload
        from anetbbs.features.virus_scan import ScanResult
        with tempfile.TemporaryDirectory() as fs_root:
            with self.app.app_context():
                self._make_area('AVSCAN1', 'users', fs_root)
                u = User(username='ftpavuser1', email='fav1@example.com',
                        is_active=True)
                u.set_password('x')
                db.session.add(u)
                db.session.commit()

            handler = self._make_handler('ftpavuser1', fs_root=fs_root)
            fpath = self._make_upload_file(fs_root, 'AVSCAN1', 'evil.exe')

            fake_result = ScanResult(infected=True, signature='Test.EICAR',
                                     message='infected', scanner_available=True)
            with patch('anetbbs.features.virus_scan.scan_path',
                      return_value=fake_result):
                handler.on_file_received(fpath)

            self.assertFalse(os.path.exists(fpath),
                             'infected file must be removed from disk')
            with self.app.app_context():
                self.assertIsNone(
                    FileUpload.query.filter_by(filename='evil.exe').first(),
                    'infected upload must never get a FileUpload row')

    def test_clean_upload_still_recorded(self):
        """Baseline -- scan_path() reporting clean must not block the
        existing, working upload-tracking behavior."""
        from anetbbs.models import db, User, FileUpload
        from anetbbs.features.virus_scan import ScanResult
        with tempfile.TemporaryDirectory() as fs_root:
            with self.app.app_context():
                self._make_area('AVSCAN2', 'users', fs_root)
                u = User(username='ftpavuser2', email='fav2@example.com',
                        is_active=True)
                u.set_password('x')
                db.session.add(u)
                db.session.commit()

            handler = self._make_handler('ftpavuser2', fs_root=fs_root)
            fpath = self._make_upload_file(fs_root, 'AVSCAN2', 'clean.zip')

            fake_result = ScanResult(infected=False, signature='', message='clean',
                                     scanner_available=True)
            with patch('anetbbs.features.virus_scan.scan_path',
                      return_value=fake_result):
                handler.on_file_received(fpath)

            self.assertTrue(os.path.exists(fpath))
            with self.app.app_context():
                self.assertIsNotNone(
                    FileUpload.query.filter_by(filename='clean.zip').first())

    def test_scan_crash_fails_open(self):
        """Same fail-open posture as the web upload routes: a broken
        scanner must never block a legitimate upload."""
        from anetbbs.models import db, User, FileUpload
        with tempfile.TemporaryDirectory() as fs_root:
            with self.app.app_context():
                self._make_area('AVSCAN3', 'users', fs_root)
                u = User(username='ftpavuser3', email='fav3@example.com',
                        is_active=True)
                u.set_password('x')
                db.session.add(u)
                db.session.commit()

            handler = self._make_handler('ftpavuser3', fs_root=fs_root)
            fpath = self._make_upload_file(fs_root, 'AVSCAN3', 'ok.zip')

            with patch('anetbbs.features.virus_scan.scan_path',
                      side_effect=RuntimeError('clamscan exploded')):
                handler.on_file_received(fpath)

            self.assertTrue(os.path.exists(fpath))
            with self.app.app_context():
                self.assertIsNotNone(
                    FileUpload.query.filter_by(filename='ok.zip').first())

    # -- QWK node password constant-time compare ---------------------------

    def test_qwk_node_correct_password_still_authenticates(self):
        from anetbbs.models import db, QWKNode
        with self.app.app_context():
            db.session.add(QWKNode(packet_id='HMACTEST', name='HMAC Test',
                                   password='s3cret', is_active=True))
            db.session.commit()

        with tempfile.TemporaryDirectory() as data_dir:
            authorizer = self._make_authorizer(data_dir)
            handler = MagicMock()
            handler.remote_ip = '198.51.100.20'
            with self.app.app_context():
                authorizer.validate_authentication('HMACTEST', 's3cret', handler)
                self.assertTrue(authorizer.has_user('HMACTEST'))

    def test_qwk_node_wrong_password_rejected(self):
        from anetbbs.models import db, QWKNode
        from pyftpdlib.authorizers import AuthenticationFailed
        with self.app.app_context():
            db.session.add(QWKNode(packet_id='HMACTEST2', name='HMAC Test 2',
                                   password='s3cret', is_active=True))
            db.session.commit()

        with tempfile.TemporaryDirectory() as data_dir:
            authorizer = self._make_authorizer(data_dir)
            handler = MagicMock()
            handler.remote_ip = '198.51.100.21'
            with self.app.app_context():
                with self.assertRaises(AuthenticationFailed):
                    authorizer.validate_authentication('HMACTEST2', 'wrong', handler)


if __name__ == '__main__':
    unittest.main()
