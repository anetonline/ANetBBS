"""Exercise only install.sh's configuration writer, without running installation."""
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class InstallShBbsDomainTests(unittest.TestCase):
    def _generated_env(self, domain, mode):
        script = (ROOT / 'install.sh').read_text()
        # This subshell only writes .env. Never execute the installer's
        # package, account, service or database setup steps in a test.
        block = re.search(r'^\(\numask 177\n.*?^ENVEOF\n\)',
                          script, re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(block, 'configuration writer not found')
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / '.env'
            env = {
                'PATH': os.defpath,
                'DOMAIN': domain,
                'INSTALL_MODE': mode,
                'INSTALL_DIR': tmp,
                'ENV_FILE': str(env_file),
                'BBS_NAME': 'InSight BBS',
                'BBS_DESC': 'Test BBS',
                'SYSOP_NAME': 'neo67',
                'ENABLE_NGINX': 'n',
            }
            subprocess.run(['bash', '-e', '-c', block.group()],
                           env=env, check=True, capture_output=True, text=True)
            self.assertEqual(env_file.stat().st_mode & 0o777, 0o600)
            return env_file.read_text().splitlines()

    def test_public_hostname_is_saved_in_every_install_mode(self):
        # Test mode can sit behind an existing Caddy HTTPS proxy (#43).
        for mode in ('production', 'behind', 'test'):
            with self.subTest(mode=mode):
                lines = self._generated_env('bbs.ihlau.net', mode)
                self.assertIn('BBS_DOMAIN=bbs.ihlau.net', lines)
                self.assertIn('BBS_NAME=InSight BBS', lines)
                self.assertEqual(sum(l.startswith('BBS_DOMAIN=') for l in lines), 1)

    def test_local_only_defaults_leave_public_identity_blank(self):
        for host in ('localhost', 'LOCALHOST', 'localhost.', 'bbs.localhost',
                     '127.0.0.1', '0.0.0.0', '::1', '[::1]', '::', '[::]', ''):
            with self.subTest(host=host):
                self.assertIn('BBS_DOMAIN=', self._generated_env(host, 'test'))

    def test_public_hostname_trailing_dot_is_normalized(self):
        self.assertIn('BBS_DOMAIN=bbs.ihlau.net',
                      self._generated_env('bbs.ihlau.net.', 'behind'))


if __name__ == '__main__':
    unittest.main()
