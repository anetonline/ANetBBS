"""Regression test for the WEB_PORT / MRC bridge port collision check in
anetbbs/web/preflight.py.

Two related bugs, found during a documentation audit that led to tracing
both binding paths through to their real socket calls:

1. install.sh defaults WEB_PORT to 8080 in 'test'/'behind' install modes,
   but used to also hardcode MRC_BRIDGE_PORT=8080 unconditionally --
   fresh installs in the Pi-hobbyist ("test") mode could have gunicorn
   and the MRC bridge both trying to bind 127.0.0.1:8080. Fixed by
   deriving the MRC default from WEB_PORT+1 instead of a fixed value.

2. The preflight check meant to catch this collision after the fact was
   itself broken -- it tried to regex a port number out of the MRC
   bridge's systemd ExecStart line, but that line is just
   `python -m mrc.bridge.main` with no port in it at all (the bridge
   reads its port from its own config.json). The regex could never
   match, so mrc_bind was always None and the check silently never
   fired. Fixed to read config.json's web_listen_port directly.
"""
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anetbbs.web.preflight import _check_port_consistency


class PortCollisionCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp_root = str(Path(__file__).resolve().parent / '.preflight_port_test')
        self.mrc_dir = os.path.join(self.tmp_root, 'mrc', 'bridge')
        os.makedirs(self.mrc_dir, exist_ok=True)

    def tearDown(self):
        import shutil
        if os.path.isdir(self.tmp_root):
            shutil.rmtree(self.tmp_root)

    def _write_mrc_config(self, port):
        with open(os.path.join(self.mrc_dir, 'config.json'), 'w') as f:
            json.dump({'web_listen_port': port}, f)

    def test_detects_collision_when_ports_match(self):
        self._write_mrc_config(8080)
        result = _check_port_consistency({'WEB_PORT': 8080, 'INSTALL_DIR': self.tmp_root})
        self.assertEqual(result['status'], 'warn')
        self.assertIn('both want', result['detail'])

    def test_no_warning_when_ports_differ(self):
        self._write_mrc_config(8081)
        result = _check_port_consistency({'WEB_PORT': 8080, 'INSTALL_DIR': self.tmp_root})
        self.assertEqual(result['status'], 'ok')

    def test_missing_mrc_config_does_not_crash(self):
        # No config.json written -- MRC not installed, or config missing.
        result = _check_port_consistency({'WEB_PORT': 8080, 'INSTALL_DIR': self.tmp_root})
        self.assertEqual(result['status'], 'ok')

    def test_malformed_mrc_config_does_not_crash(self):
        with open(os.path.join(self.mrc_dir, 'config.json'), 'w') as f:
            f.write('{not valid json')
        result = _check_port_consistency({'WEB_PORT': 8080, 'INSTALL_DIR': self.tmp_root})
        self.assertEqual(result['status'], 'ok')


if __name__ == '__main__':
    unittest.main()
