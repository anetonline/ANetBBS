"""Regression tests for the daily file download quota (FR: Firehawke,
2026-07-24) actually being enforced in the ANSI telnet/SSH terminal's
single-file download path -- anetbbs.features.bbs_ui.BBSMenuUI.
_download_file(). See anetbbs/features/file_quota.py for the tier-
resolution rule, and tests/test_file_quota.py for the helper-module-
level coverage; this file proves the wiring at the actual call site,
same relationship test_petscii_file_download.py has to the PETSCII
download path.
"""
import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class _FakeSession:
    def __init__(self, user, responses):
        self.user = user
        self._responses = list(responses)
        self.written = []

    async def write(self, text):
        self.written.append(text)

    async def read_line(self, prompt=''):
        if prompt:
            await self.write(prompt)
        if not self._responses:
            raise AssertionError(
                f'_FakeSession.read_line() called with prompt={prompt!r} but '
                'the scripted response queue is empty')
        return self._responses.pop(0)

    def transcript(self):
        return ''.join(self.written)


class BbsUiFileQuotaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.bbs_ui_quota_test.db')
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

    def _patched_app(self):
        return patch('anetbbs.features.bbs_ui._app', return_value=self.app)

    def _make_tiered_user(self, username, level, quota_bytes):
        from anetbbs.models import db, User, FileQuotaTier
        with self.app.app_context():
            db.session.add(FileQuotaTier(min_access_level=level,
                                         daily_quota_bytes=quota_bytes))
            u = User(username=username, email=f'{username}@example.com',
                    password_hash='x', is_admin=False, access_level=level)
            db.session.add(u)
            db.session.commit()
            return {'id': u.id, 'username': username, 'access_level': level}

    def test_terminal_download_blocked_over_quota(self):
        from anetbbs.features.bbs_ui import BBSMenuUI

        user_dict = self._make_tiered_user('termquota1', 21, 50)  # 50-byte quota

        tmp_dir = tempfile.mkdtemp()
        fpath = os.path.join(tmp_dir, 'big.bin')
        with open(fpath, 'wb') as fh:
            fh.write(b'x' * 100)   # bigger than the 50-byte quota

        f = {'name': 'big.bin', 'size': 100, 'path': fpath}
        session = _FakeSession(user_dict, ['X', ''])
        ui = BBSMenuUI(session)

        with patch('anetbbs.features.xfer.send_file',
                  new_callable=AsyncMock) as mock_send, \
             self._patched_app():
            asyncio.run(ui._download_file(f, 'http://web', ['xmodem']))

        mock_send.assert_not_called()
        self.assertIn('quota', session.transcript().lower())

        with self.app.app_context():
            from anetbbs.models import FileQuotaUsage
            row = FileQuotaUsage.query.filter_by(user_id=user_dict['id']).first()
            self.assertEqual(row.bytes_used_today if row else 0, 0,
                             'a rejected download must not consume the quota')

    def test_terminal_download_allowed_under_quota_and_consumes(self):
        from anetbbs.features.bbs_ui import BBSMenuUI

        user_dict = self._make_tiered_user('termquota2', 22, 1_000_000)

        tmp_dir = tempfile.mkdtemp()
        fpath = os.path.join(tmp_dir, 'small.bin')
        with open(fpath, 'wb') as fh:
            fh.write(b'x' * 100)

        f = {'name': 'small.bin', 'size': 100, 'path': fpath}
        session = _FakeSession(user_dict, ['X', ''])
        ui = BBSMenuUI(session)

        with patch('anetbbs.features.xfer.send_file',
                  new_callable=AsyncMock, return_value=True) as mock_send, \
             self._patched_app():
            asyncio.run(ui._download_file(f, 'http://web', ['xmodem']))

        mock_send.assert_called_once()

        with self.app.app_context():
            from anetbbs.models import FileQuotaUsage
            row = FileQuotaUsage.query.filter_by(user_id=user_dict['id']).first()
            self.assertIsNotNone(row)
            self.assertEqual(row.bytes_used_today, 100)


if __name__ == '__main__':
    unittest.main()
