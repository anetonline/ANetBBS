"""Phase 3 of multi-hub-identity: per-identity nodelist generation.

generate_nodelist() (anetbbs/echomail/nodelist.py) previously listed
every active BinkPNode regardless of which hub identity it belonged to,
and both callers (hub_admin.nodelist() HTTP route, write_nodelist_to_area()
scheduled handler) hardcoded zone=1200/net=1/hub_node=1/'ANotherNetwork'
instead of reading a HubIdentity row. These tests prove a second,
non-default identity gets its own correctly-scoped nodelist -- distinct
zone:net, distinct node list -- while the default identity's output is
unchanged (same seeded values it always had).
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class NodelistGenerateFunctionTests(unittest.TestCase):
    """Pure generate_nodelist() tests -- needs an app context for the
    BinkPNode query but no HTTP layer."""

    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.nodelist_fn_test.db')
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

    def _identity(self, slug, **kw):
        from anetbbs.models import db, HubIdentity
        with self.app.app_context():
            identity = HubIdentity.query.filter_by(slug=slug).first()
            if identity is None:
                identity = HubIdentity(name=kw.pop('name', slug), slug=slug,
                                       is_active=True, is_default=False, **kw)
                db.session.add(identity)
                db.session.commit()
            return identity.id

    def test_no_identity_filter_lists_all_nodes(self):
        from anetbbs.models import db, BinkPNode
        from anetbbs.echomail.nodelist import generate_nodelist
        id_a = self._identity('nl-fn-a')
        id_b = self._identity('nl-fn-b')
        with self.app.app_context():
            db.session.add(BinkPNode(name='NodeA', ftn_address='4000:1/11',
                                     password='x', is_active=True, hub_identity_id=id_a))
            db.session.add(BinkPNode(name='NodeB', ftn_address='4000:1/12',
                                     password='x', is_active=True, hub_identity_id=id_b))
            db.session.commit()
            content = generate_nodelist(4000, 1, 1, 'TestHub', 'Loc', 'Sysop')
        self.assertIn('NodeA', content)
        self.assertIn('NodeB', content)

    def test_identity_filter_scopes_to_just_that_identity(self):
        from anetbbs.models import db, BinkPNode
        from anetbbs.echomail.nodelist import generate_nodelist
        id_a = self._identity('nl-fn-scope-a')
        id_b = self._identity('nl-fn-scope-b')
        with self.app.app_context():
            db.session.add(BinkPNode(name='ScopeNodeA', ftn_address='4001:1/11',
                                     password='x', is_active=True, hub_identity_id=id_a))
            db.session.add(BinkPNode(name='ScopeNodeB', ftn_address='4001:1/12',
                                     password='x', is_active=True, hub_identity_id=id_b))
            db.session.commit()
            content = generate_nodelist(4001, 1, 1, 'TestHub', 'Loc', 'Sysop',
                                        hub_identity_id=id_a)
        self.assertIn('ScopeNodeA', content)
        self.assertNotIn('ScopeNodeB', content)


class NodelistRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.nodelist_route_test.db')
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

    def _second_identity(self, slug, **kw):
        from anetbbs.models import db, HubIdentity
        with self.app.app_context():
            identity = HubIdentity.query.filter_by(slug=slug).first()
            if identity is None:
                identity = HubIdentity(
                    name=kw.pop('name', 'SecondNodelistNet'), slug=slug,
                    binkp_zone=kw.pop('binkp_zone', 5000),
                    binkp_net=kw.pop('binkp_net', 2),
                    binkp_hub_node=kw.pop('binkp_hub_node', 1),
                    is_active=True, is_default=False, **kw)
                db.session.add(identity)
                db.session.commit()
            return identity.id

    def test_default_route_no_slug_returns_nodelist(self):
        client = self.app.test_client()
        resp = client.get('/admin/echomail/hub/nodelist')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'ANotherNetwork', resp.data)

    def test_unknown_slug_404s(self):
        client = self.app.test_client()
        resp = client.get('/admin/echomail/hub/nodelist/no-such-identity')
        self.assertEqual(resp.status_code, 404)

    def test_inactive_identity_slug_404s(self):
        from anetbbs.models import db, HubIdentity
        with self.app.app_context():
            db.session.add(HubIdentity(name='InactiveNL', slug='inactive-nl',
                                       is_active=False, is_default=False))
            db.session.commit()
        client = self.app.test_client()
        resp = client.get('/admin/echomail/hub/nodelist/inactive-nl')
        self.assertEqual(resp.status_code, 404)

    def test_second_identity_nodelist_has_its_own_zone_net_and_nodes(self):
        from anetbbs.models import db, BinkPNode
        second_id = self._second_identity('nl-route-second', binkp_zone=5001, binkp_net=3)
        default_binkp_zone_helper = self._second_identity('nl-route-default-helper')
        with self.app.app_context():
            db.session.add(BinkPNode(name='RouteScopedNode', ftn_address='5001:3/2',
                                     password='x', is_active=True,
                                     hub_identity_id=second_id))
            db.session.add(BinkPNode(name='OtherIdentityNode', ftn_address='9:9/9',
                                     password='x', is_active=True,
                                     hub_identity_id=default_binkp_zone_helper))
            db.session.commit()

        client = self.app.test_client()
        resp = client.get('/admin/echomail/hub/nodelist/nl-route-second')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn('Zone,5001,', body)
        self.assertIn('RouteScopedNode', body)
        self.assertNotIn('OtherIdentityNode', body)

    def test_default_identity_nodelist_excludes_other_identity_nodes(self):
        from anetbbs.models import db, BinkPNode
        second_id = self._second_identity('nl-route-exclusion-check')
        with self.app.app_context():
            db.session.add(BinkPNode(name='ExcludedFromDefault', ftn_address='5002:9/2',
                                     password='x', is_active=True,
                                     hub_identity_id=second_id))
            db.session.commit()

        client = self.app.test_client()
        resp = client.get('/admin/echomail/hub/nodelist')
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn('ExcludedFromDefault', resp.get_data(as_text=True))

    def test_404_without_hub_mode(self):
        self.app.config['REGISTRY_MODE_ENABLED'] = False
        try:
            client = self.app.test_client()
            resp = client.get('/admin/echomail/hub/nodelist')
            self.assertEqual(resp.status_code, 404)
        finally:
            self.app.config['REGISTRY_MODE_ENABLED'] = True


class WriteNodelistToAreaTests(unittest.TestCase):
    """write_nodelist_to_area() writes to a real FileArea's storage_path
    -- tested with a temp storage dir and a minimal seeded FileArea row,
    not the real ANN.FILES.NODELIST seed data."""

    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.nodelist_write_test.db')
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
        self.storage_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__('shutil').rmtree(self.storage_dir, ignore_errors=True))
        from anetbbs.models import db, FileArea
        with self.app.app_context():
            if not FileArea.query.filter_by(tag='ANN.FILES.NODELIST').first():
                db.session.add(FileArea(tag='ANN.FILES.NODELIST', name='Nodelist',
                                        storage_path=self.storage_dir, is_active=True))
                db.session.commit()
            else:
                area = FileArea.query.filter_by(tag='ANN.FILES.NODELIST').first()
                area.storage_path = self.storage_dir
                db.session.commit()

    def test_writes_default_identity_by_default(self):
        from anetbbs.echomail.nodelist import write_nodelist_to_area
        with self.app.app_context():
            summary = write_nodelist_to_area()
        self.assertIn('NODELIST.', summary)
        files = [f for f in os.listdir(self.storage_dir) if f.upper().startswith('NODELIST.')]
        self.assertEqual(len(files), 1)

    def test_explicit_identity_uses_its_own_zone_net_and_nodes(self):
        from anetbbs.models import db, HubIdentity, BinkPNode
        from anetbbs.echomail.nodelist import write_nodelist_to_area
        with self.app.app_context():
            identity = HubIdentity(name='WriteAreaSecond', slug='write-area-second',
                                   binkp_zone=6000, binkp_net=1, binkp_hub_node=1,
                                   is_active=True, is_default=False)
            db.session.add(identity)
            db.session.commit()
            db.session.add(BinkPNode(name='WriteAreaNode', ftn_address='6000:1/2',
                                     password='x', is_active=True,
                                     hub_identity_id=identity.id))
            db.session.commit()

            write_nodelist_to_area(hub_identity=identity)

        files = [f for f in os.listdir(self.storage_dir) if f.upper().startswith('NODELIST.')]
        self.assertEqual(len(files), 1)
        with open(os.path.join(self.storage_dir, files[0])) as f:
            content = f.read()
        self.assertIn('Zone,6000,', content)
        self.assertIn('WriteAreaNode', content)

    def test_second_call_replaces_prior_file_not_accumulates(self):
        from anetbbs.echomail.nodelist import write_nodelist_to_area
        with self.app.app_context():
            write_nodelist_to_area()
            write_nodelist_to_area()
        files = [f for f in os.listdir(self.storage_dir) if f.upper().startswith('NODELIST.')]
        self.assertEqual(len(files), 1)


if __name__ == '__main__':
    unittest.main()
