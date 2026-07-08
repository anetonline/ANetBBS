"""Tests confirming all 8 Webhook event types actually fire, not just
`shout`. Before this change, `post`/`bulletin`/`login`/`achievement`/
`broadcast`/`sysop_page`/`echomail` were selectable in the admin UI and
listed in the Webhook model's docstring, but nothing in the codebase
ever called fire() for them -- a webhook configured for any of those
would sit there showing "never called" forever.

Each test patches `anetbbs.features.webhooks.fire` (the function is
imported locally inside each call site with `from ...webhooks import
fire`, so patching the source module intercepts it correctly) and
asserts it gets called with the right event name when the
corresponding action happens, rather than doing a real HTTP round
trip (fire() spins up a background thread for actual delivery, which
would make these tests flaky and slow for no extra coverage)."""
import os
import sys
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class WebhooksAllEventsWiredTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.webhooks_all_events_test.db')
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

    def _make_user(self, username, is_admin=False):
        from anetbbs.models import db, User
        with self.app.app_context():
            u = User.query.filter_by(username=username).first()
            if not u:
                u = User(username=username, is_admin=is_admin, access_level=255 if is_admin else 10,
                        email=f'{username}@example.com')
                u.set_password('x')
                db.session.add(u)
                db.session.commit()
            return u.id

    def _client_as(self, user_id):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(user_id)
            sess['_fresh'] = True
        return client

    def test_new_board_post_fires_post_event(self):
        from anetbbs.models import db, Board
        uid = self._make_user('webhooktest_poster')
        with self.app.app_context():
            board = Board(name='WebhookTestBoard', description='x')
            db.session.add(board)
            db.session.commit()
            board_id = board.id

        client = self._client_as(uid)
        with patch('anetbbs.features.webhooks.fire') as mock_fire:
            resp = client.post(f'/boards/{board_id}/new', data={
                'subject': 'Webhook test subject', 'content': 'Webhook test body',
            }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        events = [c.args[0] for c in mock_fire.call_args_list]
        self.assertIn('post', events)

    def test_reply_fires_post_event(self):
        from anetbbs.models import db, Board, Post
        uid = self._make_user('webhooktest_replier')
        with self.app.app_context():
            board = Board(name='WebhookTestReplyBoard', description='x')
            db.session.add(board)
            db.session.flush()
            root = Post(board_id=board.id, author_id=uid, subject='Root', content='root body')
            db.session.add(root)
            db.session.commit()
            root_id = root.id

        client = self._client_as(uid)
        with patch('anetbbs.features.webhooks.fire') as mock_fire:
            resp = client.post(f'/boards/post/{root_id}/reply', data={
                'content': 'Webhook test reply body',
            }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        events = [c.args[0] for c in mock_fire.call_args_list]
        self.assertIn('post', events)

    def test_bulletin_fires_bulletin_event(self):
        uid = self._make_user('webhooktest_admin', is_admin=True)
        client = self._client_as(uid)
        with patch('anetbbs.features.webhooks.fire') as mock_fire:
            resp = client.post('/admin/bulletins/add', data={
                'title': 'Webhook test bulletin', 'content': 'body',
                'is_pinned': '', 'expires_at': '',
            }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        events = [c.args[0] for c in mock_fire.call_args_list]
        self.assertIn('bulletin', events)

    def test_web_login_fires_login_event(self):
        from anetbbs.models import db, User
        with self.app.app_context():
            u = User.query.filter_by(username='webhooktest_loginuser').first()
            if not u:
                u = User(username='webhooktest_loginuser', is_admin=False, access_level=10,
                        email='webhooktest_loginuser@example.com', is_verified=True)
                u.set_password('loginpass123')
                db.session.add(u)
                db.session.commit()

        client = self.app.test_client()
        with patch('anetbbs.features.webhooks.fire') as mock_fire:
            resp = client.post('/auth/login', data={
                'username': 'webhooktest_loginuser', 'password': 'loginpass123',
            }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        events = [c.args[0] for c in mock_fire.call_args_list]
        self.assertIn('login', events)

    def test_achievement_award_fires_achievement_event(self):
        from anetbbs.models import db, User
        from anetbbs.features import achievements

        with self.app.app_context():
            u = User(username='webhooktest_achiever', is_admin=False, access_level=10,
                     email='webhooktest_achiever@example.com')
            u.set_password('x')
            db.session.add(u)
            db.session.commit()
            user_id = u.id

        with self.app.app_context():
            u = User.query.get(user_id)
            with patch('anetbbs.features.webhooks.fire') as mock_fire:
                # _check_first_login just checks login_count >= 1 -- force
                # it true without needing a real login flow.
                u.login_count = 1
                db.session.commit()
                newly = achievements.check_for_user(u)
            self.assertIn('first_login', newly)
            events = [c.args[0] for c in mock_fire.call_args_list]
            self.assertIn('achievement', events)

    def test_broadcast_fires_broadcast_event(self):
        from anetbbs.features.multinode import broadcast
        with patch('anetbbs.features.webhooks.fire') as mock_fire:
            broadcast('webhooktest_broadcaster', 'hello everyone', kind='msg')
        events = [c.args[0] for c in mock_fire.call_args_list]
        self.assertIn('broadcast', events)

    def test_sysop_page_fires_sysop_page_event(self):
        from anetbbs.models import db, User
        from anetbbs.features import sysop_paging

        with self.app.app_context():
            u = User.query.filter_by(username='webhooktest_pager').first()
            if not u:
                u = User(username='webhooktest_pager', is_admin=False, access_level=10,
                        email='webhooktest_pager@example.com')
                u.set_password('x')
                db.session.add(u)
                db.session.commit()
            user_id = u.id

        os.environ['DATABASE_URL'] = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        try:
            with patch('anetbbs.features.webhooks.fire') as mock_fire:
                page_id = sysop_paging.page_sysop(user_id, 'help please', service='telnet')
            self.assertGreater(page_id, 0)
            events = [c.args[0] for c in mock_fire.call_args_list]
            self.assertIn('sysop_page', events)
        finally:
            os.environ.pop('DATABASE_URL', None)

    def test_inbound_echomail_import_fires_echomail_event(self):
        from anetbbs.models import db, EchomailNetwork, EchoArea
        from anetbbs.echomail.poller import _import_message

        with self.app.app_context():
            net = EchomailNetwork(name='WebhookTestEchoNet', network_type='binkp')
            db.session.add(net)
            db.session.flush()
            area = EchoArea(network_id=net.id, tag='WEBHOOK.TEST', name='Webhook Test',
                            is_active=True, is_subscribed=True)
            db.session.add(area)
            db.session.commit()

            msg_data = {
                'area_tag': 'WEBHOOK.TEST', 'msg_id': 'WEBHOOKTEST_1',
                'from_name': 'Tester', 'to_name': 'All',
                'subject': 'Webhook echomail test', 'body': 'hello',
            }
            with patch('anetbbs.features.webhooks.fire') as mock_fire:
                result = _import_message(net, msg_data)
            self.assertEqual(result, 1)
            events = [c.args[0] for c in mock_fire.call_args_list]
            self.assertIn('echomail', events)


if __name__ == '__main__':
    unittest.main()
