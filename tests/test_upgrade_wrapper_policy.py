"""The root upgrade wrapper's release-identity and signature policy.

Runs the real deploy/run_upgrade.sh (root-only paths swapped, see
tests/_upgrade_harness.py) against releases served over loopback http, the
hub arrangement. Every refusal also checks that nothing trusted changed:
the installed-release record, and that update.sh never ran.
"""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from tests._upgrade_harness import (Wrapper, make_key, make_release, serve,
                                    sha256_file, sign, signer_line)

HAVE_KEYGEN = shutil.which('ssh-keygen') is not None


@unittest.skipUnless(HAVE_KEYGEN and shutil.which('curl') and shutil.which('flock'),
                     'needs ssh-keygen, curl and flock')
class WrapperPolicy(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='wpol-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.srv_dir = self.tmp / 'srv'
        self.srv_dir.mkdir()
        self.srv = serve(str(self.srv_dir))
        self.addCleanup(self.srv.shutdown)
        self.host = f'localhost:{self.srv.server_address[1]}'
        self.w = Wrapper(self.tmp / 'w', registry_host=self.host)
        self.key = make_key(self.tmp)
        self.w.trust_file('release-signers', signer_line(self.key))
        self.marker = self.tmp / 'update-ran'
        self._n = 0

    def _release(self, version, *, signed=True, key=None, **kw):
        self._n += 1
        tb = make_release(self.tmp / f'b{self._n}', self.srv_dir, version,
                          state_path=self.w.state, marker=self.marker, **kw)
        if signed:
            sign(key or self.key, tb)
        url = f'http://{self.host}/{tb.name}'
        return url, sha256_file(tb)

    def _state(self):
        return self.w.state.read_text() if self.w.state.exists() else None

    def _assert_refused(self, r, text, state_before):
        out = r.stdout + r.stderr
        self.assertNotEqual(r.returncode, 0, out)
        self.assertIn(text, out)
        self.assertEqual(self._state(), state_before, 'installed-release record changed')
        self.assertFalse(self.marker.exists(), 'update.sh ran on a refused release')

    # ── success paths ────────────────────────────────────────────────────
    def test_signed_newer_release_installs_and_is_recorded(self):
        url, sha = self._release('v1.1.75')
        r = self.w.run('v1.1.75', url, sha, url + '.sig', detached=False)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr + self.w.log_text)
        log = self.w.log_text
        self.assertRegex(log, r'run_id=\S+')
        run_id = log.split('run_id=')[1].split()[0]
        self.assertIn('release signature verified', log)
        self.assertIn('archive identity verified: ANetBBS-v1.1.75', log)
        self.assertIn(f'result=ok run_id={run_id}', log)
        self.assertIn('INSTALLED_VERSION=v1.1.75', self._state())
        # update.sh was told exactly what it is installing.
        self.assertEqual(self.marker.read_text().strip(),
                         f'--install-dir {self.w.inst}|v1.1.75|{sha}')

    def test_operator_unsigned_policy_allows_registry_host_only(self):
        self.w.write_conf(require_signed='no', set_by='operator', registry_host=self.host)
        url, sha = self._release('v1.1.75', signed=False)
        r = self.w.run('v1.1.75', url, sha)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('mode=unsigned', r.stdout)

    # ── release identity ─────────────────────────────────────────────────
    def test_same_version_reinstall_refused(self):
        before = self._state()
        url, sha = self._release('v1.1.74')
        self._assert_refused(self.w.run('v1.1.74', url, sha, url + '.sig'),
                             'is not newer than the installed v1.1.74', before)

    def test_downgrade_refused(self):
        self.w.write_state('v1.1.80')
        before = self._state()
        url, sha = self._release('v1.1.75')
        self._assert_refused(self.w.run('v1.1.75', url, sha, url + '.sig'),
                             'is not newer than the installed v1.1.80', before)

    def test_below_floor_refused_even_if_state_is_older(self):
        self.w.write_state('v1.1.70')
        before = self._state()
        url, sha = self._release('v1.1.73')
        self._assert_refused(self.w.run('v1.1.73', url, sha, url + '.sig'),
                             'older than the minimum v1.1.74', before)

    def test_prerelease_versions_refused(self):
        before = self._state()
        self._assert_refused(self.w.run('v1.2a1.5', f'http://{self.host}/ANetBBS-v1.2a1.5.tar.gz', 'a' * 64),
                             'is not a stable vX.Y.Z', before)

    def test_missing_or_untrusted_state_refused(self):
        url, sha = self._release('v1.1.75')
        self.w.state.chmod(0o666)
        r = self.w.run('v1.1.75', url, sha, url + '.sig')
        self.assertIn('no trusted record of the installed release', r.stderr)
        self.w.state.unlink()
        r = self.w.run('v1.1.75', url, sha, url + '.sig')
        self.assertIn('no trusted record of the installed release', r.stderr)
        self.assertFalse(self.marker.exists())

    def test_inner_version_mismatch_refused_before_update_sh(self):
        before = self._state()
        url, sha = self._release('v1.1.75', inner_version='v1.1.73')
        self._assert_refused(self.w.run('v1.1.75', url, sha, url + '.sig'),
                             "archive VERSION 'v1.1.73' does not match requested v1.1.75", before)

    def test_wrong_top_directory_refused(self):
        before = self._state()
        url, sha = self._release('v1.1.75', top='ANetBBS-v1.1.73')
        self._assert_refused(self.w.run('v1.1.75', url, sha, url + '.sig'),
                             'archive top-level directory is not ANetBBS-v1.1.75', before)

    def test_update_sh_that_does_not_record_state_fails(self):
        before = self._state()
        tb = make_release(self.tmp / 'nostate', self.srv_dir, 'v1.1.75', marker=self.marker)
        sign(self.key, tb)
        url = f'http://{self.host}/{tb.name}'
        r = self.w.run('v1.1.75', url, sha256_file(tb), url + '.sig')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('did not record v1.1.75 as installed', r.stderr)
        self.assertEqual(self._state(), before)

    def test_digest_mismatch_refused(self):
        before = self._state()
        url, _ = self._release('v1.1.75')
        self._assert_refused(self.w.run('v1.1.75', url, 'b' * 64, url + '.sig'),
                             'sha256 mismatch', before)

    def test_denylisted_archive_refused_before_download(self):
        before = self._state()
        url, sha = self._release('v1.1.75')
        self.w.trust_file('release-denylist', f'# rejected\n{sha} rejected candidate\n')
        r = self.w.run('v1.1.75', url, sha, url + '.sig')
        self._assert_refused(r, 'is on the denylist', before)
        self.assertNotIn('downloading', r.stdout)

    # ── signatures ───────────────────────────────────────────────────────
    def test_unsigned_refused_when_signatures_required(self):
        before = self._state()
        url, sha = self._release('v1.1.75', signed=False)
        self._assert_refused(self.w.run('v1.1.75', url, sha),
                             'no signature was offered', before)

    def test_required_but_no_trusted_signer_refused(self):
        (self.w.trust / 'release-signers').unlink()
        before = self._state()
        url, sha = self._release('v1.1.75')
        self._assert_refused(self.w.run('v1.1.75', url, sha, url + '.sig'),
                             'lists no trusted key', before)

    def test_unknown_key_refused(self):
        before = self._state()
        other = make_key(self.tmp, 'other')
        url, sha = self._release('v1.1.75', key=other)
        self._assert_refused(self.w.run('v1.1.75', url, sha, url + '.sig'),
                             'did NOT verify', before)

    def test_revoked_key_refused_even_when_listed_as_signer(self):
        before = self._state()
        self.w.trust_file('release-revoked-keys',
                          '# revoked\n' + Path(str(self.key) + '.pub').read_text())
        url, sha = self._release('v1.1.75')
        self._assert_refused(self.w.run('v1.1.75', url, sha, url + '.sig'),
                             'did NOT verify', before)

    def test_tampered_archive_with_valid_old_signature_refused(self):
        before = self._state()
        url, _ = self._release('v1.1.75')
        tb = self.srv_dir / 'ANetBBS-v1.1.75.tar.gz'
        sig = (Path(str(tb) + '.sig')).read_bytes()
        tb2 = make_release(self.tmp / 'evil', self.tmp, 'v1.1.75', state_path=self.w.state,
                           marker=self.marker, extra_files={'evil': 'x'})
        shutil.copy(tb2, tb)
        Path(str(tb) + '.sig').write_bytes(sig)
        self._assert_refused(self.w.run('v1.1.75', url, sha256_file(tb), url + '.sig'),
                             'did NOT verify', before)

    def test_non_root_trust_files_refused(self):
        before = self._state()
        url, sha = self._release('v1.1.75')
        p = self.w.trust_file('release-revoked-keys', '# none\n')
        p.chmod(0o666)
        self._assert_refused(self.w.run('v1.1.75', url, sha, url + '.sig'),
                             'is not root-owned', before)

    def test_unsigned_policy_without_operator_still_requires_signatures(self):
        # Only "no" turns signatures off; anything else (missing, typo) is yes.
        self.w.write_conf(require_signed='maybe', registry_host=self.host)
        before = self._state()
        url, sha = self._release('v1.1.75', signed=False)
        self._assert_refused(self.w.run('v1.1.75', url, sha),
                             'no signature was offered', before)

    def test_unsigned_from_other_host_refused_even_when_allowed(self):
        self.w.write_conf(require_signed='no', set_by='operator', registry_host='bbs.a-net.fyi')
        before = self._state()
        url, sha = self._release('v1.1.75', signed=False)
        self._assert_refused(self.w.run('v1.1.75', url, sha),
                             "does not match REGISTRY_HOST 'bbs.a-net.fyi'", before)

    # ── transport and arguments ──────────────────────────────────────────
    def test_bad_urls_refused_without_download(self):
        before = self._state()
        for url in ('http://bbs.a-net.fyi/ANetBBS-v1.1.75.tar.gz',
                    'http://10.0.0.5:5000/ANetBBS-v1.1.75.tar.gz',
                    'http://localhost.evil.example/ANetBBS-v1.1.75.tar.gz',
                    'http://127.0.0.1:5000@evil.example/ANetBBS-v1.1.75.tar.gz',
                    'https://user@bbs.a-net.fyi/ANetBBS-v1.1.75.tar.gz',
                    'https://bbs.a-net.fyi/ANetBBS-v1.1.75.zip',
                    'ftp://bbs.a-net.fyi/ANetBBS-v1.1.75.tar.gz'):
            r = self.w.run('v1.1.75', url, 'a' * 64, url + '.sig')
            self.assertNotEqual(r.returncode, 0, url)
            self.assertNotIn('downloading', r.stdout, url)
        for sig in ('https://bbs.a-net.fyi/x.sig', 'http://evil.example/x.tar.gz.sig',
                    'https://bbs.a-net.fyi/a;b.tar.gz.sig'):
            r = self.w.run('v1.1.75', 'https://bbs.a-net.fyi/ANetBBS-v1.1.75.tar.gz', 'a' * 64, sig)
            self.assertIn('signature url', r.stderr, sig)
        self.assertEqual(self._state(), before)

    def test_curl_is_required(self):
        bindir = self.tmp / 'nocurl'
        bindir.mkdir()
        for tool in ('bash', 'stat', 'grep', 'cut', 'tr', 'cat', 'date', 'od', 'flock'):
            src = shutil.which(tool)
            if src:
                os.symlink(src, bindir / tool)
        r = self.w.run('v1.1.75', 'https://bbs.a-net.fyi/ANetBBS-v1.1.75.tar.gz', 'a' * 64,
                       env={'PATH': str(bindir)})
        self.assertIn('curl is required', r.stderr)

    # ── log and lock ─────────────────────────────────────────────────────
    def test_refusal_before_detach_never_touches_the_log(self):
        self.w.log_dir.mkdir()
        (self.w.log_dir / 'upgrade.log').write_text('previous run output\n')
        url, sha = self._release('v1.1.74')     # same version: refused
        r = self.w.run('v1.1.74', url, sha, url + '.sig', detached=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.w.log_text, 'previous run output\n')

    def test_second_invocation_refused_while_lock_held(self):
        import fcntl
        url, sha = self._release('v1.1.75')
        self.w.log_dir.mkdir()
        (self.w.log_dir / 'upgrade.log').write_text('running upgrade\n')
        with open(self.w.lock, 'a') as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            r = self.w.run('v1.1.75', url, sha, url + '.sig', detached=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('another upgrade is already running', r.stderr)
        self.assertEqual(self.w.log_text, 'running upgrade\n')
        self.assertFalse(self.marker.exists())

    def test_failure_in_detached_phase_ends_with_result_fail(self):
        url, _ = self._release('v1.1.75')
        r = self.w.run('v1.1.75', url, 'c' * 64, url + '.sig', detached=False)
        self.assertNotEqual(r.returncode, 0)
        log = self.w.log_text
        run_id = log.split('run_id=')[1].split()[0]
        self.assertIn('sha256 mismatch', log)
        self.assertIn(f'result=fail run_id={run_id}', log)


class WrapperStaticShape(unittest.TestCase):
    def test_no_service_controlled_inputs(self):
        code = '\n'.join(l for l in Path(__file__).resolve().parent.parent.joinpath(
            'deploy', 'run_upgrade.sh').read_text().splitlines() if not l.lstrip().startswith('#'))
        for bad in ('.env', 'REGISTRY_URL', 'BASH_SOURCE', 'wget', '$INSTALL_DIR/VERSION',
                    '. /etc/anetbbs.install', 'source '):
            self.assertNotIn(bad, code)
        self.assertIn('MIN_VERSION=v1.1.74', code)


if __name__ == '__main__':
    unittest.main()
