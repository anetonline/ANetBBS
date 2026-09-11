"""Regression test for a cross-tenant leak found in a full InterBBS-admin
audit: qwk_subscribe()/qwk_subscribe_all() (hub_admin.py) both refuse to
subscribe a node to an EchoArea whose network belongs to a DIFFERENT
HubIdentity than the node's own -- but binkp_subscribe()/
binkp_subscribe_all() had no such guard at all, despite BinkPNode.hub_identity_id
carrying the exact same "scopes inbound BinkP auth ... so nodes of
different hub identities can't authenticate against each other's AKA"
contract (see that column's own comment in models.py).

Impact confirmed real, not theoretical: the tosser
(echomail/tosser.py's toss_message()/toss_area_messages()) queues mail
to every EchoAreaNode row with no hub-identity filter at all -- so a
BinkP node subscribed (even by mistake, via the admin UI) to a
different hub identity's area actually receives that other network's
echomail on the next poll/toss, defeating the multi-hub-identity
isolation the feature exists to provide.

Mirrors tests/test_binkp_subscribe_all.py and
tests/test_qwk_subscribe_all.py's fixture shape.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class BinkpSubscribeCrossIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.binkp_subscribe_cross_identity_test.db')
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
            admin = User.query.filter_by(username='binkpxidtest').first()
            if not admin:
                from anetbbs.models import db
                admin = User(username='binkpxidtest', is_admin=True,
                            access_level=255,
                            email='binkpxidtest@example.com')
                admin.set_password('x')
                db.session.add(admin)
                db.session.commit()
            admin_id = admin.id
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(admin_id)
            sess['_fresh'] = True
        return client

    def _two_identities(self, tag):
        """Create a fresh pair of HubIdentity rows with slugs unique to
        this call (each test in this class shares one DB across the
        whole test run, so a fixed slug would collide on the second
        test)."""
        from anetbbs.models import db, HubIdentity
        id_a = HubIdentity(name=f'NetworkA-{tag}', slug=f'xid-neta-{tag}', is_active=True)
        id_b = HubIdentity(name=f'NetworkB-{tag}', slug=f'xid-netb-{tag}', is_active=True)
        db.session.add_all([id_a, id_b])
        db.session.commit()
        return id_a.id, id_b.id

    def test_binkp_subscribe_refuses_cross_identity_single_area(self):
        """binkp_subscribe (one-at-a-time) must refuse to subscribe a
        node to an area on a different hub identity's network -- same
        guard qwk_subscribe() already has."""
        from anetbbs.models import db, EchomailNetwork, EchoArea, BinkPNode, EchoAreaNode
        with self.app.app_context():
            id_a, id_b = self._two_identities('single')
            net_b = EchomailNetwork(name='NetB-Binkp', network_type='binkp',
                                    hub_identity_id=id_b)
            db.session.add(net_b)
            db.session.flush()
            area_b = EchoArea(network_id=net_b.id, tag='NETB.ONE', name='B1',
                              is_active=True)
            db.session.add(area_b)
            node_a = BinkPNode(ftn_address='1:9/1.1', name='NodeA', password='x',
                               is_active=True, hub_identity_id=id_a)
            db.session.add(node_a)
            db.session.commit()
            node_id = node_a.id
            area_id = area_b.id

        client = self._client()
        resp = client.post(f'/admin/echomail/hub/binkp/{node_id}/subscribe',
                           data={'area_id': area_id, 'action': 'subscribe'},
                           follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'different hub identity', resp.data)

        with self.app.app_context():
            self.assertEqual(
                EchoAreaNode.query.filter_by(node_id=node_id, echo_area_id=area_id).count(),
                0, 'node subscribed to a different hub identity\'s area -- '
                   'cross-tenant leak (tosser has no identity filter, so it '
                   'would actually receive that network\'s mail)')

    def test_binkp_subscribe_allows_same_identity_area(self):
        """Sanity check: the new guard must not block the ordinary
        same-identity (or identity-agnostic NULL) case."""
        from anetbbs.models import db, EchomailNetwork, EchoArea, BinkPNode, EchoAreaNode
        with self.app.app_context():
            id_a, _id_b = self._two_identities('samecheck')
            net_a = EchomailNetwork(name='NetA-Binkp', network_type='binkp',
                                    hub_identity_id=id_a)
            db.session.add(net_a)
            db.session.flush()
            area_a = EchoArea(network_id=net_a.id, tag='NETA.ONE', name='A1',
                              is_active=True)
            db.session.add(area_a)
            node_a = BinkPNode(ftn_address='1:9/1.2', name='NodeA2', password='x',
                               is_active=True, hub_identity_id=id_a)
            db.session.add(node_a)
            db.session.commit()
            node_id = node_a.id
            area_id = area_a.id

        client = self._client()
        resp = client.post(f'/admin/echomail/hub/binkp/{node_id}/subscribe',
                           data={'area_id': area_id, 'action': 'subscribe'},
                           follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            self.assertEqual(
                EchoAreaNode.query.filter_by(node_id=node_id, echo_area_id=area_id).count(),
                1, 'same-identity subscribe was wrongly blocked')

    def test_binkp_subscribe_all_excludes_other_identity_areas(self):
        """binkp_subscribe_all (bulk) must exclude areas belonging to a
        different hub identity's network from the bulk-add, same as
        qwk_subscribe_all() already does."""
        from anetbbs.models import db, EchomailNetwork, EchoArea, BinkPNode, EchoAreaNode
        with self.app.app_context():
            id_a, id_b = self._two_identities('bulk')
            net_a = EchomailNetwork(name='BulkNetA', network_type='binkp',
                                    hub_identity_id=id_a)
            net_b = EchomailNetwork(name='BulkNetB', network_type='binkp',
                                    hub_identity_id=id_b)
            db.session.add_all([net_a, net_b])
            db.session.flush()
            db.session.add_all([
                EchoArea(network_id=net_a.id, tag='BULKA.ONE', name='A1', is_active=True),
                EchoArea(network_id=net_b.id, tag='BULKB.ONE', name='B1', is_active=True),
            ])
            node_a = BinkPNode(ftn_address='1:9/1.3', name='NodeA3', password='x',
                               is_active=True, hub_identity_id=id_a)
            db.session.add(node_a)
            db.session.commit()
            node_id = node_a.id
            net_a_id, net_b_id = net_a.id, net_b.id

        client = self._client()
        # Sysop (or a compromised/confused admin session) selects BOTH
        # networks -- node_a's own identity must still only get its own
        # network's area, not net_b's.
        resp = client.post(f'/admin/echomail/hub/binkp/{node_id}/subscribe-all',
                           data={'network_ids': [net_a_id, net_b_id]},
                           follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            subs = EchoAreaNode.query.filter_by(node_id=node_id).all()
            tags = {EchoArea.query.get(s.echo_area_id).tag for s in subs}
            self.assertEqual(tags, {'BULKA.ONE'},
                             'bulk-subscribe pulled in a different hub '
                             'identity\'s area -- cross-tenant leak')


if __name__ == '__main__':
    unittest.main()
