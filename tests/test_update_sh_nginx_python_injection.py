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


class UpdateShNginxFullSectionEndToEndTests(unittest.TestCase):
    """Two real bugs shipped in v1.1.27 and caused a live production
    outage (2026-10-06): update.sh crashed partway through a real
    sysop's upgrade, after services were already stopped, leaving the
    site down until manually restarted.

    Both bugs were invisible to the narrower per-block tests above
    because those test scripts never set `set -euo pipefail` the way
    the real update.sh does at its own top, AND because extracting
    each insert-block in isolation skips over the pre-existing
    port-mismatch-check code that sits between them. This test runs
    the ENTIRE "Patch nginx config" section as one unit, under the
    same `set -euo pipefail` the real script runs under, against a
    config missing both /static/ and /mrcws entirely -- the exact
    real-world shape (an older install.sh-generated config) that
    triggers both bugs at once:

    1. The /static/ and /mrcws insert blocks used a heredoc followed
       by a backslash-continued `&&/||` chain for their success/
       failure handling. That parsed and ran fine under this
       sandbox's bash, but failed with "syntax error near unexpected
       token `fi'" on the production server's bash -- a known
       bash-version-sensitive heredoc-parsing edge case. Fixed by
       restructuring to `if cmd <<'EOF'; then ... else ... fi`,
       matching this file's own pre-existing, proven-safe heredoc
       pattern (the DATABASE_URL/SECRET_KEY migration blocks).
    2. The pre-existing MRC-port-mismatch check assigns
       `VAR="$(pipeline)"` where the pipeline's last stage can
       legitimately find no match (no .env key yet, or no existing
       "127.0.0.1:PORT/ws;" line at all -- exactly the case where
       /mrcws doesn't exist yet). Under `set -e -o pipefail`, that
       silently aborts the whole script right there with no error
       message. Fixed with a trailing `|| true` on both assignments
       (both are already null-checked by the `[[ -n "$VAR" ]]` below
       them, so this doesn't paper over a real failure)."""

    @classmethod
    def setUpClass(cls):
        repo_root = Path(__file__).resolve().parent.parent
        with open(repo_root / 'update.sh') as f:
            content = f.read()
        start_marker = ('# ─── Patch nginx config (known bug fixes from past releases)')
        section = _extract(
            content, start_marker,
            '\nelse\n    skip "No nginx config at $NGINX_AVAIL')
        # section now runs from the comment header through the body of
        # the `if [[ -f "$NGINX_AVAIL" ]]; then ... ` -- strip down to
        # just the `if`'s own condition/body (NGINX_AVAIL is set by the
        # test harness itself, matching how the real script's own
        # outer variable is already in scope by this point).
        if_start = section.index('if [[ -f "$NGINX_AVAIL" ]]; then')
        cls.full_section = ('if true; then'
                             + section[if_start + len('if [[ -f "$NGINX_AVAIL" ]]; then'):]
                             + '\nfi\n')

    def _run(self, nginx_config_body, web_port='5000', mrc_port='5001'):
        fakebin = Path('/tmp/.update_sh_test_fakebin')
        fakebin.mkdir(exist_ok=True)
        for name in ('nginx', 'systemctl'):
            p = fakebin / name
            p.write_text('#!/bin/sh\nexit 0\n')
            p.chmod(0o755)
        # INSTALL_DIR must be a real, existing directory -- the script
        # reads $INSTALL_DIR/.env for the real WEB_PORT/MRC_BRIDGE_PORT
        # values (used by both the mrc-port-mismatch check and the
        # /mrcws insert block), so it can't be a non-existent literal
        # like '/opt/anetbbs' the way the narrower per-block tests
        # above get away with (those never read .env at all).
        install_dir_path = Path('/tmp') / f'.update_sh_test_install_{id(self)}'
        install_dir_path.mkdir(exist_ok=True)
        (install_dir_path / '.env').write_text(
            f'WEB_PORT={web_port}\nMRC_BRIDGE_PORT={mrc_port}\n')
        self._install_dir_path = install_dir_path
        script = (
            'set -euo pipefail\n'
            f'export PATH="{fakebin}:$PATH"\n'
            'info() { printf "[INFO] %s\\n" "$*"; }\n'
            'ok() { printf "OK: %s\\n" "$*"; }\n'
            'warn() { printf "WARN: %s\\n" "$*"; }\n'
            'skip() { printf "SKIP: %s\\n" "$*"; }\n'
            'NGINX_AVAIL="$(mktemp)"\n'
            f'cat > "$NGINX_AVAIL" <<\'CONF\'\n{nginx_config_body}\nCONF\n'
            f'INSTALL_DIR="{install_dir_path}"\n'
            f'BACKUP_DIR="{install_dir_path}"\n'
            f'{self.full_section}\n'
            'cat "$NGINX_AVAIL"\n'
            'rm -f "$NGINX_AVAIL"\n'
        )
        result = subprocess.run(['bash', '-c', script],
                                 capture_output=True, text=True)
        import shutil
        shutil.rmtree(install_dir_path, ignore_errors=True)
        return result

    def test_config_missing_both_locations_entirely_completes_without_aborting(self):
        """The exact shape that triggered both real bugs: an older
        config with neither /static/ nor /mrcws yet."""
        out = self._run('server {\n    listen 80;\n}\n')
        self.assertEqual(out.returncode, 0, msg=f'stdout={out.stdout!r} stderr={out.stderr!r}')
        self.assertNotIn('syntax error', out.stderr)
        self.assertIn('location /static/', out.stdout)
        self.assertIn('location /mrcws', out.stdout)
        self.assertIn('location = /mrc-auth-check', out.stdout)

    def test_config_already_fully_configured_is_a_clean_noop(self):
        existing = (
            'server {\n'
            '    listen 80;\n'
            '    location /static/ {\n'
            '        alias /opt/anetbbs/anetbbs/static/;\n'
            '    }\n'
            '    location = /mrc-auth-check {\n'
            '        proxy_pass http://127.0.0.1:5000/mrc/auth-check;\n'
            '    }\n'
            '    location /mrcws {\n'
            '        proxy_pass         http://127.0.0.1:5001/ws;\n'
            '        proxy_set_header   Host              $host;\n'
            '        proxy_set_header   X-Real-IP         $remote_addr;\n'
            '        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;\n'
            '        proxy_set_header   X-Forwarded-Proto $scheme;\n'
            '    }\n'
            '}\n'
        )
        out = self._run(existing)
        self.assertEqual(out.returncode, 0, msg=f'stdout={out.stdout!r} stderr={out.stderr!r}')
        self.assertNotIn('syntax error', out.stderr)


if __name__ == '__main__':
    unittest.main()
