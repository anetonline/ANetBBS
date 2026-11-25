"""Web auto-update keeps working for both registry arrangements:

- a hub whose REGISTRY_URL is http://localhost:<port>/ (its own releases,
  over loopback http, signed because hubs require signatures);
- peers whose REGISTRY_URL is https://<hub>/ while the hub advertises
  http:// links (reverse proxy without TRUST_PROXY_HEADERS).

Plain http to anything but loopback stays refused.
"""
import functools
import http.server
import os
import shutil
import subprocess
import tarfile
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
DEPLOY = REPO / 'deploy'


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def _serve(directory):
    handler = functools.partial(_Quiet, directory=directory)
    srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class WrapperTransport(unittest.TestCase):
    """The real run_upgrade.sh, past detach, root-only paths swapped."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='lbk-')
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

    def _run(self, *args, host, require='no'):
        with open(self.conf, 'w') as f:
            f.write(f'INSTALL_DIR={self.inst}\nSERVICE_USER=anetbbs\n'
                    f'REGISTRY_HOST={host}\nREQUIRE_SIGNED_UPGRADES={require}\n')
        os.chmod(self.conf, 0o644)
        env = dict(os.environ, ANETBBS_UPGRADE_DETACHED='1', PATH='/usr/bin:/bin')
        return subprocess.run([self._script(), *args], capture_output=True, text=True,
                              timeout=60, env=env)

    def test_non_loopback_http_and_disguised_hosts_refused(self):
        sha = 'a' * 64
        for url in ('http://bbs.a-net.fyi/ANetBBS-v9.9.9.tar.gz',
                    'http://10.0.0.5:5000/ANetBBS-v9.9.9.tar.gz',
                    'http://localhost.evil.example/ANetBBS-v9.9.9.tar.gz',
                    'http://127.0.0.1:5000@evil.example/ANetBBS-v9.9.9.tar.gz',
                    'http://localhost:5evil.example/ANetBBS-v9.9.9.tar.gz'):
            r = self._run('v9.9.9', url, sha, host='localhost:5000')
            self.assertNotEqual(r.returncode, 0, url)
            self.assertNotIn('downloading', r.stdout + r.stderr, url)

    def test_unsigned_loopback_http_downloads_when_host_trusted(self):
        srv_dir = os.path.join(self.tmp, 'srv')
        os.makedirs(srv_dir)
        with open(os.path.join(srv_dir, 'ANetBBS-v9.9.9.tar.gz'), 'wb') as f:
            f.write(b'not the expected bytes')
        srv = _serve(srv_dir)
        self.addCleanup(srv.shutdown)
        host = f'127.0.0.1:{srv.server_address[1]}'
        r = self._run('v9.9.9', f'http://{host}/ANetBBS-v9.9.9.tar.gz', 'a' * 64, host=host)
        self.assertIn('sha256 mismatch', r.stderr)       # downloaded over loopback http

    @unittest.skipUnless(shutil.which('ssh-keygen'), 'ssh-keygen not installed')
    def test_hub_signed_release_over_loopback_http_completes(self):
        # Hub arrangement: REGISTRY_URL=http://localhost:<port>/, signatures required.
        srv_dir = os.path.join(self.tmp, 'srv')
        top = os.path.join(self.tmp, 'build', 'ANetBBS-v9.9.9')
        os.makedirs(top)
        os.makedirs(srv_dir)
        marker = os.path.join(self.tmp, 'update-ran')
        with open(os.path.join(top, 'update.sh'), 'w') as f:
            f.write(f'#!/bin/bash\necho "$@" > {marker}\n')
        tb = os.path.join(srv_dir, 'ANetBBS-v9.9.9.tar.gz')
        with tarfile.open(tb, 'w:gz') as t:
            t.add(top, arcname='ANetBBS-v9.9.9')
        key = os.path.join(self.tmp, 'k')
        subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', key,
                        '-C', 'anetbbs-release'], check=True)
        subprocess.run(['ssh-keygen', '-Y', 'sign', '-f', key, '-n', 'anetbbs-release', tb],
                       check=True, capture_output=True)
        with open(key + '.pub') as f, open(self.signers, 'w') as out:
            out.write('anetbbs-release ' + f.read())
        os.chmod(self.signers, 0o644)
        sha = subprocess.run(['sha256sum', tb], capture_output=True, text=True,
                             check=True).stdout.split()[0]
        srv = _serve(srv_dir)
        self.addCleanup(srv.shutdown)
        host = f'localhost:{srv.server_address[1]}'
        base = f'http://{host}/ANetBBS-v9.9.9.tar.gz'
        r = self._run('v9.9.9', base, sha, base + '.sig', host=host, require='yes')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('release signature verified', r.stdout)
        self.assertIn('upgrade to v9.9.9 complete', r.stdout)
        self.assertTrue(os.path.exists(marker))
        # Unsigned on the same hub is still refused.
        r = self._run('v9.9.9', base, sha, host=host, require='yes')
        self.assertIn('requires signed upgrades', r.stderr)


class HubInstallConf(unittest.TestCase):
    def test_hub_localhost_registry_url_kept_as_trusted_host(self):
        tmp = tempfile.mkdtemp(prefix='hubconf-')
        self.addCleanup(shutil.rmtree, tmp, True)
        inst = os.path.join(tmp, 'inst')
        os.makedirs(inst)
        with open(os.path.join(inst, '.env'), 'w') as f:
            f.write('REGISTRY_URL=http://localhost:5000/\nREGISTRY_MODE_ENABLED=true\n')
        conf = os.path.join(tmp, 'anetbbs.install')
        script = (f'set -eu -o pipefail\n. "{DEPLOY}/privileged_helpers.sh"\n'
                  f'anetbbs_write_install_conf "{inst}" anetbbs "{conf}"\n')
        r = subprocess.run(['bash', '-c', script], capture_output=True, text=True,
                           env=dict(os.environ, ANETBBS_REGISTRY_HOST='',
                                    ANETBBS_REQUIRE_SIGNED_UPGRADES=''))
        self.assertEqual(r.returncode, 0, r.stderr)
        text = open(conf).read()
        self.assertIn('REGISTRY_HOST=localhost:5000\n', text)
        self.assertIn('REQUIRE_SIGNED_UPGRADES=yes\n', text)
        # .env is untouched.
        self.assertIn('REGISTRY_URL=http://localhost:5000/',
                      open(os.path.join(inst, '.env')).read())


class WebTransportRules(unittest.TestCase):
    def test_transport_ok(self):
        from anetbbs.web import upgrades as u
        ok = ['https://bbs.a-net.fyi/downloads/ANetBBS-v1.tar.gz',
              'http://localhost:5000/downloads/ANetBBS-v1.tar.gz',
              'http://127.0.0.1/downloads/ANetBBS-v1.tar.gz',
              'http://[::1]:5000/downloads/ANetBBS-v1.tar.gz']
        bad = ['http://bbs.a-net.fyi/downloads/ANetBBS-v1.tar.gz',
               'http://localhost.evil.example/ANetBBS-v1.tar.gz',
               'http://127.0.0.1:5000@evil.example/ANetBBS-v1.tar.gz',
               'https://h/ANetBBS-v1.zip', 'ftp://localhost/ANetBBS-v1.tar.gz']
        for url in ok:
            self.assertTrue(u._transport_ok(url, '.tar.gz'), url)
        for url in bad:
            self.assertFalse(u._transport_ok(url, '.tar.gz'), url)
        self.assertTrue(u._valid_sig_url('http://localhost:5000/d/ANetBBS-v1.tar.gz.sig'))
        self.assertFalse(u._valid_sig_url('http://bbs.a-net.fyi/d/ANetBBS-v1.tar.gz.sig'))

    def test_align_scheme(self):
        from anetbbs.web import upgrades as u
        reg = 'https://bbs.a-net.fyi/'
        self.assertEqual(u._align_scheme('http://bbs.a-net.fyi/d/x.tar.gz', reg),
                         'https://bbs.a-net.fyi/d/x.tar.gz')
        # Other hosts and http registries are left alone.
        self.assertEqual(u._align_scheme('http://other.example/d/x.tar.gz', reg),
                         'http://other.example/d/x.tar.gz')
        self.assertEqual(u._align_scheme('http://localhost:5000/d/x.tar.gz',
                                         'http://localhost:5000/'),
                         'http://localhost:5000/d/x.tar.gz')

    def test_fetch_upstream_aligns_advertised_urls(self):
        from anetbbs.web import upgrades as u
        body = {'version': 'v9.9.9', 'sha256': 'a' * 64, 'size': 10,
                'url': 'http://bbs.a-net.fyi/downloads/ANetBBS-v9.9.9.tar.gz',
                'sig_url': 'http://bbs.a-net.fyi/downloads/ANetBBS-v9.9.9.tar.gz.sig'}
        resp = mock.Mock(status_code=200)
        resp.json.return_value = body
        with mock.patch.object(u.requests, 'get', return_value=resp):
            data, err = u._fetch_upstream({'REGISTRY_URL': 'https://bbs.a-net.fyi/'},
                                          force=True)
        self.assertIsNone(err)
        self.assertEqual(data['url'], 'https://bbs.a-net.fyi/downloads/ANetBBS-v9.9.9.tar.gz')
        self.assertTrue(data['sig_url'].startswith('https://bbs.a-net.fyi/'))
        self.assertTrue(body['url'].startswith('http://'))   # caller's dict not mutated


if __name__ == '__main__':
    unittest.main()
