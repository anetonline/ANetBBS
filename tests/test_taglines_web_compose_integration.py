"""Integration test: the "Add a tagline" scrollable listbox on the web
compose forms must actually cause the SPECIFIC picked tagline to be
appended to the saved content. Covers boards.new_post() and
boards.reply_post() as representative web-side call sites --
pm.py/netmail.py/echomail.py follow the exact same
`request.form.get('tagline_id', type=int)` pattern (see
models.get_active_taglines/format_tagline_append), so this also stands
in as a template-level smoke test that the listbox's field name
('tagline_id') matches what each route actually reads.

This replaced an earlier "add a random one, blind" checkbox design --
reported live ("you should be able to pick from a scrollable list, not
just a random one. So you should be able to see it").
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class TaglinesWebComposeIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.taglines_web_compose_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User, Board, Tagline
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        with cls.app.app_context():
            db.create_all()
            user = User.query.filter_by(username='tagcompose_user').first()
            if not user:
                user = User(username='tagcompose_user',
                            email='tagcompose_user@example.com',
                            password_hash='x', access_level=100, is_admin=True)
                db.session.add(user)
                db.session.commit()
            cls.user_id = user.id

            board = Board.query.filter_by(name='Tagline Compose Board').first()
            if not board:
                board = Board(name='Tagline Compose Board', description='x')
                db.session.add(board)
                db.session.commit()
            cls.board_id = board.id

            # Two distinct active taglines -- proves the SPECIFIC one
            # picked is the one that gets appended, not just "a" random
            # one (the behavior this replaced).
            Tagline.query.delete()
            db.session.commit()
            db.session.add(Tagline(text='THE_PICKED_TAGLINE', is_active=True))
            db.session.add(Tagline(text='THE_OTHER_TAGLINE', is_active=True))
            db.session.commit()
            cls.tagline_id = Tagline.query.filter_by(
                text='THE_PICKED_TAGLINE').first().id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _client(self):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(self.user_id)
            sess['_fresh'] = True
        return client

    def test_new_post_appends_the_specifically_picked_tagline(self):
        from anetbbs.models import Post
        client = self._client()
        resp = client.post(f'/boards/{self.board_id}/new', data={
            'subject': 'Tagline Test Post',
            'content': 'The body of my post.',
            'tagline_id': str(self.tagline_id),
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            post = Post.query.filter_by(subject='Tagline Test Post').first()
            self.assertIsNotNone(post)
            self.assertIn('The body of my post.', post.content)
            self.assertIn('THE_PICKED_TAGLINE', post.content)
            self.assertNotIn('THE_OTHER_TAGLINE', post.content,
                             'only the specifically picked tagline should be '
                             'appended, not some other random pool entry')
            self.assertIn('-- ', post.content)

    def test_new_post_omits_tagline_when_none_picked(self):
        from anetbbs.models import Post
        client = self._client()
        resp = client.post(f'/boards/{self.board_id}/new', data={
            'subject': 'No Tagline Test Post',
            'content': 'Plain body, no signature.',
            'tagline_id': '',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            post = Post.query.filter_by(subject='No Tagline Test Post').first()
            self.assertIsNotNone(post)
            self.assertEqual(post.content, 'Plain body, no signature.')
            self.assertNotIn('THE_PICKED_TAGLINE', post.content)
            self.assertNotIn('THE_OTHER_TAGLINE', post.content)

    def test_reply_post_appends_the_specifically_picked_tagline(self):
        from anetbbs.models import Post
        with self.app.app_context():
            root = Post(board_id=self.board_id, author_id=self.user_id,
                        subject='Root For Reply Test', content='root body')
            from anetbbs.models import db
            db.session.add(root)
            db.session.commit()
            root_id = root.id

        client = self._client()
        resp = client.post(f'/boards/post/{root_id}/reply', data={
            'subject': 'Re: Root For Reply Test',
            'content': 'My reply body.',
            'tagline_id': str(self.tagline_id),
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            reply = Post.query.filter_by(parent_id=root_id).first()
            self.assertIsNotNone(reply)
            self.assertIn('My reply body.', reply.content)
            self.assertIn('THE_PICKED_TAGLINE', reply.content)

    def test_new_post_form_ignores_a_disabled_taglines_id(self):
        """A tagline_id for an inactive/nonexistent Tagline must be
        ignored rather than crashing or silently appending nothing
        malformed -- e.g. a stale option value after a sysop disables
        one between page load and submit."""
        from anetbbs.models import Post
        client = self._client()
        resp = client.post(f'/boards/{self.board_id}/new', data={
            'subject': 'Bad Tagline Id Post',
            'content': 'Body text.',
            'tagline_id': '999999',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            post = Post.query.filter_by(subject='Bad Tagline Id Post').first()
            self.assertIsNotNone(post)
            self.assertEqual(post.content, 'Body text.')


if __name__ == '__main__':
    unittest.main()
