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


if __name__ == '__main__':
    unittest.main()
