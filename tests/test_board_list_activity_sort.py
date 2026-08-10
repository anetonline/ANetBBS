"""Regression test for a real gap Jerry found: message boards always
sorted by the sysop-configured manual Board.order field, with no way
to sort/filter by recent activity to find what's new ("recent posts
are kind of hard to find... maybe a filter for order by date").
Covers both the web ?sort=activity query param
(anetbbs/web/boards.py::list_boards()) and the terminal 'A' hotkey
toggle (anetbbs/features/bbs_ui.py::list_boards()).
"""
import asyncio
import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class BoardListActivitySortTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.board_activity_sort_test.db')
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
            user = User(username='boardsorttest', email='bst@example.com',
                       is_active=True)
            user.set_password('boardsortpassword123')
            db.session.add(user)
            db.session.commit()
            cls.user_id = user.id

            # Deliberately configured so manual order (A, B, C) is the
            # OPPOSITE of recency (C is newest, A is oldest) -- proves
            # sort=activity actually changes the order, not coincidence.
            board_a = Board(name='Board A (oldest activity)', order=1, is_active=True,
                            min_access_level=0)
            board_b = Board(name='Board B (no posts)', order=2, is_active=True,
                            min_access_level=0)
            board_c = Board(name='Board C (newest activity)', order=3, is_active=True,
                            min_access_level=0)
            db.session.add_all([board_a, board_b, board_c])
            db.session.commit()
            cls.board_a_id, cls.board_b_id, cls.board_c_id = board_a.id, board_b.id, board_c.id

            now = datetime.utcnow()
            db.session.add(Post(board_id=board_a.id, author_id=user.id,
                                subject='old', content='old post',
                                created_at=now - timedelta(days=5)))
            db.session.add(Post(board_id=board_c.id, author_id=user.id,
                                subject='new', content='new post',
                                created_at=now - timedelta(minutes=1)))
            db.session.commit()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def test_default_sort_is_unchanged_manual_order(self):
        client = self.app.test_client()
        resp = client.get('/boards/')
        body = resp.data.decode()
        idx_a = body.find('Board A')
        idx_c = body.find('Board C')
        self.assertLess(idx_a, idx_c, 'default view must still be manual Board.order (A before C)')

    def test_sort_activity_puts_most_recently_posted_board_first(self):
        client = self.app.test_client()
        resp = client.get('/boards/?sort=activity')
        body = resp.data.decode()
        idx_a = body.find('Board A')
        idx_c = body.find('Board C')
        self.assertNotEqual(idx_a, -1)
        self.assertNotEqual(idx_c, -1)
        self.assertLess(idx_c, idx_a,
                        'sort=activity must put Board C (posted 1 minute ago) '
                        'before Board A (posted 5 days ago), reversing the '
                        'manual Board.order (1 < 3)')

    def test_terminal_activity_toggle_reorders_board_list(self):
        from anetbbs.features.bbs_ui import BBSMenuUI

        class _FakeSession:
            def __init__(self, keys):
                self.user = {'id': 1, 'access_level': 100, 'is_admin': True}
                self.written = []
                self._keys = list(keys)
                self.window_size = (80, 24)

            async def write(self, text):
                self.written.append(text)

            async def read_line(self, prompt=''):
                if not self._keys:
                    return 'Q'
                return self._keys.pop(0)

        session = _FakeSession(keys=['A', 'Q'])  # toggle to activity sort, then quit
        ui = BBSMenuUI(session)

        with patch('anetbbs.features.bbs_ui._app', return_value=self.app):
            asyncio.run(ui.list_boards())

        out = ''.join(session.written)
        # Two full-screen redraws happen (initial + after pressing 'A') --
        # take the LAST one, which is the post-toggle redraw.
        frames = out.split('\x1b[2J\x1b[H')
        last_frame = frames[-1]
        self.assertIn('Sort: Recent Activity', last_frame,
                      "pressing 'A' must switch the footer to activity sort")
        idx_a = last_frame.find('Board A')
        idx_c = last_frame.find('Board C')
        self.assertNotEqual(idx_a, -1)
        self.assertNotEqual(idx_c, -1)
        self.assertLess(idx_c, idx_a,
                        'terminal activity sort must also put the most '
                        'recently posted board (Board C) before the oldest '
                        '(Board A)')


if __name__ == '__main__':
    unittest.main()
