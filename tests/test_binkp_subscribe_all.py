"""Tests for the BinkP node "Subscribe to All" bulk action -- BinkP had
no bulk-subscribe at all before this, only one-at-a-time via
binkp_subscribe(). Mirrors tests/test_qwk_subscribe_all.py's shape,
including requiring an explicit `network_ids` selection (checkboxes in
the admin UI) rather than silently sweeping every BinkP network on the
install.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class BinkpSubscribeAllTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.binkp_subscribe_all_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
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

    def _client(self):
        from anetbbs.models import User
        with self.app.app_context():
            admin = User.query.filter_by(username='binkpsuballtest').first()
            if not admin:
                from anetbbs.models import db
                admin = User(username='binkpsuballtest', is_admin=True,
                            access_level=255,
                            email='binkpsuballtest@example.com')
                admin.set_password('x')
                db.session.add(admin)
                db.session.commit()
            admin_id = admin.id
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(admin_id)
            sess['_fresh'] = True
        return client

    def test_subscribe_all_only_covers_selected_binkp_network_not_qwk(self):
        from anetbbs.models import db, EchomailNetwork, EchoArea, BinkPNode, EchoAreaNode
        with self.app.app_context():
            binkp_net = EchomailNetwork(name='TestBinkpNet', network_type='binkp')
            qwk_net = EchomailNetwork(name='TestQWKNet', network_type='qwk')
            db.session.add_all([binkp_net, qwk_net])
            db.session.flush()

            db.session.add_all([
                EchoArea(network_id=binkp_net.id, tag='BINKP.ONE', name='B1', is_active=True),
                EchoArea(network_id=binkp_net.id, tag='BINKP.TWO', name='B2', is_active=True),
                EchoArea(network_id=qwk_net.id, tag='QWK.ONE', name='Q1', is_active=True),
            ])
            node = BinkPNode(ftn_address='1:2/3.4', name='Test', password='x',
                             is_active=True)
            db.session.add(node)
            db.session.commit()
            node_id = node.id
            binkp_net_id = binkp_net.id

        client = self._client()
        resp = client.post(f'/admin/echomail/hub/binkp/{node_id}/subscribe-all',
                           data={'network_ids': [binkp_net_id]},
                           follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            subs = EchoAreaNode.query.filter_by(node_id=node_id).all()
            tags = {EchoArea.query.get(s.echo_area_id).tag for s in subs}
            self.assertEqual(tags, {'BINKP.ONE', 'BINKP.TWO'})

    def test_subscribe_all_requires_at_least_one_network_selected(self):
        from anetbbs.models import db, EchomailNetwork, EchoArea, BinkPNode, EchoAreaNode
        with self.app.app_context():
            net = EchomailNetwork(name='NoSelectionBinkpNet', network_type='binkp')
            db.session.add(net)
            db.session.flush()
            db.session.add(EchoArea(network_id=net.id, tag='NOSEL.ONE', name='N1', is_active=True))
            node = BinkPNode(ftn_address='1:2/3.5', name='Test', password='x', is_active=True)
            db.session.add(node)
            db.session.commit()
            node_id = node.id

        client = self._client()
        resp = client.post(f'/admin/echomail/hub/binkp/{node_id}/subscribe-all',
                           follow_redirects=True)  # no network_ids at all
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Pick at least one network', resp.data)

        with self.app.app_context():
            self.assertEqual(EchoAreaNode.query.filter_by(node_id=node_id).count(), 0)

    def test_subscribe_all_does_not_duplicate_existing_subscriptions(self):
        from anetbbs.models import db, EchomailNetwork, EchoArea, BinkPNode, EchoAreaNode
        with self.app.app_context():
            net = EchomailNetwork(name='NoDupBinkpNet', network_type='binkp')
            db.session.add(net)
            db.session.flush()
            area1 = EchoArea(network_id=net.id, tag='DUP.ONE', name='D1', is_active=True)
            area2 = EchoArea(network_id=net.id, tag='DUP.TWO', name='D2', is_active=True)
            db.session.add_all([area1, area2])
            db.session.flush()
            node = BinkPNode(ftn_address='1:2/3.6', name='Test', password='x', is_active=True)
            db.session.add(node)
            db.session.flush()
            db.session.add(EchoAreaNode(node_id=node.id, echo_area_id=area1.id))
            db.session.commit()
            node_id = node.id
            net_id = net.id

        client = self._client()
        client.post(f'/admin/echomail/hub/binkp/{node_id}/subscribe-all',
                    data={'network_ids': [net_id]},
                    follow_redirects=True)

        with self.app.app_context():
            subs = EchoAreaNode.query.filter_by(node_id=node_id).all()
            self.assertEqual(len(subs), 2)
            area_ids = [s.echo_area_id for s in subs]
            self.assertEqual(len(area_ids), len(set(area_ids)),
                             'duplicate echo_area_id subscription created')
            tags = {EchoArea.query.get(s.echo_area_id).tag for s in subs}
            self.assertEqual(tags, {'DUP.ONE', 'DUP.TWO'})


if __name__ == '__main__':
    unittest.main()
