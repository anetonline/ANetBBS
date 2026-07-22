"""Regression tests for PETSCII file downloads (Jerry: "looks like we
need to add download as well") -- XMODEM only, per sysop's choice (the
protocol real C64 terminal software like Novaterm/CCGMS/64NIC+ most
reliably supports). Reuses features.xfer.send_file() as-is rather than
building a PETSCII-specific transfer implementation: PETSCII connections
are plain telnet sockets (core/petscii_server.py's own docstring calls
it a "telnet listener"), and xfer.py's transport detection already
treats anything that isn't SSH/rlogin as telnet, so its existing IAC
escaping applies correctly with zero PETSCII-specific code needed there.
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
        self._forced_width = 40

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

    async def clear_screen(self):
        self.written.append('[CLR]')

    @property
    def petscii_width(self):
        return self._forced_width

    def transcript(self):
        return ''.join(self.written)


class PetsciiFileDownloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.petscii_download_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        cls._real_file_dir = tempfile.mkdtemp()
        cls._real_file_path = os.path.join(cls._real_file_dir, 'realfile.txt')
        with open(cls._real_file_path, 'w') as fh:
            fh.write('hello world')

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User, FileArea, FileUpload
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()
            alice = User(username='alice', email='alice@example.com',
                        password_hash='x', access_level=100)
            db.session.add(alice)
            db.session.commit()
            cls.alice_id = alice.id

            farea = FileArea(name='Test Files', tag='TESTFILES', is_active=True,
                             is_sysop_only=False, min_access_level=10,
                             storage_path=None)
            db.session.add(farea)
            db.session.commit()
            cls.farea_id = farea.id

            upload_real = FileUpload(
                uploader_id=alice.id, filename='realfile.txt',
                original_filename='realfile.txt', file_path=cls._real_file_path,
                file_size=11, description='A real file on disk.',
                is_public=True, file_area_id=farea.id)
            upload_missing = FileUpload(
                uploader_id=alice.id, filename='missing.txt',
                original_filename='missing.txt', file_path='/nonexistent/missing.txt',
                file_size=99, description='', is_public=True, file_area_id=farea.id)
            db.session.add_all([upload_real, upload_missing])
            db.session.commit()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)
        import shutil
        shutil.rmtree(cls._real_file_dir, ignore_errors=True)

    def _patched_app(self):
        return patch('anetbbs.features.bbs_ui._app', return_value=self.app)

    def _alice(self):
        return {'id': self.alice_id, 'username': 'alice', 'access_level': 100}

    def test_listing_is_numbered_for_selection(self):
        from anetbbs.features.petscii_ui import _files_browse
        session = _FakeSession(self._alice(), ['Q'])
        with self._patched_app():
            asyncio.run(_files_browse(session, self.farea_id, 'Test Files'))
        txt = session.transcript()
        self.assertIn('#=download, E=extended info, Q=back:', txt)

    def test_long_prompt_with_more_option_wraps_cleanly_not_mid_word(self):
        """Real report: on the real ANetBBS file area (18 files, forcing
        M=more), the combined prompt '#=download, E=extended info,
        M=more, Q=back: ' (46 chars) exceeded 40 columns and hard-broke
        mid-word ('Q=back' -> 'Q=b' / 'ack') since _paginated_pick's
        prompt construction wasn't width-aware."""
        from anetbbs.features.petscii_ui import _files_browse, PAGE_LINES
        from anetbbs.models import db, FileUpload
        extra_ids = []
        with self.app.app_context():
            for n in range(PAGE_LINES + 5):
                u = FileUpload(
                    uploader_id=self.alice_id, filename=f'extra{n}.zip',
                    original_filename=f'extra{n}.zip', file_path='',
                    file_size=1024, description='', is_public=True,
                    file_area_id=self.farea_id)
                db.session.add(u)
            db.session.commit()
            extra_ids = [u.id for u in FileUpload.query.filter(
                FileUpload.filename.like('extra%')).all()]
        try:
            session = _FakeSession(self._alice(), ['Q'])
            with self._patched_app():
                asyncio.run(_files_browse(session, self.farea_id, 'Test Files'))
            txt = session.transcript()
            self.assertIn('M=more', txt)
            self.assertNotIn('Q=b\r', txt,
                             "'Q=back' must not be split mid-word across lines")
            for screen in txt.split('[CLR]'):
                for line in screen.split('\r\n'):
                    visible = line.replace('\x12', '').replace('\x92', '')
                    self.assertLessEqual(len(visible), 40,
                                         f'prompt line exceeds 40 cols: {line!r}')
        finally:
            with self.app.app_context():
                FileUpload.query.filter(FileUpload.id.in_(extra_ids)).delete(
                    synchronize_session=False)
                db.session.commit()

    def test_downloading_a_real_file_calls_send_file_with_xmodem(self):
        from anetbbs.features.petscii_ui import _files_browse
        # Files ordered newest-first (created_at desc) -- missing.txt was
        # added after realfile.txt, so it's row 1, realfile.txt is row 2.
        session = _FakeSession(self._alice(), ['2', '', 'Q'])
        with patch('anetbbs.features.xfer.available_protocols',
                   return_value=['xmodem']), \
             patch('anetbbs.features.xfer.send_file',
                  new_callable=AsyncMock, return_value=True) as mock_send:
            with self._patched_app():
                asyncio.run(_files_browse(session, self.farea_id, 'Test Files'))
        mock_send.assert_called_once()
        call_args = mock_send.call_args[0]
        self.assertEqual(call_args[1], self._real_file_path)
        self.assertEqual(call_args[2], 'xmodem')
        self.assertIn('transfer complete', session.transcript())

    def test_downloading_a_missing_file_shows_error_not_crash(self):
        from anetbbs.features.petscii_ui import _files_browse
        # missing.txt is row 1 (newest).
        session = _FakeSession(self._alice(), ['1', '', 'Q'])
        with self._patched_app():
            asyncio.run(_files_browse(session, self.farea_id, 'Test Files'))
        # Word-wrapped now (see _write_wrapped) -- "server disk" can land
        # on its own line at 40 cols, so check for a shorter substring
        # that survives wrapping rather than the full unwrapped phrase.
        self.assertIn('file not found on server', session.transcript())

    def test_no_xmodem_available_shows_clear_message(self):
        from anetbbs.features.petscii_ui import _files_browse
        session = _FakeSession(self._alice(), ['2', '', 'Q'])
        with patch('anetbbs.features.xfer.available_protocols', return_value=[]):
            with self._patched_app():
                asyncio.run(_files_browse(session, self.farea_id, 'Test Files'))
        self.assertIn('not available on this server', session.transcript())

    def test_download_prompt_reachable_without_reading_any_description(self):
        """Real report: descriptions used to be shown inline for every
        file, so reaching the download prompt meant paging through all
        of them first -- and quitting early with Q at any -- More --
        exited the whole area instead of skipping to the download
        prompt ("there are numbers, but there is no way to download").
        The brief listing must show the download prompt immediately,
        with zero -- More -- prompts in between."""
        from anetbbs.features.petscii_ui import _files_browse
        session = _FakeSession(self._alice(), ['Q'])
        with self._patched_app():
            asyncio.run(_files_browse(session, self.farea_id, 'Test Files'))
        txt = session.transcript()
        self.assertNotIn('-- More --', txt,
                         'the brief listing must never need its own pagination '
                         'before reaching the download prompt')
        self.assertIn('#=download', txt)

    def test_e_shows_extended_description_then_returns_to_listing(self):
        from anetbbs.features.petscii_ui import _files_browse
        # E -> 2(realfile.txt) -> ''(press ENTER after description) ->
        # Q(back out of the listing)
        session = _FakeSession(self._alice(), ['E', '2', '', 'Q'])
        with self._patched_app():
            asyncio.run(_files_browse(session, self.farea_id, 'Test Files'))
        txt = session.transcript()
        self.assertIn('A real file on disk.', txt,
                      'E must show the full description on demand')
        self.assertIn('File Info: realfile.txt', txt)
        # Must return to the brief listing afterward, not exit outright.
        self.assertEqual(txt.count('#=download'), 2,
                         'listing must be shown again after viewing extended info')

    def test_status_messages_wrap_at_word_boundaries_not_mid_word(self):
        """Real report: 'Starting XMODEM send of A-NET<-door-scores1.5.zip
        -- start your terminal's receive now.' written unwrapped hard-
        broke mid-word/mid-filename at the 40-col terminal boundary
        ('A-NET<-door-score' / 's1.5.zip'). The long filename needs its
        own FileUpload row to reproduce the exact overflow condition."""
        from anetbbs.features.petscii_ui import _files_browse
        from anetbbs.models import db, FileUpload
        long_name = 'A-NET-door-scores1.5-a-really-long-filename-indeed.zip'
        with self.app.app_context():
            upload = FileUpload(
                uploader_id=self.alice_id, filename=long_name,
                original_filename=long_name, file_path=self._real_file_path,
                file_size=11, description='', is_public=True,
                file_area_id=self.farea_id)
            db.session.add(upload)
            db.session.commit()
        try:
            # Row 1 -- newest upload.
            session = _FakeSession(self._alice(), ['1', '', 'Q'])
            with patch('anetbbs.features.xfer.available_protocols',
                       return_value=['xmodem']), \
                 patch('anetbbs.features.xfer.send_file',
                      new_callable=AsyncMock, return_value=True):
                with self._patched_app():
                    asyncio.run(_files_browse(session, self.farea_id, 'Test Files'))
            # Split on [CLR] first -- a real screen clear starts a new
            # frame, so a trailing prompt with no newline of its own
            # (e.g. "Press ENTER...") must not be measured as if it were
            # glued onto the FOLLOWING screen's first line.
            for screen in session.transcript().split('[CLR]'):
                for line in screen.split('\r\n'):
                    visible = line.replace('\x12', '').replace('\x92', '')
                    self.assertLessEqual(len(visible), 40,
                                         f'unwrapped line exceeds 40 cols: {line!r}')
        finally:
            with self.app.app_context():
                FileUpload.query.filter_by(filename=long_name).delete()
                db.session.commit()

    def test_brief_listing_never_shows_the_description_text(self):
        from anetbbs.features.petscii_ui import _files_browse
        session = _FakeSession(self._alice(), ['Q'])
        with self._patched_app():
            asyncio.run(_files_browse(session, self.farea_id, 'Test Files'))
        self.assertNotIn('A real file on disk.', session.transcript(),
                         'description must not appear until E is used')


if __name__ == '__main__':
    unittest.main()
