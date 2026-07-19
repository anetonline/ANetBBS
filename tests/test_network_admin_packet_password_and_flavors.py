"""Feature test: the echomail network admin form now has a spot for the
FTS-0001 packet password (distinct from the BinkP session password) and
per-network Crash/Hold/Direct netmail flavor defaults. Covers the
create route, the edit route's populate_obj wiring for the new boolean
fields, and the same "leave password blank to keep the current value"
behavior already used for binkp_password/areafix_password/qwk_password.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class NetworkAdminPacketPasswordTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.network_admin_flavor_test.db')
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

    def _admin_client(self):
        from anetbbs.models import db, User
        with self.app.app_context():
            admin = User.query.filter_by(username='netadminflavortest').first()
            if not admin:
                admin = User(username='netadminflavortest', is_admin=True,
                            access_level=255,
                            email='netadminflavortest@example.com')
                admin.set_password('x')
                db.session.add(admin)
                db.session.commit()
            admin_id = admin.id
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(admin_id)
            sess['_fresh'] = True
        return client

    def test_create_network_with_packet_password_and_flavors(self):
        from anetbbs.models import EchomailNetwork
        client = self._admin_client()
        resp = client.post('/admin/echomail/networks/new', data={
            'name': 'FlavorTestNet', 'network_type': 'binkp',
            'binkp_port': '24554', 'poll_interval_minutes': '60',
            'packet_password': 'PKTPASS1',
            'default_crash': 'y', 'default_hold': 'y', 'default_direct': 'y',
            'is_active': 'y',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            net = EchomailNetwork.query.filter_by(name='FlavorTestNet').first()
            self.assertIsNotNone(net)
            self.assertEqual(net.packet_password, 'PKTPASS1')
            self.assertTrue(net.default_crash)
            self.assertTrue(net.default_hold)
            self.assertTrue(net.default_direct)

    def test_flavors_independently_selectable_not_mutually_exclusive(self):
        """Only Hold checked -- Crash/Direct must stay False, not get
        forced on/off as a group."""
        from anetbbs.models import EchomailNetwork
        client = self._admin_client()
        client.post('/admin/echomail/networks/new', data={
            'name': 'HoldOnlyNet', 'network_type': 'binkp',
            'binkp_port': '24554', 'poll_interval_minutes': '60',
            'default_hold': 'y',
        }, follow_redirects=True)
        with self.app.app_context():
            net = EchomailNetwork.query.filter_by(name='HoldOnlyNet').first()
            self.assertIsNotNone(net)
            self.assertFalse(net.default_crash)
            self.assertTrue(net.default_hold)
            self.assertFalse(net.default_direct)

    def test_editing_with_blank_packet_password_preserves_existing_value(self):
        """Matches the established binkp_password/areafix_password/
        qwk_password behavior -- a PasswordField never round-trips its
        stored value into the rendered form, so leaving it blank on edit
        must NOT wipe out what's already configured."""
        from anetbbs.models import db, EchomailNetwork
        with self.app.app_context():
            net = EchomailNetwork(name='PreserveNet', network_type='binkp',
                                  packet_password='ORIGINAL1')
            db.session.add(net)
            db.session.commit()
            net_id = net.id

        client = self._admin_client()
        resp = client.post(f'/admin/echomail/networks/{net_id}/edit', data={
            'name': 'PreserveNet', 'network_type': 'binkp',
            'binkp_port': '24554', 'poll_interval_minutes': '60',
            'packet_password': '',  # left blank
            'default_crash': 'y',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            net = EchomailNetwork.query.get(net_id)
            self.assertEqual(net.packet_password, 'ORIGINAL1',
                             'blank packet_password field must preserve the prior value')
            self.assertTrue(net.default_crash, 'other fields must still update normally')

    def test_editing_with_new_packet_password_replaces_it(self):
        from anetbbs.models import db, EchomailNetwork
        with self.app.app_context():
            net = EchomailNetwork(name='ReplaceNet', network_type='binkp',
                                  packet_password='OLDVALUE')
            db.session.add(net)
            db.session.commit()
            net_id = net.id

        client = self._admin_client()
        client.post(f'/admin/echomail/networks/{net_id}/edit', data={
            'name': 'ReplaceNet', 'network_type': 'binkp',
            'binkp_port': '24554', 'poll_interval_minutes': '60',
            'packet_password': 'NEWVALUE1',
        }, follow_redirects=True)

        with self.app.app_context():
            net = EchomailNetwork.query.get(net_id)
            self.assertEqual(net.packet_password, 'NEWVALUE1')


if __name__ == '__main__':
    unittest.main()
