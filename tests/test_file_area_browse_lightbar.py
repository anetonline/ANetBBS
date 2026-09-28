"""Regression tests for the file-area browser's conversion from
N=Next/P=Prev + type-a-number paging to a real arrow-key lightbar
(BBSMenuUI._file_area_browse(), anetbbs/features/bbs_ui.py), requested
live 2026-09-29 ("file areas... once you enter an area, you have to
use the n-next b-back to go through the pages... make that section
ALSO lightbar scrollable").

Batch download and extended-description viewing already existed
(_batch_download(), _view_file_desc()) -- this only changes how files
get SELECTED for them: Space toggles a file into/out of a batch set
(kept across scrolling), B downloads everything marked, Enter
downloads just the highlighted file, V views its description, same as
before just via lightbar navigation instead of typing numbers.

Uses the same real-seeded-DB + scripted-response fake-session
technique as test_msg_scan_preference.py, mocking the actual transfer/
description helpers (_download_file, _batch_download, _view_file_desc,
_upload_terminal_file) since those are unchanged and already covered
elsewhere -- these tests are about the SELECTION loop around them.
"""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class _FakeSession:
    def __init__(self, keys=None, lines=None):
        self.written = []
        self._keys = list(keys or [])
        self._lines = list(lines or [])

    async def write(self, text):
        self.written.append(text)

    async def read_key_arrow(self):
        return self._keys.pop(0) if self._keys else 'Q'

    async def read_line(self, prompt=''):
        if prompt:
            await self.write(prompt)
        return self._lines.pop(0) if self._lines else ''

    def transcript(self):
        return ''.join(self.written)


def _strip_ansi(text):
    import re
    return re.sub(r'\x1b\[[0-9;]*m', '', text)


class FileAreaBrowseLightbarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent
                          / '.file_area_browse_lightbar_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()
            u = User(username='filebrowsetest', email='fbt@example.com',
                    password_hash='x')
            db.session.add(u)
            db.session.commit()
            cls.uploader_id = u.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def setUp(self):
        # Each test's own area_filter='top' query sees every top-level
        # FileUpload row regardless of prefix -- clear between tests so
        # one test's seeded files can't leak into another's "empty
        # area" assertions.
        from anetbbs.models import db, FileUpload
        with self.app.app_context():
            FileUpload.query.delete()
            db.session.commit()

    def _seed_files(self, n, prefix='file'):
        from anetbbs.models import db, FileUpload
        with self.app.app_context():
            for i in range(n):
                db.session.add(FileUpload(
                    uploader_id=self.uploader_id,
                    filename=f'{prefix}{i}.zip',
                    original_filename=f'{prefix}{i}.zip',
                    file_path=f'/tmp/{prefix}{i}.zip',
                    file_size=1000 + i,
                    description=f'Description for {prefix}{i}',
                ))
            db.session.commit()

    def _ui(self, keys=None, lines=None):
        from anetbbs.features.bbs_ui import BBSMenuUI
        session = _FakeSession(keys=keys, lines=lines)
        return BBSMenuUI(session), session

    def _patched_app(self):
        return patch('anetbbs.features.bbs_ui._app', return_value=self.app)

    def test_enter_downloads_the_highlighted_file(self):
        self._seed_files(3, prefix='enterdl')
        ui, session = self._ui(keys=['DOWN', 'ENTER'])
        with self._patched_app(), \
             patch.object(ui, '_download_file', new=AsyncMock()) as mock_dl:
            asyncio.run(ui._file_area_browse(
                area_id=None, area_name='Top', area_filter='top',
                can_upload=False, uploads_dir='/tmp', web_base='',
                protos=[]))
        mock_dl.assert_called_once()
        downloaded = mock_dl.call_args.args[0]
        self.assertTrue(downloaded['name'].startswith('enterdl'))

    def test_v_views_the_highlighted_files_description(self):
        self._seed_files(2, prefix='viewdesc')
        ui, session = self._ui(keys=['V', 'Q'])
        with self._patched_app(), \
             patch.object(ui, '_view_file_desc', new=AsyncMock()) as mock_view:
            asyncio.run(ui._file_area_browse(
                area_id=None, area_name='Top', area_filter='top',
                can_upload=False, uploads_dir='/tmp', web_base='',
                protos=[]))
        mock_view.assert_called_once()

    def test_space_marks_then_b_batch_downloads_only_marked_files(self):
        self._seed_files(3, prefix='batch')
        # Mark item 0, move down, mark item 1, then trigger batch DL.
        ui, session = self._ui(keys=[' ', 'DOWN', ' ', 'B'])
        with self._patched_app(), \
             patch.object(ui, '_batch_download', new=AsyncMock()) as mock_batch:
            asyncio.run(ui._file_area_browse(
                area_id=None, area_name='Top', area_filter='top',
                can_upload=False, uploads_dir='/tmp', web_base='',
                protos=[]))
        mock_batch.assert_called_once()
        batch_files = mock_batch.call_args.args[0]
        self.assertEqual(len(batch_files), 2,
                         'only the two Space-marked files should be batched')

    def test_b_with_nothing_marked_shows_a_message_not_a_crash(self):
        self._seed_files(2, prefix='nomark')
        ui, session = self._ui(keys=['B', 'Q'], lines=[''])
        with self._patched_app(), \
             patch.object(ui, '_batch_download', new=AsyncMock()) as mock_batch:
            asyncio.run(ui._file_area_browse(
                area_id=None, area_name='Top', area_filter='top',
                can_upload=False, uploads_dir='/tmp', web_base='',
                protos=[]))
        mock_batch.assert_not_called()
        self.assertIn('No files marked', _strip_ansi(session.transcript()))

    def test_q_backs_out_cleanly(self):
        self._seed_files(2, prefix='quit')
        ui, session = self._ui(keys=['Q'])
        with self._patched_app():
            asyncio.run(ui._file_area_browse(
                area_id=None, area_name='Top', area_filter='top',
                can_upload=False, uploads_dir='/tmp', web_base='',
                protos=[]))
        # No assertion beyond "returns cleanly" -- the real regression
        # this guards is _rss_lightbar's own 'quit' tuple shape being
        # handled instead of raising on an unexpected result[0].

    def test_empty_area_offers_upload_not_just_a_dead_end(self):
        # A brand-new area with zero files must still let a sysop/user
        # upload the first one -- _rss_lightbar() itself auto-quits on
        # an empty row list, so this path is handled before it's ever
        # called.
        ui, session = self._ui(lines=['U'])
        with self._patched_app(), \
             patch.object(ui, '_upload_terminal_file', new=AsyncMock()) as mock_up, \
             patch.object(ui, '_rss_lightbar', new=AsyncMock(
                 side_effect=AssertionError(
                     '_rss_lightbar must not be called for an empty area'))):
            asyncio.run(ui._file_area_browse(
                area_id=None, area_name='Empty Area', area_filter='top',
                can_upload=True, uploads_dir='/tmp', web_base='',
                protos=['zmodem']))
        mock_up.assert_called_once()

    def test_empty_area_without_upload_permission_just_shows_back_prompt(self):
        ui, session = self._ui(lines=[''])
        with self._patched_app():
            asyncio.run(ui._file_area_browse(
                area_id=None, area_name='Empty Area', area_filter='top',
                can_upload=False, uploads_dir='/tmp', web_base='',
                protos=[]))
        self.assertIn('no files here yet', _strip_ansi(session.transcript()))

    def test_marked_file_shows_a_visible_checkbox_marker(self):
        self._seed_files(1, prefix='marker')
        ui, session = self._ui(keys=[' ', 'Q'])
        with self._patched_app():
            asyncio.run(ui._file_area_browse(
                area_id=None, area_name='Top', area_filter='top',
                can_upload=False, uploads_dir='/tmp', web_base='',
                protos=[]))
        # After marking, the redraw must show [x] somewhere in the
        # transcript -- proves the mark actually took visible effect,
        # not just internal state.
        self.assertIn('[x]', _strip_ansi(session.transcript()))


if __name__ == '__main__':
    unittest.main()
