"""Regression tests for ANetBBS Pulse (anetbbs/web/pulse.py), the
read-only mobile sysop status dashboard.

Covers: admin gating on every route, the JSON status payload shape,
the manifest/service-worker routes, private-cache headers, and the
per-section resilience fix -- a single unhealthy table (e.g. mid-
migration, or briefly locked) must degrade that one section to an
empty/zero result instead of 500ing the whole dashboard.
"""
import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class PulseDashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.pulse_dashboard_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import (Board, CallerLog, NodeActivity, Post,
                                    User, UserSession, db)
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()
            admin = User(username='pulseadmin', email='pulseadmin@example.com',
                        is_admin=True, is_active=True)
            admin.set_password('adminpassword123')
            plain = User(username='pulseplain', email='pulseplain@example.com',
                        is_active=True)
            plain.set_password('plainpassword123')
            db.session.add_all([admin, plain])
            db.session.commit()
            cls.admin_id = admin.id
            cls.plain_id = plain.id

            board = Board(name='Pulse Test Board')
            db.session.add(board)
            db.session.commit()
            db.session.add(Post(board_id=board.id, author_id=admin.id,
                                subject='hi', content='hello'))
            db.session.add(CallerLog(user_id=plain.id, username='pulseplain',
                                     service='telnet', duration_seconds=42))
            db.session.add(NodeActivity(
                slot=1, user_id=plain.id, username='pulseplain',
                protocol='ssh', page='Main Menu', action='browsing',
                last_seen=datetime.utcnow()))
            db.session.add(UserSession(
                user_id=admin.id, session_key='pulsetestkey',
                last_seen=datetime.utcnow(), page='/admin/pulse/'))
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

    # -- Access control ----------------------------------------------

    def test_index_requires_login(self):
        client = self.app.test_client()
        resp = client.get('/admin/pulse/')
        self.assertIn(resp.status_code, (302, 401))

    def test_status_api_requires_login(self):
        client = self.app.test_client()
        resp = client.get('/admin/pulse/api/status')
        self.assertIn(resp.status_code, (302, 401))

    def test_non_admin_is_denied_index(self):
        client = self._client_as(self.plain_id)
        resp = client.get('/admin/pulse/')
        self.assertIn(resp.status_code, (302, 403))

    def test_non_admin_is_denied_status_api(self):
        client = self._client_as(self.plain_id)
        resp = client.get('/admin/pulse/api/status')
        self.assertIn(resp.status_code, (302, 403))

    def test_non_admin_is_denied_manifest_and_sw(self):
        client = self._client_as(self.plain_id)
        for path in ('/admin/pulse/manifest.webmanifest', '/admin/pulse/sw.js'):
            resp = client.get(path)
            self.assertIn(resp.status_code, (302, 403), path)

    # -- Admin happy path ----------------------------------------------

    def test_admin_sees_index(self):
        client = self._client_as(self.admin_id)
        resp = client.get('/admin/pulse/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'pulse', resp.data.lower())

    def test_status_api_shape(self):
        client = self._client_as(self.admin_id)
        resp = client.get('/admin/pulse/api/status')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        for key in ('ok', 'updated', 'version', 'bbs_name', 'host',
                   'summary', 'services', 'metrics', 'totals', 'nodes',
                   'web_users', 'recent_callers'):
            self.assertIn(key, data)
        self.assertGreaterEqual(data['totals']['boards'], 1)
        self.assertGreaterEqual(data['totals']['posts'], 1)
        self.assertEqual(data['totals']['terminal_online'], len(data['nodes']))
        self.assertTrue(any(n['username'] == 'pulseplain' for n in data['nodes']))
        self.assertTrue(any(w['last_seen'] for w in data['web_users']))
        self.assertTrue(any(c['username'] == 'pulseplain'
                            for c in data['recent_callers']))

    def test_status_api_never_leaks_ip_or_peer(self):
        """Pulse's stated privacy guarantee: caller IP and peer address
        are never in the payload, even though CallerLog/NodeActivity
        rows carry them internally."""
        client = self._client_as(self.admin_id)
        resp = client.get('/admin/pulse/api/status')
        body = resp.get_data(as_text=True)
        self.assertNotIn('ip_address', body)
        self.assertNotIn('"peer"', body)

    def test_status_api_cache_headers(self):
        client = self._client_as(self.admin_id)
        resp = client.get('/admin/pulse/api/status')
        self.assertIn('no-store', resp.headers.get('Cache-Control', ''))

    def test_manifest_route(self):
        client = self._client_as(self.admin_id)
        resp = client.get('/admin/pulse/manifest.webmanifest')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data['start_url'], '/admin/pulse/')
        self.assertEqual(data['scope'], '/admin/pulse/')
        icon_srcs = [icon['src'] for icon in data['icons']]
        self.assertIn('/static/pulse/icon.svg', icon_srcs)
        self.assertIn('/static/pulse/apple-touch-icon.png', icon_srcs)

    def test_service_worker_route(self):
        client = self._client_as(self.admin_id)
        resp = client.get('/admin/pulse/sw.js')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get('Service-Worker-Allowed'),
                         '/admin/pulse/')

    # -- Resilience: fix for the shared-endpoint 500 gap -----------------

    def test_status_api_survives_node_activity_failure(self):
        """NodeActivity.query blowing up (mid-migration, locked table,
        anything) must degrade to an empty nodes list, not 500 the
        whole dashboard -- the same guarantee CallerLog already had.

        Patching Model.query needs a live app context (Flask-SQLAlchemy's
        query descriptor resolves against current_app), so the patch and
        the request both run inside one explicit app_context() push.
        """
        from anetbbs.models import NodeActivity
        client = self._client_as(self.admin_id)
        with self.app.app_context():
            with patch.object(NodeActivity, 'query') as mock_query:
                mock_query.filter.side_effect = RuntimeError('table is locked')
                resp = client.get('/admin/pulse/api/status')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data['nodes'], [])
        self.assertEqual(data['totals']['terminal_online'], 0)

    def test_status_api_survives_user_session_failure(self):
        from anetbbs.models import UserSession
        client = self._client_as(self.admin_id)
        with self.app.app_context():
            with patch.object(UserSession, 'query') as mock_query:
                mock_query.filter.side_effect = RuntimeError('table is locked')
                resp = client.get('/admin/pulse/api/status')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data['web_users'], [])
        self.assertEqual(data['totals']['web_online'], 0)

    def test_status_api_survives_totals_failure(self):
        """User/Board/Post counts failing must not take down the
        sections that already succeeded (nodes/web_users/recent_callers).

        Patches Query.count() specifically, not the whole User.query
        descriptor -- Flask-Login's own user_loader calls
        User.query.get() on every request, so replacing the descriptor
        wholesale would break login itself (a test-harness artifact,
        not a real pulse.py bug) and produce a 302-to-login instead of
        exercising the actual failure path this test is for.
        """
        from anetbbs.models import User
        client = self._client_as(self.admin_id)
        with self.app.app_context():
            query_cls = type(User.query)
            with patch.object(query_cls, 'count',
                              side_effect=RuntimeError('db unavailable')):
                resp = client.get('/admin/pulse/api/status')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data['totals']['users'], 0)
        self.assertEqual(data['totals']['boards'], 0)
        # nodes/recent_callers were queried independently and must
        # still be populated.
        self.assertTrue(any(n['username'] == 'pulseplain' for n in data['nodes']))


if __name__ == '__main__':
    unittest.main()
