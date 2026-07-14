"""Regression tests for wiring anetbbs/web/preflight.py's
_check_sudo_escalation() into upgrades.py's install()/rollback() routes.

Real sysops reported the web-triggered "Check for Updates -> Install"
button failing with a "no sudo rights"-style error and no useful detail
in the UI -- root cause: install.sh never wrote /etc/sudoers.d/anetbbs
(only update.sh did), so on any fresh install that hadn't separately run
`sudo bash update.sh` once, _spawn_upgrade()'s `sudo -n ...` subprocess
was doomed from the start. Fixed at the code level in install.sh (no
shell test harness exists in this repo, so that half is verified by
direct code reading -- see the plan file). This file covers the OTHER
half: the web route itself should now check first and return a clear,
actionable error instead of silently spawning a subprocess that's
certain to fail.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.core  # noqa: F401 -- circular-import workaround, matches
                      # the existing pattern in test_container_self_upgrade.py
from anetbbs.web import upgrades
from flask import Flask
from flask_login import LoginManager, UserMixin, login_user


class _FakeAdminUser(UserMixin):
    id = 'test-admin'
    is_admin = True


class InstallSudoPreflightTests(unittest.TestCase):
    def _push_request(self, path, data):
        # A real request context (not a mocked `request` LocalProxy) --
        # mock.patch()'s own introspection of `request` before replacing
        # it needs an active request to resolve against, and a real
        # context also means real request.form.get() behavior for free.
        # install()/rollback() are wrapped in @login_required, which
        # needs a real flask_login-initialized app + a logged-in user
        # to get past, even though _require_admin() itself is mocked
        # away below.
        app = Flask(__name__)
        app.secret_key = 'test'
        LoginManager().init_app(app)
        ctx = app.test_request_context(path, method='POST', data=data)
        ctx.push()
        self.addCleanup(ctx.pop)
        login_user(_FakeAdminUser())

    def setUp(self):
        upgrades._install_state['running'] = False

    def test_install_returns_409_with_fix_text_when_sudo_escalation_fails(self):
        self._push_request('/admin/upgrades/install', {'version': 'v1.0b2.99'})
        fail_check = {'name': 'sudo escalation from web service',
                     'status': 'fail',
                     'detail': 'sudo -n -l rejected wrapper: not allowed',
                     'fix': 'Re-run sudo bash update.sh — it rewrites '
                            '/etc/sudoers.d/anetbbs with the grant for the '
                            'current service user + install path.'}
        with patch.object(upgrades, '_RUNTIME', 'systemd'), \
             patch.object(upgrades, '_parse_version', return_value=(1, 0, 'b', 2, 99)), \
             patch.object(upgrades, '_fetch_upstream',
                          return_value=({'version': 'v1.0b2.99', 'sha256': 'a' * 64,
                                        'url': 'https://example.com/x.tar.gz'}, None)), \
             patch.object(upgrades, '_version_newer', return_value=True), \
             patch.object(upgrades, '_wrapper_path', return_value='/tmp/fake_wrapper.sh'), \
             patch('os.path.isfile', return_value=True), \
             patch.object(upgrades, '_check_sudo_escalation', return_value=fail_check), \
             patch.object(upgrades, '_spawn_upgrade') as mock_spawn, \
             patch.object(upgrades, '_require_admin'):
            resp = upgrades.install()

        # install() returns (jsonify(...), status) or just jsonify(...);
        # normalize both shapes.
        if isinstance(resp, tuple):
            body_resp, status = resp
        else:
            body_resp, status = resp, 200
        body = body_resp.get_json()

        self.assertEqual(status, 409)
        self.assertFalse(body['ok'])
        self.assertIn('not allowed', body['error'])
        self.assertIn('update.sh', body['error'])
        mock_spawn.assert_not_called()

    def test_install_proceeds_to_spawn_when_sudo_escalation_ok(self):
        self._push_request('/admin/upgrades/install', {'version': 'v1.0b2.99'})
        ok_check = {'name': 'sudo escalation from web service', 'status': 'ok',
                   'detail': 'sudoers permits /tmp/fake_wrapper.sh', 'fix': ''}
        with patch.object(upgrades, '_RUNTIME', 'systemd'), \
             patch.object(upgrades, '_parse_version', return_value=(1, 0, 'b', 2, 99)), \
             patch.object(upgrades, '_fetch_upstream',
                          return_value=({'version': 'v1.0b2.99', 'sha256': 'a' * 64,
                                        'url': 'https://example.com/x.tar.gz'}, None)), \
             patch.object(upgrades, '_version_newer', return_value=True), \
             patch.object(upgrades, '_wrapper_path', return_value='/tmp/fake_wrapper.sh'), \
             patch('os.path.isfile', return_value=True), \
             patch.object(upgrades, '_check_sudo_escalation', return_value=ok_check), \
             patch.object(upgrades, '_spawn_upgrade') as mock_spawn, \
             patch.object(upgrades, '_patch_wrapper_if_needed'), \
             patch('builtins.open', MagicMock()), \
             patch('anetbbs.web.upgrades.url_for', return_value='/admin/upgrades/log'), \
             patch.object(upgrades, '_require_admin'):
            resp = upgrades.install()

        if isinstance(resp, tuple):
            body_resp, status = resp
        else:
            body_resp, status = resp, 200
        body = body_resp.get_json()

        self.assertEqual(status, 200)
        self.assertTrue(body['ok'])
        mock_spawn.assert_called_once()

    def test_rollback_returns_409_when_sudo_escalation_fails(self):
        self._push_request('/admin/upgrades/rollback', {'version': 'v1.0b2.10'})
        fail_check = {'name': 'sudo escalation from web service',
                     'status': 'fail',
                     'detail': 'sudo -n -l rejected wrapper: not allowed',
                     'fix': 'Re-run sudo bash update.sh.'}
        fake_row = {'version': 'v1.0b2.10', 'name': 'ANetBBS-v1.0b2.10.tar.gz',
                   'size': 123, 'mtime': 1700000000.0, 'path': '/tmp/fake.tar.gz',
                   'parsed': (1, 0, 'b', 2, 10)}
        with patch.object(upgrades, '_parse_version', return_value=(1, 0, 'b', 2, 10)), \
             patch.object(upgrades, '_scan_releases', return_value=[fake_row]), \
             patch.object(upgrades, '_version_newer', return_value=False), \
             patch.object(upgrades, 'VERSION', 'v1.0b2.99'), \
             patch.object(upgrades, '_sha256_of', return_value='b' * 64), \
             patch('anetbbs.web.upgrades.url_for', return_value='/downloads/x'), \
             patch.object(upgrades, '_wrapper_path', return_value='/tmp/fake_wrapper.sh'), \
             patch('os.path.isfile', return_value=True), \
             patch.object(upgrades, '_check_sudo_escalation', return_value=fail_check), \
             patch.object(upgrades, '_spawn_upgrade') as mock_spawn, \
             patch.object(upgrades, '_require_admin'):
            resp = upgrades.rollback()

        if isinstance(resp, tuple):
            body_resp, status = resp
        else:
            body_resp, status = resp, 200
        body = body_resp.get_json()

        self.assertEqual(status, 409)
        self.assertFalse(body['ok'])
        mock_spawn.assert_not_called()


if __name__ == '__main__':
    unittest.main()
