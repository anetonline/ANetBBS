"""Guard: the in-image CI test run must have every repo file the tests read.

Real CI failure (v1.1.76 push): .dockerignore keeps deploy/sudoers.anetbbs
out of the runtime image, but tests/test_audit_h1_privileged_helpers.py
reads it, so "Run test suite inside the built image" failed with
FileNotFoundError on GitHub while every local run (full checkout) passed.

The fix mounts the checkout's copy read-only into the test container. This
test keeps that in step: any existing repo file that a test references by
a literal path and that .dockerignore excludes must be mounted by the
workflow's test step at the same path. Otherwise a new test that reads an
excluded file would pass locally and fail only on GitHub again.

This is a CI-config check, so it needs the checkout's .github/ and
.dockerignore. Inside the image (.github/ excluded) it is skipped; it runs
in local and pre-release suites.
"""
import os
import re
import shutil
import subprocess
import unittest

try:
    import yaml
except ImportError:     # not in the runtime image; this check runs from the checkout
    yaml = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW = os.path.join(ROOT, '.github', 'workflows', 'docker-build.yml')
DOCKERIGNORE = os.path.join(ROOT, '.dockerignore')
TEST_STEP = 'Run test suite inside the built image'

# 'deploy/sudoers.anetbbs'  and  ROOT / 'deploy' / 'sudoers.anetbbs'
# and os.path.join(ROOT, 'deploy', 'sudoers.anetbbs')
_LITERAL = re.compile(r"""['"]([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+)['"]""")
_JOINED = re.compile(r"""['"]([A-Za-z0-9_.-]+)['"](?:\s*(?:/|,)\s*['"]([A-Za-z0-9_.-]+)['"])+""")


def _ignore_patterns():
    pats = []
    with open(DOCKERIGNORE) as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith('#'):
                pats.append(line)
    return pats


def _match(pattern, rel):
    """Docker .dockerignore semantics: patterns are anchored at the context
    root (no implicit "match at any depth"), `**` spans directories, and a
    pattern that matches a directory excludes everything under it."""
    pattern = pattern.strip('/')
    rx = ''
    i = 0
    while i < len(pattern):
        if pattern.startswith('**/', i):
            rx += '(?:.*/)?'          # zero or more leading directories
            i += 3
        elif pattern.startswith('**', i):
            rx += '.*'
            i += 2
        elif pattern[i] == '*':
            rx += '[^/]*'
            i += 1
        elif pattern[i] == '?':
            rx += '[^/]'
            i += 1
        else:
            rx += re.escape(pattern[i])
            i += 1
    parts = rel.split('/')
    return any(re.fullmatch(rx, '/'.join(parts[:n])) for n in range(1, len(parts) + 1))


def _ignored(rel, pats):
    excluded = False
    for p in pats:  # later patterns win; "!" re-includes
        neg = p.startswith('!')
        if _match(p[1:] if neg else p, rel):
            excluded = not neg
    return excluded


def _tracked_files():
    out = subprocess.run(['git', '-C', ROOT, 'ls-files', '-z'], capture_output=True, check=True)
    return set(f for f in out.stdout.decode('utf-8', 'replace').split('\0') if f)


# Files that are deliberately absent from the image, read only by tests that
# cover the bare-metal bundle and skip cleanly when the file is missing.
INTENTIONALLY_ABSENT = {
    'vendor/games/anetdraw/anetdraw-x64': 'bare-metal bundled door binary; '
        'test_anetdraw_bundled_door_seed skipUnless it is present',
    'vendor/games/anetdraw/anetdraw-arm64': 'bare-metal bundled door binary; '
        'test_anetdraw_bundled_door_seed skipUnless it is present',
}


def _referenced_paths():
    tracked = _tracked_files()
    refs = {}
    tdir = os.path.join(ROOT, 'tests')
    for name in sorted(os.listdir(tdir)):
        if not name.endswith('.py'):
            continue
        text = open(os.path.join(tdir, name), encoding='utf-8', errors='replace').read()
        cands = set(_LITERAL.findall(text))
        for m in _JOINED.finditer(text):
            cands.add('/'.join(re.findall(r"""['"]([A-Za-z0-9_.-]+)['"]""", m.group(0))))
        for c in cands:
            c = c.lstrip('./')
            if c in tracked:
                refs.setdefault(c, set()).add(name)
    return refs


@unittest.skipUnless(os.path.isfile(WORKFLOW) and os.path.isfile(DOCKERIGNORE)
                     and os.path.exists(os.path.join(ROOT, '.git')),
                     'needs the git checkout, .github/ and .dockerignore (not present inside the image)')
@unittest.skipUnless(shutil.which('git'), 'git not available')
@unittest.skipIf(yaml is None, 'PyYAML not installed')
class DockerCiTestInputs(unittest.TestCase):
    def _test_step_run(self):
        wf = yaml.safe_load(open(WORKFLOW))
        steps = [s for s in wf['jobs']['build-and-test']['steps'] if s.get('name') == TEST_STEP]
        self.assertEqual(len(steps), 1, 'test step not found exactly once')
        return steps[0]['run']

    def test_every_dockerignored_file_read_by_tests_is_mounted(self):
        run = self._test_step_run()
        pats = _ignore_patterns()
        missing = []
        for rel, users in sorted(_referenced_paths().items()):
            if _ignored(rel, pats) and rel not in INTENTIONALLY_ABSENT:
                mount = f'"$PWD/{rel}:/app/{rel}:ro"'
                if mount not in run:
                    missing.append(f'{rel} (used by {", ".join(sorted(users))})')
        self.assertEqual(missing, [], 'dockerignored files read by tests but not mounted '
                         'into the in-image test run: ' + '; '.join(missing))

    def test_matcher_follows_docker_semantics(self):
        pats = _ignore_patterns()
        self.assertTrue(_ignored('deploy/sudoers.anetbbs', pats))
        self.assertTrue(_ignored('vendor/games/anetdraw/anetdraw-x64', pats))
        self.assertTrue(_ignored('.github/workflows/docker-build.yml', pats))
        # anchored at the context root: nested dirs of the same name stay in the image
        self.assertFalse(_ignored('anetbbs/data/default_taglines.txt', pats))
        self.assertFalse(_ignored('mrc/mystic_client/vendor/mrc_client.py', pats))
        self.assertFalse(_ignored('tests/test_docker_ci_test_inputs.py', pats))

    def test_sudoers_template_is_mounted_read_only(self):
        # The concrete regression: must be supplied read-only, never skipped.
        self.assertIn('"$PWD/deploy/sudoers.anetbbs:/app/deploy/sudoers.anetbbs:ro"',
                      self._test_step_run())
        self.assertTrue(_ignored('deploy/sudoers.anetbbs', _ignore_patterns()),
                        'if the template is no longer dockerignored, drop the mount')

    def test_run_keeps_all_batches_and_stop_on_failure(self):
        run = self._test_step_run()
        self.assertIn('split -n l/15', run)
        self.assertIn('-x -q', run)
        self.assertIn('set -e', run)


if __name__ == '__main__':
    unittest.main()
