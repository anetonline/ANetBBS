"""Real local WebSockets revoke the signed BBS login used at upgrade."""
import asyncio
from datetime import timedelta
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from flask import Flask
from flask_login.utils import encode_cookie
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from anetbbs.config import TestingConfig
from anetbbs.models import User, db
from mrc.bridge.main import BridgeApp


class _FakeMRC:
    connected = True

    async def send_packet(self, _packet):
        pass


class MRCCookieRevocationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {
            'FLASK_ENV': 'testing',
            'DATABASE_URL': 'sqlite:///' + str(self.root / 'bbs.db'),
        })
        self.env.start()
        self.key = patch.object(TestingConfig, 'SECRET_KEY', 'mrc-cookie-test-key')
        self.key.start()
        self.interval = patch('mrc.bridge.main.BBS_AUTH_RECHECK_SECONDS', 60)
        self.interval.start()
        self.engine = create_engine(os.environ['DATABASE_URL'])
        db.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            for name in ('alice', 'bob'):
                user = User(username=name, email=name + '@example.com')
                user.set_password('initial-password')
                session.add(user)
            session.commit()
        self.cookie_app = Flask(__name__)
        self.cookie_app.config.from_object(TestingConfig)
        config = {
            'mrc_host': 'test.invalid', 'mrc_port': 1,
            'bridge_bbs': 'Test', 'platform_info': 'Test',
            'data_dir': str(self.root / 'bridge-data'),
            'ws_disconnect_grace_seconds': 0,
            'join_packet_delay_ms': 0,
        }
        config_path = self.root / 'bridge.json'
        config_path.write_text(json.dumps(config))
        self.bridge = BridgeApp(str(config_path))
        self.bridge.mrc = _FakeMRC()
        app = web.Application()
        app.router.add_get('/ws', self.bridge.handle_websocket)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.engine.dispose()
        self.interval.stop()
        self.key.stop()
        self.env.stop()
        self.tmp.cleanup()

    def _cookie(self, username='alice', remember=False):
        with Session(self.engine) as session:
            identifier = session.query(User).filter_by(username=username).one().get_id()
        if remember:
            value = encode_cookie(identifier, key=self.cookie_app.secret_key)
            return {'Cookie': 'remember_token=' + value}
        serializer = self.cookie_app.session_interface.get_signing_serializer(self.cookie_app)
        return {'Cookie': 'session=' + serializer.dumps({'_user_id': identifier})}

    def _change(self, username='alice'):
        with Session(self.engine) as session:
            user = session.query(User).filter_by(username=username).one()
            user.set_password('replacement-password')
            session.commit()

    async def _open(self, headers=None):
        ws = await self.client.ws_connect('/ws', headers=headers)
        await asyncio.wait_for(ws.receive_json(), 2)  # welcome
        await asyncio.wait_for(ws.receive_json(), 2)  # bridge status
        return ws

    async def _assert_closed(self, ws):
        message = await asyncio.wait_for(ws.receive(), 2)
        self.assertIn(message.type, (aiohttp.WSMsgType.CLOSE,
                                   aiohttp.WSMsgType.CLOSED,
                                   aiohttp.WSMsgType.ERROR))
        await ws.close()

    async def test_existing_session_cannot_send_after_password_change(self):
        ws = await self._open(self._cookie())
        self._change()
        await ws.send_json({'type': 'ping', 't': 'revoked'})
        await self._assert_closed(ws)

    async def test_idle_browser_session_is_closed_without_another_message(self):
        with patch('mrc.bridge.main.BBS_AUTH_RECHECK_SECONDS', 0.02):
            ws = await self._open(self._cookie())
            self._change()
            await self._assert_closed(ws)

    async def test_fresh_session_and_unrelated_account_remain_usable(self):
        self._change()
        ws = await self._open(self._cookie())
        self._change('bob')
        await ws.send_json({'type': 'ping', 't': 123})
        self.assertEqual((await ws.receive_json())['type'], 'pong')
        self._change()
        await ws.send_json({'type': 'ping'})
        await self._assert_closed(ws)

    async def test_remember_cookie_is_bound_and_old_cookie_cannot_reconnect(self):
        headers = self._cookie(remember=True)
        ws = await self._open(headers)
        self._change()
        await ws.send_json({'type': 'ping'})
        await self._assert_closed(ws)
        with self.assertRaises(aiohttp.WSServerHandshakeError) as error:
            await self.client.ws_connect('/ws', headers=headers)
        self.assertEqual(error.exception.status, 401)

    async def test_invalid_bbs_cookies_are_rejected_before_upgrade(self):
        for name in ('session', 'remember_token'):
            with self.subTest(cookie=name):
                with self.assertRaises(aiohttp.WSServerHandshakeError) as error:
                    await self.client.ws_connect('/ws', headers={'Cookie': name + '=invalid'})
                self.assertEqual(error.exception.status, 401)

    async def test_browser_socket_fails_closed_if_bbs_validation_becomes_unavailable(self):
        ws = await self._open(self._cookie())
        with patch('mrc.bridge.bbs_auth._identifier_is_current', side_effect=RuntimeError('DB unavailable')):
            await ws.send_json({'type': 'ping'})
            await self._assert_closed(ws)

    async def test_expired_signed_session_is_rejected(self):
        with patch.object(TestingConfig, 'PERMANENT_SESSION_LIFETIME', timedelta(seconds=10), create=True):
            with patch('itsdangerous.timed.TimestampSigner.get_timestamp',
                       return_value=int(time.time()) - 100):
                headers = self._cookie()
            with self.assertRaises(aiohttp.WSServerHandshakeError) as error:
                await self.client.ws_connect('/ws', headers=headers)
            self.assertEqual(error.exception.status, 401)

    async def test_cookie_less_and_unrelated_cookie_clients_keep_working(self):
        for headers in (None, {'Cookie': 'unrelated=value'}):
            with self.subTest(headers=headers):
                ws = await self._open(headers)
                self._change()
                await ws.send_json({'type': 'ping'})
                self.assertEqual((await ws.receive_json())['type'], 'pong')
                await ws.close()
