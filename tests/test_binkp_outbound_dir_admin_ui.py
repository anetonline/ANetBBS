"""Admin-UI coverage for the new BinkP outbound spool directory (see
anetbbs/echomail/binkp.py's resolve_outbound_dir docstring and
test_binkp_outbound_dir.py for the underlying logic).

Jerry asked "where is the outbound directory for binkp?" while about to
start testing an external door (ANetCHESS) that writes its own netmail
packets straight to disk and needs a real, discoverable filesystem path
to drop them in -- not just something a sysop would have to read source
to find. The resolved per-peer path is now shown on the Echomail
Networks list (echomail_admin.networks) and a BinkPNode's own detail
page (hub_admin.binkp_node_detail), the two places a sysop already goes
to configure a peer.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class OutboundDirAdminUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent /
                          '.binkp_outbound_dir_admin_ui_test.db')
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
        from anetbbs.models import User, db
        with self.app.app_context():
            admin = User.query.filter_by(username='outbounddirui').first()
            if not admin:
                admin = User(username='outbounddirui', is_admin=True,
                            access_level=255,
                            email='outbounddirui@example.com')
                admin.set_password('x')
                db.session.add(admin)
                db.session.commit()
            admin_id = admin.id
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(admin_id)
            sess['_fresh'] = True
        return client

    def test_networks_page_shows_resolved_outbound_spool_path(self):
        from anetbbs.models import db, EchomailNetwork
        from anetbbs.echomail.binkp import resolve_outbound_dir
        with self.app.app_context():
            net = EchomailNetwork(name='SmokeNet', network_type='binkp',
                                  our_address='1200:1/1', hub_address='1200:1/2',
                                  is_active=True)
            db.session.add(net)
            db.session.commit()
            expected = resolve_outbound_dir(
                self.app.config.get('DATA_DIR') or 'data', '1200:1/2')
        resp = self._client().get('/admin/echomail/networks')
        self.assertEqual(resp.status_code, 200)
        body = resp.data.decode()
        self.assertIn('Outbound Spool', body)
        self.assertIn(expected, body)

    def test_qwk_network_shows_no_outbound_spool_path(self):
        from anetbbs.models import db, EchomailNetwork
        with self.app.app_context():
            net = EchomailNetwork(name='QwkSmokeNet', network_type='qwk',
                                  qwk_host='qwk.example.test', is_active=True)
            db.session.add(net)
            db.session.commit()
        resp = self._client().get('/admin/echomail/networks')
        self.assertEqual(resp.status_code, 200)
        # A QWK network has no BinkP spool -- must render the em-dash
        # placeholder rather than crash on a missing dict key.
        body = resp.data.decode()
        self.assertIn('QwkSmokeNet', body)

    def test_binkp_node_detail_page_shows_resolved_outbound_spool_path(self):
        from anetbbs.models import db, BinkPNode
        from anetbbs.echomail.binkp import resolve_outbound_dir
        with self.app.app_context():
            node = BinkPNode(ftn_address='1200:1/9', name='SmokeNode',
                             password='x', is_active=True)
            db.session.add(node)
            db.session.commit()
            node_id = node.id
            expected = resolve_outbound_dir(
                self.app.config.get('DATA_DIR') or 'data', '1200:1/9')
        resp = self._client().get(f'/admin/echomail/hub/binkp/{node_id}')
        self.assertEqual(resp.status_code, 200)
        body = resp.data.decode()
        self.assertIn('Outbound Spool', body)
        self.assertIn(expected, body)


if __name__ == '__main__':
    unittest.main()
