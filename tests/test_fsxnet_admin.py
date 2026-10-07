"""Tests for the fsxNet IBOL/IBLC admin settings page
(anetbbs/web/fsxnet_admin.py) -- GET /admin/fsxnet/ and
POST /admin/fsxnet/settings.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class FsxnetAdminTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.fsxnet_admin_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User, EchomailNetwork
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        with cls.app.app_context():
            db.create_all()
            admin = User(username='fsxnetadmin', email='fsxnetadmin@example.com',
                        password_hash='x', is_admin=True)
            db.session.add(admin)
            net = EchomailNetwork(name='fsxNet', network_type='binkp',
                                  our_address='21:4/999', is_active=True)
            db.session.add(net)
            db.session.commit()
            cls.admin_id = admin.id
            cls.network_id = net.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def setUp(self):
        self._orig = {
            k: self.app.config.get(k) for k in (
                'FSXNET_IBOL_ENABLED', 'FSXNET_IBLC_ENABLED', 'FSXNET_NETWORK_ID',
                'FSXNET_AREA_TAG', 'FSXNET_SYSTEM_NAME', 'FSXNET_TELNET_PORT',
                'FSXNET_HIDE_SYSOP')
        }
        self.addCleanup(self._restore)

        # settings() calls _write_env_keys(_env_path(), ...) -- _env_path()
        # resolves to the REAL project .env by default. Patch it to a
        # throwaway temp file for every test in this class, or running
        # this suite writes real FSXNET_* keys into the actual checkout's
        # .env (confirmed live: a run without this patch left
        # FSXNET_IBOL_ENABLED=true etc. sitting in the real .env
        # afterward, which then got picked up by python-dotenv in any
        # later process and silently pre-set that config value true).
        import tempfile
        from anetbbs.web import fsxnet_admin as _fsxnet_admin_mod
        self._fake_env = tempfile.NamedTemporaryFile(
            mode='w', suffix='.env', delete=False)
        self._fake_env.close()
        self.addCleanup(lambda: os.path.exists(self._fake_env.name) and os.remove(self._fake_env.name))
        self._orig_env_path = _fsxnet_admin_mod._env_path
        _fsxnet_admin_mod._env_path = lambda: self._fake_env.name
        self.addCleanup(lambda: setattr(_fsxnet_admin_mod, '_env_path', self._orig_env_path))

    def _restore(self):
        for k, v in self._orig.items():
            self.app.config[k] = v

    def _admin_client(self):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True
        return client

    def test_index_loads(self):
        client = self._admin_client()
        resp = client.get('/admin/fsxnet/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'fsxNet', resp.data)

    def test_enabling_without_a_network_is_a_clean_error(self):
        client = self._admin_client()
        resp = client.post('/admin/fsxnet/settings', data={
            'ibol_enabled': 'on', 'network_id': '', 'area_tag': 'FSX_DAT',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(self.app.config.get('FSXNET_IBOL_ENABLED'))

    def test_settings_saved_and_area_created(self):
        from anetbbs.models import EchoArea
        client = self._admin_client()
        resp = client.post('/admin/fsxnet/settings', data={
            'ibol_enabled': 'on', 'iblc_enabled': 'on',
            'network_id': str(self.network_id), 'area_tag': 'FSX_DAT',
            'system_name': 'Test BBS', 'telnet_port': '2323',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(self.app.config.get('FSXNET_IBOL_ENABLED'))
        self.assertTrue(self.app.config.get('FSXNET_IBLC_ENABLED'))
        self.assertEqual(self.app.config.get('FSXNET_SYSTEM_NAME'), 'Test BBS')
        self.assertEqual(self.app.config.get('FSXNET_TELNET_PORT'), '2323')

        with self.app.app_context():
            area = EchoArea.query.filter_by(network_id=self.network_id, tag='FSX_DAT').first()
            self.assertIsNotNone(area, 'enabling must create the FSX_DAT area right away')
            self.assertTrue(area.is_sysop_only)

    def test_invalid_telnet_port_is_a_clean_error(self):
        client = self._admin_client()
        resp = client.post('/admin/fsxnet/settings', data={
            'ibol_enabled': 'on', 'network_id': str(self.network_id),
            'area_tag': 'FSX_DAT', 'telnet_port': 'not-a-port',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(self.app.config.get('FSXNET_IBOL_ENABLED'))

    def test_qwk_network_is_rejected(self):
        from anetbbs.models import db, EchomailNetwork
        with self.app.app_context():
            qwk_net = EchomailNetwork(name='QwkOnly', network_type='qwk', is_active=True)
            db.session.add(qwk_net)
            db.session.commit()
            qwk_net_id = qwk_net.id

        client = self._admin_client()
        resp = client.post('/admin/fsxnet/settings', data={
            'ibol_enabled': 'on', 'network_id': str(qwk_net_id), 'area_tag': 'FSX_DAT',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(self.app.config.get('FSXNET_IBOL_ENABLED'))


if __name__ == '__main__':
    unittest.main()
