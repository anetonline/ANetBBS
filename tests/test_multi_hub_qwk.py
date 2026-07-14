"""Phase 4 of multi-hub-identity: QWK hub multi-identity support.

Three independent "what's our hub system ID" resolvers existed
(anetbbs/web/qwk_hub.py's _hub_id(), anetbbs/echomail/qwk_hub_ftp.py's
_HUB_ID default arg, anetbbs/ftp/server.py's on_login), none reading
HubIdentity.qwk_hub_id at all -- a node on a second hub identity would
silently get the DEFAULT identity's packets/filenames. These tests
prove the new shared resolve_hub_id() (anetbbs/echomail/qwk_hub_ftp.py)
is actually identity-aware, and that node/subscription creation now
stamps/respects hub_identity_id end to end.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class ResolveHubIdTests(unittest.TestCase):
    """Pure function tests -- needs an app context for the HubIdentity
    relationship lookup but no HTTP layer."""

    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.resolve_hub_id_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'
        from anetbbs.web_app import create_app
        from anetbbs.models import db
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def setUp(self):
        self._orig_env = os.environ.get('QWK_HUB_ID')
        os.environ.pop('QWK_HUB_ID', None)

    def tearDown(self):
        if self._orig_env is None:
            os.environ.pop('QWK_HUB_ID', None)
        else:
            os.environ['QWK_HUB_ID'] = self._orig_env

    def _second_identity(self, slug, qwk_hub_id):
        from anetbbs.models import db, HubIdentity
        with self.app.app_context():
            identity = HubIdentity.query.filter_by(slug=slug).first()
            if identity is None:
                identity = HubIdentity(name=slug, slug=slug, qwk_hub_id=qwk_hub_id,
                                       is_active=True, is_default=False)
                db.session.add(identity)
                db.session.commit()
            return identity.id

    def test_no_node_falls_back_to_env_var(self):
        from anetbbs.echomail.qwk_hub_ftp import resolve_hub_id
        os.environ['QWK_HUB_ID'] = 'ENVHUB'
        with self.app.app_context():
            self.assertEqual(resolve_hub_id(None), 'ENVHUB')

    def test_no_node_no_env_falls_back_to_bbs_name(self):
        from anetbbs.echomail.qwk_hub_ftp import resolve_hub_id
        os.environ.pop('QWK_HUB_ID', None)
        orig_bbs = os.environ.get('BBS_NAME')
        os.environ['BBS_NAME'] = 'My Test BBS!'
        try:
            with self.app.app_context():
                # Truncated to 8 chars, same as the pre-existing legacy
                # resolver -- 'MY TEST BBS!' strips to 'MYTESTBBS' (9
                # chars) then [:8].
                self.assertEqual(resolve_hub_id(None), 'MYTESTBB')
        finally:
            if orig_bbs is None:
                os.environ.pop('BBS_NAME', None)
            else:
                os.environ['BBS_NAME'] = orig_bbs

    def test_node_on_default_identity_uses_env_var(self):
        from anetbbs.models import db, QWKNode, HubIdentity
        from anetbbs.echomail.qwk_hub_ftp import resolve_hub_id
        os.environ['QWK_HUB_ID'] = 'DEFAULTENV'
        with self.app.app_context():
            default_id = HubIdentity.query.filter_by(is_default=True).first().id
            node = QWKNode(packet_id='DEFNODE', name='Default Node', password='x',
                           is_active=True, hub_identity_id=default_id)
            db.session.add(node)
            db.session.commit()
            self.assertEqual(resolve_hub_id(node), 'DEFAULTENV')

    def test_node_on_second_identity_uses_its_own_qwk_hub_id(self):
        from anetbbs.models import db, QWKNode
        from anetbbs.echomail.qwk_hub_ftp import resolve_hub_id
        os.environ['QWK_HUB_ID'] = 'SHOULDNOTWIN'
        second_id = self._second_identity('resolve-second', 'SECONDID')
        with self.app.app_context():
            node = QWKNode(packet_id='SECNODE', name='Second Node', password='x',
                           is_active=True, hub_identity_id=second_id)
            db.session.add(node)
            db.session.commit()
            self.assertEqual(resolve_hub_id(node), 'SECONDID')

    def test_two_identities_produce_different_hub_ids(self):
        from anetbbs.models import db, QWKNode
        from anetbbs.echomail.qwk_hub_ftp import resolve_hub_id
        id_a = self._second_identity('resolve-a', 'HUBA')
        id_b = self._second_identity('resolve-b', 'HUBB')
        with self.app.app_context():
            node_a = QWKNode(packet_id='NODEA', name='A', password='x',
                             is_active=True, hub_identity_id=id_a)
            node_b = QWKNode(packet_id='NODEB', name='B', password='x',
                             is_active=True, hub_identity_id=id_b)
            db.session.add_all([node_a, node_b])
            db.session.commit()
            self.assertEqual(resolve_hub_id(node_a), 'HUBA')
            self.assertEqual(resolve_hub_id(node_b), 'HUBB')
            self.assertNotEqual(resolve_hub_id(node_a), resolve_hub_id(node_b))


class QwkHubRouteMultiIdentityTests(unittest.TestCase):
    """download_qwk / upload_rep route tests -- a node on a second
    identity must get its own <HUBID>.QWK filename and its REP upload
    must be matched against that same identity's <HUBID>.MSG."""

    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.qwk_hub_route_multi_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'
        from anetbbs.web_app import create_app
        from anetbbs.models import db
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _second_identity(self, slug, qwk_hub_id):
        from anetbbs.models import db, HubIdentity
        with self.app.app_context():
            identity = HubIdentity.query.filter_by(slug=slug).first()
            if identity is None:
                identity = HubIdentity(name=slug, slug=slug, qwk_hub_id=qwk_hub_id,
                                       is_active=True, is_default=False)
                db.session.add(identity)
                db.session.commit()
            return identity.id

    def test_download_filename_reflects_node_identity(self):
        import base64
        from anetbbs.models import db, QWKNode
        second_id = self._second_identity('qwkroute-second', 'ROUTEHUB')
        with self.app.app_context():
            db.session.add(QWKNode(packet_id='RTNODE', name='RT', password='pw',
                                   is_active=True, hub_identity_id=second_id))
            db.session.commit()

        client = self.app.test_client()
        auth = base64.b64encode(b'RTNODE:pw').decode()
        resp = client.get('/qwkhub/RTNODE.qwk',
                          headers={'Authorization': f'Basic {auth}'})
        self.assertEqual(resp.status_code, 200)
        self.assertIn('ROUTEHUB.QWK', resp.headers.get('Content-Disposition', ''))

    def test_rep_upload_matched_against_node_identity_msg_filename(self):
        """import_rep_packet() must look for <THIS NODE'S RESOLVED
        HUB_ID>.MSG inside the uploaded zip -- proven unambiguously by
        mocking the parser and asserting it's invoked only when the
        zip's inner filename actually matches (a plain 0-messages
        result is the same either way, so that alone can't prove this)."""
        import io
        import zipfile
        from unittest.mock import patch
        from anetbbs.models import db, QWKNode
        from anetbbs.web.qwk_hub import import_rep_packet

        second_id = self._second_identity('qwkroute-rep-second', 'REPHUB')
        with self.app.app_context():
            node = QWKNode(packet_id='REPNODE', name='RN', password='pw',
                           is_active=True, hub_identity_id=second_id)
            db.session.add(node)
            db.session.commit()

            buf = io.BytesIO()
            with zipfile.ZipFile(buf, 'w') as zf:
                zf.writestr('REPHUB.MSG', b'')
            with patch('anetbbs.web.qwk_hub._parse_messages_dat', return_value=[]) as mock_parse:
                import_rep_packet(node, buf.getvalue())
                mock_parse.assert_called_once()

            # A zip with some OTHER identity's hub_id filename must be
            # rejected as "no matching .MSG" without ever reaching the
            # parser -- proves the lookup is this node's own identity,
            # not any hardcoded/global value.
            buf2 = io.BytesIO()
            with zipfile.ZipFile(buf2, 'w') as zf:
                zf.writestr('SOMEOTHERHUB.MSG', b'')
            with patch('anetbbs.web.qwk_hub._parse_messages_dat', return_value=[]) as mock_parse2:
                n = import_rep_packet(node, buf2.getvalue())
                mock_parse2.assert_not_called()
                self.assertEqual(n, 0)


class HubAdminQwkIdentityTests(unittest.TestCase):
    """hub_admin.py route tests: node create/edit identity assignment,
    cross-identity subscribe guard, request-approval identity stamping."""

    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.hub_admin_qwk_identity_test.db')
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

    def _admin_client(self, username):
        from anetbbs.models import db, User
        with self.app.app_context():
            u = User.query.filter_by(username=username).first()
            if not u:
                u = User(username=username, is_admin=True, email=f'{username}@example.com')
                u.set_password('x')
                db.session.add(u)
                db.session.commit()
            uid = u.id
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(uid)
            sess['_fresh'] = True
        return client

    def _second_identity(self, slug):
        from anetbbs.models import db, HubIdentity
        with self.app.app_context():
            identity = HubIdentity.query.filter_by(slug=slug).first()
            if identity is None:
                identity = HubIdentity(name=slug, slug=slug, qwk_hub_id=slug.upper()[:8],
                                       is_active=True, is_default=False)
                db.session.add(identity)
                db.session.commit()
            return identity.id

    def test_new_qwk_node_assigns_chosen_identity(self):
        second_id = self._second_identity('hubadmin-qwk-new')
        client = self._admin_client('qwk_new_admin')
        resp = client.post('/admin/echomail/hub/qwk/new', data={
            'packet_id': 'NEWNODE', 'name': 'New Node', 'password': 'secretpw',
            'sysop': '', 'email': '', 'notes': '',
            'is_active': 'y', 'hub_identity_id': str(second_id),
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        with self.app.app_context():
            from anetbbs.models import QWKNode
            node = QWKNode.query.filter_by(packet_id='NEWNODE').first()
            self.assertIsNotNone(node)
            self.assertEqual(node.hub_identity_id, second_id)

    def test_edit_qwk_node_changes_identity(self):
        from anetbbs.models import db, QWKNode, HubIdentity
        second_id = self._second_identity('hubadmin-qwk-edit')
        with self.app.app_context():
            default_id = HubIdentity.query.filter_by(is_default=True).first().id
            node = QWKNode(packet_id='EDITNODE', name='Edit Node', password='pw',
                           is_active=True, hub_identity_id=default_id)
            db.session.add(node)
            db.session.commit()
            node_id = node.id

        client = self._admin_client('qwk_edit_admin')
        resp = client.post(f'/admin/echomail/hub/qwk/{node_id}/edit', data={
            'packet_id': 'EDITNODE', 'name': 'Edit Node',
            'sysop': '', 'email': '', 'notes': '',
            'is_active': 'y', 'hub_identity_id': str(second_id),
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        with self.app.app_context():
            node = QWKNode.query.get(node_id)
            self.assertEqual(node.hub_identity_id, second_id)

    def test_subscribe_blocks_cross_identity_area(self):
        from anetbbs.models import db, QWKNode, EchomailNetwork, EchoArea
        second_id = self._second_identity('hubadmin-qwk-subscribe-guard')
        with self.app.app_context():
            node = QWKNode(packet_id='GUARDNODE', name='Guard', password='pw',
                           is_active=True, hub_identity_id=second_id)
            db.session.add(node)

            net = EchomailNetwork(name='DefaultIdentityNet', network_type='qwk',
                                  is_active=True)  # hub_identity_id defaults
            db.session.add(net)
            db.session.flush()
            area = EchoArea(tag='DEF.AREA', name='Default Area',
                            network_id=net.id, is_active=True)
            db.session.add(area)
            db.session.commit()
            node_id, area_id = node.id, area.id

        client = self._admin_client('qwk_guard_admin')
        resp = client.post(f'/admin/echomail/hub/qwk/{node_id}/subscribe', data={
            'area_id': str(area_id), 'action': 'subscribe',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        with self.app.app_context():
            from anetbbs.models import QWKNodeLastSent
            sub = QWKNodeLastSent.query.filter_by(node_id=node_id, echo_area_id=area_id).first()
            self.assertIsNone(sub, 'cross-identity subscription must be refused')

    def test_subscribe_allows_same_identity_area(self):
        from anetbbs.models import db, QWKNode, EchomailNetwork, EchoArea
        second_id = self._second_identity('hubadmin-qwk-subscribe-ok')
        with self.app.app_context():
            node = QWKNode(packet_id='OKNODE', name='Ok', password='pw',
                           is_active=True, hub_identity_id=second_id)
            db.session.add(node)
            net = EchomailNetwork(name='SecondIdentityNet', network_type='qwk',
                                  is_active=True, hub_identity_id=second_id)
            db.session.add(net)
            db.session.flush()
            area = EchoArea(tag='SEC.AREA', name='Second Area',
                            network_id=net.id, is_active=True)
            db.session.add(area)
            db.session.commit()
            node_id, area_id = node.id, area.id

        client = self._admin_client('qwk_ok_admin')
        client.post(f'/admin/echomail/hub/qwk/{node_id}/subscribe', data={
            'area_id': str(area_id), 'action': 'subscribe',
        }, follow_redirects=True)
        with self.app.app_context():
            from anetbbs.models import QWKNodeLastSent
            sub = QWKNodeLastSent.query.filter_by(node_id=node_id, echo_area_id=area_id).first()
            self.assertIsNotNone(sub, 'same-identity subscription should succeed')

    def test_subscribe_all_excludes_other_identity_areas(self):
        from anetbbs.models import db, QWKNode, EchomailNetwork, EchoArea
        second_id = self._second_identity('hubadmin-qwk-suball')
        with self.app.app_context():
            node = QWKNode(packet_id='SUBALLNODE', name='SubAll', password='pw',
                           is_active=True, hub_identity_id=second_id)
            db.session.add(node)

            own_net = EchomailNetwork(name='SubAllOwnNet', network_type='qwk',
                                      is_active=True, hub_identity_id=second_id)
            other_net = EchomailNetwork(name='SubAllOtherNet', network_type='qwk',
                                        is_active=True)  # defaults to default identity
            db.session.add_all([own_net, other_net])
            db.session.flush()
            own_area = EchoArea(tag='SUBALL.OWN', name='Own', network_id=own_net.id, is_active=True)
            other_area = EchoArea(tag='SUBALL.OTHER', name='Other', network_id=other_net.id, is_active=True)
            db.session.add_all([own_area, other_area])
            db.session.commit()
            node_id, own_net_id, other_net_id = node.id, own_net.id, other_net.id
            own_area_id, other_area_id = own_area.id, other_area.id

        client = self._admin_client('qwk_suball_admin')
        client.post(f'/admin/echomail/hub/qwk/{node_id}/subscribe-all', data={
            'network_ids': [str(own_net_id), str(other_net_id)],
        }, follow_redirects=True)
        with self.app.app_context():
            from anetbbs.models import QWKNodeLastSent
            subscribed = {s.echo_area_id for s in
                         QWKNodeLastSent.query.filter_by(node_id=node_id).all()}
            self.assertIn(own_area_id, subscribed)
            self.assertNotIn(other_area_id, subscribed)

    def test_approve_qwk_request_stamps_identity(self):
        from anetbbs.models import db, QWKNodeRequest, QWKNode
        second_id = self._second_identity('hubadmin-qwk-approve')
        with self.app.app_context():
            req = QWKNodeRequest(bbs_name='ApproveIdentityBBS', packet_id='APRVID',
                                 hub_identity_id=second_id, status='pending')
            db.session.add(req)
            db.session.commit()
            req_id = req.id

        client = self._admin_client('qwk_approve_identity_admin')
        client.post(f'/admin/echomail/hub/qwk/requests/{req_id}/approve',
                    follow_redirects=True)
        with self.app.app_context():
            req = QWKNodeRequest.query.get(req_id)
            node = QWKNode.query.get(req.node_id)
            self.assertEqual(node.hub_identity_id, second_id)

    def test_qwk_node_request_defaults_to_default_identity(self):
        """The terminal wizard / /qwkhub/apply API never set
        hub_identity_id explicitly -- confirms the model-level default
        (matching every other hub_identity_id FK) picks it up
        automatically, so those unmodified call sites need no changes."""
        from anetbbs.models import db, QWKNodeRequest, HubIdentity
        with self.app.app_context():
            req = QWKNodeRequest(bbs_name='DefaultIdentityReqBBS', packet_id='DEFREQ')
            db.session.add(req)
            db.session.commit()
            default_id = HubIdentity.query.filter_by(is_default=True).first().id
            self.assertEqual(req.hub_identity_id, default_id)


if __name__ == '__main__':
    unittest.main()
