"""Regression tests for a broader logging audit (2026-10-26): most web
blueprints never wrote any UserActivity row at all for real user
actions -- only web/auth.py's login/register/logout/password-reset did.
anetbbs/web/boards.py now logs via the shared
anetbbs.core.activity_log.web_log_activity() helper at these call sites:

  - view_board() -> 'board_viewed' (coarse: once per board opened, not
                     once per thread/post viewed inside it -- view_post()
                     deliberately logs nothing)
  - new_post()   -> 'board_post'
  - reply_post() -> 'board_reply'

Each test drives the real route through a real Flask test client and
asserts the resulting UserActivity row's activity_type, details, and --
critically -- caller_log_id, which must match the id auth.py's login
route stashes in flask_session (session['caller_log_id']). Without that
correlation the row exists but never surfaces in the admin caller-log
drill-down, same bug class closed for file downloads in
test_web_file_download_activity_correlation.py.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class WebBoardsActivityLoggingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent /
                         '.web_boards_activity_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User, Board, Post, CallerLog
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        with cls.app.app_context():
            db.create_all()

            user = User(username='boardacttest', email='boardacttest@example.com',
                       password_hash='x', access_level=100, is_admin=True)
            db.session.add(user)
            db.session.commit()
            cls.user_id = user.id

            cl = CallerLog(user_id=user.id, username='boardacttest', service='web')
            db.session.add(cl)
            db.session.commit()
            cls.caller_log_id = cl.id

            board = Board(name='Activity Test Board', description='x',
                         is_active=True, min_access_level=0, min_write_level=0)
            db.session.add(board)
            db.session.commit()
            cls.board_id = board.id

            # A pre-existing top-level post for the reply() test.
            seed = Post(board_id=board.id, author_id=user.id,
                       subject='Seed Post Subject', content='Seed post body.')
            db.session.add(seed)
            db.session.commit()
            cls.seed_post_id = seed.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _client(self):
        """A logged-in client whose Flask session also carries
        caller_log_id, mirroring what auth.py's real login route
        stashes there."""
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(self.user_id)
            sess['_fresh'] = True
            sess['caller_log_id'] = self.caller_log_id
        return client

    def test_view_board_logs_coarse_board_viewed_event(self):
        from anetbbs.models import UserActivity
        client = self._client()
        resp = client.get(f'/boards/{self.board_id}')
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            activity = (UserActivity.query
                       .filter_by(user_id=self.user_id,
                                 activity_type='board_viewed')
                       .order_by(UserActivity.id.desc()).first())
            self.assertIsNotNone(
                activity, 'opening a board must log a board_viewed '
                'UserActivity row')
            self.assertEqual(activity.caller_log_id, self.caller_log_id)
            self.assertIn('Activity Test Board', activity.details)

    def test_new_post_logs_board_post(self):
        from anetbbs.models import UserActivity
        client = self._client()
        resp = client.post(f'/boards/{self.board_id}/new', data={
            'subject': 'Brand New Board Post Subject',
            'content': 'This is the body of a brand new post.',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            activity = (UserActivity.query
                       .filter_by(user_id=self.user_id,
                                 activity_type='board_post')
                       .order_by(UserActivity.id.desc()).first())
            self.assertIsNotNone(
                activity, 'creating a new board post must log a '
                'board_post UserActivity row')
            self.assertEqual(activity.caller_log_id, self.caller_log_id)
            self.assertIn('Brand New Board Post Subject', activity.details)

    def test_reply_post_logs_board_reply(self):
        from anetbbs.models import UserActivity
        client = self._client()
        resp = client.post(f'/boards/post/{self.seed_post_id}/reply', data={
            'content': 'This is a reply to the seed post.',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            activity = (UserActivity.query
                       .filter_by(user_id=self.user_id,
                                 activity_type='board_reply')
                       .order_by(UserActivity.id.desc()).first())
            self.assertIsNotNone(
                activity, 'replying to a post must log a board_reply '
                'UserActivity row, not board_post')
            self.assertEqual(activity.caller_log_id, self.caller_log_id)
            self.assertIn('Seed Post Subject', activity.details)


if __name__ == '__main__':
    unittest.main()
