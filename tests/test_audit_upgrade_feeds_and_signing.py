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

    def test_private_feed_host_carried_over_once(self):
        self._env('REGISTRY_URL=https://feed.private.example/x\n')
        text, err = self._write()
        self.assertIn('REGISTRY_HOST=feed.private.example\n', text)
        self.assertIn('one-time migration', err)
        # Later edits to .env never change the trusted host.
        self._env('REGISTRY_URL=https://evil.example\n')
        text, err = self._write()
        self.assertIn('REGISTRY_HOST=feed.private.example\n', text)
        self.assertIn('evil.example', err)    # warned, not trusted

    def test_default_feed_when_unset(self):
        self._env('BBS_NAME=x\n')
        text, _ = self._write()
        self.assertIn('REGISTRY_HOST=bbs.a-net.fyi\n', text)
        self.assertIn('REQUIRE_SIGNED_UPGRADES=no\n', text)

    def test_hub_requires_signed_by_default_and_override_works(self):
        self._env('REGISTRY_MODE_ENABLED=true\n')
        text, _ = self._write()
        self.assertIn('REQUIRE_SIGNED_UPGRADES=yes\n', text)
        # Preserved even if .env later says otherwise.
        self._env('REGISTRY_MODE_ENABLED=false\n')
        text, _ = self._write()
        self.assertIn('REQUIRE_SIGNED_UPGRADES=yes\n', text)
        text, _ = self._write(ANETBBS_REQUIRE_SIGNED_UPGRADES='no')
        self.assertIn('REQUIRE_SIGNED_UPGRADES=no\n', text)

    def test_empty_signers_template_installs_nothing(self):
        dst = os.path.join(self.tmp, 'etc', 'release-signers')
        r = _bash(f'anetbbs_install_release_signers "{REPO}" "{dst}"')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(dst))


@unittest.skipUnless(shutil.which('ssh-keygen'), 'ssh-keygen not installed')
class WrapperSignaturePolicy(unittest.TestCase):
    """Runs the real run_upgrade.sh, past detach, with its root-only paths
    swapped for temp files. Network access is never reached for the
    refusal cases; the signed-from-other-host case stops at download."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='sig-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.inst = os.path.join(self.tmp, 'inst')
        os.makedirs(os.path.join(self.inst, 'data'))
        self.conf = os.path.join(self.tmp, 'anetbbs.install')
        self.signers = os.path.join(self.tmp, 'release-signers')

    def _script(self):
        src = (DEPLOY / 'run_upgrade.sh').read_text()
        uid = os.getuid()
        for old, new in (
                ('CONF=/etc/anetbbs.install', f'CONF={self.conf}'),
                ('[ "$(stat -c %u "$CONF")" = 0 ]', f'[ "$(stat -c %u "$CONF")" = {uid} ]'),
                ('SIGNERS=/etc/anetbbs/release-signers', f'SIGNERS={self.signers}'),
                ('&& [ "$(stat -c %u "$SIGNERS")" = 0 ]', f'&& [ "$(stat -c %u "$SIGNERS")" = {uid} ]')):
            self.assertEqual(src.count(old), 1, old)
            src = src.replace(old, new)
        p = os.path.join(self.tmp, 'run_upgrade.sh')
        with open(p, 'w') as f:
            f.write(src)
        os.chmod(p, 0o755)
        return p

    def _run(self, *args, require='no', signers=''):
        with open(self.conf, 'w') as f:
            f.write(f'INSTALL_DIR={self.inst}\nSERVICE_USER=anetbbs\n'
                    f'REGISTRY_HOST=bbs.a-net.fyi\nREQUIRE_SIGNED_UPGRADES={require}\n')
        os.chmod(self.conf, 0o644)
        if signers:
            with open(self.signers, 'w') as f:
                f.write(signers)
            os.chmod(self.signers, 0o644)
        env = dict(os.environ, ANETBBS_UPGRADE_DETACHED='1', PATH='/usr/bin:/bin')
        return subprocess.run([self._script(), *args], capture_output=True, text=True,
                              timeout=60, env=env)

    SHA = 'a' * 64

    def test_unsigned_from_other_host_refused(self):
        r = self._run('v9.9.9', 'https://mirror.example/ANetBBS-v9.9.9.tar.gz', self.SHA)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('refusing an unsigned download', r.stderr)

    def test_required_signature_missing_refused(self):
        r = self._run('v9.9.9', 'https://bbs.a-net.fyi/ANetBBS-v9.9.9.tar.gz', self.SHA,
                      require='yes')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('requires signed upgrades', r.stderr)

    def test_signature_without_trusted_signers_does_not_bypass_host(self):
        r = self._run('v9.9.9', 'https://mirror.example/ANetBBS-v9.9.9.tar.gz', self.SHA,
                      'https://mirror.example/ANetBBS-v9.9.9.tar.gz.sig')
        self.assertIn('refusing an unsigned download', r.stderr)

    def test_signed_release_may_come_from_any_host(self):
        r = self._run('v9.9.9', 'https://mirror.invalid/ANetBBS-v9.9.9.tar.gz', self.SHA,
                      'https://mirror.invalid/ANetBBS-v9.9.9.tar.gz.sig',
                      signers='anetbbs-release ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIDummyKeyOnlyForPolicyTest x\n')
        self.assertNotIn('REGISTRY_HOST', r.stderr)
        self.assertIn('download failed', r.stderr)   # got past the origin policy

    def test_bad_signature_url_shape_refused(self):
        r = self._run('v9.9.9', 'https://bbs.a-net.fyi/ANetBBS-v9.9.9.tar.gz', self.SHA,
                      'http://bbs.a-net.fyi/x.sig')
        self.assertIn('must be https', r.stderr)


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
