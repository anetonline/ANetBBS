"""Route-level tests for the new "Poll Now" button on downstream BinkP
nodes (hub_admin.poll_binkp_node) -- covers the request/response layer;
the actual poll mechanics (_do_poll_node, ack gating, network
resolution) are covered by tests/test_poller_node_poll_now.py.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class PollNodeRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.hub_admin_pollnode_route_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['REGISTRY_MODE_ENABLED'] = True
        with cls.app.app_context():
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _admin_client(self, username='pollnode_admin'):
        from anetbbs.models import db, User
        with self.app.app_context():
            u = User.query.filter_by(username=username).first()
            if not u:
                u = User(username=username, is_admin=True,
                        email=f'{username}@example.com')
                u.set_password('x')
                db.session.add(u)
                db.session.commit()
            uid = u.id
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(uid)
            sess['_fresh'] = True
        return client

    def _make_node(self, name, ftn, binkp_host=None):
        from anetbbs.models import db, EchomailNetwork, BinkPNode
        net = EchomailNetwork(name=f'{name}Net', network_type='binkp',
                              our_address='1:1/1', hub_address='1:1/1',
                              is_active=True)
        db.session.add(net)
        db.session.commit()
        node = BinkPNode(name=name, ftn_address=ftn, password='x',
                         network_id=net.id, binkp_host=binkp_host)
        db.session.add(node)
        db.session.commit()
        return node.id

    def test_detail_page_shows_disabled_button_without_host(self):
        with self.app.app_context():
            node_id = self._make_node('NoHost', '1:1/50')
        client = self._admin_client()
        r = client.get(f'/admin/echomail/hub/binkp/{node_id}')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'disabled', r.data)
        self.assertIn(b'Poll Now', r.data)

    def test_detail_page_shows_enabled_button_with_host(self):
        with self.app.app_context():
            node_id = self._make_node('HasHost', '1:1/51', binkp_host='peer.example.test')
        client = self._admin_client()
        r = client.get(f'/admin/echomail/hub/binkp/{node_id}')
        self.assertEqual(r.status_code, 200)
        self.assertIn(f'/admin/echomail/hub/binkp/{node_id}/poll'.encode(), r.data)

    def test_poll_without_host_flashes_warning_and_does_not_start_thread(self):
        with self.app.app_context():
            node_id = self._make_node('NoHostPoll', '1:1/52')
        client = self._admin_client()
        with patch('threading.Thread') as mock_thread:
            r = client.post(f'/admin/echomail/hub/binkp/{node_id}/poll',
                            follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'no BinkP host configured', r.data)
        mock_thread.assert_not_called()

    def test_poll_with_host_triggers_background_poll(self):
        with self.app.app_context():
            node_id = self._make_node('HasHostPoll', '1:1/53', binkp_host='peer.example.test')
        client = self._admin_client()
        with patch('anetbbs.echomail.poller.poll_node_now') as mock_poll:
            r = client.post(f'/admin/echomail/hub/binkp/{node_id}/poll',
                            follow_redirects=True)
            # The route spawns a real daemon thread -- give it a moment to
            # run the (mocked, instant) target before asserting.
            import time
            for _ in range(50):
                if mock_poll.called:
                    break
                time.sleep(0.02)
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Poll of HasHostPoll triggered', r.data)
        mock_poll.assert_called_once()
        self.assertEqual(mock_poll.call_args[0][1], node_id)


if __name__ == '__main__':
    unittest.main()
