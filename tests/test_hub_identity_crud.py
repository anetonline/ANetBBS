"""Route tests for HubIdentity admin CRUD (anetbbs/web/hub_admin.py's
/admin/echomail/hub/identities/* routes) -- Phase 1 of the
multi-hub-identity feature. Purely additive: nothing else in the app
consumes HubIdentity yet, so these tests only cover the CRUD itself
(create, edit, set-default invariant, delete guards).
"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_DATA_DIR = Path(__file__).resolve().parents[1] / 'data'


def _snapshot_data_dir():
    if not _DATA_DIR.is_dir():
        return set()
    return set(_DATA_DIR.iterdir())


def _make_app(db_path):
    import anetbbs.config as cfg_mod
    if os.path.exists(db_path):
        os.remove(db_path)
    cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path}'
    os.environ['FLASK_ENV'] = 'testing'

    from anetbbs.web_app import create_app
    app = create_app('testing')
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    app.config['REGISTRY_MODE_ENABLED'] = True
    return app


class HubIdentityCrudTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._data_dir_before = _snapshot_data_dir()
        import anetbbs.config as cfg_mod
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._orig_flask_env = os.environ.get('FLASK_ENV')

    @classmethod
    def tearDownClass(cls):
        import anetbbs.config as cfg_mod
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        if cls._orig_flask_env is None:
            os.environ.pop('FLASK_ENV', None)
        else:
            os.environ['FLASK_ENV'] = cls._orig_flask_env
        for entry in _snapshot_data_dir() - cls._data_dir_before:
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _login_as_admin(self, app, client):
        from anetbbs.models import db, User
        with app.app_context():
            admin = User.query.filter_by(username='admin').first()
            if admin is None:
                admin = User(username='admin', email='admin@example.com', is_admin=True)
                admin.set_password('password123')
                db.session.add(admin)
                db.session.commit()
            admin_id = admin.id
        with client.session_transaction() as sess:
            sess['_user_id'] = str(admin_id)
            sess['_fresh'] = True

    def test_create_second_identity(self):
        app = _make_app(str(Path(self._tmp.name) / 'a.db'))
        client = app.test_client()
        self._login_as_admin(app, client)

        resp = client.post('/admin/echomail/hub/identities/new', data={
            'name': 'Second Network', 'slug': 'secondnet',
            'qwk_hub_id': 'SECOND', 'binkp_zone': '2', 'binkp_net': '1',
            'binkp_hub_node': '1', 'binkp_domain': 'secnet',
            'nodelist_sysop': '', 'nodelist_location': '',
            'nodelist_phone': '', 'nodelist_speed': '115200',
            'is_active': 'y',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        from anetbbs.models import HubIdentity
        with app.app_context():
            identities = HubIdentity.query.order_by(HubIdentity.name).all()
            self.assertEqual(len(identities), 2)
            second = HubIdentity.query.filter_by(slug='secondnet').first()
            self.assertIsNotNone(second)
            self.assertEqual(second.qwk_hub_id, 'SECOND')
            self.assertEqual(second.binkp_zone, 2)
            self.assertFalse(second.is_default,
                              'a newly created identity must not silently steal default')

    def test_duplicate_slug_rejected(self):
        app = _make_app(str(Path(self._tmp.name) / 'b.db'))
        client = app.test_client()
        self._login_as_admin(app, client)
        from anetbbs.models import HubIdentity
        with app.app_context():
            default_slug = HubIdentity.query.filter_by(is_default=True).one().slug

        resp = client.post('/admin/echomail/hub/identities/new', data={
            'name': 'Collides', 'slug': default_slug,
            'binkp_hub_node': '1', 'nodelist_speed': '115200',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        with app.app_context():
            self.assertEqual(HubIdentity.query.count(), 1,
                              'duplicate slug must not create a second row')

    def test_set_default_moves_flag_off_old_default(self):
        app = _make_app(str(Path(self._tmp.name) / 'c.db'))
        client = app.test_client()
        self._login_as_admin(app, client)
        from anetbbs.models import db, HubIdentity
        with app.app_context():
            original_default_id = HubIdentity.query.filter_by(is_default=True).one().id
            second = HubIdentity(name='Second', slug='second2',
                                 binkp_zone=2, binkp_net=1, binkp_hub_node=1)
            db.session.add(second)
            db.session.commit()
            second_id = second.id

        resp = client.post(f'/admin/echomail/hub/identities/{second_id}/set-default',
                           follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with app.app_context():
            defaults = HubIdentity.query.filter_by(is_default=True).all()
            self.assertEqual(len(defaults), 1, 'exactly one row must be default')
            self.assertEqual(defaults[0].id, second_id)
            old = HubIdentity.query.get(original_default_id)
            self.assertFalse(old.is_default)

    def test_cannot_delete_the_only_identity(self):
        app = _make_app(str(Path(self._tmp.name) / 'd.db'))
        client = app.test_client()
        self._login_as_admin(app, client)
        from anetbbs.models import HubIdentity
        with app.app_context():
            only_id = HubIdentity.query.one().id

        resp = client.post(f'/admin/echomail/hub/identities/{only_id}/delete',
                           follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        with app.app_context():
            self.assertEqual(HubIdentity.query.count(), 1)

    def test_cannot_delete_the_default_identity_even_with_others_present(self):
        app = _make_app(str(Path(self._tmp.name) / 'e.db'))
        client = app.test_client()
        self._login_as_admin(app, client)
        from anetbbs.models import db, HubIdentity
        with app.app_context():
            default_id = HubIdentity.query.filter_by(is_default=True).one().id
            second = HubIdentity(name='Second', slug='second3',
                                 binkp_zone=2, binkp_net=1, binkp_hub_node=1)
            db.session.add(second)
            db.session.commit()

        resp = client.post(f'/admin/echomail/hub/identities/{default_id}/delete',
                           follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        with app.app_context():
            self.assertIsNotNone(HubIdentity.query.get(default_id),
                                  'default identity must survive the delete attempt')
            self.assertEqual(HubIdentity.query.count(), 2)

    def test_cannot_delete_identity_with_nodes_assigned(self):
        app = _make_app(str(Path(self._tmp.name) / 'f.db'))
        client = app.test_client()
        self._login_as_admin(app, client)
        from anetbbs.models import db, HubIdentity, BinkPNode
        with app.app_context():
            second = HubIdentity(name='Second', slug='second4',
                                 binkp_zone=2, binkp_net=1, binkp_hub_node=1)
            db.session.add(second)
            db.session.commit()
            node = BinkPNode(name='Node A', ftn_address='2:1/100', password='x',
                             hub_identity_id=second.id)
            db.session.add(node)
            db.session.commit()
            second_id = second.id

        resp = client.post(f'/admin/echomail/hub/identities/{second_id}/delete',
                           follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        with app.app_context():
            self.assertIsNotNone(HubIdentity.query.get(second_id),
                                  'identity with a node still assigned must not be deletable')

    def test_delete_succeeds_once_unused_and_not_default(self):
        app = _make_app(str(Path(self._tmp.name) / 'g.db'))
        client = app.test_client()
        self._login_as_admin(app, client)
        from anetbbs.models import db, HubIdentity
        with app.app_context():
            second = HubIdentity(name='Unused', slug='unused',
                                 binkp_zone=3, binkp_net=1, binkp_hub_node=1)
            db.session.add(second)
            db.session.commit()
            second_id = second.id

        resp = client.post(f'/admin/echomail/hub/identities/{second_id}/delete',
                           follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        with app.app_context():
            self.assertIsNone(HubIdentity.query.get(second_id))


if __name__ == '__main__':
    unittest.main()
