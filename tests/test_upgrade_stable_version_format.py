"""Regression tests for the v1.0.0 stable-release version format (added
alongside the existing vX.Y[ab]Z.NN alpha/beta form -- see
anetbbs/web/upgrades.py's _parse_version() docstring). A stable release
must parse correctly, must always compare as newer than any alpha/beta
build of the same major.minor regardless of beta build number, and the
privileged deploy/run_upgrade.sh wrapper's arg-validation regex plus its
Python-side self-healing patcher (_patch_wrapper_if_needed) both need
to accept the new form without weakening the injection-defense the
original regex existed for.
"""
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class VersionParsingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.upgrade_stable_ver_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def test_stable_version_parses(self):
        from anetbbs.web.upgrades import _parse_version
        self.assertEqual(_parse_version('v1.0.0'), (1, 0, 2, 0, 0))
        self.assertEqual(_parse_version('v1.0.1'), (1, 0, 2, 1, 0))
        self.assertEqual(_parse_version('v2.3.15'), (2, 3, 2, 15, 0))

    def test_beta_and_alpha_still_parse_unchanged(self):
        from anetbbs.web.upgrades import _parse_version
        self.assertEqual(_parse_version('v1.0b2.208'), (1, 0, 1, 2, 208))
        self.assertEqual(_parse_version('v1.0a3.5'), (1, 0, 0, 3, 5))

    def test_garbage_still_rejected(self):
        from anetbbs.web.upgrades import _parse_version
        for bad in ('v1.0', 'v1.0c2.5', '1.0.0', 'v1.0.0.1', '', None,
                   'v1.0.0; rm -rf /'):
            self.assertIsNone(_parse_version(bad), f'{bad!r} should not parse')

    def test_stable_release_always_newer_than_any_beta_of_same_minor(self):
        from anetbbs.web.upgrades import _version_newer
        self.assertTrue(_version_newer('v1.0.0', 'v1.0b2.999'),
                        'a stable 1.0.0 must outrank every 1.0 beta build, '
                        'no matter how high the beta build number is')
        self.assertFalse(_version_newer('v1.0b2.999', 'v1.0.0'))

    def test_stable_patches_compare_among_themselves(self):
        from anetbbs.web.upgrades import _version_newer
        self.assertTrue(_version_newer('v1.0.1', 'v1.0.0'))
        self.assertFalse(_version_newer('v1.0.0', 'v1.0.1'))

    def test_tarball_regex_matches_both_forms(self):
        from anetbbs.web.upgrades import _TARBALL_RE
        self.assertTrue(_TARBALL_RE.match('ANetBBS-v1.0.0.tar.gz'))
        self.assertTrue(_TARBALL_RE.match('ANetBBS-v1.0b2.208.tar.gz'))
        self.assertFalse(_TARBALL_RE.match('ANetBBS-v1.0.tar.gz'))


class PatchWrapperMigrationTests(unittest.TestCase):
    """_patch_wrapper_if_needed() self-heals an on-disk run_upgrade.sh
    that still has an older version regex -- test all 3 generations."""

    def _write_and_patch(self, script_body):
        from anetbbs.web.upgrades import _patch_wrapper_if_needed
        with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False) as f:
            f.write(script_body)
            path = f.name
        try:
            _patch_wrapper_if_needed(path)
            with open(path) as f:
                return f.read()
        finally:
            os.unlink(path)

    def test_alpha_only_wrapper_ends_up_on_final_regex(self):
        body = 'if ! [[ "$VERSION" =~ ^v[0-9]+\\.[0-9]+a[0-9]+\\.[0-9]+$ ]]; then\n'
        result = self._write_and_patch(body)
        self.assertIn(r'^v[0-9]+\.[0-9]+([ab][0-9]+\.[0-9]+|\.[0-9]+)$', result)
        self.assertNotIn(r'^v[0-9]+\.[0-9]+a[0-9]+\.[0-9]+$', result)

    def test_alpha_plus_beta_wrapper_ends_up_on_final_regex(self):
        body = 'if ! [[ "$VERSION" =~ ^v[0-9]+\\.[0-9]+[ab][0-9]+\\.[0-9]+$ ]]; then\n'
        result = self._write_and_patch(body)
        self.assertIn(r'^v[0-9]+\.[0-9]+([ab][0-9]+\.[0-9]+|\.[0-9]+)$', result)

    def test_already_current_wrapper_is_left_alone(self):
        body = ('if ! [[ "$VERSION" =~ '
               r'^v[0-9]+\.[0-9]+([ab][0-9]+\.[0-9]+|\.[0-9]+)$'
               ' ]]; then\n')
        result = self._write_and_patch(body)
        self.assertEqual(result, body,
                         'an already-current wrapper must not be rewritten')

    def test_patched_regex_actually_accepts_stable_version_in_bash(self):
        """Not just a string check -- prove the resulting regex is
        valid bash extended-regex syntax and behaves correctly, since
        this is a real, security-sensitive arg-validation gate."""
        import subprocess
        body = 'if ! [[ "$VERSION" =~ ^v[0-9]+\\.[0-9]+a[0-9]+\\.[0-9]+$ ]]; then\n'
        patched = self._write_and_patch(body)
        regex_line = patched.strip()
        for version, should_match in [
            ('v1.0.0', True), ('v1.0b2.208', True), ('v1.0a3.5', True),
            ('v1.0', False), ('v1.0.0; rm -rf /', False),
        ]:
            script = (
                f'VERSION="{version}"\n' + regex_line +
                '\n  echo NO\nelse\n  echo YES\nfi\n'
            )
            out = subprocess.run(['bash', '-c', script],
                                 capture_output=True, text=True)
            got_match = out.stdout.strip() == 'YES'
            self.assertEqual(got_match, should_match,
                             f'{version!r}: expected match={should_match}, '
                             f'bash said {out.stdout.strip()!r} '
                             f'(stderr: {out.stderr.strip()!r})')


class DeployWrapperTemplateTests(unittest.TestCase):
    """The shipped deploy/run_upgrade.sh template itself must already
    carry the final regex -- fresh installs shouldn't need the
    self-heal patch at all."""

    def test_shipped_template_accepts_stable_and_beta(self):
        repo_root = Path(__file__).resolve().parent.parent
        wrapper = repo_root / 'deploy' / 'run_upgrade.sh'
        with open(wrapper) as f:
            content = f.read()
        m = re.search(r'\$VERSION"\s*=~\s*(\S+)\s*\]\]', content)
        self.assertIsNotNone(m, "couldn't find the version regex in run_upgrade.sh")
        pattern = m.group(1)
        for version, should_match in [
            ('v1.0.0', True), ('v1.0b2.208', True), ('v1.0a3.5', True),
            ('v1.0', False),
        ]:
            script = f'VERSION="{version}"\nif [[ "$VERSION" =~ {pattern} ]]; then echo Y; else echo N; fi\n'
            out = __import__('subprocess').run(['bash', '-c', script],
                                                capture_output=True, text=True)
            got = out.stdout.strip() == 'Y'
            self.assertEqual(got, should_match, f'{version!r} -> {out.stdout.strip()!r}')


if __name__ == '__main__':
    unittest.main()
