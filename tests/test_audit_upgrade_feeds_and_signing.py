"""Web auto-update keeps working for private feeds after the H-1 hardening,
and signed releases install from any host.

- First update after H-1: the feed host already trusted via .env
  REGISTRY_URL is carried into root-owned /etc/anetbbs.install (same trust
  the old wrapper gave it), then never silently changed.
- A release signed by a key in root-owned release-signers may come from any
  https host; an unsigned one only from the trusted host, and not at all
  when REQUIRE_SIGNED_UPGRADES=yes (default on registry hubs).
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEPLOY = REPO / 'deploy'


def _bash(script, env=None):
    full = f'set -eu -o pipefail\n. "{DEPLOY}/privileged_helpers.sh"\n{script}\n'
    return subprocess.run(['bash', '-c', full], capture_output=True, text=True,
                          timeout=30, env=dict(os.environ, **(env or {})))


class InstallConfMigration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='feed-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.conf = os.path.join(self.tmp, 'anetbbs.install')
        self.inst = os.path.join(self.tmp, 'inst')
        os.makedirs(self.inst)

    def _env(self, text):
        with open(os.path.join(self.inst, '.env'), 'w') as f:
            f.write(text)

    def _write(self, **env):
        base = {'ANETBBS_REGISTRY_HOST': '', 'ANETBBS_REQUIRE_SIGNED_UPGRADES': ''}
        base.update(env)
        r = _bash(f'anetbbs_write_install_conf "{self.inst}" anetbbs "{self.conf}"', env=base)
        self.assertEqual(r.returncode, 0, r.stderr)
        return open(self.conf).read(), r.stderr

    def test_env_registry_url_never_becomes_trusted(self):
        # .env belongs to the service account; nothing in it is trusted.
        self._env('REGISTRY_URL=https://feed.private.example/x\nREGISTRY_MODE_ENABLED=false\n')
        text, _ = self._write()
        self.assertIn('REGISTRY_HOST=bbs.a-net.fyi\n', text)
        self.assertNotIn('feed.private.example', text)

    def test_signatures_required_by_default_everywhere(self):
        for env in ('BBS_NAME=x\n', 'REGISTRY_MODE_ENABLED=true\n', 'REGISTRY_MODE_ENABLED=false\n'):
            self._env(env)
            if os.path.exists(self.conf):
                os.remove(self.conf)
            text, _ = self._write()
            self.assertIn('REQUIRE_SIGNED_UPGRADES=yes\n', text, env)
            self.assertIn('REQUIRE_SIGNED_SET_BY=default\n', text, env)

    def test_operator_opt_out_is_explicit_and_sticks(self):
        text, _ = self._write(ANETBBS_REQUIRE_SIGNED_UPGRADES='no',
                              ANETBBS_REGISTRY_HOST='feed.private.example')
        self.assertIn('REQUIRE_SIGNED_UPGRADES=no\n', text)
        self.assertIn('REQUIRE_SIGNED_SET_BY=operator\n', text)
        self.assertIn('REGISTRY_HOST=feed.private.example\n', text)
        os.chmod(self.conf, 0o644)
        text, _ = self._write()                      # later run, no override
        self.assertIn('REQUIRE_SIGNED_UPGRADES=no\n', text)
        self.assertIn('REGISTRY_HOST=feed.private.example\n', text)
        text, _ = self._write(ANETBBS_REQUIRE_SIGNED_UPGRADES='yes')
        self.assertIn('REQUIRE_SIGNED_UPGRADES=yes\n', text)

    def test_unsigned_setting_not_set_by_an_operator_is_reset(self):
        with open(self.conf, 'w') as f:
            f.write('INSTALL_DIR=/x\nSERVICE_USER=anetbbs\nREGISTRY_HOST=bbs.a-net.fyi\n'
                    'REQUIRE_SIGNED_UPGRADES=no\n')
        os.chmod(self.conf, 0o644)
        text, _ = self._write()
        self.assertIn('REQUIRE_SIGNED_UPGRADES=yes\n', text)

    def test_untrusted_existing_conf_is_not_read(self):
        with open(self.conf, 'w') as f:
            f.write('INSTALL_DIR=/x\nSERVICE_USER=anetbbs\nREGISTRY_HOST=evil.example\n'
                    'REQUIRE_SIGNED_UPGRADES=no\nREQUIRE_SIGNED_SET_BY=operator\n')
        os.chmod(self.conf, 0o666)
        text, _ = self._write()
        self.assertIn('REGISTRY_HOST=bbs.a-net.fyi\n', text)
        self.assertIn('REQUIRE_SIGNED_UPGRADES=yes\n', text)

    def test_empty_signers_template_installs_nothing(self):
        fake = os.path.join(self.tmp, 'fakerepo')
        os.makedirs(os.path.join(fake, 'deploy'))
        with open(os.path.join(fake, 'deploy', 'release-signers'), 'w') as f:
            f.write('# comments only\n\n')
        dst = os.path.join(self.tmp, 'etc', 'release-signers')
        r = _bash(f'anetbbs_install_release_signers "{fake}" "{dst}"')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(dst))

    def test_shipped_signers_are_well_formed_and_install(self):
        lines = [ln for ln in (DEPLOY / 'release-signers').read_text().splitlines()
                 if ln.strip() and not ln.lstrip().startswith('#')]
        self.assertTrue(lines, 'deploy/release-signers ships no key')
        for ln in lines:
            self.assertRegex(ln, r'^anetbbs-release ssh-ed25519 AAAAC3NzaC1lZDI1NTE5[A-Za-z0-9+/]+={0,2}( \S+)?$')
        dst = os.path.join(self.tmp, 'etc', 'release-signers')
        r = _bash(f'anetbbs_install_release_signers "{REPO}" "{dst}"')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(Path(dst).read_text(), (DEPLOY / 'release-signers').read_text())
        self.assertEqual(os.stat(dst).st_mode & 0o777, 0o644)
        if shutil.which('ssh-keygen'):
            # ssh-keygen must accept the file: a signature from an unrelated
            # key has to be rejected as "not trusted", not as a parse error.
            key = os.path.join(self.tmp, 'other')
            subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', key],
                           check=True)
            data = os.path.join(self.tmp, 'blob')
            Path(data).write_bytes(b'x')
            subprocess.run(['ssh-keygen', '-Y', 'sign', '-f', key, '-n', 'anetbbs-release', data],
                           check=True, capture_output=True)
            v = subprocess.run(['ssh-keygen', '-Y', 'verify', '-f', dst, '-I', 'anetbbs-release',
                                '-n', 'anetbbs-release', '-s', data + '.sig'],
                               stdin=open(data, 'rb'), capture_output=True, text=True)
            self.assertNotEqual(v.returncode, 0)
            self.assertNotRegex(v.stderr.lower(), r'line \d+|invalid|parse')


# Wrapper signature policy: tests/test_upgrade_wrapper_policy.py.


@unittest.skipUnless(shutil.which('ssh-keygen'), 'ssh-keygen not installed')
class SignVerifyRoundTrip(unittest.TestCase):
    """The exact commands build-release.sh and run_upgrade.sh use."""

    def test_roundtrip_and_tamper(self):
        tmp = tempfile.mkdtemp(prefix='rt-')
        self.addCleanup(shutil.rmtree, tmp, True)
        key = os.path.join(tmp, 'k')
        subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', key,
                        '-C', 'anetbbs-release'], check=True)
        tar = os.path.join(tmp, 'ANetBBS-v9.9.9.tar.gz')
        with open(tar, 'wb') as f:
            f.write(b'release bytes')
        subprocess.run(['ssh-keygen', '-Y', 'sign', '-f', key, '-n', 'anetbbs-release', tar],
                       check=True, capture_output=True)
        signers = os.path.join(tmp, 'signers')
        with open(key + '.pub') as f, open(signers, 'w') as out:
            out.write('anetbbs-release ' + f.read())

        def verify():
            with open(tar, 'rb') as fh:
                return subprocess.run(['ssh-keygen', '-Y', 'verify', '-f', signers,
                                       '-I', 'anetbbs-release', '-n', 'anetbbs-release',
                                       '-s', tar + '.sig'], stdin=fh,
                                      capture_output=True).returncode
        self.assertEqual(verify(), 0)
        with open(tar, 'ab') as f:
            f.write(b'tampered')
        self.assertNotEqual(verify(), 0)


class WebAdvertisesAndPassesSignature(unittest.TestCase):
    def test_build_cmd_and_sig_url_validation(self):
        from anetbbs.web import upgrades
        cmd = upgrades.build_upgrade_cmd('/w', 'v1.2.3', 'https://h/x.tar.gz', 'a' * 64,
                                         'https://h/x.tar.gz.sig')
        self.assertEqual(cmd[-1], 'https://h/x.tar.gz.sig')
        self.assertEqual(len(upgrades.build_upgrade_cmd('/w', 'v1', 'u', 's')), 4)
        self.assertTrue(upgrades._valid_sig_url('https://h/ANetBBS-v1.tar.gz.sig'))
        for bad in ('http://h/x.tar.gz.sig', 'https://h/x.sig', 'https://h/a;b.tar.gz.sig'):
            self.assertFalse(upgrades._valid_sig_url(bad), bad)

    def test_latest_payload_includes_sig_url_when_present(self):
        import anetbbs.config as cfg_mod
        orig = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        tmp = tempfile.mkdtemp(prefix='lp-')
        self.addCleanup(shutil.rmtree, tmp, True)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{tmp}/t.db'
        try:
            from anetbbs.web_app import create_app
            app = create_app('testing')
            rel = os.path.join(tmp, 'releases')
            os.makedirs(rel)
            app.config['DOWNLOADS_DIR'] = rel
            tar = os.path.join(rel, 'ANetBBS-v9.9.9.tar.gz')
            with open(tar, 'wb') as f:
                f.write(b'x')
            import hashlib
            with open(tar + '.sha256', 'w') as f:
                f.write(hashlib.sha256(b'x').hexdigest() + '  ANetBBS-v9.9.9.tar.gz\n')
            body = app.test_client().get('/api/releases/latest').get_json() or {}
            self.assertIsNone(body.get('sig_url'))
            with open(tar + '.sig', 'w') as f:
                f.write('sig')
            body = app.test_client().get('/api/releases/latest').get_json() or {}
            self.assertTrue((body.get('sig_url') or '').endswith('ANetBBS-v9.9.9.tar.gz.sig'), body)
        finally:
            cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = orig


if __name__ == '__main__':
    unittest.main()
