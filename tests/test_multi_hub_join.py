"""Phase 2 of multi-hub-identity: per-identity join config/requests.

Phases 0-1 (already shipped) gave every HubIdentity-scoped row a
hub_identity_id FK, defaulting to the install's default identity at
flush time -- fully additive, zero behavior change for the common
single-hub-identity install. Those existing tests (test_network_join.py,
test_hub_identity_crud.py) already prove the single-identity path is
untouched -- this file proves the *second* identity path actually works:
its own join form URL, its own config/infopack, its own request queue,
and approval creating nodes stamped with the right identity.
"""
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


def _make_test_zip(path, members):
    with zipfile.ZipFile(path, 'w') as zf:
        for name, content in members.items():
            zf.writestr(name, content)


class MultiHubJoinTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.multi_hub_join_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        cls.join_dir = tempfile.mkdtemp()
        cfg_mod.TestingConfig.NETWORK_JOIN_DIR = cls.join_dir

        from anetbbs.web_app import create_app
        from anetbbs.models import db
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['REGISTRY_MODE_ENABLED'] = True
        cls.app.config['NETWORK_JOIN_DIR'] = cls.join_dir
        with cls.app.app_context():
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)
        import shutil
        shutil.rmtree(cls.join_dir, ignore_errors=True)

    def _admin_client(self, username):
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

    def _second_identity(self, slug, name='SecondNet'):
        """Create (or fetch) a second, active, non-default HubIdentity."""
        from anetbbs.models import db, HubIdentity
        with self.app.app_context():
            identity = HubIdentity.query.filter_by(slug=slug).first()
            if identity is None:
                identity = HubIdentity(name=name, slug=slug, qwk_hub_id='SECOND',
                                       binkp_zone=2, binkp_net=1,
                                       is_active=True, is_default=False)
                db.session.add(identity)
                db.session.commit()
            return identity.id

    def _enable_join_form(self, hub_identity_id=None):
        from anetbbs.models import db, NetworkJoinConfig
        with self.app.app_context():
            cfg = NetworkJoinConfig.get(hub_identity_id=hub_identity_id)
            cfg.enabled = True
            cfg.network_name = 'SecondNetJoin'
            cfg.rules_text = 'Second identity rules text.'
            db.session.commit()

    # -- slug routing -------------------------------------------------

    def test_unknown_slug_404s(self):
        client = self.app.test_client()
        resp = client.get('/join/does-not-exist/')
        self.assertEqual(resp.status_code, 404)

    def test_inactive_identity_slug_404s(self):
        from anetbbs.models import db, HubIdentity
        with self.app.app_context():
            identity = HubIdentity(name='Inactive', slug='inactive-net',
                                   is_active=False, is_default=False)
            db.session.add(identity)
            db.session.commit()
        client = self.app.test_client()
        resp = client.get('/join/inactive-net/')
        self.assertEqual(resp.status_code, 404)

    def test_second_identity_join_form_independent_of_default(self):
        second_id = self._second_identity('secondnet')
        self._enable_join_form(hub_identity_id=second_id)

        # Default identity's own join form config is untouched (still
        # whatever the default-identity test file leaves it as) --
        # what matters here is the second identity's slugged page shows
        # ITS OWN rules text, not the default's.
        client = self.app.test_client()
        resp = client.get('/join/secondnet/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('Second identity rules text.', resp.get_data(as_text=True))

    def test_post_to_slugged_form_stamps_hub_identity_id(self):
        second_id = self._second_identity('secondnet-post')
        self._enable_join_form(hub_identity_id=second_id)

        client = self.app.test_client()
        get_resp = client.get('/join/secondnet-post/')
        import re
        token = re.search(r'name="csrf_token" value="([^"]+)"', get_resp.get_data(as_text=True))
        resp = client.post('/join/secondnet-post/', data={
            'csrf_token': token.group(1) if token else '',
            'name': 'SecondApplicant', 'bbs_name': 'SecondBBS',
            'email': 'second@example.com',
            'binkp_ftn_address': '2:1/50',
            'rules_ack': 'y',
        })
        self.assertEqual(resp.status_code, 200)
        with self.app.app_context():
            from anetbbs.models import NetworkJoinRequest
            req = NetworkJoinRequest.query.filter_by(bbs_name='SecondBBS').first()
            self.assertIsNotNone(req)
            self.assertEqual(req.hub_identity_id, second_id)

    # -- infopack isolation --------------------------------------------

    def test_infopack_isolated_per_identity(self):
        from anetbbs.models import db, NetworkJoinConfig
        second_id = self._second_identity('secondnet-infopack')

        default_zip = os.path.join(self.join_dir, 'shared_name.zip')
        _make_test_zip(default_zip, {'rules.txt': 'DEFAULT identity rules'})
        second_dir = os.path.join(self.join_dir, str(second_id))
        os.makedirs(second_dir, exist_ok=True)
        second_zip = os.path.join(second_dir, 'shared_name.zip')
        _make_test_zip(second_zip, {'rules.txt': 'SECOND identity rules'})

        with self.app.app_context():
            default_cfg = NetworkJoinConfig.get()
            default_cfg.enabled = True
            default_cfg.infopack_filename = 'shared_name.zip'
            default_cfg.infopack_original_filename = 'infopack.zip'

            second_cfg = NetworkJoinConfig.get(hub_identity_id=second_id)
            second_cfg.enabled = True
            second_cfg.infopack_filename = 'shared_name.zip'
            second_cfg.infopack_original_filename = 'infopack.zip'
            db.session.commit()

        client = self.app.test_client()
        default_resp = client.get('/join/infopack.zip')
        second_resp = client.get('/join/secondnet-infopack/infopack.zip')
        self.assertEqual(default_resp.status_code, 200)
        self.assertEqual(second_resp.status_code, 200)
        self.assertNotEqual(default_resp.data, second_resp.data,
                            'two identically-named infopacks for different '
                            'identities must not collide on disk')

    # -- admin upload/config routes carry hub_identity_id ---------------

    def test_admin_upload_infopack_lands_in_identity_subdir(self):
        second_id = self._second_identity('secondnet-upload')
        client = self._admin_client('upload_admin')

        zpath = os.path.join(tempfile.mkdtemp(), 'up.zip')
        _make_test_zip(zpath, {'rules.txt': 'uploaded rules text'})
        with open(zpath, 'rb') as f:
            from io import BytesIO
            data = BytesIO(f.read())
        data.name = 'up.zip'

        resp = client.post('/admin/echomail/hub/join/upload', data={
            'hub_identity_id': str(second_id),
            'infopack': (data, 'up.zip'),
        }, content_type='multipart/form-data', follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        expected_path = os.path.join(self.join_dir, str(second_id), 'up.zip')
        self.assertTrue(os.path.exists(expected_path),
                        f'expected uploaded infopack at {expected_path}')

        with self.app.app_context():
            from anetbbs.models import NetworkJoinConfig
            cfg = NetworkJoinConfig.get(hub_identity_id=second_id)
            self.assertEqual(cfg.infopack_filename, 'up.zip')
            self.assertEqual(cfg.rules_text, 'uploaded rules text')

    def test_admin_config_save_scoped_to_identity(self):
        second_id = self._second_identity('secondnet-config')
        client = self._admin_client('config_admin')
        resp = client.post('/admin/echomail/hub/join/config', data={
            'hub_identity_id': str(second_id),
            'enabled': 'y',
            'network_name': 'SecondConfigName',
            'binkp_zone': '2',
            'binkp_net': '5',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            from anetbbs.models import NetworkJoinConfig, HubIdentity
            second_cfg = NetworkJoinConfig.get(hub_identity_id=second_id)
            self.assertEqual(second_cfg.network_name, 'SecondConfigName')
            self.assertEqual(second_cfg.binkp_zone, 2)
            self.assertEqual(second_cfg.binkp_net, 5)

            default_id = HubIdentity.query.filter_by(is_default=True).first().id
            default_cfg = NetworkJoinConfig.get(hub_identity_id=default_id)
            self.assertNotEqual(default_cfg.network_name, 'SecondConfigName',
                                "saving the second identity's config must "
                                "not touch the default identity's row")

    # -- approval flow: node creation stamped with the right identity --

    def test_approve_request_stamps_binkp_node_with_its_identity(self):
        from anetbbs.models import db, NetworkJoinRequest, BinkPNode
        second_id = self._second_identity('secondnet-approve-binkp')
        with self.app.app_context():
            req = NetworkJoinRequest(
                hub_identity_id=second_id,
                bbs_name='SecondApproveBBS', name='Approver2',
                email='approver2@example.com',
                binkp_ftn_address='2:1/900', rules_ack=True)
            db.session.add(req)
            db.session.commit()
            req_id = req.id

        client = self._admin_client('approve_binkp_admin')
        client.post(f'/admin/echomail/hub/join/requests/{req_id}/approve',
                    follow_redirects=True)

        with self.app.app_context():
            req = NetworkJoinRequest.query.get(req_id)
            self.assertEqual(req.status, 'approved')
            node = BinkPNode.query.get(req.binkp_node_id)
            self.assertEqual(node.hub_identity_id, second_id)

    def test_approve_request_stamps_qwk_node_with_its_identity(self):
        from anetbbs.models import db, NetworkJoinRequest, QWKNode
        second_id = self._second_identity('secondnet-approve-qwk')
        with self.app.app_context():
            req = NetworkJoinRequest(
                hub_identity_id=second_id,
                bbs_name='SecondApproveQwkBBS', name='Approver3',
                email='approver3@example.com',
                qwk_packet_id='SECQWK1', rules_ack=True)
            db.session.add(req)
            db.session.commit()
            req_id = req.id

        client = self._admin_client('approve_qwk_admin')
        client.post(f'/admin/echomail/hub/join/requests/{req_id}/approve',
                    follow_redirects=True)

        with self.app.app_context():
            req = NetworkJoinRequest.query.get(req_id)
            self.assertEqual(req.status, 'approved')
            node = QWKNode.query.get(req.qwk_node_id)
            self.assertEqual(node.hub_identity_id, second_id)

    def test_next_binkp_node_address_scoped_by_identity(self):
        from anetbbs.models import db, BinkPNode
        from anetbbs.web.hub_admin import _next_binkp_node_address
        default_id = self._second_identity('secondnet-numbering-default-helper')
        second_id = self._second_identity('secondnet-numbering-second')
        with self.app.app_context():
            db.session.add(BinkPNode(name='DefaultsideNode', ftn_address='3000:1/9',
                                     password='x', is_active=True,
                                     hub_identity_id=default_id))
            db.session.commit()
            # Same zone:net reused by a different identity with no nodes
            # of its own yet -- must NOT see the other identity's /9 and
            # jump to /10; must start fresh at /2.
            self.assertEqual(
                _next_binkp_node_address(3000, 1, hub_identity_id=second_id),
                '3000:1/2')
            self.assertEqual(
                _next_binkp_node_address(3000, 1, hub_identity_id=default_id),
                '3000:1/10')

    # -- join requests admin list: identity filter -----------------------

    def test_join_requests_identity_filter(self):
        from anetbbs.models import db, NetworkJoinRequest
        second_id = self._second_identity('secondnet-filter')
        with self.app.app_context():
            db.session.add(NetworkJoinRequest(
                hub_identity_id=second_id, bbs_name='FilterOnlyOnSecondBBS',
                name='Filterer', email='filterer@example.com',
                binkp_ftn_address='2:1/901', rules_ack=True))
            db.session.commit()

        client = self._admin_client('filter_admin')
        resp = client.get(f'/admin/echomail/hub/join/requests?identity={second_id}')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn('FilterOnlyOnSecondBBS', body)


if __name__ == '__main__':
    unittest.main()
