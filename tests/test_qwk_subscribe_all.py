"""Tests for the QWK node "Subscribe to All" bulk action -- Jerry asked
for this after having to click Subscribe once per area, one at a time,
for every ANN.* area. Scoped to QWK-transport networks only (not every
area across the whole BBS, including BinkP-only networks a QWK node
has no business receiving).
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class QwkSubscribeAllTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.qwk_subscribe_all_test.db')
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
            admin = User.query.filter_by(username='qwksuballtest').first()
            if not admin:
                from anetbbs.models import db
                admin = User(username='qwksuballtest', is_admin=True,
                            access_level=255,
                            email='qwksuballtest@example.com')
                admin.set_password('x')
                db.session.add(admin)
                db.session.commit()
            admin_id = admin.id
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(admin_id)
            sess['_fresh'] = True
        return client

    def test_subscribe_all_only_covers_qwk_networks_not_binkp(self):
        from anetbbs.models import (db, EchomailNetwork, EchoArea, QWKNode,
                                    QWKNodeLastSent)
        with self.app.app_context():
            qwk_net = EchomailNetwork(name='TestQWKNet', network_type='qwk')
            binkp_net = EchomailNetwork(name='TestBinkpNet', network_type='binkp')
            db.session.add_all([qwk_net, binkp_net])
            db.session.flush()

            db.session.add_all([
                EchoArea(network_id=qwk_net.id, tag='QWK.ONE', name='Q1', is_active=True),
                EchoArea(network_id=qwk_net.id, tag='QWK.TWO', name='Q2', is_active=True),
                EchoArea(network_id=binkp_net.id, tag='BINKP.ONE', name='B1', is_active=True),
            ])
            node = QWKNode(packet_id='TESTNODE', name='Test', password='x',
                           is_active=True)
            db.session.add(node)
            db.session.commit()
            node_id = node.id

        client = self._client()
        resp = client.post(f'/admin/echomail/hub/qwk/{node_id}/subscribe-all',
                           follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            # Real seeded ANotherNetwork QWK areas also get subscribed by
            # this same action (create_app('testing') seeds them too) --
            # assert our test areas are a subset, not an exact match.
            subs = QWKNodeLastSent.query.filter_by(node_id=node_id).all()
            tags = {EchoArea.query.get(s.echo_area_id).tag for s in subs}
            self.assertTrue({'QWK.ONE', 'QWK.TWO'}.issubset(tags))
            self.assertNotIn('BINKP.ONE', tags)

    def test_subscribe_all_assigns_sequential_conf_numbers(self):
        from anetbbs.models import (db, EchomailNetwork, EchoArea, QWKNode,
                                    QWKNodeLastSent)
        with self.app.app_context():
            net = EchomailNetwork(name='ConfNumNet', network_type='qwk')
            db.session.add(net)
            db.session.flush()
            for i in range(3):
                db.session.add(EchoArea(network_id=net.id, tag=f'CONF.{i}',
                                        name=f'C{i}', is_active=True))
            node = QWKNode(packet_id='CONFTEST', name='Test', password='x',
                           is_active=True)
            db.session.add(node)
            db.session.commit()
            node_id = node.id

        client = self._client()
        client.post(f'/admin/echomail/hub/qwk/{node_id}/subscribe-all',
                    follow_redirects=True)

        with self.app.app_context():
            # Real seeded areas get subscribed too, so conf numbers won't
            # be exactly [1,2,3] for just our 3 -- assert the invariant
            # that actually matters: no gaps, no duplicates, contiguous
            # from 1.
            subs = (QWKNodeLastSent.query.filter_by(node_id=node_id)
                    .order_by(QWKNodeLastSent.conf_number).all())
            self.assertGreaterEqual(len(subs), 3)
            conf_numbers = [s.conf_number for s in subs]
            self.assertEqual(len(conf_numbers), len(set(conf_numbers)),
                             'duplicate conf_number assigned')
            self.assertEqual(conf_numbers, list(range(1, len(conf_numbers) + 1)),
                             'conf numbers are not contiguous starting from 1')

    def test_subscribe_all_does_not_duplicate_existing_subscriptions(self):
        from anetbbs.models import (db, EchomailNetwork, EchoArea, QWKNode,
                                    QWKNodeLastSent)
        with self.app.app_context():
            net = EchomailNetwork(name='NoDupNet', network_type='qwk')
            db.session.add(net)
            db.session.flush()
            area1 = EchoArea(network_id=net.id, tag='DUP.ONE', name='D1', is_active=True)
            area2 = EchoArea(network_id=net.id, tag='DUP.TWO', name='D2', is_active=True)
            db.session.add_all([area1, area2])
            db.session.flush()
            node = QWKNode(packet_id='DUPTEST', name='Test', password='x',
                           is_active=True)
            db.session.add(node)
            db.session.flush()
            db.session.add(QWKNodeLastSent(node_id=node.id, echo_area_id=area1.id,
                                           conf_number=1))
            db.session.commit()
            node_id = node.id

        client = self._client()
        client.post(f'/admin/echomail/hub/qwk/{node_id}/subscribe-all',
                    follow_redirects=True)

        with self.app.app_context():
            # Real seeded areas get subscribed too -- check the invariant
            # that matters: no duplicate echo_area_id for this node, and
            # our two specific tags each appear exactly once (area1 was
            # already subscribed before subscribe-all ran; it must not
            # have been re-added as a second row).
            subs = QWKNodeLastSent.query.filter_by(node_id=node_id).all()
            area_ids = [s.echo_area_id for s in subs]
            self.assertEqual(len(area_ids), len(set(area_ids)),
                             'duplicate echo_area_id subscription created')
            tags = [EchoArea.query.get(s.echo_area_id).tag for s in subs]
            self.assertEqual(tags.count('DUP.ONE'), 1)
            self.assertEqual(tags.count('DUP.TWO'), 1)


if __name__ == '__main__':
    unittest.main()
