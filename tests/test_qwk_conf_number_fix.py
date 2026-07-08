"""Tests for the QWK conference-number fix -- live-caught, not
hypothetical: a real sysop posted a message from a QWK-connected node
into 'ANN.LINUX' and it silently vanished. Root cause traced through
two layers:

1. The ANotherNetwork install seeder (web_app.py) created QWK-side
   EchoArea rows using the same symbolic tag as the BinkP side
   ('ANN.LINUX') for all 26 areas -- but QWK's wire format only ever
   carries a numeric conference number, nothing else. Any post into
   one of these areas from a QWK node falls back to conference 0
   (private mail), and the hub's REP importer has no subscription ever
   registered at conf 0, so the message is dropped outright, not just
   misfiled.

2. Conference numbers were assigned per-node-subscription
   (auto-incremented independently for each node), not as a fixed
   property of the area itself -- so even a numerically-tagged area
   could get different numbers for different nodes, which breaks the
   premise of a shared area catalog.

This file covers: the seeder assigning stable numeric tags to fresh
installs, self-healing an already-seeded install with the old
symbolic tags (renaming + fixing existing subscriptions), subscribe
routes deriving conf_number from the area's own tag instead of
auto-incrementing, and form validation blocking a new non-numeric-tag
QWK area from being created in the first place.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class QwkConfNumberFixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.qwk_conf_number_fix_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        cls.app.config['REGISTRY_MODE_ENABLED'] = True

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def test_fresh_seed_qwk_areas_have_numeric_tags(self):
        from anetbbs.models import EchoArea, EchomailNetwork
        with self.app.app_context():
            qwk_net = EchomailNetwork.query.filter_by(name='ANotherNetwork (QWK)').first()
            self.assertIsNotNone(qwk_net)
            areas = EchoArea.query.filter_by(network_id=qwk_net.id).all()
            self.assertGreaterEqual(len(areas), 26)
            for area in areas:
                self.assertTrue((area.tag or '').isdigit(),
                                f'QWK area {area.name!r} has non-numeric tag {area.tag!r}')
            tags = [a.tag for a in areas]
            self.assertEqual(len(tags), len(set(tags)), 'duplicate QWK conference numbers')

    def test_fresh_seed_binkp_areas_still_have_symbolic_tags(self):
        from anetbbs.models import EchoArea, EchomailNetwork
        with self.app.app_context():
            binkp_net = EchomailNetwork.query.filter_by(name='ANotherNetwork').first()
            self.assertIsNotNone(binkp_net)
            area = EchoArea.query.filter_by(network_id=binkp_net.id, name='Linux & Open Source').first()
            self.assertIsNotNone(area)
            self.assertEqual(area.tag, 'ANN.LINUX')

    def test_qwk_area_conf_number_matches_order_plus_one(self):
        from anetbbs.models import EchoArea, EchomailNetwork
        with self.app.app_context():
            qwk_net = EchomailNetwork.query.filter_by(name='ANotherNetwork (QWK)').first()
            area = EchoArea.query.filter_by(network_id=qwk_net.id, name='Linux & Open Source').first()
            self.assertIsNotNone(area)
            self.assertEqual(area.tag, str(area.order + 1))

    def test_reseed_migrates_stale_symbolic_qwk_tag_and_fixes_subscriptions(self):
        """Simulate an already-seeded install from before this fix: a QWK
        area with the old symbolic tag, plus a real subscription pointing
        at a stale conf_number. Re-running the seeder should rename the
        tag AND bring the subscription's conf_number in line."""
        from anetbbs.models import db, EchoArea, EchomailNetwork, QWKNode, QWKNodeLastSent
        with self.app.app_context():
            qwk_net = EchomailNetwork.query.filter_by(name='ANotherNetwork (QWK)').first()
            area = EchoArea.query.filter_by(network_id=qwk_net.id, name='Technology').first()
            self.assertIsNotNone(area)
            # Force it back to the old (broken) symbolic tag, as if this
            # row predates the fix.
            area.tag = 'ANN.TECH'
            node = QWKNode(packet_id='RESEEDTEST', name='Test', password='x', is_active=True)
            db.session.add(node)
            db.session.flush()
            sub = QWKNodeLastSent(node_id=node.id, echo_area_id=area.id, conf_number=999)
            db.session.add(sub)
            db.session.commit()
            area_id = area.id
            sub_id = sub.id
            expected_conf = area.order + 1

        from anetbbs.web_app import _create_default_data
        with self.app.app_context():
            _create_default_data()

        with self.app.app_context():
            migrated_area = EchoArea.query.get(area_id)
            self.assertEqual(migrated_area.tag, str(expected_conf))
            migrated_sub = QWKNodeLastSent.query.get(sub_id)
            self.assertEqual(migrated_sub.conf_number, expected_conf)

    def test_qwk_subscribe_assigns_conf_number_from_area_tag(self):
        from anetbbs.models import db, EchoArea, EchomailNetwork, QWKNode, QWKNodeLastSent, User
        with self.app.app_context():
            net = EchomailNetwork(name='ConfFromTagNet', network_type='qwk')
            db.session.add(net)
            db.session.flush()
            area = EchoArea(network_id=net.id, tag='777', name='Test Area', is_active=True)
            db.session.add(area)
            node = QWKNode(packet_id='CONFFROMTAG', name='Test', password='x', is_active=True)
            db.session.add(node)
            db.session.commit()
            node_id = node.id
            area_id = area.id

            admin = User.query.filter_by(username='qwkconftest').first()
            if not admin:
                admin = User(username='qwkconftest', is_admin=True, access_level=255,
                            email='qwkconftest@example.com')
                admin.set_password('x')
                db.session.add(admin)
                db.session.commit()
            admin_id = admin.id

        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(admin_id)
            sess['_fresh'] = True

        client.post(f'/admin/echomail/hub/qwk/{node_id}/subscribe',
                    data={'area_id': area_id, 'action': 'subscribe'},
                    follow_redirects=True)

        with self.app.app_context():
            sub = QWKNodeLastSent.query.filter_by(node_id=node_id, echo_area_id=area_id).first()
            self.assertIsNotNone(sub)
            self.assertEqual(sub.conf_number, 777)

    def test_two_nodes_subscribing_to_same_area_get_same_conf_number(self):
        """The premise of a shared area catalog: every node must see the
        same conference number for the same area, not an independent
        per-node sequence."""
        from anetbbs.models import db, EchoArea, EchomailNetwork, QWKNode, QWKNodeLastSent, User
        with self.app.app_context():
            net = EchomailNetwork(name='SharedConfNet', network_type='qwk')
            db.session.add(net)
            db.session.flush()
            area = EchoArea(network_id=net.id, tag='555', name='Shared Area', is_active=True)
            db.session.add(area)
            node_a = QWKNode(packet_id='SHAREDA', name='A', password='x', is_active=True)
            node_b = QWKNode(packet_id='SHAREDB', name='B', password='x', is_active=True)
            db.session.add_all([node_a, node_b])
            db.session.commit()
            area_id = area.id
            node_a_id = node_a.id
            node_b_id = node_b.id

            admin = User.query.filter_by(username='qwkconftest2').first()
            if not admin:
                admin = User(username='qwkconftest2', is_admin=True, access_level=255,
                            email='qwkconftest2@example.com')
                admin.set_password('x')
                db.session.add(admin)
                db.session.commit()
            admin_id = admin.id

        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(admin_id)
            sess['_fresh'] = True

        # Node A subscribes to some other area first, so it would have
        # gotten a different number under the OLD per-node auto-increment
        # scheme -- this is exactly the scenario that used to diverge.
        other_area_id = None
        with self.app.app_context():
            other = EchoArea(network_id=EchomailNetwork.query.filter_by(
                name='SharedConfNet').first().id, tag='1', name='Other', is_active=True)
            db.session.add(other)
            db.session.commit()
            other_area_id = other.id
        client.post(f'/admin/echomail/hub/qwk/{node_a_id}/subscribe',
                    data={'area_id': other_area_id, 'action': 'subscribe'},
                    follow_redirects=True)

        client.post(f'/admin/echomail/hub/qwk/{node_a_id}/subscribe',
                    data={'area_id': area_id, 'action': 'subscribe'},
                    follow_redirects=True)
        client.post(f'/admin/echomail/hub/qwk/{node_b_id}/subscribe',
                    data={'area_id': area_id, 'action': 'subscribe'},
                    follow_redirects=True)

        with self.app.app_context():
            sub_a = QWKNodeLastSent.query.filter_by(node_id=node_a_id, echo_area_id=area_id).first()
            sub_b = QWKNodeLastSent.query.filter_by(node_id=node_b_id, echo_area_id=area_id).first()
            self.assertEqual(sub_a.conf_number, 555)
            self.assertEqual(sub_b.conf_number, 555)

    def test_qwk_subscribe_all_assigns_conf_number_from_area_tag(self):
        from anetbbs.models import db, EchoArea, EchomailNetwork, QWKNode, QWKNodeLastSent, User
        with self.app.app_context():
            net = EchomailNetwork(name='SubAllConfNet', network_type='qwk')
            db.session.add(net)
            db.session.flush()
            area1 = EchoArea(network_id=net.id, tag='100', name='A1', is_active=True)
            area2 = EchoArea(network_id=net.id, tag='200', name='A2', is_active=True)
            db.session.add_all([area1, area2])
            node = QWKNode(packet_id='SUBALLCONF', name='Test', password='x', is_active=True)
            db.session.add(node)
            db.session.commit()
            node_id = node.id
            net_id = net.id
            area1_id = area1.id
            area2_id = area2.id

            admin = User.query.filter_by(username='qwkconftest3').first()
            if not admin:
                admin = User(username='qwkconftest3', is_admin=True, access_level=255,
                            email='qwkconftest3@example.com')
                admin.set_password('x')
                db.session.add(admin)
                db.session.commit()
            admin_id = admin.id

        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(admin_id)
            sess['_fresh'] = True

        client.post(f'/admin/echomail/hub/qwk/{node_id}/subscribe-all',
                    data={'network_ids': [net_id]}, follow_redirects=True)

        with self.app.app_context():
            subs = {s.echo_area_id: s.conf_number for s in
                    QWKNodeLastSent.query.filter_by(node_id=node_id).all()}
            self.assertEqual(subs.get(area1_id), 100)
            self.assertEqual(subs.get(area2_id), 200)


class QwkAreaFormValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.qwk_area_form_validation_test.db')
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

    def _client(self):
        from anetbbs.models import User, db
        with self.app.app_context():
            admin = User.query.filter_by(username='areaformtest').first()
            if not admin:
                admin = User(username='areaformtest', is_admin=True, access_level=255,
                            email='areaformtest@example.com')
                admin.set_password('x')
                db.session.add(admin)
                db.session.commit()
            admin_id = admin.id
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(admin_id)
            sess['_fresh'] = True
        return client

    def test_new_area_rejects_symbolic_tag_on_qwk_network(self):
        from anetbbs.models import db, EchomailNetwork, EchoArea
        with self.app.app_context():
            net = EchomailNetwork(name='ValidateQWKNet', network_type='qwk')
            db.session.add(net)
            db.session.commit()
            net_id = net.id

        client = self._client()
        resp = client.post('/admin/echomail/areas/new', data={
            'network_id': net_id, 'tag': 'ANN.SYMBOLIC', 'name': 'Bad Area',
            'order': 0, 'min_access_level': 10,
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            self.assertIsNone(EchoArea.query.filter_by(network_id=net_id, name='Bad Area').first())

    def test_new_area_accepts_numeric_tag_on_qwk_network(self):
        from anetbbs.models import db, EchomailNetwork, EchoArea
        with self.app.app_context():
            net = EchomailNetwork(name='ValidateQWKNet2', network_type='qwk')
            db.session.add(net)
            db.session.commit()
            net_id = net.id

        client = self._client()
        resp = client.post('/admin/echomail/areas/new', data={
            'network_id': net_id, 'tag': '4242', 'name': 'Good Area',
            'order': 0, 'min_access_level': 10,
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            area = EchoArea.query.filter_by(network_id=net_id, name='Good Area').first()
            self.assertIsNotNone(area)
            self.assertEqual(area.tag, '4242')

    def test_new_area_symbolic_tag_still_allowed_on_binkp_network(self):
        from anetbbs.models import db, EchomailNetwork, EchoArea
        with self.app.app_context():
            net = EchomailNetwork(name='ValidateBinkpNet', network_type='binkp')
            db.session.add(net)
            db.session.commit()
            net_id = net.id

        client = self._client()
        resp = client.post('/admin/echomail/areas/new', data={
            'network_id': net_id, 'tag': 'FIDO.GENERAL', 'name': 'Fido Area',
            'order': 0, 'min_access_level': 10,
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            area = EchoArea.query.filter_by(network_id=net_id, name='Fido Area').first()
            self.assertIsNotNone(area)
            self.assertEqual(area.tag, 'FIDO.GENERAL')


if __name__ == '__main__':
    unittest.main()
