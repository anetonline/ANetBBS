"""Regression test for a real live bug reported by Jerry (2026-09-01):
"Internal Server Error... I just tried to delete a user and got this
error." Root cause: User has 50+ NOT NULL foreign keys pointing at it
across the schema (GameSession.user_id, PrivateMessage.sender_id,
ChatMessage.user_id, etc.), and only two relationships (posts,
messages) have cascade='all, delete-orphan' configured. For every
other one, SQLAlchemy's default parent-delete behavior tries to NULL
out the child's foreign-key column, which a NOT NULL column rejects at
the database level -- an unhandled IntegrityError that surfaced as a
raw 500 for any user with real activity (a fresh, never-used account
has nothing to collide with, which is why this was easy to miss
testing manually).

Reproduced directly against GameSession (a real, common case -- any
user who has ever played a door game trips this) before writing the
fix, confirming the exact IntegrityError shape:
    NOT NULL constraint failed: game_sessions.user_id

Fixed by catching IntegrityError around the delete+commit in both
delete_user() and bulk_users()'s delete action, rolling back cleanly,
and pointing the sysop at the existing, already-working Ban/Deactivate
alternative instead of a raw error page. Does not attempt to solve the
much larger question of what SHOULD happen to a deleted user's
messages/posts/game history across 50+ tables -- that's a real design
decision, not something to guess at under a live-bug banner.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class DeleteUserIntegrityErrorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.delete_user_integrity_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _make_admin(self, username):
        from anetbbs.models import db, User
        u = User(username=username, email=f'{username}@example.com',
                is_admin=True, is_active=True)
        u.set_password('adminintegritytestpass123')
        db.session.add(u)
        db.session.commit()
        return u.id

    def _client_as(self, user_id):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(user_id)
            sess['_fresh'] = True
        return client

    def _make_target_with_game_session(self, username):
        """A plain user PLUS a real GameSession row -- the exact real
        shape that reproduces the NOT NULL constraint failure (this
        FK is nullable=False, with no cascade configured on the
        relationship)."""
        from anetbbs.models import db, User, Game, GameSession
        target = User(username=username, email=f'{username}@example.com')
        target.set_password('targetpass123')
        db.session.add(target)
        game = Game.query.filter_by(slug='delete-user-test-game').first()
        if game is None:
            game = Game(name='Delete User Test Game', slug='delete-user-test-game',
                       game_type='door_native')
            db.session.add(game)
        db.session.commit()
        db.session.add(GameSession(game_id=game.id, user_id=target.id, node_number=1))
        db.session.commit()
        return target.id

    def test_deleting_a_user_with_game_history_does_not_500(self):
        from anetbbs.models import User
        with self.app.app_context():
            admin_id = self._make_admin('deladmin1')
            target_id = self._make_target_with_game_session('delwithhistory1')

        client = self._client_as(admin_id)
        resp = client.post(f'/admin/users/{target_id}/delete', follow_redirects=True)
        self.assertEqual(resp.status_code, 200,
                         'must not surface a raw 500 -- the IntegrityError '
                         'must be caught')
        self.assertIn(b'related records', resp.data)
        self.assertIn(b'Ban/Deactivate', resp.data)

        with self.app.app_context():
            self.assertIsNotNone(User.query.get(target_id),
                                 'the user must NOT have been partially '
                                 'deleted -- the failed commit must roll back cleanly')

    def test_deleting_a_user_with_no_related_data_still_works_normally(self):
        """Sanity check the fix doesn't break the common/simple case --
        a fresh account with nothing referencing it must still delete
        cleanly, matching pre-fix behavior for that scenario."""
        from anetbbs.models import User, db
        with self.app.app_context():
            admin_id = self._make_admin('deladmin2')
            plain = User(username='delplain1', email='delplain1@example.com')
            plain.set_password('plainpass123')
            db.session.add(plain)
            db.session.commit()
            plain_id = plain.id

        client = self._client_as(admin_id)
        resp = client.post(f'/admin/users/{plain_id}/delete', follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'deleted', resp.data)

        with self.app.app_context():
            self.assertIsNone(User.query.get(plain_id))

    def test_bulk_delete_one_blocked_user_does_not_prevent_deleting_others(self):
        from anetbbs.models import User, db
        with self.app.app_context():
            admin_id = self._make_admin('deladmin3')
            blocked_id = self._make_target_with_game_session('delblocked1')
            plain = User(username='delplain2', email='delplain2@example.com')
            plain.set_password('plainpass123')
            db.session.add(plain)
            db.session.commit()
            plain_id = plain.id

        client = self._client_as(admin_id)
        resp = client.post('/admin/users/bulk', data={
            'action': 'delete',
            'user_ids': [str(blocked_id), str(plain_id)],
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Deleted 1', resp.data)
        self.assertIn(b'delblocked1', resp.data)

        with self.app.app_context():
            self.assertIsNotNone(User.query.get(blocked_id),
                                 'the blocked user must survive')
            self.assertIsNone(User.query.get(plain_id),
                              'the safely-deletable user must still be removed')


if __name__ == '__main__':
    unittest.main()
