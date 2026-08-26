"""Regression tests for registry_client.py's half of the heartbeat_key
security fix (see anetbbs/web/registry.py / test_registry_hijack_fixes.py
for the hub-side fix). The client must persist the key it gets back
from /register and send it on every subsequent /heartbeat, and must
treat a 401 (stale/missing key) the same as a 404 (unknown host) —
fall back to a fresh register rather than giving up.
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.msp import registry_client


class FakeApp:
    def __init__(self, config, tmp_dir):
        self.config = config
        self._tmp_dir = tmp_dir

    def __getattr__(self, name):
        raise AttributeError(name)


def _fake_response(status_code, json_body, content_type='application/json'):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body
    resp.headers = {'content-type': content_type}
    resp.text = str(json_body)
    return resp


class RegistryClientHeartbeatKeyTests(unittest.TestCase):
    def _app(self, tmp_dir, **extra_config):
        config = {
            'REGISTRY_URL': 'https://hub.example.com',
            'BBS_DOMAIN': 'me.example.com',
            'BBS_NAME': 'My BBS',
            'SYSOP_NAME': 'Me',
            'SYSOP_EMAIL': 'me@example.com',
            'DATA_DIR': tmp_dir,
            'BASE_DIR': tmp_dir,
        }
        config.update(extra_config)
        return FakeApp(config, tmp_dir)

    def test_register_response_heartbeat_key_is_persisted_to_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = self._app(tmp)
            register_resp = _fake_response(200, {
                'ok': True, 'status': 'pending_verification',
                'host': 'me.example.com', 'heartbeat_key': 'the-secret-key',
                'message': 'check your email',
            })
            with patch.object(registry_client.requests, 'Session') as mock_session_cls:
                sess = mock_session_cls.return_value
                sess.post.return_value = register_resp
                result = registry_client._tick(app)
            self.assertTrue(result['ok'])
            self.assertEqual(result['action'], 'register')
            state = registry_client._load_state(app)
            self.assertEqual(state.get('heartbeat_key'), 'the-secret-key')

    def test_heartbeat_sends_the_stored_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = self._app(tmp)
            # Pre-seed state as if a prior register already happened.
            registry_client._save_state(app, {
                'host_registered': 'me.example.com',
                'heartbeat_key': 'stored-key-123',
            })
            heartbeat_resp = _fake_response(200, {'ok': True, 'is_listed': True})
            with patch.object(registry_client.requests, 'Session') as mock_session_cls:
                sess = mock_session_cls.return_value
                sess.post.return_value = heartbeat_resp
                result = registry_client._tick(app)
            self.assertTrue(result['ok'])
            self.assertEqual(result['action'], 'heartbeat')
            sent_json = sess.post.call_args.kwargs['json']
            self.assertEqual(sent_json['heartbeat_key'], 'stored-key-123')

    def test_heartbeat_401_falls_back_to_register_like_404(self):
        """A 401 (stale/missing key on the hub's side) must trigger the
        same self-heal path as a 404 (unknown host) -- NOT just fail."""
        with tempfile.TemporaryDirectory() as tmp:
            app = self._app(tmp)
            registry_client._save_state(app, {
                'host_registered': 'me.example.com',
                'heartbeat_key': 'a-now-stale-key',
            })
            heartbeat_401 = _fake_response(401, {'ok': False, 'error': 'bad key'})
            register_200 = _fake_response(200, {
                'ok': True, 'status': 'pending_approval',
                'host': 'me.example.com', 'heartbeat_key': 'brand-new-key',
                'message': 'already verified',
            })
            with patch.object(registry_client.requests, 'Session') as mock_session_cls:
                sess = mock_session_cls.return_value
                sess.post.side_effect = [heartbeat_401, register_200]
                result = registry_client._tick(app)
            self.assertTrue(result['ok'])
            self.assertEqual(result['action'], 'register')
            self.assertEqual(sess.post.call_count, 2)
            state = registry_client._load_state(app)
            self.assertEqual(state.get('heartbeat_key'), 'brand-new-key')

    def test_verify_url_absent_from_response_is_handled_without_crashing(self):
        """When the hub emailed the verify link instead of returning it
        (the normal case now), body.get('verify_url') is None -- the
        client must degrade gracefully, not KeyError."""
        with tempfile.TemporaryDirectory() as tmp:
            app = self._app(tmp)
            register_resp = _fake_response(200, {
                'ok': True, 'status': 'pending_verification',
                'host': 'me.example.com', 'heartbeat_key': 'k',
                'message': 'Check your email for a verification link.',
                # NOTE: no verify_url / verify_token keys at all.
            })
            with patch.object(registry_client.requests, 'Session') as mock_session_cls:
                sess = mock_session_cls.return_value
                sess.post.return_value = register_resp
                result = registry_client._tick(app)
            self.assertTrue(result['ok'])
            self.assertEqual(result['verify_url'], '')
            state = registry_client._load_state(app)
            self.assertEqual(state.get('verify_url'), '')


if __name__ == '__main__':
    unittest.main()
