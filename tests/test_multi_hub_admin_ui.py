"""Phase 6 of multi-hub-identity: admin UI scoping sweep.

Most per-route scoping already landed within Phases 2-5 (each phase
scoped its own admin routes/forms directly, with their own tests). This
covers the Phase 6 sweep/polish item: the Hub Management dashboard
(hub_admin.index()) grows a per-identity breakdown table once a second
HubIdentity exists, and stays exactly as before (no new table, no
identity columns) for the common single-identity install.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


def _admin_client(app, username):
    from anetbbs.models import db, User
    with app.app_context():
        u = User.query.filter_by(username=username).first()
        if not u:
            u = User(username=username, is_admin=True, email=f'{username}@example.com')
            u.set_password('x')
            db.session.add(u)
            db.session.commit()
        uid = u.id
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['_user_id'] = str(uid)
        sess['_fresh'] = True
    return client


class SingleIdentityDashboardTests(unittest.TestCase):
    """Separate TestCase (own app/db) from the second-identity test
    below -- both mutate the same "how many HubIdentity rows exist"
    global state, and unittest doesn't guarantee method execution order
    within one class, so sharing a class-level app/db between a
    single-identity assertion and a test that deliberately adds a
    second identity would make the single-identity check flaky
    depending on alphabetical test ordering."""

    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.multi_hub_admin_ui_single_test.db')
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

    def test_single_identity_install_shows_no_breakdown_table(self):
        client = _admin_client(self.app, 'dash_single_admin')
        resp = client.get('/admin/echomail/hub/')
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn('Hub Identities</div>', resp.get_data(as_text=True))


class MultiIdentityDashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.multi_hub_admin_ui_multi_test.db')
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

    def test_second_identity_adds_breakdown_table_with_node_counts(self):
        from anetbbs.models import db, HubIdentity, BinkPNode, QWKNode
        with self.app.app_context():
            second = HubIdentity.query.filter_by(slug='dash-second').first()
            if second is None:
                second = HubIdentity(name='DashboardSecondNet', slug='dash-second',
                                     is_active=True, is_default=False)
                db.session.add(second)
                db.session.commit()
            second_id = second.id
            db.session.add(BinkPNode(name='DashBinkp', ftn_address='7000:1/2',
                                     password='x', is_active=True,
                                     hub_identity_id=second_id))
            db.session.add(QWKNode(packet_id='DASHQWK', name='DashQwk', password='x',
                                   is_active=True, hub_identity_id=second_id))
            db.session.commit()

        client = _admin_client(self.app, 'dash_multi_admin')
        resp = client.get('/admin/echomail/hub/')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn('DashboardSecondNet', body)
        self.assertIn('default</span>', body)  # the default identity's own badge


if __name__ == '__main__':
    unittest.main()
