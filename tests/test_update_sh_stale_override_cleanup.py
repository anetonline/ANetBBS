"""Regression test for the cleanup half of the 2026-10-27 outage fix.

v1.1.56/v1.1.57 shipped a drop-in (anetbbs-web.service.d/
20-nonewprivileges-override.conf) believing an explicit
NoNewPrivileges=no there would override the implicit force from the
seccomp-backed directives in 10-security-hardening.conf. It didn't --
confirmed live, a fresh restart with the override in place still
showed NoNewPrivs: 1 in /proc (installing a seccomp filter requires
CAP_SYS_ADMIN or the kernel's no_new_privs bit, and systemd hardcodes
the latter for an unprivileged unit regardless of config). The real
fix (see test_update_sh_hardening_retrofit.py) removes the
seccomp-backed directives from anetbbs-web entirely and puts
NoNewPrivileges=no directly into 10-security-hardening.conf instead,
making the separate 20-override drop-in redundant. This test checks
update.sh actually removes that now-stale file on any install that
picked it up from the earlier, ineffective fix.
"""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_BASH = shutil.which('bash') or '/bin/bash'
_LINE = (
    'rm -f /etc/systemd/system/anetbbs-web.service.d/'
    '20-nonewprivileges-override.conf'
)


def _extract_line():
    content = (REPO_ROOT / 'update.sh').read_text()
    assert _LINE in content, (
        'the stale 20-nonewprivileges-override.conf cleanup line is gone '
        'from update.sh -- this test needs a matching update')
    return _LINE


class _Harness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.line = _extract_line()

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.fake_root = Path(self.tmpdir.name) / 'etc-systemd-system'
        self.fake_root.mkdir()

    def _run(self):
        retargeted = self.line.replace(
            '/etc/systemd/system', str(self.fake_root))
        result = subprocess.run([_BASH, '-c', retargeted],
                                capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, f'cleanup line failed: {result.stderr}'
        return result

    def _stale_path(self):
        return (self.fake_root / 'anetbbs-web.service.d'
                / '20-nonewprivileges-override.conf')


class RemovesStaleOverrideTests(_Harness):
    def test_removes_a_pre_existing_stale_override_file(self):
        dropin_dir = self.fake_root / 'anetbbs-web.service.d'
        dropin_dir.mkdir()
        self._stale_path().write_text('[Service]\nNoNewPrivileges=no\n')
        self._run()
        self.assertFalse(self._stale_path().exists())

    def test_leaves_the_sibling_hardening_dropin_alone(self):
        dropin_dir = self.fake_root / 'anetbbs-web.service.d'
        dropin_dir.mkdir()
        self._stale_path().write_text('[Service]\nNoNewPrivileges=no\n')
        sibling = dropin_dir / '10-security-hardening.conf'
        sibling.write_text('[Service]\nProtectHome=yes\n')
        self._run()
        self.assertTrue(sibling.exists())
        self.assertIn('ProtectHome=yes', sibling.read_text())

    def test_is_a_no_op_when_no_stale_file_exists(self):
        result = self._run()
        self.assertEqual(result.returncode, 0)
        self.assertFalse(self._stale_path().exists())


if __name__ == '__main__':
    unittest.main()
