"""Regression test for a live-caught QWK/BinkP node edit bug.

Jerry reported: a sysop joined ANotherNetwork as a QWK node with a
typo'd name/packet ID, and there was no way to fix it without also
resetting the node's password -- which Jerry doesn't know (it was
hub-generated and given to the node's sysop privately), so fixing a
typo would have silently locked them out. He ended up deleting the
node and having them re-register from scratch.

Root cause: `QWKNodeForm.password` (and the identical `BinkPNodeForm.
password`) was declared with `DataRequired()`. The edit routes
(`edit_qwk_node`/`edit_binkp_node`) already had the *correct* logic --
`if form.password.data: node.password = ...` -- meaning "leave blank to
keep the current password" was always the intent, and the template
already said so. But `DataRequired()` rejects the whole submission
before that logic ever runs whenever the password box is left blank
(which is always, on a GET of the edit page -- PasswordField never
re-renders the stored value), so no field could ever be edited without
retyping a brand new password, silently rotating live credentials.

Fixed by changing `password` to `Optional()` on both forms, with an
explicit "password required" check added to the two *create* routes
(`new_qwk_node`/`new_binkp_node`) so new nodes still can't be created
with a blank password -- only editing an existing node's other fields
without touching its password is now actually possible.
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


class HubNodeEditPasswordTests(unittest.TestCase):
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

    def test_qwk_node_edit_with_blank_password_keeps_current_password_and_saves_tag(self):
        app = _make_app(str(Path(self._tmp.name) / 'a.db'))
        client = app.test_client()
        self._login_as_admin(app, client)

        from anetbbs.models import db, QWKNode
        with app.app_context():
            db.create_all()
            node = QWKNode(packet_id='WRONGID', name='Typo Name', password='original-secret')
            db.session.add(node)
            db.session.commit()
            node_id = node.id

        resp = client.post(f'/admin/echomail/hub/qwk/{node_id}/edit', data={
            'packet_id': 'FIXEDID',
            'name': 'Correct Name',
            'sysop': '', 'email': '',
            'password': '',   # left blank -- must NOT be rejected, must NOT clear the password
            'is_active': 'y',
            'notes': '',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with app.app_context():
            saved = QWKNode.query.get(node_id)
            self.assertEqual(saved.packet_id, 'FIXEDID',
                              'tag/packet_id fix must be saved even with password left blank')
            self.assertEqual(saved.name, 'Correct Name')
            self.assertEqual(saved.password, 'original-secret',
                              'password must be left untouched when the field is blank')

    def test_qwk_node_create_still_requires_a_password(self):
        app = _make_app(str(Path(self._tmp.name) / 'b.db'))
        client = app.test_client()
        self._login_as_admin(app, client)
        from anetbbs.models import db
        with app.app_context():
            db.create_all()

        resp = client.post('/admin/echomail/hub/qwk/new', data={
            'packet_id': 'NEWNODE', 'name': 'New Node',
            'sysop': '', 'email': '',
            'password': '',   # blank on CREATE must still be rejected
            'is_active': 'y', 'notes': '',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        from anetbbs.models import QWKNode
        with app.app_context():
            self.assertIsNone(QWKNode.query.filter_by(packet_id='NEWNODE').first(),
                               'a node must not be created with a blank password')

    def test_binkp_node_edit_with_blank_password_keeps_current_password(self):
        app = _make_app(str(Path(self._tmp.name) / 'c.db'))
        client = app.test_client()
        self._login_as_admin(app, client)

        from anetbbs.models import db, BinkPNode
        with app.app_context():
            db.create_all()
            node = BinkPNode(name='Typo Name', ftn_address='1:2/3.4',
                              password='original-secret')
            db.session.add(node)
            db.session.commit()
            node_id = node.id

        resp = client.post(f'/admin/echomail/hub/binkp/{node_id}/edit', data={
            'name': 'Correct Name',
            'ftn_address': '1:2/3.4',
            'password': '',
            'sysop': '', 'system_name': '', 'location': '', 'email': '',
            'phone': '', 'baud': '115200',
            'is_active': 'y', 'notes': '',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with app.app_context():
            saved = BinkPNode.query.get(node_id)
            self.assertEqual(saved.name, 'Correct Name')
            self.assertEqual(saved.password, 'original-secret')

    def test_binkp_node_create_still_requires_a_password(self):
        app = _make_app(str(Path(self._tmp.name) / 'd.db'))
        client = app.test_client()
        self._login_as_admin(app, client)
        from anetbbs.models import db
        with app.app_context():
            db.create_all()

        resp = client.post('/admin/echomail/hub/binkp/new', data={
            'name': 'New Node', 'ftn_address': '9:9/9.9',
            'password': '',
            'sysop': '', 'system_name': '', 'location': '', 'email': '',
            'phone': '', 'baud': '115200',
            'is_active': 'y', 'notes': '',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        from anetbbs.models import BinkPNode
        with app.app_context():
            self.assertIsNone(BinkPNode.query.filter_by(ftn_address='9:9/9.9').first(),
                               'a node must not be created with a blank password')


if __name__ == '__main__':
    unittest.main()
