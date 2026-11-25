"""update.sh git-clone path survives an upstream history rewrite.

Removing a file from old commits rewrites every later commit ID, so
`git pull --ff-only` refuses even on a clean, unmodified checkout. Before
this change update.sh only warned "files may already be up to date" and
git installs silently stopped updating. anetbbs_git_update now moves to the
new history only when the checkout is clean and its content is identical to
a commit on the new upstream branch; anything else is refused and left alone.
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HELPERS = REPO / 'deploy' / 'privileged_helpers.sh'

ENV = dict(os.environ, GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@example.test',
           GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@example.test',
           GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)


def git(cwd, *args):
    return subprocess.run(['git', '-C', str(cwd), *args], check=True, env=ENV,
                          capture_output=True, text=True).stdout.strip()


@unittest.skipUnless(shutil.which('git'), 'git not installed')
class GitUpdateHistoryRewrite(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='gitrw-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.work = self.tmp / 'work'
        self.origin = self.tmp / 'origin.git'
        git(self.tmp, 'init', '-q', '-b', 'main', str(self.work))
        (self.work / 'a.txt').write_text('a\n')
        (self.work / 'old.log').write_text('secret log\n')
        git(self.work, 'add', '.')
        git(self.work, 'commit', '-q', '-m', 'c1 with log')
        git(self.work, 'rm', '-q', 'old.log')
        (self.work / 'b.txt').write_text('b\n')
        git(self.work, 'add', '.')
        git(self.work, 'commit', '-q', '-m', 'c2 removes log')
        git(self.tmp, 'clone', '-q', '--bare', str(self.work), str(self.origin))
        git(self.work, 'remote', 'add', 'origin', str(self.origin))
        self.inst = self.tmp / 'inst'
        git(self.tmp, 'clone', '-q', str(self.origin), str(self.inst))
        (self.inst / '.env').write_text('KEEP=1\n')          # untracked runtime file

    def _rewrite_upstream_and_add(self, name):
        """Same content as c1/c2 without old.log, plus one new commit."""
        w = self.work
        git(w, 'checkout', '-q', '--orphan', 'rw')
        git(w, 'rm', '-q', '-rf', '--cached', '.')
        for f in ('old.log', 'b.txt'):
            (w / f).unlink(missing_ok=True)
        git(w, 'add', 'a.txt')
        git(w, 'commit', '-q', '-m', 'c1 rewritten')
        (w / 'b.txt').write_text('b\n')
        git(w, 'add', 'b.txt')
        git(w, 'commit', '-q', '-m', 'c2 rewritten')
        (w / name).write_text('new\n')
        git(w, 'add', name)
        git(w, 'commit', '-q', '-m', 'c3 new work')
        git(w, 'push', '-q', '--force', 'origin', 'rw:main')

    def _update(self):
        script = (f'ok(){{ echo "OK $*"; }}; warn(){{ echo "WARN $*"; }}; info(){{ echo "INFO $*"; }}\n'
                  f'. "{HELPERS}"\nanetbbs_git_update "{self.inst}" env\n')
        return subprocess.run(['bash', '-c', script], capture_output=True, text=True,
                              env=ENV, timeout=60)

    def test_normal_fast_forward(self):
        (self.work / 'c.txt').write_text('c\n')
        git(self.work, 'add', 'c.txt')
        git(self.work, 'commit', '-q', '-m', 'c3')
        git(self.work, 'push', '-q', 'origin', 'main')
        r = self._update()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((self.inst / 'c.txt').exists())

    def test_rewritten_history_is_followed(self):
        self._rewrite_upstream_and_add('c.txt')
        # The old update.sh path: a plain ff-only pull is refused here.
        old = subprocess.run(['git', '-C', str(self.inst), 'pull', '--ff-only', '-q'],
                             env=ENV, capture_output=True)
        self.assertNotEqual(old.returncode, 0)
        self.assertFalse((self.inst / 'c.txt').exists())
        r = self._update()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('rewritten upstream history', r.stdout)
        self.assertTrue((self.inst / 'c.txt').exists())
        self.assertEqual(git(self.inst, 'rev-parse', 'HEAD'),
                         git(self.origin, 'rev-parse', 'main'))
        self.assertEqual((self.inst / '.env').read_text(), 'KEEP=1\n')
        self.assertNotIn('c1 with log', git(self.inst, 'log', '--format=%s'))

    def test_local_edits_block_the_move(self):
        (self.inst / 'a.txt').write_text('sysop edit\n')
        self._rewrite_upstream_and_add('c.txt')
        r = self._update()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('local changes', r.stdout)
        self.assertEqual((self.inst / 'a.txt').read_text(), 'sysop edit\n')
        self.assertFalse((self.inst / 'c.txt').exists())

    def test_local_commits_block_the_move(self):
        (self.inst / 'mine.txt').write_text('x\n')
        git(self.inst, 'add', 'mine.txt')
        subprocess.run(['git', '-C', str(self.inst), 'commit', '-q', '-m', 'local'],
                       check=True, env=ENV)
        before = git(self.inst, 'rev-parse', 'HEAD')
        self._rewrite_upstream_and_add('c.txt')
        r = self._update()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('local commits', r.stdout)
        self.assertEqual(git(self.inst, 'rev-parse', 'HEAD'), before)


if __name__ == '__main__':
    unittest.main()
