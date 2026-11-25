"""The release archive must never contain doors/ or data/dos-games/.

Runs the real build-release.sh against a throwaway git repository that
has both trees present (one of them even force-tracked), with output
written to a temporary directory.
"""
import os
import shutil
import subprocess
import tarfile
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@unittest.skipUnless(shutil.which('git'), 'git not available')
@unittest.skipUnless(os.path.isfile(os.path.join(ROOT, 'build-release.sh')),
                     'build-release.sh is not shipped in release archives')
class BuildReleaseExcludesDoors(unittest.TestCase):
    def test_doors_and_dos_games_not_shipped(self):
        tmp = tempfile.mkdtemp(prefix='brel-')
        self.addCleanup(shutil.rmtree, tmp, True)
        repo = os.path.join(tmp, 'repo')
        out = os.path.join(tmp, 'out')
        os.makedirs(out)
        os.makedirs(os.path.join(repo, 'doors', 'synch', 'game'))
        os.makedirs(os.path.join(repo, 'data', 'dos-games'))
        shutil.copy(os.path.join(ROOT, 'build-release.sh'), repo)
        files = {
            'VERSION': '9.9.99\n', 'update.sh': '#!/bin/sh\n',
            '.gitignore': 'doors/\ndata/dos-games/\n',
            'doors/synch/game/game.js': 'x', 'data/dos-games/doom.zip': 'PK',
            'data/dos-games/duke3d.zip': 'PK', 'keep.txt': 'ok',
        }
        for rel, body in files.items():
            with open(os.path.join(repo, rel), 'w') as f:
                f.write(body)
        env = dict(os.environ, ANETBBS_RELEASE_OUT_DIR=out,
                   ANETBBS_SIGNING_KEY=os.path.join(tmp, 'nokey'),
                   GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1')
        git = ['git', '-C', repo, '-c', 'user.email=t@t', '-c', 'user.name=t']
        subprocess.run(git + ['init', '-q'], check=True, env=env)
        subprocess.run(git + ['add', '.'], check=True, env=env)
        # Force-track one file to prove the script's own filter, not
        # just .gitignore, keeps it out.
        subprocess.run(git + ['add', '-f', 'data/dos-games/doom.zip'],
                       check=True, env=env)
        subprocess.run(git + ['commit', '-qm', 'x'], check=True, env=env)
        r = subprocess.run(['bash', os.path.join(repo, 'build-release.sh')],
                           cwd=repo, env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with tarfile.open(os.path.join(out, 'ANetBBS-v9.9.99.tar.gz')) as t:
            names = t.getnames()
        self.assertIn('ANetBBS-v9.9.99/keep.txt', names)
        bad = [n for n in names if '/doors/' in n or '/dos-games/' in n
               or n.endswith('.zip')]
        self.assertEqual(bad, [])



@unittest.skipUnless(shutil.which('git') and shutil.which('ssh-keygen'), 'git/ssh-keygen not available')
@unittest.skipUnless(os.path.isfile(os.path.join(ROOT, 'build-release.sh')),
                     'build-release.sh is not shipped in release archives')
class BuildReleaseIsCommitBoundAndReproducible(unittest.TestCase):
    """Review finding F12: the archive comes from the commit, from a clean
    tree, normalized, and the signature is always checked against the
    signer file that commit ships."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='brel2-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = os.path.join(self.tmp, 'repo')
        self.out = os.path.join(self.tmp, 'out')
        os.makedirs(os.path.join(self.repo, 'deploy'))
        os.makedirs(self.out)
        shutil.copy(os.path.join(ROOT, 'build-release.sh'), self.repo)
        self.key = os.path.join(self.tmp, 'key')
        subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', self.key,
                        '-C', 'anetbbs-release'], check=True)
        with open(self.key + '.pub') as f:
            self.pub = f.read()
        for rel, body in {'VERSION': 'v9.9.99\n', 'update.sh': '#!/bin/sh\n',
                          'keep.txt': 'ok', '.gitignore': '*.log\n',
                          'deploy/release-signers': 'anetbbs-release ' + self.pub,
                          'deploy/release-revoked-keys': '# none\n'}.items():
            with open(os.path.join(self.repo, rel), 'w') as f:
                f.write(body)
        self.env = dict(os.environ, ANETBBS_RELEASE_OUT_DIR=self.out,
                        ANETBBS_SIGNING_KEY=self.key,
                        ANETBBS_SIGNING_LEDGER=os.path.join(self.tmp, 'ledger'),
                        GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1')
        self._git('init', '-q')
        self._commit('one')

    def _git(self, *a):
        return subprocess.run(['git', '-C', self.repo, '-c', 'user.email=t@t',
                               '-c', 'user.name=t', *a], check=True, env=self.env,
                              capture_output=True, text=True)

    def _commit(self, msg):
        self._git('add', '-A')
        self._git('commit', '-qm', msg)

    def _build(self, cwd=None):
        return subprocess.run(['bash', os.path.join(self.repo, 'build-release.sh')],
                              cwd=cwd or self.repo, env=self.env, capture_output=True, text=True)

    def _sha(self):
        with open(os.path.join(self.out, 'ANetBBS-v9.9.99.tar.gz.sha256')) as f:
            return f.read().split()[0]

    def test_reproducible_normalized_and_signed(self):
        r = self._build()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        first = self._sha()
        r = self._build(cwd=self.tmp)          # from another directory: same bytes, still verified
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self._sha(), first)
        self.assertIn('Signed:', r.stdout)
        with tarfile.open(os.path.join(self.out, 'ANetBBS-v9.9.99.tar.gz')) as t:
            for m in t.getmembers():
                self.assertEqual((m.uid, m.gid, m.uname, m.gname), (0, 0, '', ''), m.name)
                self.assertFalse(m.mode & 0o022, m.name)

    def test_dirty_tree_refused(self):
        with open(os.path.join(self.repo, 'keep.txt'), 'w') as f:
            f.write('edited, not committed')
        r = self._build()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('working tree is not clean', r.stdout + r.stderr)
        os.remove(os.path.join(self.repo, 'keep.txt'))
        self._git('checkout', '--', 'keep.txt')
        with open(os.path.join(self.repo, 'stray.txt'), 'w') as f:
            f.write('untracked')
        r = self._build()
        self.assertNotEqual(r.returncode, 0)

    def test_signer_file_mismatch_refused(self):
        other = os.path.join(self.tmp, 'other')
        subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', other], check=True)
        with open(os.path.join(self.repo, 'deploy', 'release-signers'), 'w') as f:
            f.write('anetbbs-release ' + open(other + '.pub').read())
        self._commit('other signer')
        r = self._build()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('does not verify', r.stdout + r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.out, 'ANetBBS-v9.9.99.tar.gz.sig')))

    def test_revoked_key_refused(self):
        with open(os.path.join(self.repo, 'deploy', 'release-revoked-keys'), 'w') as f:
            f.write('# revoked\n' + self.pub)
        self._commit('revoke')
        r = self._build()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('release-revoked-keys', r.stdout + r.stderr)

    def test_same_version_different_contents_not_signed_twice(self):
        self.assertEqual(self._build().returncode, 0)
        with open(os.path.join(self.repo, 'keep.txt'), 'w') as f:
            f.write('different payload, same version')
        self._commit('two')
        r = self._build()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('already signed', r.stdout + r.stderr)

if __name__ == '__main__':
    unittest.main()
