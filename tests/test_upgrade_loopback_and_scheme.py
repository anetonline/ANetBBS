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
    """The real run_upgrade.sh (tests/_upgrade_harness.py), loopback http."""

    def setUp(self):
        from tests._upgrade_harness import Wrapper
        self.tmp = tempfile.mkdtemp(prefix='lbk-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.w = Wrapper(os.path.join(self.tmp, 'w'), require_signed='no',
                         set_by='operator', registry_host='localhost:5000')

    def test_non_loopback_http_and_disguised_hosts_refused(self):
        sha = 'a' * 64
        for url in ('http://bbs.a-net.fyi/ANetBBS-v1.1.75.tar.gz',
                    'http://10.0.0.5:5000/ANetBBS-v1.1.75.tar.gz',
                    'http://localhost.evil.example/ANetBBS-v1.1.75.tar.gz',
                    'http://127.0.0.1:5000@evil.example/ANetBBS-v1.1.75.tar.gz',
                    'http://localhost:5evil.example/ANetBBS-v1.1.75.tar.gz'):
            r = self.w.run('v1.1.75', url, sha)
            self.assertNotEqual(r.returncode, 0, url)
            self.assertNotIn('downloading', r.stdout + r.stderr, url)

    def test_unsigned_loopback_http_downloads_when_operator_allows_it(self):
        srv_dir = os.path.join(self.tmp, 'srv')
        os.makedirs(srv_dir)
        with open(os.path.join(srv_dir, 'ANetBBS-v1.1.75.tar.gz'), 'wb') as f:
            f.write(b'not the expected bytes')
        srv = _serve(srv_dir)
        self.addCleanup(srv.shutdown)
        host = f'127.0.0.1:{srv.server_address[1]}'
        self.w.write_conf(require_signed='no', set_by='operator', registry_host=host)
        r = self.w.run('v1.1.75', f'http://{host}/ANetBBS-v1.1.75.tar.gz', 'a' * 64)
        self.assertIn('sha256 mismatch', r.stderr)       # downloaded over loopback http

    # The hub's signed loopback-http path, end to end, is covered by
    # tests/test_upgrade_wrapper_policy.py (it serves over http://localhost).


class HubInstallConf(unittest.TestCase):
    def test_hub_conf_requires_signatures_and_ignores_env_feed(self):
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
        # Signed releases install from any host, loopback http included, so
        # the hub's own http://localhost:5000/ feed works without trusting
        # anything from .env.
        self.assertIn('REQUIRE_SIGNED_UPGRADES=yes\n', text)
        self.assertIn('REGISTRY_HOST=bbs.a-net.fyi\n', text)
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
