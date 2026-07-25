"""Regression tests for the daily file download quota (FR: Firehawke,
2026-07-24) applying to FTP RETR, not just web/terminal. See
anetbbs/features/file_quota.py for the tier-resolution rule.

AnetbbsFTPHandler is an asyncore-based pyftpdlib handler that normally
needs a real connected socket to construct -- these tests bypass
__init__ (object.__new__) and set only the attributes ftp_RETR()/
on_file_sent() actually read, patching the pyftpdlib superclass's
ftp_RETR so no real file transfer machinery runs.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class FtpFileQuotaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.ftp_quota_test.db')
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

    def _make_handler(self, username, qwk_node_id=None):
        from anetbbs.ftp.server import AnetbbsFTPHandler
        h = object.__new__(AnetbbsFTPHandler)
        h.app = self.app
        h.username = username
        h._qwk_node_id = qwk_node_id
        h.respond = MagicMock()
        return h

    def _make_tiered_user(self, username, level, quota_bytes):
        from anetbbs.models import db, User, FileQuotaTier
        with self.app.app_context():
            db.session.add(FileQuotaTier(min_access_level=level,
                                         daily_quota_bytes=quota_bytes))
            u = User(username=username, email=f'{username}@example.com',
                    password_hash='x', is_admin=False, access_level=level)
            db.session.add(u)
            db.session.commit()
            return u.id

    def _make_file(self, nbytes):
        tmp_dir = tempfile.mkdtemp()
        path = os.path.join(tmp_dir, 'quotafile.bin')
        with open(path, 'wb') as fh:
            fh.write(b'x' * nbytes)
        return path

    def test_ftp_retr_blocked_over_quota(self):
        user_id = self._make_tiered_user('ftpquota1', 31, 50)
        fpath = self._make_file(100)
        handler = self._make_handler('ftpquota1')

        with patch('pyftpdlib.handlers.FTPHandler.ftp_RETR') as mock_super_retr:
            result = handler.ftp_RETR(fpath)

        mock_super_retr.assert_not_called()
        self.assertIsNone(result)
        handler.respond.assert_called_once()
        self.assertIn('550', handler.respond.call_args[0][0])
        self.assertIn('quota', handler.respond.call_args[0][0].lower())

        with self.app.app_context():
            from anetbbs.models import FileQuotaUsage
            row = FileQuotaUsage.query.filter_by(user_id=user_id).first()
            self.assertEqual(row.bytes_used_today if row else 0, 0)

    def test_ftp_retr_allowed_under_quota_calls_super(self):
        self._make_tiered_user('ftpquota2', 32, 1_000_000)
        fpath = self._make_file(100)
        handler = self._make_handler('ftpquota2')

        with patch('pyftpdlib.handlers.FTPHandler.ftp_RETR',
                  return_value=fpath) as mock_super_retr:
            result = handler.ftp_RETR(fpath)

        mock_super_retr.assert_called_once_with(fpath)
        self.assertEqual(result, fpath)
        handler.respond.assert_not_called()

    def test_ftp_on_file_sent_consumes_quota(self):
        user_id = self._make_tiered_user('ftpquota3', 33, 1_000_000)
        fpath = self._make_file(250)
        handler = self._make_handler('ftpquota3')

        handler.on_file_sent(fpath)

        with self.app.app_context():
            from anetbbs.models import FileQuotaUsage
            row = FileQuotaUsage.query.filter_by(user_id=user_id).first()
            self.assertIsNotNone(row)
            self.assertEqual(row.bytes_used_today, 250)

    def test_ftp_anonymous_login_bypasses_quota_entirely(self):
        """No local User row for 'anonymous' -- must not error, must not
        block, must not touch the quota tables."""
        fpath = self._make_file(100)
        handler = self._make_handler('anonymous')

        with patch('pyftpdlib.handlers.FTPHandler.ftp_RETR',
                  return_value=fpath) as mock_super_retr:
            result = handler.ftp_RETR(fpath)

        mock_super_retr.assert_called_once_with(fpath)
        self.assertEqual(result, fpath)
        handler.respond.assert_not_called()

    def test_ftp_qwk_node_session_bypasses_quota(self):
        """QWK-node FTP sessions (bulk offline-mail packet retrieval)
        are out of scope for this feature -- see ftp_RETR's docstring."""
        self._make_tiered_user('ftpquota4', 34, 1)   # 1-byte quota, would reject anything
        fpath = self._make_file(100)
        handler = self._make_handler('ftpquota4', qwk_node_id=42)

        with patch('pyftpdlib.handlers.FTPHandler.ftp_RETR',
                  return_value=fpath) as mock_super_retr:
            result = handler.ftp_RETR(fpath)

        mock_super_retr.assert_called_once_with(fpath)
        self.assertEqual(result, fpath)


if __name__ == '__main__':
    unittest.main()
