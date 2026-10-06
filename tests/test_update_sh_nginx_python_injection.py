"""Regression test for a real audit finding: update.sh's nginx
self-healing patches (the /static/ location insert and the /mrcws +
/mrc-auth-check location insert) used to splice INSTALL_DIR (and the
.env-derived REAL_WEB_PORT/REAL_MRC_PORT) directly into a `python3 -c`
triple-quoted string literal via bash interpolation. A sysop-supplied
--install-dir containing a single quote or a literal ''' sequence
could break out of the string and inject arbitrary Python into a
script that runs as root during update.

Fixed the same way the sibling DATABASE_URL/SECRET_KEY migration
blocks already were: the variable travels in as a real environment
variable, read back via os.environ inside a quoted heredoc, so bash
never expands anything inside the Python source at all.

This test runs the real patch blocks copied out of update.sh against
synthetic nginx configs, in actual bash -- not a reimplementation --
so it can't drift from what's actually shipped.
"""
import re
import subprocess
import unittest
from pathlib import Path


def _extract(content, start_marker, end_marker):
    start = content.index(start_marker)
    end = content.index(end_marker, start)
    return content[start:end]


class UpdateShNginxStaticBlockInjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        repo_root = Path(__file__).resolve().parent.parent
        with open(repo_root / 'update.sh') as f:
            content = f.read()
        cls.static_block_code = _extract(
            content,
            "    if ! grep -q 'location /static/'",
            "    # Fix: Add /mrcws and /mrc-auth-check locations")

    def _run(self, install_dir, nginx_config_body='server {\n}\n'):
        script = (
            'NGINX_AVAIL="$(mktemp)"\n'
            f'cat > "$NGINX_AVAIL" <<\'CONF\'\n{nginx_config_body}\nCONF\n'
            'info() { :; }\n'
            'ok() { :; }\n'
            'warn() { :; }\n'
            'NGINX_CHANGED=false\n'
            f'INSTALL_DIR={install_dir!r}\n'
            f'{self.static_block_code}\n'
            'cat "$NGINX_AVAIL"\n'
            'rm -f "$NGINX_AVAIL"\n'
        )
        return subprocess.run(['bash', '-c', script],
                               capture_output=True, text=True)

    def test_normal_install_dir_still_inserts_the_block(self):
        out = self._run('/opt/anetbbs')
        self.assertEqual(out.returncode, 0, msg=out.stderr)
        self.assertIn('alias /opt/anetbbs/anetbbs/static/;', out.stdout)
        self.assertIn('location /static/', out.stdout)

    def test_install_dir_with_single_quote_does_not_break_out(self):
        """The real audit finding: a crafted INSTALL_DIR ending the
        Python triple-quoted string early and injecting code. Before
        the fix this either corrupted the written nginx config or
        executed the injected Python; after the fix INSTALL_DIR is
        just inert data read from os.environ, so the single quote
        lands literally in the alias path and nothing else happens."""
        malicious = "/opt/x'''; import os; os.system('touch /tmp/pwned-update-sh-test'); x = '''"
        out = self._run(malicious)
        self.assertEqual(out.returncode, 0, msg=out.stderr)
        self.assertFalse(Path('/tmp/pwned-update-sh-test').exists(),
                         'the injected os.system() call must never run -- '
                         'INSTALL_DIR must stay inert data, landing '
                         'literally in the alias path text below, not '
                         'executed as Python')
        self.assertIn(malicious + '/anetbbs/static/;', out.stdout)

    def tearDown(self):
        Path('/tmp/pwned-update-sh-test').unlink(missing_ok=True)


class UpdateShNginxMrcwsBlockInjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        repo_root = Path(__file__).resolve().parent.parent
        with open(repo_root / 'update.sh') as f:
            content = f.read()
        cls.mrcws_block_code = _extract(
            content,
            "    if ! grep -q 'location /mrcws'",
            "    # Fix: /mrcws and /socket.io/ set their OWN")

    def _run(self, real_web_port, real_mrc_port, nginx_config_body='server {\n}\n'):
        script = (
            'NGINX_AVAIL="$(mktemp)"\n'
            f'cat > "$NGINX_AVAIL" <<\'CONF\'\n{nginx_config_body}\nCONF\n'
            'info() { :; }\n'
            'ok() { :; }\n'
            'warn() { :; }\n'
            'NGINX_CHANGED=false\n'
            f'REAL_WEB_PORT={real_web_port!r}\n'
            f'REAL_MRC_PORT={real_mrc_port!r}\n'
            f'{self.mrcws_block_code}\n'
            'cat "$NGINX_AVAIL"\n'
            'rm -f "$NGINX_AVAIL"\n'
        )
        return subprocess.run(['bash', '-c', script],
                               capture_output=True, text=True)

    def test_normal_ports_still_insert_the_block(self):
        out = self._run('5000', '5001')
        self.assertEqual(out.returncode, 0, msg=out.stderr)
        self.assertIn('127.0.0.1:5000/mrc/auth-check', out.stdout)
        self.assertIn('127.0.0.1:5001/ws', out.stdout)
        self.assertIn('location /mrcws', out.stdout)

    def test_crafted_port_value_does_not_break_out(self):
        """REAL_WEB_PORT/REAL_MRC_PORT come from .env today, but the
        same environment-variable discipline applies regardless of
        current exploitability -- a crafted value must land as inert
        text, never as executed Python."""
        malicious = "5000'''; import os; os.system('touch /tmp/pwned-update-sh-test2'); x = '''"
        out = self._run(malicious, '5001')
        self.assertEqual(out.returncode, 0, msg=out.stderr)
        self.assertFalse(Path('/tmp/pwned-update-sh-test2').exists(),
                         'the injected os.system() call must never run')

    def tearDown(self):
        Path('/tmp/pwned-update-sh-test2').unlink(missing_ok=True)


if __name__ == '__main__':
    unittest.main()
