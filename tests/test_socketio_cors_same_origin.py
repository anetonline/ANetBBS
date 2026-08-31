"""Regression test for a real Medium-severity finding from a security/
performance audit (2026-08-31): web_app.py's create_app() explicitly
passed cors_allowed_origins="*" to socketio.init_app(), overriding
python-engineio's own safe default. "*" lets ANY origin's page open a
Socket.IO connection to this server carrying the visiting browser's
ANetBBS session cookie -- every real client in this app connects
same-origin (`io('/', ...)` in base.html, `io('/term', ...)` in
terminal/index.html, etc.), so there is no legitimate cross-origin use
case here. Fixed by simply not overriding the kwarg: engineio's default
(cors_allowed_origins=None) derives the allowed origin from the
request's own scheme+host (and the X-Forwarded-* pair when present),
restricting to same-origin with zero new sysop configuration needed.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class SocketIOCorsSameOriginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.socketio_cors_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app, socketio
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.socketio = socketio

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def test_cors_allowed_origins_is_not_wildcarded(self):
        configured = self.socketio.server.eio.cors_allowed_origins
        self.assertNotEqual(
            configured, '*',
            'Socket.IO must not accept connections from any origin -- '
            'every real client here connects same-origin, so "*" only '
            "grants a cross-site page the ability to ride a visitor's "
            'session cookie into a live Socket.IO connection')

    def test_cross_origin_polling_handshake_is_rejected(self):
        """End-to-end confirmation, not just the config value: a
        request carrying a foreign Origin header must not receive the
        engineio CORS allow header, and a request whose Origin doesn't
        match its own Host must be rejected outright by the polling
        handshake -- the actual mechanism the "*" misconfiguration
        would have defeated."""
        client = self.app.test_client()
        resp = client.get(
            '/socket.io/?EIO=4&transport=polling',
            headers={'Origin': 'https://evil.example.com'})
        allow_origin = resp.headers.get('Access-Control-Allow-Origin')
        self.assertNotEqual(
            allow_origin, '*',
            'a cross-origin polling handshake must not be told "*" is '
            'allowed')
        self.assertNotEqual(
            allow_origin, 'https://evil.example.com',
            "a foreign origin's own polling handshake must not be "
            'echoed back as allowed')


if __name__ == '__main__':
    unittest.main()
