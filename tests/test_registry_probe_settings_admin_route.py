"""Regression test for the new Admin -> Federation Registry probe/
staleness settings form, added in response to a live complaint from
Jerry (2026-09-01): REGISTRY_HEARTBEAT_STALE_HOURS/
REGISTRY_PROBE_INTERVAL_SEC/REGISTRY_PROBE_FAILURE_THRESHOLD used to be
.env-only with zero admin-UI exposure, so "make it configurable" meant
hand-editing .env and restarting the service. This is the new
POST /admin/registry/probe-settings route
(anetbbs/web/admin.py:registry_probe_settings) plus its form in
admin/registry.html.
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


class RegistryProbeSettingsAdminRouteTests(unittest.TestCase):
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

    def _make_env_file(self):
        env_path = Path(self._tmp.name) / '.env'
        env_path.write_text('FLASK_ENV=production\nSECRET_KEY=x\n', encoding='utf-8')
        return env_path

    def test_registry_index_shows_current_values(self):
        app = _make_app(str(Path(self._tmp.name) / 'a.db'))
        app.config['REGISTRY_PROBE_INTERVAL_SEC'] = 1800
        app.config['REGISTRY_PROBE_FAILURE_THRESHOLD'] = 40
        app.config['REGISTRY_HEARTBEAT_STALE_HOURS'] = 36
        client = app.test_client()
        self._login_as_admin(app, client)
        from anetbbs.models import db
        with app.app_context():
            db.create_all()

        resp = client.get('/admin/registry/')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn('value="1800"', body)
        self.assertIn('value="40"', body)
        self.assertIn('value="36"', body)

    def test_valid_submission_persists_to_env_and_live_config(self):
        app = _make_app(str(Path(self._tmp.name) / 'b.db'))
        client = app.test_client()
        self._login_as_admin(app, client)
        from anetbbs.models import db
        with app.app_context():
            db.create_all()

        env_path = self._make_env_file()
        import anetbbs.web.admin as admin_mod
        orig_env_path = admin_mod._env_path
        admin_mod._env_path = lambda: str(env_path)
        self.addCleanup(lambda: setattr(admin_mod, '_env_path', orig_env_path))

        resp = client.post('/admin/registry/probe-settings', data={
            'probe_interval_sec': '900',
            'probe_failure_threshold': '10',
            'heartbeat_stale_hours': '24',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        self.assertEqual(app.config['REGISTRY_PROBE_INTERVAL_SEC'], 900,
                         'live config must update immediately, no restart needed')
        self.assertEqual(app.config['REGISTRY_PROBE_FAILURE_THRESHOLD'], 10)
        self.assertEqual(app.config['REGISTRY_HEARTBEAT_STALE_HOURS'], 24)

        saved = env_path.read_text(encoding='utf-8')
        self.assertIn('REGISTRY_PROBE_INTERVAL_SEC=900', saved)
        self.assertIn('REGISTRY_PROBE_FAILURE_THRESHOLD=10', saved)
        self.assertIn('REGISTRY_HEARTBEAT_STALE_HOURS=24', saved)

    def test_invalid_submission_is_rejected_and_does_not_change_config(self):
        app = _make_app(str(Path(self._tmp.name) / 'c.db'))
        app.config['REGISTRY_PROBE_INTERVAL_SEC'] = 3600
        client = app.test_client()
        self._login_as_admin(app, client)
        from anetbbs.models import db
        with app.app_context():
            db.create_all()

        resp = client.post('/admin/registry/probe-settings', data={
            'probe_interval_sec': 'not-a-number',
            'probe_failure_threshold': '10',
            'heartbeat_stale_hours': '24',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(app.config['REGISTRY_PROBE_INTERVAL_SEC'], 3600,
                         'a bad submission must not silently corrupt the live config')

    def test_interval_below_60_seconds_is_rejected(self):
        app = _make_app(str(Path(self._tmp.name) / 'd.db'))
        app.config['REGISTRY_PROBE_INTERVAL_SEC'] = 3600
        client = app.test_client()
        self._login_as_admin(app, client)
        from anetbbs.models import db
        with app.app_context():
            db.create_all()

        resp = client.post('/admin/registry/probe-settings', data={
            'probe_interval_sec': '5',
            'probe_failure_threshold': '10',
            'heartbeat_stale_hours': '24',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(app.config['REGISTRY_PROBE_INTERVAL_SEC'], 3600)


if __name__ == '__main__':
    unittest.main()
