"""Regression tests for a test-coverage gap found in a full echomail-
subsystem audit: anetbbs/web/echomail_admin.py's custom_areafix() and
test_connection() routes had zero test coverage -- custom_areafix is
the ONLY in-UI path that can send an arbitrary AreaFix command
(including %RESCAN, %COMPRESS GZIP), and both routes had untested
password-fallback / missing-config / robot-override edge cases.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class CustomAreafixAndTestConnectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.custom_areafix_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        with cls.app.app_context():
            db.create_all()
            admin = User(username='echoadmintest', email='eat@example.com',
                        password_hash='x', is_admin=True, access_level=100)
            db.session.add(admin)
            db.session.commit()
            cls.admin_id = admin.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _client_as_admin(self):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True
        return client

    def _make_network(self, **kw):
        from anetbbs.models import db, EchomailNetwork
        with self.app.app_context():
            net = EchomailNetwork(network_type='binkp', **kw)
            db.session.add(net)
            db.session.commit()
            return net.id

    # ---- custom_areafix ----

    def test_custom_areafix_queues_a_netmail_with_typed_body(self):
        from anetbbs.models import NetmailMessage
        net_id = self._make_network(
            name='CustomAreafixNet', our_address='9:1/1',
            hub_address='9:1/2', areafix_password='secretpw')

        client = self._client_as_admin()
        resp = client.post(f'/admin/echomail/networks/{net_id}/custom_areafix',
                           data={'body': '%RESCAN AF.TEST\n%LIST'},
                           follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            nm = (NetmailMessage.query
                 .filter_by(network_id=net_id, to_name='AreaFix')
                 .order_by(NetmailMessage.id.desc()).first())
            self.assertIsNotNone(nm)
            self.assertIn('%RESCAN AF.TEST', nm.body)
            self.assertEqual(nm.subject, 'secretpw')
            self.assertEqual(nm.status, 'queued')

    def test_custom_areafix_robot_override_targets_filefix(self):
        from anetbbs.models import NetmailMessage
        net_id = self._make_network(
            name='CustomFilefixNet', our_address='9:2/1',
            hub_address='9:2/2', areafix_password='secretpw')

        client = self._client_as_admin()
        resp = client.post(
            f'/admin/echomail/networks/{net_id}/custom_areafix',
            data={'body': '+FF.TAG', 'robot': 'FileFix'}, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            nm = (NetmailMessage.query
                 .filter_by(network_id=net_id, to_name='FileFix')
                 .order_by(NetmailMessage.id.desc()).first())
            self.assertIsNotNone(nm)

    def test_custom_areafix_falls_back_to_binkp_password_when_no_areafix_password(self):
        from anetbbs.models import NetmailMessage
        net_id = self._make_network(
            name='CustomFallbackPwNet', our_address='9:3/1',
            hub_address='9:3/2', binkp_password='sessionpw')

        client = self._client_as_admin()
        client.post(f'/admin/echomail/networks/{net_id}/custom_areafix',
                    data={'body': '%LIST'}, follow_redirects=True)

        with self.app.app_context():
            nm = (NetmailMessage.query
                 .filter_by(network_id=net_id)
                 .order_by(NetmailMessage.id.desc()).first())
            self.assertEqual(nm.subject, 'sessionpw')

    def test_custom_areafix_empty_body_does_not_queue_anything(self):
        from anetbbs.models import NetmailMessage
        net_id = self._make_network(
            name='CustomEmptyBodyNet', our_address='9:4/1',
            hub_address='9:4/2', areafix_password='pw')

        client = self._client_as_admin()
        client.post(f'/admin/echomail/networks/{net_id}/custom_areafix',
                    data={'body': '   '}, follow_redirects=True)

        with self.app.app_context():
            self.assertEqual(
                NetmailMessage.query.filter_by(network_id=net_id).count(), 0)

    def test_custom_areafix_no_hub_address_does_not_queue_anything(self):
        from anetbbs.models import NetmailMessage
        net_id = self._make_network(
            name='CustomNoHubAddrNet', our_address='9:5/1',
            hub_address=None, areafix_password='pw')

        client = self._client_as_admin()
        client.post(f'/admin/echomail/networks/{net_id}/custom_areafix',
                    data={'body': '%LIST'}, follow_redirects=True)

        with self.app.app_context():
            self.assertEqual(
                NetmailMessage.query.filter_by(network_id=net_id).count(), 0)

    def test_custom_areafix_rejects_non_binkp_network(self):
        from anetbbs.models import db, EchomailNetwork, NetmailMessage
        with self.app.app_context():
            net = EchomailNetwork(network_type='qwk', name='CustomQwkNet')
            db.session.add(net)
            db.session.commit()
            net_id = net.id

        client = self._client_as_admin()
        client.post(f'/admin/echomail/networks/{net_id}/custom_areafix',
                    data={'body': '%LIST'}, follow_redirects=True)

        with self.app.app_context():
            self.assertEqual(
                NetmailMessage.query.filter_by(network_id=net_id).count(), 0)

    # ---- test_connection ----

    def test_test_connection_binkp_unreachable_host_flashes_failure_not_500(self):
        net_id = self._make_network(
            name='TestConnUnreachableNet', binkp_host='192.0.2.1',  # TEST-NET-1, never routable
            binkp_port=1)

        client = self._client_as_admin()
        resp = client.post(f'/admin/echomail/networks/{net_id}/test',
                           follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

    def test_test_connection_qwk_network_does_not_crash(self):
        from anetbbs.models import db, EchomailNetwork
        with self.app.app_context():
            net = EchomailNetwork(network_type='qwk', name='TestConnQwkNet',
                                  qwk_host='example.invalid')
            db.session.add(net)
            db.session.commit()
            net_id = net.id

        client = self._client_as_admin()
        resp = client.post(f'/admin/echomail/networks/{net_id}/test',
                           follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

    def test_test_connection_unknown_network_404s(self):
        client = self._client_as_admin()
        resp = client.post('/admin/echomail/networks/999999/test')
        self.assertEqual(resp.status_code, 404)


if __name__ == '__main__':
    unittest.main()
