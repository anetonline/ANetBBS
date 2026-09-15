"""Regression test for the expanded achievement rule set added in the
gap-analysis follow-up round (post-v1.0.77): 8 new rules tied to Game
Center scores/sessions, wiki edits, file uploads, and a higher-tier
shoutbox milestone, on top of the original 10 in
anetbbs/features/achievements.py.

Each new rule is exercised directly against real rows (not mocked) so
a check function with an ORM/import typo fails loudly here rather than
silently never firing in production.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class AchievementRulesExpansionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp_db = str(Path(__file__).resolve().parent / '.achievement_rules_test.db')
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
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)

    def setUp(self):
        from anetbbs.models import (db, User, Game, GameScore, GameSession,
                                    WikiPage, WikiRevision, FileUpload,
                                    ShoutboxPost, UserAchievement)
        self.ctx = self.app.app_context()
        self.ctx.push()
        # Clear every table a test in this file writes to, not just
        # User -- SQLite reuses primary-key ids after a delete, so a
        # fresh user in a later test can otherwise collide with an
        # earlier test's orphaned child rows (GameScore/UserAchievement/
        # etc. have no cascade-delete on their user_id FK). A real
        # instance of this was caught while writing this test file:
        # check_for_user() silently treated 'high_score' as already
        # earned for a brand-new user because a leftover
        # UserAchievement row from a prior test's (deleted) user shared
        # the same reused id.
        for model in (UserAchievement, GameScore, GameSession, WikiRevision,
                     WikiPage, FileUpload, ShoutboxPost, Game, User):
            db.session.query(model).delete()
        db.session.commit()
        self.user = User(username='badgetest', email='badgetest@example.com')
        self.user.set_password('irrelevant')
        db.session.add(self.user)
        db.session.commit()

    def tearDown(self):
        from anetbbs.models import db
        db.session.rollback()
        self.ctx.pop()

    def _award(self):
        from anetbbs.features.achievements import check_for_user
        return check_for_user(self.user)

    def test_high_score_awarded_when_user_holds_number_one(self):
        from anetbbs.models import db, Game, GameScore
        g = Game(name='Test Game', slug='ach-test-game', game_type='builtin_web')
        db.session.add(g)
        db.session.commit()
        db.session.add(GameScore(game_id=g.id, user_id=self.user.id, score=100))
        db.session.commit()
        self.assertIn('high_score', self._award())

    def test_high_score_not_awarded_when_user_is_not_top(self):
        from anetbbs.models import db, Game, GameScore, User
        g = Game(name='Test Game 2', slug='ach-test-game-2', game_type='builtin_web')
        other = User(username='otherplayer', email='other@example.com')
        other.set_password('irrelevant')
        db.session.add_all([g, other])
        db.session.commit()
        db.session.add(GameScore(game_id=g.id, user_id=self.user.id, score=10))
        db.session.add(GameScore(game_id=g.id, user_id=other.id, score=999))
        db.session.commit()
        self.assertNotIn('high_score', self._award())

    def test_game_explorer_requires_five_distinct_games(self):
        from anetbbs.models import db, Game, GameScore
        games = [Game(name=f'G{i}', slug=f'ach-explore-{i}', game_type='builtin_web')
                 for i in range(4)]
        db.session.add_all(games)
        db.session.commit()
        for g in games:
            db.session.add(GameScore(game_id=g.id, user_id=self.user.id, score=1))
        db.session.commit()
        self.assertNotIn('game_explorer', self._award())

        g5 = Game(name='G5', slug='ach-explore-5', game_type='builtin_web')
        db.session.add(g5)
        db.session.commit()
        db.session.add(GameScore(game_id=g5.id, user_id=self.user.id, score=1))
        db.session.commit()
        self.assertIn('game_explorer', self._award())

    def test_door_diver_requires_five_distinct_game_sessions(self):
        from anetbbs.models import db, Game, GameSession
        games = [Game(name=f'D{i}', slug=f'ach-door-{i}', game_type='door_native')
                 for i in range(5)]
        db.session.add_all(games)
        db.session.commit()
        for i, g in enumerate(games):
            db.session.add(GameSession(game_id=g.id, user_id=self.user.id, node_number=1))
        db.session.commit()
        self.assertIn('door_diver', self._award())

    def test_wiki_editor_and_scribe(self):
        from anetbbs.models import db, WikiPage, WikiRevision
        page = WikiPage(slug='ach-test-page', title='Test', body='hi')
        db.session.add(page)
        db.session.commit()
        db.session.add(WikiRevision(page_id=page.id, rev_num=1, title='Test',
                                    body='hi', author_id=self.user.id))
        db.session.commit()
        awarded = self._award()
        self.assertIn('wiki_editor', awarded)
        self.assertNotIn('wiki_scribe', awarded)

        for n in range(2, 11):
            db.session.add(WikiRevision(page_id=page.id, rev_num=n, title='Test',
                                        body='hi', author_id=self.user.id))
        db.session.commit()
        self.assertIn('wiki_scribe', self._award())

    def test_file_uploader_and_librarian(self):
        from anetbbs.models import db, FileUpload
        db.session.add(FileUpload(uploader_id=self.user.id, filename='a.zip',
                                  original_filename='a.zip', file_path='/tmp/a.zip'))
        db.session.commit()
        awarded = self._award()
        self.assertIn('file_uploader', awarded)
        self.assertNotIn('file_librarian', awarded)

        for i in range(2, 11):
            db.session.add(FileUpload(uploader_id=self.user.id, filename=f'{i}.zip',
                                      original_filename=f'{i}.zip',
                                      file_path=f'/tmp/{i}.zip'))
        db.session.commit()
        self.assertIn('file_librarian', self._award())

    def test_town_legend_requires_100_shouts(self):
        from anetbbs.models import db, ShoutboxPost
        for i in range(100):
            db.session.add(ShoutboxPost(user_id=self.user.id, text=f'shout {i}'))
        db.session.commit()
        self.assertIn('town_legend', self._award())

    def test_new_rules_are_idempotent_no_duplicate_award(self):
        from anetbbs.models import db, Game, GameScore, UserAchievement
        g = Game(name='Idem Game', slug='ach-idem-game', game_type='builtin_web')
        db.session.add(g)
        db.session.commit()
        db.session.add(GameScore(game_id=g.id, user_id=self.user.id, score=5))
        db.session.commit()
        first = self._award()
        second = self._award()
        self.assertIn('high_score', first)
        self.assertNotIn('high_score', second)
        rows = UserAchievement.query.filter_by(user_id=self.user.id).all()
        achievement_ids = [r.achievement_id for r in rows]
        self.assertEqual(len(achievement_ids), len(set(achievement_ids)))


if __name__ == '__main__':
    unittest.main()
