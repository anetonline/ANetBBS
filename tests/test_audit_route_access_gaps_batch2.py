"""Regression tests for four real access-control gaps found in a
pre-release audit of routes with no @login_required/admin decorator
(each verified independently against the code, not just an audit
agent's claim):

1. votes.py's vote_tally() (GET) never called _can_vote_on() the way
   cast_vote() (POST) does -- a private netmail/PM's vote tally was
   readable by anyone who could guess its id.
2. leaderboard.py's index() built top_boards/top_posters/top_reactors
   with zero Board.min_access_level filtering -- restricted/sysop-only
   board names and activity leaked to any visitor.
3/4. games.py's scores() and scoreboard() routes skipped the module's
   own _game_accessible() gate that detail()/play()/etc. already use --
   a level-gated game's scores were visible/resolvable by id anyway.
5. file_areas.py's fetch_shared() (anonymous share-link download) never
   consulted the daily file quota -- a user could self-issue a share
   link for a file they can already see and re-download it anonymously
   to bypass their own quota.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class AuditRouteAccessGapsBatch2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.audit_route_gaps_batch2_test.db')
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

    def _make_user(self, username, access_level=10, is_admin=False):
        from anetbbs.models import db, User
        u = User(username=username, email=f'{username}@example.com',
                password_hash='x', is_admin=is_admin, access_level=access_level)
        db.session.add(u)
        db.session.commit()
        return u

    def _client_as(self, user_id=None):
        client = self.app.test_client()
        if user_id is not None:
            with client.session_transaction() as sess:
                sess['_user_id'] = str(user_id)
        return client

    # --- 1. votes.py ------------------------------------------------

    def test_vote_tally_hides_private_pm_from_non_party(self):
        from anetbbs.models import db, PrivateMessage, MessageVote

        with self.app.app_context():
            sender = self._make_user('votepm_sender')
            recipient = self._make_user('votepm_recipient')
            snoop = self._make_user('votepm_snoop')
            pm = PrivateMessage(sender_id=sender.id, recipient_id=recipient.id,
                                subject='secret', body='secret body')
            db.session.add(pm)
            db.session.commit()
            db.session.add(MessageVote(user_id=recipient.id, message_type='pm',
                                       message_id=pm.id, value=1))
            db.session.commit()
            pm_id = pm.id
            snoop_id = snoop.id

        # Anonymous
        resp = self._client_as(None).get(f'/api/vote/tally?type=pm&id={pm_id}')
        self.assertEqual(resp.status_code, 404)

        # Logged in, but not a party to the PM
        resp = self._client_as(snoop_id).get(f'/api/vote/tally?type=pm&id={pm_id}')
        self.assertEqual(resp.status_code, 404)

    def test_vote_tally_still_works_for_a_party_to_the_pm(self):
        from anetbbs.models import db, PrivateMessage, MessageVote

        with self.app.app_context():
            sender = self._make_user('votepm_sender2')
            recipient = self._make_user('votepm_recipient2')
            pm = PrivateMessage(sender_id=sender.id, recipient_id=recipient.id,
                                subject='secret2', body='secret body 2')
            db.session.add(pm)
            db.session.commit()
            db.session.add(MessageVote(user_id=recipient.id, message_type='pm',
                                       message_id=pm.id, value=1))
            db.session.commit()
            pm_id = pm.id
            recipient_id = recipient.id

        resp = self._client_as(recipient_id).get(f'/api/vote/tally?type=pm&id={pm_id}')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()['up'], 1)

    def test_vote_tally_public_post_still_works_anonymously(self):
        from anetbbs.models import db, Board, Post

        with self.app.app_context():
            author = self._make_user('votepost_author')
            board = Board(name='Vote Test Board', min_access_level=0)
            db.session.add(board)
            db.session.commit()
            post = Post(board_id=board.id, author_id=author.id,
                       subject='s', content='c')
            db.session.add(post)
            db.session.commit()
            post_id = post.id

        resp = self._client_as(None).get(f'/api/vote/tally?type=post&id={post_id}')
        self.assertEqual(resp.status_code, 200)

    # --- 2. leaderboard.py -------------------------------------------

    def test_leaderboard_excludes_restricted_board(self):
        from anetbbs.models import db, Board, Post

        with self.app.app_context():
            author = self._make_user('lb_author')
            public_board = Board(name='LB Public Board', min_access_level=0)
            restricted_board = Board(name='LB Restricted Board', min_access_level=999)
            db.session.add_all([public_board, restricted_board])
            db.session.commit()
            db.session.add_all([
                Post(board_id=public_board.id, author_id=author.id,
                    subject='p1', content='c'),
                Post(board_id=restricted_board.id, author_id=author.id,
                    subject='p2', content='c'),
            ])
            db.session.commit()

        resp = self._client_as(None).get('/leaderboard/?period=all')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn('LB Public Board', body)
        self.assertNotIn('LB Restricted Board', body)

    # --- 3/4. games.py -------------------------------------------------

    def test_games_scores_excludes_gated_game(self):
        from anetbbs.models import db, Game, GameScore

        with self.app.app_context():
            player = self._make_user('gs_player')
            public_game = Game(name='GS Public Game', slug='gs-public-game',
                               game_type='builtin_web', min_access_level=0,
                               is_active=True)
            gated_game = Game(name='GS Gated Game', slug='gs-gated-game',
                              game_type='builtin_web', min_access_level=999,
                              is_active=True)
            db.session.add_all([public_game, gated_game])
            db.session.commit()
            db.session.add_all([
                GameScore(game_id=public_game.id, user_id=player.id, score=100),
                GameScore(game_id=gated_game.id, user_id=player.id, score=200),
            ])
            db.session.commit()
            gated_id = gated_game.id

        resp = self._client_as(None).get('/games/scores')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn('GS Public Game', body)
        self.assertNotIn('GS Gated Game', body)

        # Directly requesting the gated game's id must not leak its scores.
        resp = self._client_as(None).get(f'/games/scores?game_id={gated_id}')
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn('GS Gated Game', resp.get_data(as_text=True))

    def test_games_scoreboard_excludes_gated_game(self):
        from anetbbs.models import db, Game, GameScore

        with self.app.app_context():
            player = self._make_user('scb_player')
            public_game = Game(name='SCB Public Game', slug='scb-public-game',
                               game_type='builtin_web', min_access_level=0,
                               is_active=True)
            gated_game = Game(name='SCB Gated Game', slug='scb-gated-game',
                              game_type='builtin_web', min_access_level=999,
                              is_active=True)
            db.session.add_all([public_game, gated_game])
            db.session.commit()
            db.session.add_all([
                GameScore(game_id=public_game.id, user_id=player.id, score=100),
                GameScore(game_id=gated_game.id, user_id=player.id, score=200),
            ])
            db.session.commit()

        resp = self._client_as(None).get('/games/scoreboard')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn('SCB Public Game', body)
        self.assertNotIn('SCB Gated Game', body)

    # --- 5. file_areas.py fetch_shared() quota -------------------------

    def test_fetch_shared_respects_creator_quota(self):
        import secrets
        from anetbbs.models import db, FileArea, SharedFileLink, FileQuotaTier

        work_dir = tempfile.mkdtemp(prefix='shared_quota_test_')
        self.addCleanup(lambda: __import__('shutil').rmtree(work_dir, ignore_errors=True))
        fname = 'quota_share.bin'
        with open(os.path.join(work_dir, fname), 'wb') as f:
            f.write(b'x' * 1000)

        with self.app.app_context():
            creator = self._make_user('share_quota_creator', access_level=10)
            db.session.add(FileQuotaTier(min_access_level=0, daily_quota_bytes=500))
            db.session.commit()
            area = FileArea(tag='SHAREQUOTA', name='Share Quota Area',
                            storage_path=work_dir, is_active=True,
                            min_access_level=0)
            db.session.add(area)
            db.session.commit()
            token = secrets.token_urlsafe(16)
            link = SharedFileLink(token=token, created_by_id=creator.id,
                                  file_area_id=area.id, filename=fname)
            db.session.add(link)
            db.session.commit()

        # File is 1000 bytes, quota is 500 -- first fetch must be rejected.
        resp = self._client_as(None).get(f'/file-areas/shared/{token}')
        self.assertEqual(resp.status_code, 429)

    def test_fetch_shared_allowed_within_creator_quota(self):
        import secrets
        from anetbbs.models import db, FileArea, SharedFileLink, FileQuotaTier

        work_dir = tempfile.mkdtemp(prefix='shared_quota_ok_test_')
        self.addCleanup(lambda: __import__('shutil').rmtree(work_dir, ignore_errors=True))
        fname = 'quota_share_ok.bin'
        with open(os.path.join(work_dir, fname), 'wb') as f:
            f.write(b'x' * 100)

        with self.app.app_context():
            creator = self._make_user('share_quota_creator_ok', access_level=10)
            db.session.add(FileQuotaTier(min_access_level=1, daily_quota_bytes=500))
            db.session.commit()
            area = FileArea(tag='SHAREQUOTAOK', name='Share Quota Area OK',
                            storage_path=work_dir, is_active=True,
                            min_access_level=0)
            db.session.add(area)
            db.session.commit()
            token = secrets.token_urlsafe(16)
            link = SharedFileLink(token=token, created_by_id=creator.id,
                                  file_area_id=area.id, filename=fname)
            db.session.add(link)
            db.session.commit()

        resp = self._client_as(None).get(f'/file-areas/shared/{token}')
        self.assertEqual(resp.status_code, 200)


if __name__ == '__main__':
    unittest.main()
