"""H-1: the service user must not be able to reach root through the
sudo-granted helpers or through root runs of install.sh/update.sh.

Before the fix:
  * sudoers granted <INSTALL_DIR>/deploy/run_upgrade.sh and run_restore.sh,
    files the service user owns (install.sh chown -R's INSTALL_DIR);
  * the web process rewrote run_upgrade.sh in place (_patch_wrapper_if_needed);
  * run_upgrade.sh took its trusted download host from INSTALL_DIR/.env and
    had systemd (as root) append its log under INSTALL_DIR/data/;
  * update.sh/install.sh fell back to running venv pip/python, the venv's
    anetbbs-symlinks, git pull and ssh-keygen as root.

These tests need no root: static checks on the scripts plus functional
runs of the shell helpers and run_upgrade.sh against temporary paths.
"""
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEPLOY = REPO / 'deploy'
LIBEXEC = '/usr/local/libexec/anetbbs'


def _read(rel):
    return (REPO / rel).read_text()


def _code_lines(text):
    """Shell lines with comments stripped (keeps heredoc bodies; fine for
    the patterns checked here)."""
    out = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith('#'):
            continue
        out.append(line)
    return out


class SudoersGrantsOnlyRootOwnedPaths(unittest.TestCase):
    def test_template_grants_libexec_wrappers_only(self):
        text = _read('deploy/sudoers.anetbbs')
        grants = [l.strip().rstrip(',').rstrip('\\').strip()
                  for l in _code_lines(text) if 'run_upgrade' in l or 'run_restore' in l]
        self.assertEqual(sorted(grants), sorted([
            f'{LIBEXEC}/run_upgrade.sh *', f'{LIBEXEC}/run_restore.sh *']), grants)
        self.assertNotIn('/opt/anetbbs/deploy/', '\n'.join(_code_lines(text)))

    def test_installers_do_not_substitute_wrapper_paths(self):
        for rel in ('install.sh', 'update.sh'):
            text = '\n'.join(_code_lines(_read(rel)))
            with self.subTest(rel=rel):
                self.assertNotIn('s|/opt/anetbbs/deploy/run_upgrade.sh|', text)
                self.assertNotIn('$INSTALL_DIR/deploy/run_upgrade.sh', text)
                self.assertIn('anetbbs_install_root_wrappers "$SOURCE_DIR"', text)
                self.assertIn('anetbbs_write_install_conf "$INSTALL_DIR"', text)


class WebAppNeverWritesRootExecutedFiles(unittest.TestCase):
    def test_patcher_removed_and_paths_are_libexec(self):
        import anetbbs.web.upgrades as upgrades
        from anetbbs.web import privileged_paths as pp
        self.assertFalse(hasattr(upgrades, '_patch_wrapper_if_needed'))
        self.assertEqual(upgrades._wrapper_path({'INSTALL_DIR': '/opt/x'}),
                         f'{LIBEXEC}/run_upgrade.sh')
        self.assertEqual(pp.RESTORE_WRAPPER, f'{LIBEXEC}/run_restore.sh')
        self.assertTrue(pp.ROOT_UPGRADE_LOG.startswith('/var/log/anetbbs/'))

    def test_no_python_source_references_wrappers_under_install_dir(self):
        hits = []
        for p in (REPO / 'anetbbs').rglob('*.py'):
            t = p.read_text(errors='replace')
            if re.search(r"""['"]deploy['"]\s*,\s*['"]run_(upgrade|restore)\.sh""", t):
                hits.append(str(p.relative_to(REPO)))
        self.assertEqual(hits, [])


class RunUpgradeTrustsOnlyEtcConfig(unittest.TestCase):
    def test_static_shape(self):
        code = '\n'.join(_code_lines(_read('deploy/run_upgrade.sh')))
        self.assertNotIn('ENV_FILE', code)
        self.assertNotIn('REGISTRY_URL', code)
        self.assertNotIn('BASH_SOURCE', code)
        self.assertNotIn('. /etc/anetbbs.install', code)
        self.assertNotIn('http://*.tar.gz', code)
        self.assertNotIn('$INSTALL_DIR/data/anetbbs_upgrade.log', code)

    def _script(self, tmp, conf):
        src = _read('deploy/run_upgrade.sh')
        for old, new in (
                ('CONF=/etc/anetbbs.install', f'CONF={conf}'),
                ('[ "$(stat -c %u "$CONF")" = 0 ]', f'[ "$(stat -c %u "$CONF")" = {os.getuid()} ]')):
            self.assertEqual(src.count(old), 1, old)
            src = src.replace(old, new)
        path = Path(tmp) / 'run_upgrade.sh'
        path.write_text(src)
        path.chmod(0o755)
        return path

    def _run(self, *args, conf_text, env_text=None):
        with tempfile.TemporaryDirectory() as tmp:
            inst = Path(tmp) / 'inst'
            (inst / 'data').mkdir(parents=True)
            if env_text is not None:
                (inst / '.env').write_text(env_text)
            conf = Path(tmp) / 'anetbbs.install'
            conf.write_text(conf_text.replace('@INST@', str(inst)))
            conf.chmod(0o644)
            script = self._script(tmp, conf)
            env = dict(os.environ, ANETBBS_UPGRADE_DETACHED='1', PATH='/usr/bin:/bin')
            return subprocess.run([str(script), *args], capture_output=True,
                                  text=True, timeout=30, env=env)

    def test_env_registry_url_is_ignored(self):
        # .env (service-user-owned) names an attacker host; the trusted
        # REGISTRY_HOST in the root config must win, so the URL is refused
        # before any download.
        r = self._run('v9.9.9', 'https://evil.example/ANetBBS-v9.9.9.tar.gz', 'a' * 64,
                      conf_text='INSTALL_DIR=@INST@\nSERVICE_USER=anetbbs\nREGISTRY_HOST=bbs.a-net.fyi\n',
                      env_text='REGISTRY_URL=https://evil.example\n')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("does not match this install's trusted REGISTRY_HOST", r.stderr)

    def test_missing_registry_host_line_defaults_and_still_refuses_other_hosts(self):
        r = self._run('v9.9.9', 'https://evil.example/ANetBBS-v9.9.9.tar.gz', 'a' * 64,
                      conf_text='INSTALL_DIR=@INST@\nSERVICE_USER=anetbbs\n',
                      env_text='REGISTRY_URL=https://evil.example\n')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("'bbs.a-net.fyi'", r.stderr)

    def test_plain_http_refused(self):
        r = self._run('v9.9.9', 'http://bbs.a-net.fyi/ANetBBS-v9.9.9.tar.gz', 'a' * 64,
                      conf_text='INSTALL_DIR=@INST@\nSERVICE_USER=anetbbs\n')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('over https', r.stderr)


class RunRestoreDropsToServiceUser(unittest.TestCase):
    def test_file_operations_run_as_service_user(self):
        code = _code_lines(_read('deploy/run_restore.sh'))
        joined = '\n'.join(code)
        self.assertNotIn('. /etc/anetbbs.install', joined)
        for line in code:
            s = line.strip()
            if re.match(r'^(rm -rf|cp -f|chmod 600|chown )', s):
                self.fail(f'root file operation left in run_restore.sh: {s}')
        self.assertIn('as_service rm -rf -- "$BACKUP"', joined)
        self.assertGreaterEqual(joined.count('as_service cp -f --remove-destination'), 2)


class InstallersNeverRunServiceUserCodeAsRoot(unittest.TestCase):
    ROOT_EXEC = re.compile(
        r'''(^|[\s;(|&$])"\$VENV_DIR/bin/[A-Za-z0-9_.-]+"\s+\S''')

    def _offending(self, rel):
        bad = []
        for n, line in enumerate(_read(rel).splitlines(), 1):
            s = line.strip()
            if not s or s.startswith('#'):
                continue
            if s.startswith(('ExecStart=', 'warn ', 'info ', 'ok ', 'echo ')):
                continue
            if 'as_service' in s or '[[ -x' in s or '[[ -f' in s or '[[ ! -' in s:
                continue
            if self.ROOT_EXEC.search(s):
                bad.append(f'{rel}:{n}: {s}')
        return bad

    def test_no_root_venv_execution(self):
        bad = self._offending('install.sh') + self._offending('update.sh')
        # The only allowed bare occurrences are continuation lines of an
        # `as_service env VAR=... \` command (the interpreter on its own line).
        bad = [b for b in bad if not re.search(r'"\$VENV_DIR/bin/python" << ', b)]
        self.assertEqual(bad, [])

    def test_no_root_fallback_after_sudo_u(self):
        for rel in ('install.sh', 'update.sh'):
            code = '\n'.join(_code_lines(_read(rel)))
            with self.subTest(rel=rel):
                self.assertNotRegex(code, r'sudo -u "\$SERVICE_USER"')
                self.assertNotIn('"$VENV_DIR/bin/anetbbs-symlinks"', code)

    def test_heredoc_continuations_are_under_as_service(self):
        for rel in ('install.sh', 'update.sh'):
            lines = _read(rel).splitlines()
            for i, line in enumerate(lines):
                if re.search(r'"\$VENV_DIR/bin/python" << ', line):
                    j = i
                    while j > 0 and lines[j - 1].rstrip().endswith('\\'):
                        j -= 1
                    head = lines[j]
                    with self.subTest(rel=rel, line=i + 1):
                        self.assertIn('as_service env', head)

    def test_system_python_heredocs_are_isolated(self):
        for rel in ('install.sh', 'update.sh'):
            with self.subTest(rel=rel):
                self.assertNotRegex(_read(rel), r'\bpython3 << ')


def _bash(script, env=None):
    full = f'set -eu -o pipefail\n. "{DEPLOY}/privileged_helpers.sh"\n{script}\n'
    return subprocess.run(['bash', '-c', full], capture_output=True, text=True,
                          timeout=30, env=dict(os.environ, **(env or {})))


class PrivilegedHelpersBehaviour(unittest.TestCase):
    def test_dir_trust_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _bash(f'_anetbbs_dir_is_root_trusted / nobody && echo ROOT_OK; '
                      f'_anetbbs_dir_is_root_trusted "{tmp}" nobody || echo TMP_REFUSED')
            self.assertIn('ROOT_OK', r.stdout, r.stderr)
            self.assertIn('TMP_REFUSED', r.stdout, r.stderr)

    def test_wrapper_install_refuses_untrusted_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _bash(f'anetbbs_install_root_wrappers "{REPO}" nobody "{tmp}/libexec" '
                      f'&& echo INSTALLED || echo REFUSED')
            self.assertIn('REFUSED', r.stdout, r.stderr)
            self.assertFalse((Path(tmp) / 'libexec' / 'run_upgrade.sh').exists())

    def test_install_conf_defaults_and_ignores_env_registry(self):
        with tempfile.TemporaryDirectory() as tmp:
            conf = Path(tmp) / 'anetbbs.install'
            inst = Path(tmp) / 'inst'
            inst.mkdir()
            # An install that already has a trusted host never takes a new
            # one from .env (the one-time migration only applies when there
            # is no host yet -- see test_audit_upgrade_feeds_and_signing).
            conf.write_text('INSTALL_DIR=/x\nSERVICE_USER=anetbbs\nREGISTRY_HOST=bbs.a-net.fyi\n')
            (inst / '.env').write_text('REGISTRY_URL=https://evil.example/x\n')
            r = _bash(f'anetbbs_write_install_conf "{inst}" anetbbs "{conf}"',
                      env={'ANETBBS_REGISTRY_HOST': ''})
            self.assertEqual(r.returncode, 0, r.stderr)
            text = conf.read_text()
            self.assertIn(f'INSTALL_DIR={inst}\n', text)
            self.assertIn('SERVICE_USER=anetbbs\n', text)
            self.assertIn('REGISTRY_HOST=bbs.a-net.fyi\n', text)
            self.assertNotIn('evil.example', text)
            self.assertIn('evil.example', r.stderr)   # warned, not trusted
            self.assertEqual(oct(conf.stat().st_mode & 0o777), '0o644')

    def test_install_conf_preserves_and_overrides_registry_host(self):
        with tempfile.TemporaryDirectory() as tmp:
            conf = Path(tmp) / 'anetbbs.install'
            conf.write_text('INSTALL_DIR=/x\nSERVICE_USER=a\nREGISTRY_HOST=feed.example.org\n')
            r = _bash(f'anetbbs_write_install_conf /opt/anetbbs anetbbs "{conf}" /nonexistent',
                      env={'ANETBBS_REGISTRY_HOST': ''})
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('REGISTRY_HOST=feed.example.org\n', conf.read_text())
            r = _bash(f'anetbbs_write_install_conf /opt/anetbbs anetbbs "{conf}" /nonexistent',
                      env={'ANETBBS_REGISTRY_HOST': 'other.example.net'})
            self.assertIn('REGISTRY_HOST=other.example.net\n', conf.read_text())
            r = _bash(f'anetbbs_write_install_conf /opt/anetbbs anetbbs "{conf}" /nonexistent',
                      env={'ANETBBS_REGISTRY_HOST': 'bad host;rm'})
            self.assertIn('REGISTRY_HOST=bbs.a-net.fyi\n', conf.read_text())

    def test_as_service_refuses_root_or_empty(self):
        r = _bash('anetbbs_as_service root /bin/true && echo RAN || echo REFUSED')
        self.assertIn('REFUSED', r.stdout)
        r = _bash('anetbbs_as_service "" /bin/true && echo RAN || echo REFUSED')
        self.assertIn('REFUSED', r.stdout)

    def test_as_service_runs_directly_when_already_that_user(self):
        import getpass
        me = getpass.getuser()
        if me == 'root':
            self.skipTest('running as root')
        r = _bash(f'anetbbs_as_service {me} id -un')
        self.assertEqual(r.stdout.strip(), me, r.stderr)

    def test_console_script_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            inst = Path(tmp) / 'inst'
            (inst / 'venv' / 'bin').mkdir(parents=True)
            (inst / 'venv' / 'bin' / 'anetbbs-cfg').write_text('')
            bindir = Path(tmp) / 'bin'
            bindir.mkdir()
            r = _bash(f'anetbbs_link_console_scripts "{inst}" "{bindir}"')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(os.readlink(bindir / 'anetbbs-cfg'),
                             str(inst / 'venv' / 'bin' / 'anetbbs-cfg'))
            self.assertFalse((bindir / 'anetbbs-web').exists())


if __name__ == '__main__':
    unittest.main()
