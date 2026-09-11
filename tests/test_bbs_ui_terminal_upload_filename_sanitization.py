"""Regression test for a real finding from a security/performance audit:
BBSMenuUI._upload_terminal_file() (anetbbs/features/bbs_ui.py) stored
the filename the user's own ZMODEM/YMODEM/XMODEM client sent --
verbatim -- into FileUpload.original_filename, unlike the web upload
route (anetbbs/web/files.py's upload()), which has always run every
filename through werkzeug's secure_filename() first.

A filename with an embedded ANSI/CSI escape sequence, once uploaded
through the terminal, would get rendered unsanitized straight into
every OTHER caller's own terminal the moment they browsed that file
area (BBSMenuUI._file_area_browse()'s listing table renders
f['name'] directly) -- the same chrome-injection shape already found
and fixed elsewhere in this module for remote echomail/InstantMessage
content, except this one is reachable by any regular user's own
upload, no network-peer compromise required.

Fixed by running the client-supplied filename through
werkzeug.utils.secure_filename() immediately after recv_file()
returns, matching the web route's own precedent exactly.
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

_EVIL_NAME = 'evil\x1b[2J\x1b[1;1Hname.zip'


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


class BbsUiTerminalUploadFilenameSanitizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.bbs_ui_upload_fname_test.db')
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

    def test_terminal_upload_filename_is_sanitized_before_storage(self):
        from anetbbs.features.bbs_ui import BBSMenuUI
        from anetbbs.models import db, User

        with self.app.app_context():
            u = User(username='uploadtest1', email='uploadtest1@example.com',
                     password_hash='x', is_admin=False, access_level=10)
            db.session.add(u)
            db.session.commit()
            user_id = u.id

        recv_dir = tempfile.mkdtemp()
        recv_path = os.path.join(recv_dir, 'received_payload')
        with open(recv_path, 'wb') as fh:
            fh.write(b'payload bytes')

        uploads_dir = tempfile.mkdtemp()

        session = _FakeSession(
            {'id': user_id, 'username': 'uploadtest1', 'access_level': 10},
            responses=['Z', '', ''])
        ui = BBSMenuUI(session)

        with patch('anetbbs.features.xfer.recv_file', new_callable=AsyncMock,
                  return_value=[(_EVIL_NAME, recv_path)]) as mock_recv, \
             self._patched_app():
            asyncio.run(ui._upload_terminal_file(
                area_id=None, uploads_dir=uploads_dir, protos=['zmodem'],
                storage_path=''))

        mock_recv.assert_called_once()

        with self.app.app_context():
            from anetbbs.models import FileUpload
            row = FileUpload.query.filter_by(uploader_id=user_id).first()
            self.assertIsNotNone(row, 'upload should have been recorded')
            self.assertNotIn('\x1b', row.original_filename,
                             'stored filename must not carry a raw ESC byte')
            self.assertNotIn('[2J', row.original_filename)

        # The uploader's own "[Uploaded: ...]" confirmation must also be clean
        # (self-inflicted, but still shouldn't hit the terminal raw).
        self.assertNotIn('\x1b', session.transcript())


if __name__ == '__main__':
    unittest.main()
