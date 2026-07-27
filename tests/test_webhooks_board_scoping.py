"""Regression tests for a deferred finding from a full message-boards
security audit, fixed on Jerry's explicit follow-up request: 'post'
webhooks fired for EVERY board with no way to scope one to a single
board -- an admin wiring up a public "new post" Discord/Slack mirror
also silently mirrored sysop-only/VIP-restricted board content to that
external channel. Webhook.board_id (NULL = all boards, the pre-existing
behavior) closes this; only meaningful for event='post'.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class WebhookBoardScopingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.webhook_board_scoping_test.db')
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
        # Webhook rows from a prior test method would otherwise still be
        # 'post'/active in the shared class-level DB and get included in
        # the next test's fire() query, over-counting Thread calls.
        from anetbbs.models import db, Webhook
        with self.app.app_context():
            Webhook.query.delete()
            db.session.commit()

    def _make_boards(self, name_a, name_b):
        from anetbbs.models import db, Board
        with self.app.app_context():
            a = Board(name=name_a, description='x', is_active=True)
            b = Board(name=name_b, description='x', is_active=True)
            db.session.add_all([a, b])
            db.session.commit()
            return a.id, b.id

    def _make_webhook(self, event='post', board_id=None, url='http://example.test/hook'):
        from anetbbs.models import db, Webhook
        with self.app.app_context():
            w = Webhook(name='TestHook', url=url, event=event,
                       is_active=True, board_id=board_id)
            db.session.add(w)
            db.session.commit()
            return w.id

    # ---- fire()'s filtering logic ----

    def test_fire_skips_webhook_scoped_to_a_different_board(self):
        from anetbbs.features.webhooks import fire
        board_a, board_b = self._make_boards('ScopeSkipA', 'ScopeSkipB')
        self._make_webhook(event='post', board_id=board_a)

        with self.app.app_context(), \
             patch('anetbbs.features.webhooks.threading.Thread') as mock_thread:
            fire('post', {'board_id': board_b, 'user': 'x',
                          'subject': 's', 'content': 'c'})
        mock_thread.assert_not_called()

    def test_fire_delivers_webhook_scoped_to_the_matching_board(self):
        from anetbbs.features.webhooks import fire
        board_a, _ = self._make_boards('ScopeMatchA', 'ScopeMatchB')
        self._make_webhook(event='post', board_id=board_a)

        with self.app.app_context(), \
             patch('anetbbs.features.webhooks.threading.Thread') as mock_thread:
            fire('post', {'board_id': board_a, 'user': 'x',
                          'subject': 's', 'content': 'c'})
        mock_thread.assert_called_once()

    def test_fire_delivers_unscoped_webhook_for_any_board(self):
        """board_id=None (the default, and the pre-existing behavior for
        anyone who already has a 'post' webhook configured) must keep
        firing for every board."""
        from anetbbs.features.webhooks import fire
        board_a, board_b = self._make_boards('ScopeAllA', 'ScopeAllB')
        self._make_webhook(event='post', board_id=None)

        with self.app.app_context(), \
             patch('anetbbs.features.webhooks.threading.Thread') as mock_thread:
            fire('post', {'board_id': board_a, 'user': 'x',
                          'subject': 's', 'content': 'c'})
            fire('post', {'board_id': board_b, 'user': 'x',
                          'subject': 's', 'content': 'c'})
        self.assertEqual(mock_thread.call_count, 2)

    def test_fire_ignores_board_id_for_non_post_events(self):
        """A board_id stuck on a non-'post' webhook (shouldn't happen via
        the admin form, but defend against it anyway) must never suppress
        that event -- board scoping only makes sense for 'post'."""
        from anetbbs.features.webhooks import fire
        board_a, _ = self._make_boards('ScopeIgnoreA', 'ScopeIgnoreB')
        self._make_webhook(event='login', board_id=board_a)

        with self.app.app_context(), \
             patch('anetbbs.features.webhooks.threading.Thread') as mock_thread:
            fire('login', {'user': 'x', 'service': 'web'})
        mock_thread.assert_called_once()

    # ---- admin route ----

    def _admin_client(self):
        from anetbbs.models import db, User
        with self.app.app_context():
            u = User.query.filter_by(username='webhookscopeadmin').first()
            if not u:
                u = User(username='webhookscopeadmin', is_admin=True,
                        email='webhookscopeadmin@example.com')
                u.set_password('x')
                db.session.add(u)
                db.session.commit()
            uid = u.id
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(uid)
            sess['_fresh'] = True
        return client

    def test_admin_form_stores_board_id_for_post_event(self):
        board_a, _ = self._make_boards('AdminScopeA', 'AdminScopeB')
        client = self._admin_client()
        resp = client.post('/admin/webhooks', data={
            'action': 'add', 'name': 'ScopedFromForm',
            'url': 'http://example.test/scoped', 'event': 'post',
            'board_id': str(board_a), 'is_active': 'y',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        from anetbbs.models import Webhook
        with self.app.app_context():
            w = Webhook.query.filter_by(name='ScopedFromForm').first()
            self.assertIsNotNone(w)
            self.assertEqual(w.board_id, board_a)

    def test_admin_form_ignores_board_id_for_non_post_event(self):
        board_a, _ = self._make_boards('AdminIgnoreA', 'AdminIgnoreB')
        client = self._admin_client()
        client.post('/admin/webhooks', data={
            'action': 'add', 'name': 'UnscopedLogin',
            'url': 'http://example.test/unscoped', 'event': 'login',
            'board_id': str(board_a), 'is_active': 'y',
        }, follow_redirects=True)
        from anetbbs.models import Webhook
        with self.app.app_context():
            w = Webhook.query.filter_by(name='UnscopedLogin').first()
            self.assertIsNotNone(w)
            self.assertIsNone(w.board_id,
                             'board_id must be ignored for non-post events')

    def test_admin_form_all_boards_option_leaves_board_id_null(self):
        client = self._admin_client()
        client.post('/admin/webhooks', data={
            'action': 'add', 'name': 'AllBoardsHook',
            'url': 'http://example.test/all', 'event': 'post',
            'board_id': '', 'is_active': 'y',
        }, follow_redirects=True)
        from anetbbs.models import Webhook
        with self.app.app_context():
            w = Webhook.query.filter_by(name='AllBoardsHook').first()
            self.assertIsNotNone(w)
            self.assertIsNone(w.board_id)


if __name__ == '__main__':
    unittest.main()
