"""Regression test for a real live production outage (2026-10-27).

Jerry's own live `/admin/upgrades/` self-upgrade button (and,
separately, the Service Control Center's restart/backup buttons --
anetbbs/web/control.py and anetbbs/web/backups_admin.py shell out to
`sudo` the exact same way) started failing with:

    sudo: The "no new privileges" flag is set, which prevents sudo
    from running as root.

Root cause, confirmed directly against his live box's own
`systemctl cat anetbbs-web.service` output: neither the main unit
file nor its `10-security-hardening.conf` drop-in (written by
update.sh's `_retrofit_hardening anetbbs-web` call, which deliberately
OMITS a literal `NoNewPrivileges=` line specifically so sudo keeps
working) contained the string `NoNewPrivileges` anywhere -- yet
`/proc/<MainPID>/status` showed `NoNewPrivs: 1` for the real running
process. Per systemd.exec(5), `NoNewPrivileges=yes` is IMPLIED by
several of the OTHER hardening directives that drop-in sets
(`SystemCallFilter=`, `RestrictNamespaces=`, `ProtectKernelTunables=`,
`ProtectKernelModules=`, `ProtectKernelLogs=`, `MemoryDenyWriteExecute=`,
`RestrictRealtime=`, `LockPersonality=`) unless `NoNewPrivileges=no`
is explicitly specified to override that -- the unprivileged service
user has no CAP_SYS_ADMIN, so the kernel only allows systemd to install
the `SystemCallFilter=` seccomp filter by also setting the kernel's own
`no_new_privs` flag. Simply OMITTING the line, which every version of
every anetbbs-web.service generator in this repo did before this fix,
never actually prevented it.

Fixed with a SECOND, dedicated drop-in
(`anetbbs-web.service.d/20-nonewprivileges-override.conf`) containing
an explicit `NoNewPrivileges=no`, written unconditionally on every
update.sh run so it self-heals any existing install that already
picked up the implying directives before this fix existed -- not
folded into `_retrofit_hardening anetbbs-web`'s own args, since that
function's gate (has this unit ever received a hardening drop-in at
all) would never revisit an install already hardened before this
specific directive was known to be needed, which is exactly the
install this bug was found on.

This test extracts the REAL block out of update.sh and runs it in
actual bash against a throwaway fake "/etc/systemd/system" directory,
matching the existing pattern in test_update_sh_hardening_retrofit.py.
"""
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_BASH = shutil.which('bash') or '/bin/bash'

_BLOCK_RE = re.compile(
    r'^if \[\[ -f /etc/systemd/system/anetbbs-web\.service \]\]; then\n'
    r'.*?\nfi\n',
    re.DOTALL | re.MULTILINE,
)


def _extract_block():
    content = (REPO_ROOT / 'update.sh').read_text()
    # Anchor on the real comment so this test fails loudly (not
    # silently matching some unrelated `if [[ -f ... anetbbs-web... ]]`
    # block) if update.sh's shape ever changes.
    anchor = "Dedicated, unconditional fix for a real live production outage"
    assert anchor in content, (
        'the NoNewPrivileges override comment is gone from update.sh -- '
        'this test needs a matching update')
    tail = content[content.index(anchor):]
    m = _BLOCK_RE.search(tail)
    assert m, 'could not find the NoNewPrivileges override block in update.sh'
    block = m.group(0)
    occurrences = block.count('/etc/systemd/system')
    assert occurrences == 3, (
        f'expected exactly three literal /etc/systemd/system references to '
        f'retarget for testing, found {occurrences} -- update.sh changed '
        f'shape, this test needs a matching update')
    return block


class _Harness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.block = _extract_block()

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.fake_root = Path(self.tmpdir.name) / 'etc-systemd-system'
        self.fake_root.mkdir()

    def _run(self):
        retargeted = self.block.replace(
            '/etc/systemd/system', str(self.fake_root))
        result = subprocess.run([_BASH, '-c', retargeted],
                                capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, (
            f'override block failed: {result.stderr}')
        return result

    def _dropin_path(self):
        return (self.fake_root / 'anetbbs-web.service.d'
                / '20-nonewprivileges-override.conf')


class WritesOverrideWhenUnitExistsTests(_Harness):
    def test_writes_an_explicit_no_override_dropin(self):
        (self.fake_root / 'anetbbs-web.service').write_text('[Unit]\n')
        self._run()
        dropin = self._dropin_path()
        self.assertTrue(dropin.exists())
        text = dropin.read_text()
        self.assertIn('[Service]', text)
        self.assertIn('NoNewPrivileges=no', text)

    def test_is_idempotent_across_repeated_runs(self):
        (self.fake_root / 'anetbbs-web.service').write_text('[Unit]\n')
        self._run()
        first = self._dropin_path().read_text()
        self._run()
        second = self._dropin_path().read_text()
        self.assertEqual(first, second)

    def test_self_heals_a_unit_that_already_has_an_unrelated_dropin(self):
        """The exact shape of the real bug: a pre-existing
        10-security-hardening.conf drop-in with the implying
        directives but no NoNewPrivileges= line at all -- this fix
        must land its own, separate override regardless."""
        (self.fake_root / 'anetbbs-web.service').write_text('[Unit]\n')
        existing_dropin_dir = self.fake_root / 'anetbbs-web.service.d'
        existing_dropin_dir.mkdir()
        (existing_dropin_dir / '10-security-hardening.conf').write_text(
            '[Service]\nSystemCallFilter=@system-service\n'
            'RestrictNamespaces=yes\n')
        self._run()
        self.assertIn('NoNewPrivileges=no', self._dropin_path().read_text())
        # The pre-existing drop-in is left alone, not overwritten.
        self.assertIn(
            'SystemCallFilter=@system-service',
            (existing_dropin_dir / '10-security-hardening.conf').read_text())


class MissingUnitIsSkippedTests(_Harness):
    def test_nonexistent_unit_produces_no_error_and_no_dropin(self):
        result = self._run()
        self.assertEqual(result.returncode, 0)
        self.assertFalse(self._dropin_path().parent.exists())


if __name__ == '__main__':
    unittest.main()
