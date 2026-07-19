"""Regression test: terminal local message-board threads used the old
[MORE] page-break pager instead of the scrollable ANView reader already
used for echomail/PM reading -- reported live ("terminal - message
boards, needs the same fix we did to the echomail areas, needs ANetView
to view, it has the old, original page break currently"). Mirrors the
pattern already proven in test_bulletin_anview.py.

IMPORTANT: the class-body `list_threads`/`read_thread` methods
(anetbbs/features/bbs_ui.py, near the top of the class) are DEAD CODE --
`BBSMenuUI.list_threads = _list_threads_v2` (assigned near the bottom of
the file) shadows the class-body list_threads at every real call site, so
read_thread (only reachable from the shadowed list_threads) was never
actually invoked in production. A first pass at this fix targeted that
dead code and passed its own tests while leaving the live bug untouched.
The actually-reachable path is list_threads (== _list_threads_v2) ->
read_thread_v2 (== _read_thread_v2), which is what this test exercises.
See feedback_bbs_ui_monkeypatch memory for the general trap.

Also covers the R/N (reply/new-thread) wiring: ANView's reply/new-thread
result must route into _post_compose(), matching read_echo_area()'s own
handling of the same two outcomes -- otherwise R/N would be a silent
dead end for board readers.
"""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class FakeSession:
    def __init__(self):
        self.user = {'id': 1, 'username': 'testuser', 'access_level': 100,
                     'is_admin': True}
        self.window_size = (80, 24)
        self.written = []

    async def write(self, text):
        self.written.append(text)

    async def read_line(self, prompt=''):
        return 'Q'


class _StubANView:
    """Captures constructor args and returns a caller-chosen result
    instead of running the real interactive viewer (which would block
    forever on _read_key())."""
    last_instance = None
    next_result = 'back'

    def __init__(self, session, lines, subject=""):
        self.session = session
        self.lines = lines
        self.subject = subject
        _StubANView.last_instance = self

    async def run(self):
        return _StubANView.next_result


class MessageBoardReadThreadAnviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.read_thread_anview_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User, Board, Post
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()
            user = User.query.filter_by(username='board_test_user').first()
            if not user:
                user = User(username='board_test_user',
                            email='board_test_user@example.com',
                            password_hash='x', access_level=100, is_admin=True)
                db.session.add(user)
                db.session.commit()

            board = Board.query.filter_by(name='Test Board').first()
            if not board:
                board = Board(name='Test Board', description='x')
                db.session.add(board)
                db.session.commit()
            cls.board_id = board.id

            root = Post(board_id=board.id, author_id=user.id,
                        subject='Root Subject',
                        content='Root post body, a few lines long.\n'
                                + '\n'.join(f'line {i}' for i in range(1, 30)))
            db.session.add(root)
            db.session.commit()
            reply = Post(board_id=board.id, author_id=user.id, parent_id=root.id,
                        subject='Re: Root Subject', content='A reply body.')
            db.session.add(reply)
            db.session.commit()
            cls.root_id = root.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def test_read_thread_v2_uses_anview_not_the_more_pager(self):
        from anetbbs.features.bbs_ui import BBSMenuUI

        _StubANView.last_instance = None
        _StubANView.next_result = 'back'
        session = FakeSession()
        ui = BBSMenuUI(session)

        with patch('anetbbs.features.bbs_ui._app', return_value=self.app), \
             patch('anetbbs.features.anedit.ANView', _StubANView):
            asyncio.run(ui.read_thread_v2(self.root_id, self.board_id, 'Test Board'))

        self.assertIsNotNone(_StubANView.last_instance,
                             'ANView must be constructed to view a thread')
        self.assertEqual(_StubANView.last_instance.subject, 'Root Subject')

        all_written = ''.join(session.written)
        self.assertNotIn('--- end ---', all_written,
                         'the old [MORE] pager end-marker must not appear -- '
                         'board threads should route through ANView now')
        self.assertNotIn('--- end of thread ---', all_written)

    def test_read_thread_v2_includes_both_root_and_reply_content(self):
        from anetbbs.features.bbs_ui import BBSMenuUI

        _StubANView.last_instance = None
        _StubANView.next_result = 'back'
        session = FakeSession()
        ui = BBSMenuUI(session)

        with patch('anetbbs.features.bbs_ui._app', return_value=self.app), \
             patch('anetbbs.features.anedit.ANView', _StubANView):
            asyncio.run(ui.read_thread_v2(self.root_id, self.board_id, 'Test Board'))

        joined = '\n'.join(_StubANView.last_instance.lines)
        self.assertIn('Root post body', joined)
        self.assertIn('A reply body.', joined)
        self.assertIn('[OP]', joined)
        self.assertIn('[Reply 1]', joined)

    def test_reply_result_routes_into_post_compose_with_parent_set(self):
        # _post_compose is defined at module level then assigned onto the
        # class (`BBSMenuUI._post_compose = _post_compose` near the bottom
        # of bbs_ui.py) -- that assignment copies a reference to the
        # function object into the class __dict__ at import time, so
        # patching the module-level name afterwards does NOT affect
        # `self._post_compose(...)` calls. Patch the class attribute
        # directly instead (see feedback_bbs_ui_monkeypatch memory).
        from anetbbs.features.bbs_ui import BBSMenuUI

        _StubANView.last_instance = None
        _StubANView.next_result = 'reply'
        session = FakeSession()
        ui = BBSMenuUI(session)

        captured = {}

        async def _fake_post_compose(self, board_id, board_name, parent_id=None):
            captured['board_id'] = board_id
            captured['board_name'] = board_name
            captured['parent_id'] = parent_id

        with patch('anetbbs.features.bbs_ui._app', return_value=self.app), \
             patch('anetbbs.features.anedit.ANView', _StubANView), \
             patch.object(BBSMenuUI, '_post_compose', _fake_post_compose):
            asyncio.run(ui.read_thread_v2(self.root_id, self.board_id, 'Test Board'))

        self.assertEqual(captured.get('board_id'), self.board_id)
        self.assertEqual(captured.get('board_name'), 'Test Board')
        self.assertEqual(captured.get('parent_id'), self.root_id,
                         'reply must be threaded under the root post being read')

    def test_new_result_routes_into_post_compose_with_no_parent(self):
        from anetbbs.features.bbs_ui import BBSMenuUI

        _StubANView.last_instance = None
        _StubANView.next_result = 'new'
        session = FakeSession()
        ui = BBSMenuUI(session)

        captured = {}

        async def _fake_post_compose(self, board_id, board_name, parent_id=None):
            captured['called'] = True
            captured['parent_id'] = parent_id

        with patch('anetbbs.features.bbs_ui._app', return_value=self.app), \
             patch('anetbbs.features.anedit.ANView', _StubANView), \
             patch.object(BBSMenuUI, '_post_compose', _fake_post_compose):
            asyncio.run(ui.read_thread_v2(self.root_id, self.board_id, 'Test Board'))

        self.assertTrue(captured.get('called'))
        self.assertIsNone(captured.get('parent_id'),
                          "starting a new thread must not carry over the old post's parent")

    def test_list_threads_entry_point_resolves_to_the_anview_reader(self):
        """End-to-end from the real, reachable menu entry point:
        list_boards() -> self.list_threads(...) -- which at runtime IS
        _list_threads_v2, since BBSMenuUI.list_threads gets reassigned to
        it at import time. Picking thread #1 must reach read_thread_v2
        (ANView), not the dead class-body read_thread."""
        from anetbbs.features.bbs_ui import BBSMenuUI

        _StubANView.last_instance = None
        _StubANView.next_result = 'back'
        session = FakeSession()
        session._inputs = ['1', 'Q']

        async def _read_line(prompt=''):
            return session._inputs.pop(0) if session._inputs else 'Q'
        session.read_line = _read_line
        ui = BBSMenuUI(session)

        with patch('anetbbs.features.bbs_ui._app', return_value=self.app), \
             patch('anetbbs.features.anedit.ANView', _StubANView):
            asyncio.run(ui.list_threads(self.board_id, 'Test Board'))

        self.assertIsNotNone(_StubANView.last_instance,
                             'picking a thread from the real list_threads entry '
                             'point must reach the ANView reader')
        self.assertEqual(_StubANView.last_instance.subject, 'Root Subject')


if __name__ == '__main__':
    unittest.main()
