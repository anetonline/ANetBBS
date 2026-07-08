"""Tests for the QWK node "Subscribe to All" bulk action -- originally
requested after having to click Subscribe once per area, one at a
time, for every ANN.* area. Scoped to QWK-transport networks only (not
every area across the whole BBS, including BinkP-only networks a QWK
node has no business receiving).

Also requires an explicit `network_ids` selection now (checkboxes in
the admin UI) -- it used to silently sweep in every QWK network on the
install with no way to scope it to just one, confirmed as a real
problem for a sysop running more than one QWK network who couldn't add
a node to just its own home network without also pulling in every
other QWK network's areas.
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
            qwk_net_id = qwk_net.id

        client = self._client()
        resp = client.post(f'/admin/echomail/hub/qwk/{node_id}/subscribe-all',
                           data={'network_ids': [qwk_net_id]},
                           follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            # Scoped to just qwk_net's id -- the real seeded
            # ANotherNetwork QWK areas must NOT get swept in this time,
            # since only qwk_net.id was selected.
            subs = QWKNodeLastSent.query.filter_by(node_id=node_id).all()
            tags = {EchoArea.query.get(s.echo_area_id).tag for s in subs}
            self.assertEqual(tags, {'QWK.ONE', 'QWK.TWO'})

    def test_subscribe_all_requires_at_least_one_network_selected(self):
        from anetbbs.models import db, EchomailNetwork, EchoArea, QWKNode, QWKNodeLastSent
        with self.app.app_context():
            net = EchomailNetwork(name='NoSelectionNet', network_type='qwk')
            db.session.add(net)
            db.session.flush()
            db.session.add(EchoArea(network_id=net.id, tag='NOSEL.ONE', name='N1', is_active=True))
            node = QWKNode(packet_id='NOSELTEST', name='Test', password='x', is_active=True)
            db.session.add(node)
            db.session.commit()
            node_id = node.id

        client = self._client()
        resp = client.post(f'/admin/echomail/hub/qwk/{node_id}/subscribe-all',
                           follow_redirects=True)  # no network_ids at all
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Pick at least one network', resp.data)

        with self.app.app_context():
            self.assertEqual(QWKNodeLastSent.query.filter_by(node_id=node_id).count(), 0)

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
            net_id = net.id

        client = self._client()
        client.post(f'/admin/echomail/hub/qwk/{node_id}/subscribe-all',
                    data={'network_ids': [net_id]},
                    follow_redirects=True)

        with self.app.app_context():
            # Scoped to just net.id, so this is now an exact count.
            subs = (QWKNodeLastSent.query.filter_by(node_id=node_id)
                    .order_by(QWKNodeLastSent.conf_number).all())
            self.assertEqual(len(subs), 3)
            conf_numbers = [s.conf_number for s in subs]
            self.assertEqual(conf_numbers, [1, 2, 3],
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
            net_id = net.id

        client = self._client()
        client.post(f'/admin/echomail/hub/qwk/{node_id}/subscribe-all',
                    data={'network_ids': [net_id]},
                    follow_redirects=True)

        with self.app.app_context():
            # Scoped to just net.id: exactly 2 rows, no duplicate for
            # area1 (already subscribed before subscribe-all ran; must
            # not have been re-added as a second row).
            subs = QWKNodeLastSent.query.filter_by(node_id=node_id).all()
            self.assertEqual(len(subs), 2)
            area_ids = [s.echo_area_id for s in subs]
            self.assertEqual(len(area_ids), len(set(area_ids)),
                             'duplicate echo_area_id subscription created')
            tags = {EchoArea.query.get(s.echo_area_id).tag for s in subs}
            self.assertEqual(tags, {'DUP.ONE', 'DUP.TWO'})


if __name__ == '__main__':
    unittest.main()
