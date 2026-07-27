"""Regression tests for a full message-boards security audit (phase 3 of
a 4-phase audit list; phase 1 was auth/session, phase 2 was file areas).

Prior fixes (test_boards_access_control.py) closed the READ-side gap:
list_boards()/view_board()/view_post()/view_post_ansi()/search_posts()
all now check Board.min_access_level. This audit found the exact same
bug class on the WRITE and interaction side, never mirrored over:

- reply_post() had ZERO board-access check at all (not even read-level,
  let alone min_write_level) -- any authenticated user could reply into
  a restricted board's thread just by knowing/guessing a post_id.
- subscribe() had no access check -- a below-level user could subscribe
  to a restricted board and have every future post's subject/author
  leaked via new_post()'s subscriber-notification fan-out.
- react() had no access check -- IDOR confirming a restricted post's
  existence.
- votes.py's _can_vote_on() only checked existence for 'post'/'echomail',
  never the board/area's own min_access_level, despite its own
  docstring claiming otherwise.
- saved.py's add() let a user bookmark a restricted post and have its
  subject/author permanently shown on their own /saved/ page.
- /sitemap.xml enumerated every board/post including restricted ones,
  unauthenticated.
- notify_mentions() pushed up to 280 chars of a restricted board post's
  content to any @-mentioned user regardless of their own access level.
- No posting-flood protection at all (unlike /api/vote or /imsg/send).
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class BoardsWriteSecurityAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.boards_write_audit_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
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
        from anetbbs.features.rate_limit import _buckets
        _buckets.clear()
        from anetbbs.features import word_filter
        word_filter.invalidate()

    def _client_as(self, user_id):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(user_id)
            sess['_fresh'] = True
        return client

    def _make_user(self, username, level, is_admin=False):
        from anetbbs.models import db, User
        with self.app.app_context():
            u = User(username=username, email=f'{username}@example.com',
                     password_hash='x', is_admin=is_admin, access_level=level)
            db.session.add(u)
            db.session.commit()
            return u.id

    def _make_board(self, name, min_access_level=0, min_write_level=None):
        from anetbbs.models import db, Board
        with self.app.app_context():
            b = Board(name=name, description='x', is_active=True,
                     min_access_level=min_access_level,
                     min_write_level=min_write_level)
            db.session.add(b)
            db.session.commit()
            return b.id

    def _make_post(self, board_id, author_id, subject='Subj', content='Body'):
        from anetbbs.models import db, Post
        with self.app.app_context():
            p = Post(board_id=board_id, author_id=author_id,
                     subject=subject, content=content)
            db.session.add(p)
            db.session.commit()
            return p.id

    # ---- reply_post() ----

    def test_reply_blocked_for_user_below_board_read_level(self):
        gated = self._make_board('ReplyReadGated', min_access_level=50)
        author = self._make_user('replyreadauthor', 100)
        low = self._make_user('replyreadlow', 10)
        post_id = self._make_post(gated, author)

        client = self._client_as(low)
        resp = client.post(f'/boards/post/{post_id}/reply',
                           data={'content': 'sneaky reply'})
        self.assertEqual(resp.status_code, 403)
        from anetbbs.models import Post
        with self.app.app_context():
            self.assertEqual(Post.query.filter_by(board_id=gated).count(), 1,
                             'no reply should have been created')

    def test_reply_blocked_for_user_below_write_level_but_above_read_level(self):
        gated = self._make_board('ReplyWriteGated', min_access_level=10,
                                 min_write_level=50)
        author = self._make_user('replywriteauthor', 100)
        mid = self._make_user('replywritemid', 20)  # can read, can't write
        post_id = self._make_post(gated, author)

        client = self._client_as(mid)
        resp = client.post(f'/boards/post/{post_id}/reply',
                           data={'content': 'sneaky reply'})
        self.assertEqual(resp.status_code, 403)

    def test_reply_allowed_for_user_meeting_write_level(self):
        gated = self._make_board('ReplyAllowedGated', min_access_level=10,
                                 min_write_level=50)
        author = self._make_user('replyallowedauthor', 100)
        high = self._make_user('replyallowedhigh', 60)
        post_id = self._make_post(gated, author)

        client = self._client_as(high)
        resp = client.post(f'/boards/post/{post_id}/reply',
                           data={'content': 'legit reply'},
                           follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        from anetbbs.models import Post
        with self.app.app_context():
            self.assertEqual(Post.query.filter_by(board_id=gated).count(), 2)

    # ---- subscribe() ----

    def test_subscribe_blocked_for_user_below_access_level(self):
        gated = self._make_board('SubGated', min_access_level=50)
        low = self._make_user('sublow', 10)
        client = self._client_as(low)
        resp = client.post(f'/boards/{gated}/subscribe')
        self.assertEqual(resp.status_code, 403)
        from anetbbs.models import BoardSubscription
        with self.app.app_context():
            self.assertIsNone(
                BoardSubscription.query.filter_by(
                    user_id=low, board_id=gated).first())

    def test_subscribe_allowed_for_user_meeting_access_level(self):
        gated = self._make_board('SubAllowedGated', min_access_level=50)
        high = self._make_user('subhigh', 60)
        client = self._client_as(high)
        resp = client.post(f'/boards/{gated}/subscribe', follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        from anetbbs.models import BoardSubscription
        with self.app.app_context():
            self.assertIsNotNone(
                BoardSubscription.query.filter_by(
                    user_id=high, board_id=gated).first())

    def test_unsubscribe_still_works_regardless_of_access(self):
        """A user who already has a (legitimate, pre-existing) subscription
        must always be able to remove it, even if the board's level was
        later raised above their own."""
        from anetbbs.models import db, BoardSubscription
        gated = self._make_board('UnsubGated', min_access_level=0)
        user = self._make_user('unsubuser', 10)
        with self.app.app_context():
            db.session.add(BoardSubscription(user_id=user, board_id=gated))
            db.session.commit()
        # Raise the board's level after the fact.
        from anetbbs.models import Board
        with self.app.app_context():
            b = Board.query.get(gated)
            b.min_access_level = 90
            db.session.commit()

        client = self._client_as(user)
        # The unsubscribe itself must succeed (checked below via DB
        # state) even though the redirect target 403s -- view_board()
        # correctly refuses to show a board the user can no longer read,
        # which is a separate, expected check, not a regression here.
        client.post(f'/boards/{gated}/subscribe', follow_redirects=True)
        with self.app.app_context():
            self.assertIsNone(
                BoardSubscription.query.filter_by(
                    user_id=user, board_id=gated).first(),
                'unsubscribe must succeed even without current access')

    # ---- react() ----

    def test_react_blocked_for_user_below_access_level(self):
        gated = self._make_board('ReactGated', min_access_level=50)
        author = self._make_user('reactauthor', 100)
        low = self._make_user('reactlow', 10)
        post_id = self._make_post(gated, author)

        client = self._client_as(low)
        resp = client.post(f'/boards/post/{post_id}/react', data={'kind': 'like'})
        self.assertEqual(resp.status_code, 403)

    def test_react_allowed_for_user_meeting_access_level(self):
        gated = self._make_board('ReactAllowedGated', min_access_level=50)
        author = self._make_user('reactallowedauthor', 100)
        high = self._make_user('reactallowedhigh', 60)
        post_id = self._make_post(gated, author)

        client = self._client_as(high)
        resp = client.post(f'/boards/post/{post_id}/react',
                           data={'kind': 'like'}, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        from anetbbs.models import PostReaction
        with self.app.app_context():
            self.assertIsNotNone(
                PostReaction.query.filter_by(
                    user_id=high, post_id=post_id, kind='like').first())

    # ---- votes.py _can_vote_on() ----

    def test_vote_cast_blocked_for_post_in_gated_board(self):
        gated = self._make_board('VoteGated', min_access_level=50)
        author = self._make_user('voteauthor', 100)
        low = self._make_user('votelow', 10)
        post_id = self._make_post(gated, author)

        client = self._client_as(low)
        resp = client.post('/api/vote', json={'type': 'post', 'id': post_id, 'value': 1})
        self.assertEqual(resp.status_code, 404,
                         'vote must be refused as not-found, not leak existence')

    def test_vote_tally_blocked_anonymous_for_post_in_gated_board(self):
        gated = self._make_board('VoteTallyGated', min_access_level=50)
        author = self._make_user('votetallyauthor', 100)
        post_id = self._make_post(gated, author)

        client = self.app.test_client()  # anonymous
        resp = client.get(f'/api/vote/tally?type=post&id={post_id}')
        self.assertEqual(resp.status_code, 404)

    def test_vote_allowed_for_post_in_public_board(self):
        pub = self._make_board('VotePublic', min_access_level=0)
        author = self._make_user('votepubauthor', 100)
        low = self._make_user('votepublow', 10)
        post_id = self._make_post(pub, author)

        client = self._client_as(low)
        resp = client.post('/api/vote', json={'type': 'post', 'id': post_id, 'value': 1})
        self.assertEqual(resp.status_code, 200)

    # ---- saved.py add() ----

    def test_saved_add_blocked_for_post_in_gated_board(self):
        gated = self._make_board('SaveGated', min_access_level=50)
        author = self._make_user('saveauthor', 100)
        low = self._make_user('savelow', 10)
        post_id = self._make_post(gated, author)

        client = self._client_as(low)
        resp = client.post('/saved/add', data={'kind': 'post', 'target_id': post_id},
                           follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        from anetbbs.models import SavedMessage
        with self.app.app_context():
            self.assertIsNone(
                SavedMessage.query.filter_by(
                    user_id=low, kind='post', target_id=post_id).first(),
                'a restricted post must not be bookmarkable')

    def test_saved_add_allowed_for_post_in_accessible_board(self):
        pub = self._make_board('SavePublic', min_access_level=0)
        author = self._make_user('savepubauthor', 100)
        user = self._make_user('savepubuser', 10)
        post_id = self._make_post(pub, author)

        client = self._client_as(user)
        resp = client.post('/saved/add', data={'kind': 'post', 'target_id': post_id},
                           follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        from anetbbs.models import SavedMessage
        with self.app.app_context():
            self.assertIsNotNone(
                SavedMessage.query.filter_by(
                    user_id=user, kind='post', target_id=post_id).first())

    # ---- /sitemap.xml ----

    def test_sitemap_excludes_gated_board_and_its_posts(self):
        gated = self._make_board('SitemapGated', min_access_level=50)
        author = self._make_user('sitemapauthor', 100)
        self._make_post(gated, author, subject='Gated Sitemap Subject')

        client = self.app.test_client()
        resp = client.get('/sitemap.xml')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertNotIn(f'/boards/{gated}<', body)

    def test_sitemap_includes_public_board_and_its_posts(self):
        pub = self._make_board('SitemapPublic', min_access_level=0)
        author = self._make_user('sitemappubauthor', 100)
        post_id = self._make_post(pub, author)

        client = self.app.test_client()
        resp = client.get('/sitemap.xml')
        body = resp.get_data(as_text=True)
        self.assertIn(f'/boards/{pub}<', body)
        self.assertIn(f'/boards/post/{post_id}<', body)

    # ---- notify_mentions() access filtering ----

    def test_mention_notification_suppressed_for_user_below_board_level(self):
        gated = self._make_board('MentionGated', min_access_level=50)
        author = self._make_user('mentionauthor', 100)
        mentioned_low = self._make_user('mentionlow', 10)

        client = self._client_as(author)
        resp = client.get(f'/boards/{gated}/new')  # warm CSRF/session
        import re as _re
        token_m = _re.search(r'name="csrf_token" value="([^"]+)"',
                             resp.get_data(as_text=True))
        client.post(f'/boards/{gated}/new', data={
            'csrf_token': token_m.group(1) if token_m else '',
            'subject': 'Mention test', 'content': f'hey @mentionlow check this out',
        })
        from anetbbs.models import Notification
        with self.app.app_context():
            self.assertIsNone(
                Notification.query.filter_by(
                    user_id=mentioned_low, kind='mention').first(),
                'a below-level mentioned user must not be notified of '
                'restricted board content')

    def test_mention_notification_delivered_for_user_meeting_board_level(self):
        gated = self._make_board('MentionAllowedGated', min_access_level=50)
        author = self._make_user('mentionallowedauthor', 100)
        mentioned_high = self._make_user('mentionallowedhigh', 60)

        client = self._client_as(author)
        resp = client.get(f'/boards/{gated}/new')
        import re as _re
        token_m = _re.search(r'name="csrf_token" value="([^"]+)"',
                             resp.get_data(as_text=True))
        client.post(f'/boards/{gated}/new', data={
            'csrf_token': token_m.group(1) if token_m else '',
            'subject': 'Mention allowed test',
            'content': 'hey @mentionallowedhigh check this out',
        })
        from anetbbs.models import Notification
        with self.app.app_context():
            self.assertIsNotNone(
                Notification.query.filter_by(
                    user_id=mentioned_high, kind='mention').first())

    # ---- posting flood protection ----

    def test_board_post_rate_limit_blocks_after_threshold(self):
        pub = self._make_board('FloodPublic', min_access_level=0)
        user = self._make_user('flooduser', 10)
        client = self._client_as(user)

        resp = client.get(f'/boards/{pub}/new')
        import re as _re
        token_m = _re.search(r'name="csrf_token" value="([^"]+)"',
                             resp.get_data(as_text=True))
        token = token_m.group(1) if token_m else ''

        last_status = None
        for i in range(25):
            last_status = client.post(f'/boards/{pub}/new', data={
                'csrf_token': token,
                'subject': f'Flood {i}', 'content': 'x' * 20,
            }).status_code
        self.assertEqual(last_status, 429,
                         'unbounded posting must eventually be rate-limited')


if __name__ == '__main__':
    unittest.main()
