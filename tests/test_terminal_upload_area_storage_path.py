"""Regression test for a terminal-upload bug in anetbbs/features/bbs_ui.py:
_upload_terminal_file().

Confirmed live: a file uploaded via ZMODEM into a disk-backed file area
(one with a configured FileArea.storage_path) showed up correctly in the
terminal's own file listing, but never appeared in the web UI's file-area
page for the same area.

Root cause: the terminal upload always saved into a single generic
uploads_dir (a FileUpload DB row's backing directory), regardless of
which area was selected -- storage_path was computed by the caller
(_file_area_browse) but never actually passed down or used. Meanwhile
anetbbs/web/file_areas.py's _scan_area() -- which both the terminal
browser (when storage_path is set) and the web view use to list files --
only ever scans area.storage_path on disk and has no FileUpload DB
fallback at all. The file existed; it just wasn't where the web (or the
terminal's own primary listing branch) ever looked.

The fix branches on whether storage_path was supplied: if so, save
directly under storage_path with the real filename and create no
FileUpload row (matching file_areas.py's own web upload route and every
other file already in that area); otherwise, preserve the original
uuid-named-file + FileUpload-row behavior for areas with no disk storage
configured at all (the "General / Top-level" case).

Uses plain TestCase + manual asyncio.run() rather than
IsolatedAsyncioTestCase -- confirmed by direct reproduction that
IsolatedAsyncioTestCase hangs indefinitely (near-zero CPU, no progress)
the moment a real Flask app (which imports eventlet) is created inside
one of its tests, while a bare asyncio.run() of the exact same coroutine
in the exact same process completes in well under a second. Root cause
not fully chased down (likely an eventlet/asyncio event-loop
interaction), but the workaround is simple and matches how
tests/test_terminal_node_monitor.py already avoids this class of problem
in this codebase.
"""
import os
import sys
import shutil
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.features.bbs_ui import BBSMenuUI

_DATA_DIR = Path(__file__).resolve().parents[1] / 'data'


def _snapshot_data_dir():
    if not _DATA_DIR.is_dir():
        return set()
    return set(_DATA_DIR.iterdir())


class _FakeSession:
    """Minimal stand-in -- only what _upload_terminal_file touches before
    reaching recv_file(): write() and read_line() for the protocol/
    description prompts, plus session.user for uploader_id lookup."""

    def __init__(self, protocol_choice, description=''):
        self._answers = iter([protocol_choice, description])
        self.written = []
        self.user = {'id': 1}

    async def write(self, text):
        self.written.append(text)

    async def read_line(self, prompt=''):
        return next(self._answers, '')


def _make_menu():
    """Bypass BBSMenuUI.__init__ (heavy session/DB wiring not needed for
    this method) -- same technique already used by
    tests/test_terminal_node_monitor.py for the same reason."""
    return object.__new__(BBSMenuUI)


class TerminalUploadStoragePathTests(unittest.TestCase):
    def setUp(self):
        self.recv_tmpdir = tempfile.mkdtemp(prefix='anetbbs_test_recv_')
        self.area_storage = tempfile.mkdtemp(prefix='anetbbs_test_area_')
        self.generic_uploads = tempfile.mkdtemp(prefix='anetbbs_test_uploads_')

    def tearDown(self):
        for d in (self.recv_tmpdir, self.area_storage, self.generic_uploads):
            shutil.rmtree(d, ignore_errors=True)

    def _make_received_file(self, name, content=b'hello world'):
        path = os.path.join(self.recv_tmpdir, name)
        with open(path, 'wb') as fh:
            fh.write(content)
        return path

    def test_disk_backed_area_saves_under_storage_path_no_db_row(self):
        """The bug: this used to land in the generic uploads_dir instead,
        invisible to file_areas.py's disk-only _scan_area()."""
        src = self._make_received_file('anetmrc_v1.3.9.zip')
        menu = _make_menu()
        menu.session = _FakeSession(protocol_choice='Z', description='')

        async def fake_recv_file(session, protocol):
            return [('anetmrc_v1.3.9.zip', src)]

        async def go():
            with patch('anetbbs.features.xfer.recv_file', fake_recv_file):
                await menu._upload_terminal_file(
                    area_id=5, uploads_dir=self.generic_uploads,
                    protos=['zmodem'], storage_path=self.area_storage)

        asyncio.run(go())

        dest = os.path.join(self.area_storage, 'anetmrc_v1.3.9.zip')
        self.assertTrue(os.path.isfile(dest),
                        "file must land under the area's own storage_path "
                        "-- that's the only place file_areas.py's web view "
                        "(_scan_area) ever looks")
        self.assertEqual(os.listdir(self.generic_uploads), [],
                         "must NOT fall back to the generic uploads_dir "
                         "when a real area storage_path was supplied")

    def test_top_level_area_without_storage_path_unchanged(self):
        """Baseline: areas with no disk storage (storage_path='') must
        keep the original FileUpload-DB-row behavior -- this is the
        General/Top-level case, not a regression target."""
        data_dir_before = _snapshot_data_dir()
        import anetbbs.config as cfg_mod
        orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        orig_flask_env = os.environ.get('FLASK_ENV')

        db_dir = tempfile.mkdtemp(prefix='anetbbs_test_db_')
        db_path = os.path.join(db_dir, 'test.db')
        try:
            cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path}'
            os.environ['FLASK_ENV'] = 'testing'
            from anetbbs.web_app import create_app
            app = create_app('testing')
            app.config['TESTING'] = True

            with app.app_context():
                from anetbbs.models import db
                db.create_all()

            src = self._make_received_file('notes.txt')
            menu = _make_menu()
            menu.session = _FakeSession(protocol_choice='Z',
                                        description='test upload')

            async def fake_recv_file(session, protocol):
                return [('notes.txt', src)]

            async def go():
                with patch('anetbbs.features.bbs_ui._app', return_value=app), \
                     patch('anetbbs.features.xfer.recv_file', fake_recv_file):
                    await menu._upload_terminal_file(
                        area_id=None, uploads_dir=self.generic_uploads,
                        protos=['zmodem'], storage_path='')

            asyncio.run(go())

            with app.app_context():
                from anetbbs.models import FileUpload
                rows = FileUpload.query.all()
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0].original_filename, 'notes.txt')
                self.assertTrue(
                    rows[0].file_path.startswith(self.generic_uploads))
        finally:
            cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = orig_db_uri
            if orig_flask_env is None:
                os.environ.pop('FLASK_ENV', None)
            else:
                os.environ['FLASK_ENV'] = orig_flask_env
            shutil.rmtree(db_dir, ignore_errors=True)
            for entry in _snapshot_data_dir() - data_dir_before:
                if entry.is_dir():
                    shutil.rmtree(entry, ignore_errors=True)
                else:
                    try:
                        entry.unlink()
                    except OSError:
                        pass


if __name__ == '__main__':
    unittest.main()
