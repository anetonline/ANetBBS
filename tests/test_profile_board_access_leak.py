"""Regression test for a real access-control leak found in a pre-release
audit: /profile/<username> queried Post directly with no board-level
min_access_level check at all (boards.py's own view/search routes DO
enforce this via evaluate_access() -- see boards.py's search_posts()
for the identical bug already fixed there once before). A visitor's
public profile page leaked post subjects + board names from restricted/
sysop-only boards to ANY viewer, including a completely anonymous one
(the route has no @login_required either) -- neither the recent-posts
list nor the total_posts/total_replies stat counts respected board
visibility.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class ProfileBoardAccessLeakTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.profile_board_leak_test.db')
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

            poster = User(username='restrictedposter', email='rp@example.com',
                          password_hash='x', is_admin=False, access_level=100)
            viewer = User(username='lowlevelviewer', email='llv@example.com',
                         password_hash='x', is_admin=False, access_level=10)
            db.session.add_all([poster, viewer])
            db.session.commit()
            cls.poster_id = poster.id
            cls.viewer_id = viewer.id

            public_board = Board(name='Public Board', is_active=True,
                                 min_access_level=0)
            restricted_board = Board(name='Restricted VIP Board', is_active=True,
                                     min_access_level=90)
            db.session.add_all([public_board, restricted_board])
            db.session.commit()

            db.session.add(Post(board_id=public_board.id, author_id=poster.id,
                                subject='A public post', content='hello'))
            db.session.add(Post(board_id=restricted_board.id, author_id=poster.id,
                                subject='SECRET VIP-ONLY SUBJECT', content='shh'))
            db.session.commit()

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

    def test_anonymous_visitor_does_not_see_restricted_board_post(self):
        client = self.app.test_client()
        resp = client.get(f'/profile/restrictedposter')
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b'SECRET VIP-ONLY SUBJECT', resp.data)
        self.assertNotIn(b'Restricted VIP Board', resp.data)
        self.assertIn(b'A public post', resp.data)

    def test_low_level_logged_in_viewer_does_not_see_restricted_board_post(self):
        client = self._client_as(self.viewer_id)
        resp = client.get(f'/profile/restrictedposter')
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b'SECRET VIP-ONLY SUBJECT', resp.data)
        self.assertIn(b'A public post', resp.data)

    def test_high_level_viewer_does_see_restricted_board_post(self):
        """Sanity check: the fix must not over-hide -- a viewer who
        actually qualifies for the restricted board should still see it."""
        from anetbbs.models import db, User
        with self.app.app_context():
            vip = User(username='vipviewer', email='vip@example.com',
                      password_hash='x', is_admin=False, access_level=100)
            db.session.add(vip)
            db.session.commit()
            vip_id = vip.id

        client = self._client_as(vip_id)
        resp = client.get(f'/profile/restrictedposter')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'SECRET VIP-ONLY SUBJECT', resp.data)

    def test_total_posts_stat_excludes_restricted_board_for_low_level_viewer(self):
        client = self._client_as(self.viewer_id)
        resp = client.get(f'/profile/restrictedposter')
        self.assertEqual(resp.status_code, 200)
        # Only the one public post should count -- not the 2nd,
        # restricted-board one.
        self.assertIn(b'<strong>Posts:</strong> 1', resp.data)


if __name__ == '__main__':
    unittest.main()
