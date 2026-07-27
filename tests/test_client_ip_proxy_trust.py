"""Regression tests for a full auth-security audit finding: FOUR separate
`_client_ip()`/`_peer_ip()` helpers (web/auth.py, web/file_areas.py,
web/registry.py, web/network_join.py) all trusted a client-supplied
X-Forwarded-For header unconditionally, with no concept of whether the
request actually came through a trusted reverse proxy. Any direct
connection could spoof it to:
  - bypass IP bans / country blocks (web/auth.py)
  - make the login-rate-limit auto-ban land on an arbitrary victim IP
    instead of the attacker's own (web/auth.py, see test_auto_ban.py)
  - dodge the registry/network-join per-IP rate limiters entirely by
    sending a fresh fake IP on every request (web/registry.py,
    web/network_join.py)
  - pollute a shared file-link's audit trail with a fake IP
    (web/file_areas.py)

Fixed by removing the manual XFF parsing everywhere -- all four now
just return request.remote_addr. The ONLY place X-Forwarded-For is
trusted is web_app.py's ProxyFix, gated behind the TRUST_PROXY_HEADERS
config flag (off by default -- fail closed), which rewrites
request.remote_addr itself at the WSGI layer when a sysop has
explicitly confirmed Flask sits behind their own trusted proxy.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class ClientIpHelpersIgnoreSpoofedHeaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.client_ip_proxy_trust_test.db')
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

    def _assert_ignores_spoofed_xff(self, fn):
        with self.app.test_request_context(
                headers={'X-Forwarded-For': '198.51.100.77'},
                environ_overrides={'REMOTE_ADDR': '10.0.0.5'}):
            self.assertEqual(fn(), '10.0.0.5',
                             'spoofed X-Forwarded-For must be ignored, '
                             'real remote_addr must be used instead')

    def test_auth_client_ip_ignores_spoofed_header(self):
        from anetbbs.web.auth import _client_ip
        self._assert_ignores_spoofed_xff(_client_ip)

    def test_file_areas_client_ip_ignores_spoofed_header(self):
        from anetbbs.web.file_areas import _client_ip
        self._assert_ignores_spoofed_xff(_client_ip)

    def test_registry_peer_ip_ignores_spoofed_header(self):
        from anetbbs.web.registry import _peer_ip
        self._assert_ignores_spoofed_xff(_peer_ip)

    def test_network_join_peer_ip_ignores_spoofed_header(self):
        from anetbbs.web.network_join import _peer_ip
        self._assert_ignores_spoofed_xff(_peer_ip)


class ProxyFixOptInTests(unittest.TestCase):
    """TRUST_PROXY_HEADERS defaults to False; when a sysop explicitly
    enables it (confirming Flask sits behind their own trusted reverse
    proxy), ProxyFix must actually take effect and X-Forwarded-For must
    then correctly become request.remote_addr."""

    def test_disabled_by_default(self):
        import anetbbs.config as cm
        self.assertFalse(cm.Config.TRUST_PROXY_HEADERS)

    def test_env_var_enables_it(self):
        os.environ['TRUST_PROXY_HEADERS'] = 'true'
        try:
            import importlib
            import anetbbs.config as cm
            importlib.reload(cm)
            self.assertTrue(cm.Config.TRUST_PROXY_HEADERS)
        finally:
            del os.environ['TRUST_PROXY_HEADERS']
            import importlib
            importlib.reload(cm)

    def test_proxyfix_applied_when_enabled_rewrites_remote_addr(self):
        # Config classes read os.environ once at class-body-execution
        # (module import) time -- setting the env var this late wouldn't
        # retroactively change the already-imported TestingConfig class
        # attribute. Set the attribute directly instead, matching this
        # test suite's established pattern for other per-test config
        # overrides (e.g. SQLALCHEMY_DATABASE_URI below).
        db_path = str(Path(__file__).resolve().parent / '.proxyfix_enabled_test.db')
        if os.path.exists(db_path):
            os.remove(db_path)
        orig_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        orig_trust = cfg_mod.TestingConfig.TRUST_PROXY_HEADERS
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path}'
        cfg_mod.TestingConfig.TRUST_PROXY_HEADERS = True
        os.environ['FLASK_ENV'] = 'testing'
        try:
            from anetbbs.web_app import create_app
            app = create_app('testing')
            app.config['TESTING'] = True
            self.assertTrue(app.config.get('TRUST_PROXY_HEADERS'))

            client = app.test_client()
            captured = {}

            @app.route('/_ip_probe_test_only')
            def _probe():
                from flask import request
                captured['ip'] = request.remote_addr
                return 'ok'

            client.get('/_ip_probe_test_only',
                      headers={'X-Forwarded-For': '203.0.113.42'},
                      environ_overrides={'REMOTE_ADDR': '127.0.0.1'})
            self.assertEqual(captured['ip'], '203.0.113.42',
                             'with TRUST_PROXY_HEADERS on, ProxyFix must '
                             'rewrite remote_addr from the trusted-proxy '
                             'hop of X-Forwarded-For')
        finally:
            cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = orig_uri
            cfg_mod.TestingConfig.TRUST_PROXY_HEADERS = orig_trust
            for suffix in ('', '-wal', '-shm'):
                p = db_path + suffix
                if os.path.exists(p):
                    os.remove(p)


if __name__ == '__main__':
    unittest.main()
