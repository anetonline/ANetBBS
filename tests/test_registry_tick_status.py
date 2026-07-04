"""Regression tests for the federation-registry false-success fix,
added 2026-07-04.

Companion to test_qwk_hub_gating.py -- same underlying report from
Jerry ("basically the same issue with the built-in MSP service").
`anetbbs/msp/registry_client.py`'s `_tick()` used to `return` silently
on every failure path, including when `REGISTRY_URL` was blank -- so
its callers (the setup wizard, the "register now" admin button) had no
way to distinguish "actually registered" from "did nothing at all",
and always showed a flat "success" flash message. `_tick()` now
returns a dict describing what happened; the two callers in
anetbbs/web/admin.py check `result['ok']` before claiming success.
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.msp import registry_client


class FakeApp:
    """Just enough of a Flask app for _tick()'s app.config.get() calls
    and _state_path()'s DATA_DIR/BASE_DIR lookups -- no real Flask
    app/DB needed since _tick() never touches the database."""
    def __init__(self, config, tmp_dir):
        self.config = config
        self.config.setdefault('DATA_DIR', tmp_dir)
        self.config.setdefault('BASE_DIR', tmp_dir)


class RegistryTickStatusTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_blank_registry_url_returns_not_ok_with_a_clear_reason(self):
        app = FakeApp({'REGISTRY_URL': ''}, self._tmp.name)
        result = registry_client._tick(app)
        self.assertFalse(result['ok'])
        self.assertEqual(result['action'], 'skipped')
        self.assertIn('REGISTRY_URL', result['reason'])

    def test_successful_register_returns_ok_true(self):
        app = FakeApp({'REGISTRY_URL': 'https://bbs.a-net.fyi',
                       'BBS_DOMAIN': 'test.example.com'}, self._tmp.name)
        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.headers = {'content-type': 'application/json'}
        fake_resp.json.return_value = {'ok': True, 'status': 'pending',
                                        'verify_url': 'https://bbs.a-net.fyi/registry/verify/abc'}
        with patch.object(registry_client.requests.Session, 'post', return_value=fake_resp):
            result = registry_client._tick(app)
        self.assertTrue(result['ok'])
        self.assertEqual(result['action'], 'register')
        self.assertIn('verify_url', result)

    def test_hub_unreachable_returns_not_ok_with_the_exception_text(self):
        app = FakeApp({'REGISTRY_URL': 'https://bbs.a-net.fyi',
                       'BBS_DOMAIN': 'test.example.com'}, self._tmp.name)
        with patch.object(registry_client.requests.Session, 'post',
                          side_effect=registry_client.requests.ConnectionError('refused')):
            result = registry_client._tick(app)
        self.assertFalse(result['ok'])
        self.assertIn('refused', result['reason'])

    def test_hub_rejects_registration_returns_not_ok(self):
        app = FakeApp({'REGISTRY_URL': 'https://bbs.a-net.fyi',
                       'BBS_DOMAIN': 'test.example.com'}, self._tmp.name)
        fake_resp = MagicMock()
        fake_resp.status_code = 400
        fake_resp.headers = {'content-type': 'application/json'}
        fake_resp.text = 'bad request'
        fake_resp.json.return_value = {'ok': False}
        with patch.object(registry_client.requests.Session, 'post', return_value=fake_resp):
            result = registry_client._tick(app)
        self.assertFalse(result['ok'])
        self.assertEqual(result['action'], 'register')


if __name__ == '__main__':
    unittest.main()
