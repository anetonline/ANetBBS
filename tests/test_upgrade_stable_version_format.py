"""Regression tests for the v1.0.0 stable-release version format (added
alongside the existing vX.Y[ab]Z.NN alpha/beta form -- see
anetbbs/web/upgrades.py's _parse_version() docstring). A stable release
must parse correctly, must always compare as newer than any alpha/beta
build of the same major.minor regardless of beta build number, and the
privileged deploy/run_upgrade.sh wrapper's arg-validation regex needs
to accept the new form without weakening the injection-defense the
original regex existed for. (The old web-side self-patcher for that
regex was removed in the H-1 fix; see NoWebSideWrapperPatchingTests.)
"""
import os
import re
import sys
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
        for bad in ('v1.0', 'v1.0c2.5', 'vv1.0.0', 'v1.0.0.1', '', None,
                   'v1.0.0; rm -rf /', '1.0.0; rm -rf /'):
            self.assertIsNone(_parse_version(bad), f'{bad!r} should not parse')

    def test_leading_v_is_optional(self):
        # VERSION files have been written both ways (v1.1.72 shipped
        # "1.1.72"); both must compare as the same release.
        from anetbbs.web.upgrades import _parse_version
        self.assertEqual(_parse_version('1.0.0'), _parse_version('v1.0.0'))
        self.assertIsNotNone(_parse_version('1.1.72'))

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


class NoWebSideWrapperPatchingTests(unittest.TestCase):
    """The old in-place self-patcher (_patch_wrapper_if_needed) is gone:
    the web process must never write a script root executes (audit
    H-1). The wrapper's regex is kept current by update.sh installing
    the new copy instead."""

    def test_patcher_is_removed(self):
        import anetbbs.web.upgrades as upgrades
        self.assertFalse(hasattr(upgrades, '_patch_wrapper_if_needed'))


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
