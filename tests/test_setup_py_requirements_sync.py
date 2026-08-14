"""Regression test for setup.py's _read_requirements().

Real gap found in a security/performance audit: install.sh, update.sh,
and docker/Dockerfile all install this package via `pip install -e .`,
which uses setup.py's install_requires -- never `pip install -r
requirements.txt`. install_requires used to be a separately hand-
maintained copy of requirements.txt's floors, and had drifted out of
sync with every CVE floor bump requirements.txt received in a prior
audit round (cryptography had no floor at all; aiohttp/eventlet/
asyncssh/Pillow/requests were all still on old, pre-CVE-fix floors) --
meaning those CVE fixes never actually reached a real install,
including this project's own live production server. setup.py now
reads requirements.txt directly at setup() time so the two files
structurally cannot drift apart again; this test guards that.

Loading setup.py needs `from setuptools import setup, find_packages`
to succeed, but real setuptools isn't guaranteed importable in every
environment this test runs in -- confirmed empirically that a fresh
`python -m venv` + `pip install -e .` (the exact sequence
docker/Dockerfile and install.sh/update.sh use) does NOT leave
setuptools importable afterward on Python 3.12 (modern venv/ensurepip
only bootstraps pip, not setuptools/wheel), so the Docker CI image
this same test would run inside doesn't have it either. Injecting a
minimal fake `setuptools` module into sys.modules avoids depending on
the real package -- this test is about our own _read_requirements()
logic, not about setuptools itself.
"""
import importlib.util
import sys
import types
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _load_setup_module(capture=None):
    """Load setup.py with a fake setuptools module injected into
    sys.modules so `from setuptools import setup, find_packages`
    succeeds regardless of whether the real package is installed.
    If `capture` (a list) is given, the fake setup()'s kwargs are
    appended to it."""
    fake_setuptools = types.ModuleType('setuptools')
    fake_setuptools.find_packages = lambda *a, **k: []
    def _fake_setup(*args, **kwargs):
        if capture is not None:
            capture.append(kwargs)
    fake_setuptools.setup = _fake_setup

    orig = sys.modules.get('setuptools')
    sys.modules['setuptools'] = fake_setuptools
    try:
        spec = importlib.util.spec_from_file_location(
            'setup_under_test', str(_ROOT / 'setup.py'))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        if orig is not None:
            sys.modules['setuptools'] = orig
        else:
            sys.modules.pop('setuptools', None)
    return mod


class SetupRequirementsSyncTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_setup_module()
        self.reqs = self.mod._read_requirements()

    def test_returns_a_nonempty_list_of_requirement_strings(self):
        self.assertIsInstance(self.reqs, list)
        self.assertGreater(len(self.reqs), 20)
        for r in self.reqs:
            self.assertIsInstance(r, str)
            self.assertTrue(r.strip())

    def test_comments_and_blank_lines_are_excluded(self):
        for r in self.reqs:
            self.assertFalse(r.startswith('#'))

    def test_every_requirements_txt_line_is_represented(self):
        """The real regression guard: install_requires must contain
        EXACTLY what requirements.txt specifies, not a stale copy."""
        expected = [
            line.strip()
            for line in (_ROOT / 'requirements.txt').read_text().splitlines()
            if line.strip() and not line.strip().startswith('#')
        ]
        self.assertEqual(self.reqs, expected)

    def test_known_cve_floors_are_present(self):
        """Spot-check the specific packages round 1's audit found
        drifted -- cryptography had NO floor at all in the old
        hand-maintained list; the others were stuck on old floors."""
        joined = {r.split('>=')[0].split('==')[0].strip().lower(): r
                 for r in self.reqs}
        self.assertIn('cryptography', joined)
        self.assertIn('aiohttp', joined)
        self.assertIn('eventlet', joined)
        self.assertIn('asyncssh', joined)
        self.assertIn('pillow', joined)
        self.assertIn('requests', joined)

    def test_setup_is_actually_called_with_this_requirements_list(self):
        """Guards against the exact regression this audit found:
        confirms install_requires= is wired to _read_requirements()'s
        output, not a stale inline list that happens to still exist
        alongside it."""
        captured = []
        _load_setup_module(capture=captured)
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0].get('install_requires'), self.reqs)


if __name__ == '__main__':
    unittest.main()
