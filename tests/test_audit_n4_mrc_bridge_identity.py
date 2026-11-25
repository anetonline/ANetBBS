"""N-4: the MRC bridge must tie a browser connection to its BBS account.

Before the fix: any client that could reach the bridge (published port 8080
in Docker) could connect without a BBS login, join as any handle, and
choose the IP reported upstream; X-Forwarded-For was trusted from anyone.
"""
import asyncio
import unittest
from unittest import mock

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer, TestClient

from tests.test_mrc_bridge_websocket_connection_cap import _make_bridge


class _FakeAuth:
    identifier = '7:abc'

    def is_current(self):
        return True


class BridgeIdentity(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import tempfile
        from pathlib import Path
        self._tmp = tempfile.TemporaryDirectory()
        self.bridge = await _make_bridge(Path(self._tmp.name))
        app = web.Application()
        app.router.add_get('/ws', self.bridge.handle_websocket)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self._tmp.cleanup()

    async def test_proxied_browser_without_login_refused(self):
        with self.assertRaises(aiohttp.WSServerHandshakeError) as cm:
            await self.client.ws_connect('/ws', headers={'X-Forwarded-For': '203.0.113.5'})
        self.assertEqual(cm.exception.status, 401)

    async def test_terminal_token_admits_cookie_less_client(self):
        with mock.patch('mrc.bridge.main.terminal_token_ok', return_value=True):
            ws = await self.client.ws_connect('/ws', headers={
                'X-Forwarded-For': '203.0.113.5', 'X-ANetBBS-Bridge-Token': 't'})
            await ws.close()

    async def test_wrong_terminal_token_refused(self):
        with self.assertRaises(aiohttp.WSServerHandshakeError):
            await self.client.ws_connect('/ws', headers={
                'X-Forwarded-For': '203.0.113.5', 'X-ANetBBS-Bridge-Token': 'nope'})

    async def _join(self, ws, handle):
        await ws.send_json({'type': 'join_room', 'handle': handle, 'room': 'lobby'})
        for _ in range(6):
            msg = await asyncio.wait_for(ws.receive_json(), timeout=3)
            if msg.get('type') in ('error', 'joined'):
                return msg
        return {}

    async def test_logged_in_browser_bound_to_its_own_handle(self):
        with mock.patch('mrc.bridge.main.bind_bbs_cookie_auth', return_value=_FakeAuth()), \
                mock.patch('mrc.bridge.main.handle_for_identifier', return_value='alice'):
            ws = await self.client.ws_connect('/ws', headers={'Cookie': 'session=x'})
            try:
                await asyncio.wait_for(ws.receive_json(), timeout=3)   # welcome
                msg = await self._join(ws, 'Sysop')
                self.assertEqual(msg.get('type'), 'error')
                self.assertIn('alice', msg.get('message', ''))
                msg = await self._join(ws, 'Alice')
                self.assertNotEqual(msg.get('type'), 'error', msg)
            finally:
                await ws.close()

    async def test_browser_cannot_choose_reported_ip(self):
        with mock.patch('mrc.bridge.main.bind_bbs_cookie_auth', return_value=_FakeAuth()), \
                mock.patch('mrc.bridge.main.handle_for_identifier', return_value='alice'), \
                mock.patch.object(type(self.bridge), '_register_session_and_join',
                                  autospec=True) as reg:
            reg.side_effect = ValueError('stop here')
            ws = await self.client.ws_connect('/ws', headers={'Cookie': 'session=x'})
            try:
                await asyncio.wait_for(ws.receive_json(), timeout=3)
                await ws.send_json({'type': 'join_room', 'handle': 'alice',
                                    'room': 'lobby', 'ip': '10.9.9.9'})
                await asyncio.wait_for(ws.receive_json(), timeout=3)
            finally:
                await ws.close()
        self.assertTrue(reg.called)
        self.assertNotEqual(reg.call_args[0][4], '10.9.9.9')


class TokenDerivationMatches(unittest.TestCase):
    def test_client_and_bridge_derive_the_same_token(self):
        from anetbbs.core.mrc_bridge_token import bridge_token
        from mrc.bridge import bbs_auth
        with mock.patch.object(bbs_auth, '_cookie_app') as app:
            app.return_value.secret_key = 's3cret'
            self.assertEqual(bbs_auth.terminal_token(), bridge_token('s3cret'))
            self.assertTrue(bbs_auth.terminal_token_ok(bridge_token('s3cret')))
            self.assertFalse(bbs_auth.terminal_token_ok(bridge_token('other')))


class DockerControlService(unittest.TestCase):
    """N-3: the privileged control service accepts only fixed operations."""

    def test_allowlist(self):
        from anetbbs import docker_control_server as dcs
        with mock.patch.object(dcs, 'change_state', return_value=(True, 'ok', '')) as cs, \
                mock.patch.object(dcs, 'spawn_container_upgrade', return_value='n') as up, \
                mock.patch.object(dcs, 'unit_state', return_value={}), \
                mock.patch.object(dcs, 'read_logs', return_value='x'):
            self.assertTrue(dcs.handle_request({'op': 'change', 'unit': 'anetbbs-web',
                                                'action': 'restart'})['ok'])
            self.assertTrue(dcs.handle_request({'op': 'upgrade', 'tag': 'v1.1.74'})['ok'])
            for bad in ({'op': 'change', 'unit': 'evil', 'action': 'restart'},
                        {'op': 'change', 'unit': 'anetbbs-web', 'action': 'exec'},
                        {'op': 'upgrade', 'tag': 'latest; rm -rf /'},
                        {'op': 'upgrade', 'tag': '../x'},
                        {'op': 'run', 'image': 'alpine'},
                        {'op': 'logs', 'unit': 'anetbbs-web', 'lines': 'x'},
                        None, []):
                with self.subTest(bad=bad):
                    self.assertFalse(dcs.handle_request(bad)['ok'])
            self.assertEqual(cs.call_count, 1)
            self.assertEqual(up.call_count, 1)

    def test_web_container_has_no_docker_socket(self):
        from pathlib import Path
        text = (Path(__file__).resolve().parent.parent / 'docker' / 'compose'
                / 'docker-compose.yml').read_text()
        mounts = [l for l in text.splitlines()
                  if 'docker.sock' in l and not l.lstrip().startswith('#')]
        self.assertEqual(len(mounts), 1, mounts)
        control = text.index('\n  control:')
        top_volumes = text.index('\nvolumes:')
        self.assertTrue(control < text.index(mounts[0]) < top_volumes)


if __name__ == '__main__':
    unittest.main()
