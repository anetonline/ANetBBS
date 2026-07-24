"""Regression tests for a real access-control bug found in a full
application-wide audit: Board.min_access_level is admin-configurable
and enforced as a WRITE-gate fallback in new_post(), but was never
checked on any READ path -- list_boards()/view_board()/view_post()/
view_post_ansi()/search_posts() had no @login_required and never
consulted the field, so a board restricted to VIP/sysop-level users
was still fully readable (including full-text search) by anyone.

Note: anetbbs/features/access_control.py's evaluate_access() treats a
user with no real access_level attribute (anonymous/AnonymousUserMixin)
as level 10 -- a separate, pre-existing, deliberately-tested design
decision (see test_evaluate_access.py), not something these tests
change. These tests use min_access_level=50 so the fix is
unambiguously demonstrated regardless of that behavior.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class BoardsAccessControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.boards_access_control_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User, Board, Post
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        with cls.app.app_context():
            db.create_all()

            low_user = User(username='lowlvlboardtest', email='llbt@example.com',
                           password_hash='x', is_admin=False, access_level=10)
            high_user = User(username='hilvlboardtest', email='hlbt@example.com',
                            password_hash='x', is_admin=False, access_level=100)
            db.session.add_all([low_user, high_user])
            db.session.commit()
            cls.low_user_id = low_user.id
            cls.high_user_id = high_user.id

            gated = Board(name='VipBoardTest', description='x', is_active=True,
                         min_access_level=50)
            db.session.add(gated)
            db.session.commit()
            cls.gated_board_id = gated.id

            post = Post(board_id=gated.id, author_id=high_user.id,
                       subject='VIP-only secret content', content='Secret body text.')
            db.session.add(post)
            db.session.commit()
            cls.gated_post_id = post.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _client_as(self, user_id):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(user_id)
            sess['_fresh'] = True
        return client

    # ---- list_boards() ----

    def test_low_level_user_does_not_see_gated_board_in_listing(self):
        client = self._client_as(self.low_user_id)
        resp = client.get('/boards/')
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b'VipBoardTest', resp.data)

    def test_high_level_user_sees_gated_board_in_listing(self):
        client = self._client_as(self.high_user_id)
        resp = client.get('/boards/')
        self.assertIn(b'VipBoardTest', resp.data)

    # ---- view_board() ----

    def test_low_level_user_cannot_view_gated_board_directly(self):
        client = self._client_as(self.low_user_id)
        resp = client.get(f'/boards/{self.gated_board_id}')
        self.assertEqual(resp.status_code, 403)

    def test_high_level_user_can_view_gated_board_directly(self):
        client = self._client_as(self.high_user_id)
        resp = client.get(f'/boards/{self.gated_board_id}')
        self.assertEqual(resp.status_code, 200)

    # ---- view_post() / view_post_ansi() ----

    def test_low_level_user_cannot_read_post_in_gated_board_by_direct_id(self):
        client = self._client_as(self.low_user_id)
        resp = client.get(f'/boards/post/{self.gated_post_id}')
        self.assertEqual(resp.status_code, 403,
                         'a low-access user must not be able to read a '
                         'VIP-only post by guessing/knowing its ID')

    def test_high_level_user_can_read_post_in_gated_board(self):
        client = self._client_as(self.high_user_id)
        resp = client.get(f'/boards/post/{self.gated_post_id}')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Secret body text', resp.data)

    def test_low_level_user_cannot_read_post_ansi_view(self):
        client = self._client_as(self.low_user_id)
        resp = client.get(f'/boards/post/{self.gated_post_id}/ansi')
        self.assertEqual(resp.status_code, 403)

    # ---- search_posts() ----

    def test_low_level_user_search_does_not_return_gated_board_posts(self):
        client = self._client_as(self.low_user_id)
        resp = client.get('/boards/search?q=secret')
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b'VIP-only secret content', resp.data)

    def test_high_level_user_search_returns_gated_board_posts(self):
        client = self._client_as(self.high_user_id)
        resp = client.get('/boards/search?q=secret')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'VIP-only secret content', resp.data)

    # ---- public board (min_access_level=0) unaffected ----

    def test_public_board_still_fully_visible(self):
        from anetbbs.models import db, Board
        with self.app.app_context():
            public_board = Board(name='PublicBoardTest', description='x',
                                is_active=True, min_access_level=0)
            db.session.add(public_board)
            db.session.commit()

        client = self._client_as(self.low_user_id)
        resp = client.get('/boards/')
        self.assertIn(b'PublicBoardTest', resp.data)


if __name__ == '__main__':
    unittest.main()
