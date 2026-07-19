"""Regression test: build-release.sh builds its file list from `git
ls-files --cached --others --exclude-standard`, so anything gitignored
never ships. .gitignore's runtime-data rule was `data/` (unanchored),
which git matches against EVERY directory named "data" anywhere in the
tree -- not just the intended top-level data/ (sysop DB, uploads,
mail spools). That silently caught anetbbs/data/ too, which holds
bundled shipped content, not runtime state.

Real-world impact: anetbbs/data/default_taglines.txt (the ~200-entry
tagline seed file) never made it into ANY built release tarball across
two versions (v1.0b2.157, v1.0b2.158) -- the seed step's `open()` always
raised FileNotFoundError on a fresh install, silently caught and logged,
so the tagline feature never actually worked on a real deployed BBS
despite passing every test in this suite (tests run against the repo
checkout, which still has the file locally -- they never exercise "what
actually got tarred up").

Fixed by anchoring the rule to /data/ and adding explicit entries for
the genuine runtime-state dirs that were incidentally relying on the
old broad pattern (anetbbs/games/sbbs_doors/data/,
vendor/games/anetsims/data/).
"""
import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _git_available():
    try:
        subprocess.run(['git', '-C', str(REPO_ROOT), 'rev-parse',
                        '--is-inside-work-tree'],
                      capture_output=True, check=True, timeout=10)
        return True
    except Exception:
        return False


@unittest.skipUnless(_git_available(), 'no git repo available in this environment')
class ReleaseTarballIncludesDataDirTests(unittest.TestCase):
    def _ls_files(self):
        result = subprocess.run(
            ['git', '-C', str(REPO_ROOT), 'ls-files',
             '--cached', '--others', '--exclude-standard'],
            capture_output=True, text=True, check=True, timeout=30)
        return set(result.stdout.splitlines())

    def test_default_taglines_seed_file_is_not_gitignored(self):
        files = self._ls_files()
        self.assertIn('anetbbs/data/default_taglines.txt', files,
                      'the tagline seed file must be picked up by '
                      "build-release.sh's git-based file list, or it "
                      'silently never ships and the feature is dead on '
                      'every real install')

    def test_anetbbs_data_directory_is_not_blanket_ignored(self):
        """Guards the general class of bug, not just this one file --
        anything future added under anetbbs/data/ should ship by
        default unless specifically excluded."""
        result = subprocess.run(
            ['git', '-C', str(REPO_ROOT), 'check-ignore',
             'anetbbs/data/default_taglines.txt'],
            capture_output=True, text=True, timeout=10)
        # check-ignore exits 0 (and prints the match) if the path IS
        # ignored -- we want it to exit 1 (not ignored).
        self.assertEqual(result.returncode, 1,
                         f'anetbbs/data/ must not be gitignored: {result.stdout}')

    def test_genuine_runtime_data_dirs_still_excluded(self):
        """The fix must not accidentally un-ignore actual per-install
        runtime state (game saves, score files) while fixing the
        bundled-content case."""
        for path in ('data/some_runtime_file.db',
                     'vendor/games/anetsims/data/scores.ans',
                     'anetbbs/games/sbbs_doors/data/user/1.tw2'):
            result = subprocess.run(
                ['git', '-C', str(REPO_ROOT), 'check-ignore', path],
                capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0,
                             f'{path} must still be gitignored (runtime state)')


if __name__ == '__main__':
    unittest.main()
