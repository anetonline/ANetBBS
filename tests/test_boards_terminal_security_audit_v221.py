"""Regression tests for the terminal (ANSI + PETSCII) half of a full
message-boards security audit (phase 3 of a 4-phase audit list).

Found: unlike web/boards.py's new_post() (Board.min_write_level) and
reply_post() (Post.is_locked), the terminal composers checked NEITHER --
bbs_ui.py's _post_compose() (reached from both the 'N' new-thread and
'R'/'N' reply-from-ANView paths) and petscii_ui.py's _board_post() (its
'R' reply path specifically -- 'N' new-thread already correctly checked
can_write) let any authenticated user post/reply regardless of a board's
configured posting level or a moderator having locked the thread. Both
also skipped the sysop word-filter blocklist entirely (web already runs
subject/content through it). Also closes a latent IDOR defense-in-depth
gap: read_thread_v2()/_thread_read() never re-verified a fetched post's
board_id matched the board_id they were called with.
"""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class _FakeAnsiSession:
    def __init__(self, user, inputs=None):
        self.user = user
        self.window_size = (80, 24)
        self.written = []
        self._inputs = list(inputs or [])

    async def write(self, text):
        self.written.append(text)

    async def read_line(self, prompt=''):
        return self._inputs.pop(0) if self._inputs else 'Q'

    async def clear_screen(self):
        pass


class _FakePetsciiSession:
    def __init__(self, user, inputs=None):
        self.user = user
        self.petscii_width = 40
        self.written = []
        self._inputs = list(inputs or [])

    async def write(self, text):
        self.written.append(text)

    async def read_line(self, prompt=''):
        return self._inputs.pop(0) if self._inputs else '/abort'

    async def clear_screen(self):
        pass


class BoardsTerminalSecurityAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.boards_terminal_audit_test.db')
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
        from anetbbs.features import word_filter
        word_filter.invalidate()

    def _make_user(self, username, level, is_admin=False):
        from anetbbs.models import db, User
        with self.app.app_context():
            u = User(username=username, email=f'{username}@example.com',
                     password_hash='x', is_admin=is_admin, access_level=level)
            db.session.add(u)
            db.session.commit()
            return u.id, u.username

    def _make_board(self, name, min_access_level=0, min_write_level=None):
        from anetbbs.models import db, Board
        with self.app.app_context():
            b = Board(name=name, description='x', is_active=True,
                     min_access_level=min_access_level,
                     min_write_level=min_write_level)
            db.session.add(b)
            db.session.commit()
            return b.id

    def _make_post(self, board_id, author_id, subject='Subj', content='Body',
                   is_locked=False):
        from anetbbs.models import db, Post
        with self.app.app_context():
            p = Post(board_id=board_id, author_id=author_id,
                     subject=subject, content=content, is_locked=is_locked)
            db.session.add(p)
            db.session.commit()
            return p.id

    # ---- ANSI terminal: _post_compose() ------------------------------

    def test_ansi_post_compose_blocked_below_write_level(self):
        from anetbbs.features.bbs_ui import BBSMenuUI
        gated = self._make_board('AnsiWriteGated', min_access_level=0,
                                 min_write_level=50)
        uid, uname = self._make_user('ansiwritelow', 10)
        session = _FakeAnsiSession({'id': uid, 'username': uname,
                                    'access_level': 10, 'is_admin': False})
        ui = BBSMenuUI(session)

        with patch('anetbbs.features.bbs_ui._app', return_value=self.app), \
             patch('anetbbs.features.anedit.launch_anedit') as mock_edit:
            asyncio.run(ui._post_compose(gated, 'AnsiWriteGated'))

        mock_edit.assert_not_called()
        from anetbbs.models import Post
        with self.app.app_context():
            self.assertEqual(Post.query.filter_by(board_id=gated).count(), 0)

    def test_ansi_post_compose_allowed_meeting_write_level(self):
        from anetbbs.features.bbs_ui import BBSMenuUI
        gated = self._make_board('AnsiWriteAllowed', min_access_level=0,
                                 min_write_level=50)
        uid, uname = self._make_user('ansiwritehigh', 60)
        session = _FakeAnsiSession(
            {'id': uid, 'username': uname, 'access_level': 60, 'is_admin': False},
            inputs=['New Subject'])
        ui = BBSMenuUI(session)

        async def _fake_launch_anedit(session, quote='', subject='',
                                      username='', tagline_picker=None):
            return 'post body text'

        with patch('anetbbs.features.bbs_ui._app', return_value=self.app), \
             patch('anetbbs.features.anedit.launch_anedit', _fake_launch_anedit):
            asyncio.run(ui._post_compose(gated, 'AnsiWriteAllowed'))

        from anetbbs.models import Post
        with self.app.app_context():
            self.assertEqual(Post.query.filter_by(board_id=gated).count(), 1)

    def test_ansi_post_compose_blocked_replying_to_locked_thread(self):
        from anetbbs.features.bbs_ui import BBSMenuUI
        board = self._make_board('AnsiLockGated', min_access_level=0)
        author_id, _ = self._make_user('ansilockauthor', 100)
        root_id = self._make_post(board, author_id, is_locked=True)
        uid, uname = self._make_user('ansilockreplier', 50)
        session = _FakeAnsiSession({'id': uid, 'username': uname,
                                    'access_level': 50, 'is_admin': False})
        ui = BBSMenuUI(session)

        with patch('anetbbs.features.bbs_ui._app', return_value=self.app), \
             patch('anetbbs.features.anedit.launch_anedit') as mock_edit:
            asyncio.run(ui._post_compose(board, 'AnsiLockGated', parent_id=root_id))

        mock_edit.assert_not_called()
        from anetbbs.models import Post
        with self.app.app_context():
            self.assertEqual(Post.query.filter_by(board_id=board).count(), 1,
                             'only the original locked root post should exist')

    def test_ansi_post_compose_admin_bypasses_locked_thread(self):
        from anetbbs.features.bbs_ui import BBSMenuUI
        board = self._make_board('AnsiLockAdminGated', min_access_level=0)
        author_id, _ = self._make_user('ansilockadminauthor', 100)
        root_id = self._make_post(board, author_id, is_locked=True)
        uid, uname = self._make_user('ansilockadmin', 100, is_admin=True)
        session = _FakeAnsiSession(
            {'id': uid, 'username': uname, 'access_level': 100, 'is_admin': True},
            inputs=['Re: reply subject'])
        ui = BBSMenuUI(session)

        async def _fake_launch_anedit(session, quote='', subject='',
                                      username='', tagline_picker=None):
            return 'admin override reply'

        with patch('anetbbs.features.bbs_ui._app', return_value=self.app), \
             patch('anetbbs.features.anedit.launch_anedit', _fake_launch_anedit):
            asyncio.run(ui._post_compose(board, 'AnsiLockAdminGated', parent_id=root_id))

        from anetbbs.models import Post
        with self.app.app_context():
            self.assertEqual(Post.query.filter_by(board_id=board).count(), 2)

    def test_ansi_post_compose_applies_word_filter(self):
        from anetbbs.models import db, WordFilter
        from anetbbs.features.bbs_ui import BBSMenuUI
        with self.app.app_context():
            db.session.add(WordFilter(pattern='badword', replacement='****',
                                      is_active=True))
            db.session.commit()

        board = self._make_board('AnsiWordFilterBoard', min_access_level=0)
        uid, uname = self._make_user('ansiwfuser', 10)
        session = _FakeAnsiSession(
            {'id': uid, 'username': uname, 'access_level': 10, 'is_admin': False},
            inputs=['Subject with badword in it'])
        ui = BBSMenuUI(session)

        async def _fake_launch_anedit(session, quote='', subject='',
                                      username='', tagline_picker=None):
            return 'this body has a badword in it too'

        with patch('anetbbs.features.bbs_ui._app', return_value=self.app), \
             patch('anetbbs.features.anedit.launch_anedit', _fake_launch_anedit):
            asyncio.run(ui._post_compose(board, 'AnsiWordFilterBoard'))

        from anetbbs.models import Post
        with self.app.app_context():
            p = Post.query.filter_by(board_id=board).first()
            self.assertIsNotNone(p)
            self.assertNotIn('badword', p.subject)
            self.assertNotIn('badword', p.content)
            self.assertIn('****', p.subject)
            self.assertIn('****', p.content)

    def test_ansi_read_thread_v2_rejects_post_from_different_board(self):
        """Defense-in-depth IDOR check: a post_id whose real board_id
        doesn't match the board_id the reader was invoked with must be
        refused instead of rendered."""
        from anetbbs.features.bbs_ui import BBSMenuUI
        board_a = self._make_board('AnsiIdorBoardA', min_access_level=0)
        board_b = self._make_board('AnsiIdorBoardB', min_access_level=0)
        author_id, _ = self._make_user('ansiidorauthor', 100)
        post_in_a = self._make_post(board_a, author_id,
                                    subject='Board A secret', content='secret body')

        uid, uname = self._make_user('ansiidoruser', 10)
        session = _FakeAnsiSession({'id': uid, 'username': uname,
                                    'access_level': 10, 'is_admin': False})
        ui = BBSMenuUI(session)

        with patch('anetbbs.features.bbs_ui._app', return_value=self.app):
            # Ask to read post_in_a's id but claim it belongs to board_b.
            asyncio.run(ui.read_thread_v2(post_in_a, board_b, 'AnsiIdorBoardB'))

        joined = ''.join(session.written)
        self.assertNotIn('Board A secret', joined)
        self.assertNotIn('secret body', joined)

    # ---- PETSCII: _board_post() / _thread_read() ----------------------

    def test_petscii_board_post_blocked_below_write_level(self):
        from anetbbs.features.petscii_ui import _board_post
        gated = self._make_board('PetWriteGated', min_access_level=0,
                                 min_write_level=50)
        uid, uname = self._make_user('petwritelow', 10)
        session = _FakePetsciiSession({'id': uid, 'username': uname,
                                       'access_level': 10, 'is_admin': False})

        with patch('anetbbs.features.petscii_ui._app_ctx',
                  side_effect=lambda: self.app.app_context()):
            asyncio.run(_board_post(session, gated))

        from anetbbs.models import Post
        with self.app.app_context():
            self.assertEqual(Post.query.filter_by(board_id=gated).count(), 0)

    def test_petscii_board_post_allowed_meeting_write_level(self):
        from anetbbs.features.petscii_ui import _board_post
        gated = self._make_board('PetWriteAllowed', min_access_level=0,
                                 min_write_level=50)
        uid, uname = self._make_user('petwritehigh', 60)
        session = _FakePetsciiSession(
            {'id': uid, 'username': uname, 'access_level': 60, 'is_admin': False},
            inputs=['New Subject', 'line one', '/send'])

        with patch('anetbbs.features.petscii_ui._app_ctx',
                  side_effect=lambda: self.app.app_context()):
            asyncio.run(_board_post(session, gated))

        from anetbbs.models import Post
        with self.app.app_context():
            self.assertEqual(Post.query.filter_by(board_id=gated).count(), 1)

    def test_petscii_board_post_blocked_replying_to_locked_thread(self):
        from anetbbs.features.petscii_ui import _board_post
        board = self._make_board('PetLockGated', min_access_level=0)
        author_id, _ = self._make_user('petlockauthor', 100)
        root_id = self._make_post(board, author_id, is_locked=True)
        uid, uname = self._make_user('petlockreplier', 50)
        session = _FakePetsciiSession({'id': uid, 'username': uname,
                                       'access_level': 50, 'is_admin': False})

        with patch('anetbbs.features.petscii_ui._app_ctx',
                  side_effect=lambda: self.app.app_context()):
            asyncio.run(_board_post(session, board, parent_id=root_id))

        from anetbbs.models import Post
        with self.app.app_context():
            self.assertEqual(Post.query.filter_by(board_id=board).count(), 1,
                             'only the original locked root post should exist')

    def test_petscii_board_post_applies_word_filter(self):
        from anetbbs.models import db, WordFilter
        from anetbbs.features.petscii_ui import _board_post
        with self.app.app_context():
            db.session.add(WordFilter(pattern='naughty', replacement='####',
                                      is_active=True))
            db.session.commit()

        board = self._make_board('PetWordFilterBoard', min_access_level=0)
        uid, uname = self._make_user('petwfuser', 10)
        session = _FakePetsciiSession(
            {'id': uid, 'username': uname, 'access_level': 10, 'is_admin': False},
            inputs=['Subj with naughty word', 'body has naughty too', '/send'])

        with patch('anetbbs.features.petscii_ui._app_ctx',
                  side_effect=lambda: self.app.app_context()):
            asyncio.run(_board_post(session, board))

        from anetbbs.models import Post
        with self.app.app_context():
            p = Post.query.filter_by(board_id=board).first()
            self.assertIsNotNone(p)
            self.assertNotIn('naughty', p.subject)
            self.assertNotIn('naughty', p.content)
            self.assertIn('####', p.subject)
            self.assertIn('####', p.content)

    def test_petscii_thread_read_rejects_post_from_different_board(self):
        from anetbbs.features.petscii_ui import _thread_read
        board_a = self._make_board('PetIdorBoardA', min_access_level=0)
        board_b = self._make_board('PetIdorBoardB', min_access_level=0)
        author_id, _ = self._make_user('petidorauthor', 100)
        post_in_a = self._make_post(board_a, author_id,
                                    subject='PET Board A secret',
                                    content='pet secret body')

        uid, uname = self._make_user('petidoruser', 10)
        session = _FakePetsciiSession({'id': uid, 'username': uname,
                                       'access_level': 10, 'is_admin': False})

        with patch('anetbbs.features.petscii_ui._app_ctx',
                  side_effect=lambda: self.app.app_context()):
            asyncio.run(_thread_read(session, post_in_a, board_b, 'PetIdorBoardB'))

        joined = ''.join(session.written)
        self.assertNotIn('PET Board A secret', joined)
        self.assertNotIn('pet secret body', joined)


if __name__ == '__main__':
    unittest.main()
