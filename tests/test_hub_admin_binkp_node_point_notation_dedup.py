"""Regression test for a follow-up audit finding (v1.1.50-present sweep,
2026-10-28): `new_binkp_node()`'s duplicate-address check used an exact
string match (`BinkPNode.query.filter_by(ftn_address=...)`), which
missed that "21:3/100" and "21:3/100.0" name the same real FTN address
-- a sibling gap to the real hub-recognition bug fixed in
`binkp_server.py` for v1.1.53 (connection-time matching), which already
got point-notation-aware address comparison via
`anetbbs.echomail.routing.parse_address()`/`format_address()`.

Without this fix, an admin could end up with two BinkPNode rows for
the same real peer just by typing the point suffix differently between
the two creation attempts.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


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


class BinkPNodePointNotationDedupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
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

    def _create_node(self, client, name, ftn_address):
        return client.post('/admin/echomail/hub/binkp/new', data={
            'name': name, 'ftn_address': ftn_address,
            'password': 'secret123',
            'sysop': '', 'system_name': '', 'location': '', 'email': '',
            'phone': '', 'baud': '115200',
            'is_active': 'y', 'notes': '',
        }, follow_redirects=True)

    def test_bare_address_rejected_as_duplicate_of_explicit_zero_point(self):
        app = _make_app(str(Path(self._tmp.name) / 'a.db'))
        client = app.test_client()
        self._login_as_admin(app, client)
        from anetbbs.models import db, BinkPNode
        with app.app_context():
            db.create_all()

        self._create_node(client, 'First', '21:3/100.0')
        self._create_node(client, 'Second', '21:3/100')

        with app.app_context():
            matching = BinkPNode.query.filter(
                BinkPNode.ftn_address.in_(['21:3/100', '21:3/100.0'])).all()
            self.assertEqual(len(matching), 1,
                             '21:3/100 and 21:3/100.0 are the same real '
                             'address and must not both get a row')

    def test_explicit_zero_point_rejected_as_duplicate_of_bare_address(self):
        app = _make_app(str(Path(self._tmp.name) / 'b.db'))
        client = app.test_client()
        self._login_as_admin(app, client)
        from anetbbs.models import db, BinkPNode
        with app.app_context():
            db.create_all()

        self._create_node(client, 'First', '21:3/100')
        self._create_node(client, 'Second', '21:3/100.0')

        with app.app_context():
            matching = BinkPNode.query.filter(
                BinkPNode.ftn_address.in_(['21:3/100', '21:3/100.0'])).all()
            self.assertEqual(len(matching), 1)

    def test_genuinely_different_addresses_are_not_rejected(self):
        app = _make_app(str(Path(self._tmp.name) / 'c.db'))
        client = app.test_client()
        self._login_as_admin(app, client)
        from anetbbs.models import db, BinkPNode
        with app.app_context():
            db.create_all()

        self._create_node(client, 'First', '21:3/100')
        self._create_node(client, 'Second', '21:3/101')

        with app.app_context():
            self.assertEqual(BinkPNode.query.count(), 2)

    def test_non_ftn_address_still_falls_back_to_exact_string_match(self):
        """Not every value typed in here is guaranteed to parse as a
        valid FTN address -- the dedup check must not crash or silently
        allow a plain-string duplicate through in that case."""
        app = _make_app(str(Path(self._tmp.name) / 'd.db'))
        client = app.test_client()
        self._login_as_admin(app, client)
        from anetbbs.models import db, BinkPNode
        with app.app_context():
            db.create_all()

        self._create_node(client, 'First', 'not-a-real-address')
        self._create_node(client, 'Second', 'not-a-real-address')

        with app.app_context():
            self.assertEqual(BinkPNode.query.count(), 1)


if __name__ == '__main__':
    unittest.main()
