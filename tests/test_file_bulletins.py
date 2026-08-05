"""Regression tests for File Bulletins (.txt/.asc/.ans dropped into
FILE_BULLETINS_DIR) -- distinct from the existing DB-authored Bulletins
(Message model, web textarea). Real files are auto-registered (inactive
until the sysop enables them at Admin -> Bulletins -> Files), then
browsed via a lightbar list and read through the same CP437/ANSI-aware
ANView pipeline (launch_aneview) used for FidoNet/terminal-composed
messages -- unlike the DB bulletins, this content genuinely IS raw file
bytes, so the CP437 decode is correct here.

Also covers: the admin CRUD routes, and the new 'file_bulletin'
LoginModule dispatch type (Jerry's original framing -- "another logon/
logoff module... for bulletins").
"""
import asyncio
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod

_DATA_DIR = Path(__file__).resolve().parents[1] / 'data'


def _snapshot_data_dir():
    if not _DATA_DIR.is_dir():
        return set()
    return set(_DATA_DIR.iterdir())


class _FakeSession:
    def __init__(self, inputs=None, user=None):
        self.user = user or {'id': 1, 'username': 'testuser', 'access_level': 10,
                              'is_admin': False}
        self.window_size = (80, 24)
        self.written = []
        self._inputs = list(inputs or [])

    async def write(self, text):
        self.written.append(text)

    async def read_line(self, prompt=''):
        return self._inputs.pop(0) if self._inputs else 'Q'


class _StubANView:
    last_instance = None

    def __init__(self, session, lines, subject=""):
        self.session = session
        self.lines = lines
        self.subject = subject
        _StubANView.last_instance = self

    async def run(self):
        return 'back'


class FileBulletinsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._data_dir_before = _snapshot_data_dir()
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.file_bulletins_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'

        from anetbbs.web_app import create_app
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)
        for entry in _snapshot_data_dir() - cls._data_dir_before:
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app.config['FILE_BULLETINS_DIR'] = self._tmp.name
        with self.app.app_context():
            from anetbbs.models import db, FileBulletin
            FileBulletin.query.delete()
            db.session.commit()

    def _write(self, name, content, binary=False):
        path = os.path.join(self._tmp.name, name)
        mode = 'wb' if binary else 'w'
        with open(path, mode) as f:
            f.write(content)
        return path

    # ------------------------------------------------------------------
    # sync_bulletin_rows / get_visible_bulletins
    # ------------------------------------------------------------------

    def test_new_file_is_auto_registered_but_inactive(self):
        from anetbbs.features import file_bulletins as fb
        self._write('news.txt', 'hello')
        with self.app.app_context():
            fb.sync_bulletin_rows(self.app.config)
            from anetbbs.models import FileBulletin
            row = FileBulletin.query.filter_by(filename='news.txt').first()
            self.assertIsNotNone(row)
            self.assertFalse(row.is_active,
                             'new files must be off by default, matching every '
                             'other auto-detected-content convention in this project')
            self.assertEqual(row.title, 'News')

    def test_sync_is_idempotent_and_does_not_clobber_admin_edits(self):
        from anetbbs.features import file_bulletins as fb
        self._write('news.txt', 'hello')
        with self.app.app_context():
            fb.sync_bulletin_rows(self.app.config)
            from anetbbs.models import db, FileBulletin
            row = FileBulletin.query.filter_by(filename='news.txt').first()
            row.title = 'Sysop-Renamed Title'
            row.is_active = True
            db.session.commit()

            fb.sync_bulletin_rows(self.app.config)
            rows = FileBulletin.query.filter_by(filename='news.txt').all()
            self.assertEqual(len(rows), 1, 'must not create a duplicate row on re-sync')
            self.assertEqual(rows[0].title, 'Sysop-Renamed Title')
            self.assertTrue(rows[0].is_active)

    def test_inactive_bulletin_is_not_visible(self):
        from anetbbs.features import file_bulletins as fb
        self._write('news.txt', 'hello')
        with self.app.app_context():
            fb.sync_bulletin_rows(self.app.config)
            visible = fb.get_visible_bulletins(self.app.config)
            self.assertEqual(visible, [])

    def test_active_bulletin_with_missing_file_is_not_visible(self):
        """A row can outlive its file (e.g. a door regenerating its own
        score file elsewhere) -- the row stays, but must not be offered
        to users until the file is really there again."""
        from anetbbs.features import file_bulletins as fb
        path = self._write('news.txt', 'hello')
        with self.app.app_context():
            fb.sync_bulletin_rows(self.app.config)
            from anetbbs.models import db, FileBulletin
            row = FileBulletin.query.filter_by(filename='news.txt').first()
            row.is_active = True
            db.session.commit()
        os.remove(path)
        with self.app.app_context():
            visible = fb.get_visible_bulletins(self.app.config)
            self.assertEqual(visible, [])

    def test_active_bulletin_gated_by_min_access_level(self):
        from anetbbs.features import file_bulletins as fb
        self._write('sysoponly.txt', 'secret')
        with self.app.app_context():
            fb.sync_bulletin_rows(self.app.config)
            from anetbbs.models import db, FileBulletin
            row = FileBulletin.query.filter_by(filename='sysoponly.txt').first()
            row.is_active = True
            row.min_access_level = 100
            db.session.commit()

            self.assertEqual(fb.get_visible_bulletins(self.app.config, user_level=10), [])
            visible = fb.get_visible_bulletins(self.app.config, user_level=100)
            self.assertEqual(len(visible), 1)

    def test_non_matching_extension_is_ignored(self):
        from anetbbs.features import file_bulletins as fb
        self._write('readme.md', 'not a bulletin type')
        with self.app.app_context():
            fb.sync_bulletin_rows(self.app.config)
            from anetbbs.models import FileBulletin
            self.assertIsNone(FileBulletin.query.filter_by(filename='readme.md').first())

    def test_read_bulletin_body_preserves_raw_bytes_as_latin1(self):
        """Matches _load_display_screens's own convention -- real CP437
        art (high-bit bytes) must round-trip byte-for-byte, not get
        mangled by a UTF-8 decode attempt."""
        from anetbbs.features import file_bulletins as fb
        raw = bytes([0x1b, ord('['), ord('3'), ord('6'), ord('m')]) + bytes([0xB0, 0xB1, 0xB2])
        path = self._write('art.ans', raw, binary=True)
        body = fb.read_bulletin_body(path)
        self.assertEqual(body.encode('latin-1'), raw)

    # ------------------------------------------------------------------
    # Terminal UI (BBSMenuUI.list_file_bulletins)
    # ------------------------------------------------------------------

    def test_terminal_menu_lists_and_views_via_anview(self):
        from anetbbs.features.bbs_ui import BBSMenuUI

        self._write('news.txt', 'Real bulletin content\nsecond line')
        with self.app.app_context():
            from anetbbs.features import file_bulletins as fb
            fb.sync_bulletin_rows(self.app.config)
            from anetbbs.models import db, FileBulletin
            row = FileBulletin.query.filter_by(filename='news.txt').first()
            row.is_active = True
            db.session.commit()

        _StubANView.last_instance = None
        session = _FakeSession(inputs=['Q'])
        ui = BBSMenuUI(session)

        with patch('anetbbs.features.bbs_ui._app', return_value=self.app), \
             patch('anetbbs.features.bbs_ui.BBSMenuUI._rss_lightbar',
                   side_effect=[('enter', 0), ('quit',)]) as mock_lb, \
             patch('anetbbs.features.anedit.ANView', _StubANView):
            asyncio.run(ui.list_file_bulletins())

        self.assertTrue(mock_lb.called, 'must use the lightbar, not a numbered menu')
        self.assertIsNotNone(_StubANView.last_instance,
                             'ANView must be constructed to view a file bulletin')
        self.assertEqual(_StubANView.last_instance.subject, 'News')
        joined = '\n'.join(_StubANView.last_instance.lines)
        self.assertIn('Real bulletin content', joined)

    def test_terminal_menu_shows_placeholder_when_nothing_active(self):
        from anetbbs.features.bbs_ui import BBSMenuUI

        session = _FakeSession()
        ui = BBSMenuUI(session)
        with patch('anetbbs.features.bbs_ui._app', return_value=self.app):
            asyncio.run(ui.list_file_bulletins())
        all_written = ''.join(session.written)
        self.assertIn('no file bulletins', all_written.lower())

    def test_terminal_menu_hides_bulletin_above_users_access_level(self):
        """End-to-end version of test_active_bulletin_gated_by_min_access_level
        above, through the real BBSMenuUI.list_file_bulletins() path
        rather than calling get_visible_bulletins() directly."""
        from anetbbs.features.bbs_ui import BBSMenuUI

        self._write('highlevel.txt', 'top secret')
        with self.app.app_context():
            from anetbbs.features import file_bulletins as fb
            fb.sync_bulletin_rows(self.app.config)
            from anetbbs.models import db, FileBulletin
            row = FileBulletin.query.filter_by(filename='highlevel.txt').first()
            row.is_active = True
            row.min_access_level = 50
            db.session.commit()

        session = _FakeSession(user={'id': 1, 'username': 'lowuser',
                                     'access_level': 10, 'is_admin': False})
        ui = BBSMenuUI(session)
        with patch('anetbbs.features.bbs_ui._app', return_value=self.app):
            asyncio.run(ui.list_file_bulletins())
        all_written = ''.join(session.written)
        self.assertIn('no file bulletins', all_written.lower())


class FileBulletinsAdminRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._data_dir_before = _snapshot_data_dir()
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.file_bulletins_admin_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'

        from anetbbs.web_app import create_app
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False

        with cls.app.app_context():
            from anetbbs.models import db, User
            from werkzeug.security import generate_password_hash
            user = User.query.filter_by(username='admin').first()
            if not user:
                user = User(username='admin', email='admin@example.com',
                            password_hash=generate_password_hash('testpass123'),
                            access_level=100, is_admin=True)
                db.session.add(user)
            else:
                user.password_hash = generate_password_hash('testpass123')
            db.session.commit()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)
        for entry in _snapshot_data_dir() - cls._data_dir_before:
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app.config['FILE_BULLETINS_DIR'] = self._tmp.name
        with self.app.app_context():
            from anetbbs.models import db, FileBulletin
            FileBulletin.query.delete()
            db.session.commit()
        self.client = self.app.test_client()
        self.client.post('/auth/login',
                         data={'username': 'admin', 'password': 'testpass123'},
                         follow_redirects=True)

    def test_index_auto_registers_a_dropped_in_file(self):
        with open(os.path.join(self._tmp.name, 'scores.ans'), 'w') as f:
            f.write('x')
        resp = self.client.get('/admin/file-bulletins/')
        self.assertEqual(resp.status_code, 200)
        with self.app.app_context():
            from anetbbs.models import FileBulletin
            row = FileBulletin.query.filter_by(filename='scores.ans').first()
            self.assertIsNotNone(row)
            self.assertFalse(row.is_active)

    def test_edit_and_toggle_round_trip(self):
        with open(os.path.join(self._tmp.name, 'news.txt'), 'w') as f:
            f.write('x')
        self.client.get('/admin/file-bulletins/')  # triggers auto-register
        with self.app.app_context():
            from anetbbs.models import FileBulletin
            row_id = FileBulletin.query.filter_by(filename='news.txt').first().id

        resp = self.client.post(f'/admin/file-bulletins/{row_id}/edit', data={
            'title': 'Weekly News', 'sort_order': '5',
            'is_active': 'on', 'min_access_level': '0',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        with self.app.app_context():
            from anetbbs.models import FileBulletin
            row = FileBulletin.query.get(row_id)
            self.assertEqual(row.title, 'Weekly News')
            self.assertTrue(row.is_active)

        self.client.post(f'/admin/file-bulletins/{row_id}/toggle', follow_redirects=True)
        with self.app.app_context():
            from anetbbs.models import FileBulletin
            self.assertFalse(FileBulletin.query.get(row_id).is_active)

    def test_delete_removes_row_but_not_the_file(self):
        fpath = os.path.join(self._tmp.name, 'news.txt')
        with open(fpath, 'w') as f:
            f.write('x')
        self.client.get('/admin/file-bulletins/')
        with self.app.app_context():
            from anetbbs.models import FileBulletin
            row_id = FileBulletin.query.filter_by(filename='news.txt').first().id

        # Deliberately NOT following the redirect: the index page it
        # redirects to re-syncs from disk, and since the file is still
        # there (delete only removes the admin row, not the file) it
        # gets legitimately re-registered -- with SQLite free to reuse
        # the just-freed rowid, checking post-redirect could pass for
        # the wrong reason. Check the row is gone immediately instead.
        resp = self.client.post(f'/admin/file-bulletins/{row_id}/delete')
        self.assertEqual(resp.status_code, 302)
        with self.app.app_context():
            from anetbbs.models import FileBulletin
            self.assertIsNone(FileBulletin.query.get(row_id))
        self.assertTrue(os.path.isfile(fpath), 'deleting the row must not touch the file')


class FileBulletinLoginModuleDispatchTests(unittest.TestCase):
    """Jerry's original framing: 'another logon/logoff module... for
    bulletins' -- confirms 'file_bulletin' really is wired into the
    LoginModule dispatcher, the same way 'wall'/'ansi' are."""

    def test_dispatch_calls_show_file_bulletins(self):
        from anetbbs.features.login_modules import _dispatch

        with patch('anetbbs.features.file_bulletins.show_file_bulletins') as mock_show:
            async def _fake(*a, **k):
                return None
            mock_show.side_effect = _fake
            asyncio.run(_dispatch(session=object(), module_type='file_bulletin', params={}))
        mock_show.assert_called_once()

    def test_module_type_is_a_real_admin_choice(self):
        from anetbbs.web.login_modules_admin import MODULE_TYPES
        types = dict(MODULE_TYPES)
        self.assertIn('file_bulletin', types)


if __name__ == '__main__':
    unittest.main()
