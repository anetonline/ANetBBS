"""Regression tests for the container self-upgrade flow, added
2026-07-04 as part of the ANetBBS containerization work.

anetbbs/web/upgrades.py's install() route previously only knew how to
spawn deploy/run_upgrade.sh via `sudo systemd-run --scope`, which
doesn't exist inside a container. It now branches on ANETBBS_RUNTIME
(same module-level pattern as anetbbs/web/control.py):
  - systemd (default): unchanged tarball/sha256/wrapper flow.
  - docker-single: no in-place button, returns a clear 501 telling the
    sysop to pull+recreate manually (self-replacing PID 1 inside its
    own single container is out of scope for v1).
  - docker-compose: spawns a detached sibling "updater" container via
    control_docker.spawn_container_upgrade(), which runs
    docker compose pull && up -d against the sysop's own project files.

These tests patch anetbbs.web.upgrades._RUNTIME directly, same
technique used in test_control_runtime_dispatch.py.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.core  # noqa: F401 -- circular-import workaround
from anetbbs.web import upgrades
from flask import Flask


class DockerTagFieldTests(unittest.TestCase):
    def setUp(self):
        # Even patch()-ing current_app (a werkzeug LocalProxy) requires
        # a bound app context -- mock's own introspection of the
        # original object triggers the proxy's attribute resolution.
        self._app_ctx = Flask(__name__).app_context()
        self._app_ctx.push()
        self.addCleanup(self._app_ctx.pop)

    def test_latest_payload_includes_docker_tag_matching_version(self):
        fake_row = {
            'version': 'v1.0b2.99', 'name': 'ANetBBS-v1.0b2.99.tar.gz',
            'size': 12345, 'mtime': 1700000000.0, 'path': '/tmp/fake.tar.gz',
            'parsed': (1, 0, 'b', 2, 99),
        }
        fake_req = MagicMock()
        fake_req.host_url = 'https://bbs.example.com/'
        with patch.object(upgrades, '_scan_releases', return_value=[fake_row]), \
             patch.object(upgrades, '_sha256_of', return_value='a' * 64), \
             patch('anetbbs.web.upgrades.url_for', return_value='/downloads/x'), \
             patch('anetbbs.web.upgrades.current_app'):
            payload = upgrades._latest_payload(fake_req)
        self.assertEqual(payload['docker_tag'], 'v1.0b2.99')
        self.assertEqual(payload['docker_tag'], payload['version'])


class InstallContainerDispatchTests(unittest.TestCase):
    def setUp(self):
        # jsonify() needs a bound Flask app context -- a bare Flask app
        # is enough, _install_container() doesn't touch the real
        # ANetBBS app/DB at all.
        self._app_ctx = Flask(__name__).app_context()
        self._app_ctx.push()
        self.addCleanup(self._app_ctx.pop)

    def test_docker_single_returns_501_not_supported(self):
        with patch.object(upgrades, '_RUNTIME', 'docker-single'):
            resp, status = upgrades._install_container(
                'v1.0b2.99', {'docker_tag': 'v1.0b2.99'})
        self.assertEqual(status, 501)
        body = resp.get_json()
        self.assertFalse(body['ok'])
        self.assertIn('single-container', body['error'])

    def test_docker_compose_spawns_updater_and_returns_ok(self):
        upgrades._install_state['running'] = False
        with patch.object(upgrades, '_RUNTIME', 'docker-compose'), \
             patch('anetbbs.web.upgrades.url_for', return_value='/admin/upgrades/log'), \
             patch('builtins.open', MagicMock()):
            resp = upgrades._install_container(
                'v1.0b2.99', {'docker_tag': 'v1.0b2.99'})
        body = resp.get_json()
        self.assertTrue(body['ok'])

    def test_docker_compose_rejects_concurrent_install(self):
        upgrades._install_state['running'] = True
        try:
            with patch.object(upgrades, '_RUNTIME', 'docker-compose'), \
                 patch('builtins.open', MagicMock()):
                resp, status = upgrades._install_container(
                    'v1.0b2.99', {'docker_tag': 'v1.0b2.99'})
            self.assertEqual(status, 409)
        finally:
            upgrades._install_state['running'] = False


if __name__ == '__main__':
    unittest.main()
