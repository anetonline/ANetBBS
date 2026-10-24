"""Regression test for a real finding during v1.1.54 Pi staging
(2026-10-24): every systemd unit-creation block in update.sh is gated
on "if the unit file doesn't exist yet" -- so an install that has only
ever run update.sh (never a fresh install.sh) since the sandboxing
directives (ProtectHome=, NoNewPrivileges=, ProtectSystem=, etc.) were
added across two earlier security audits never actually received them.
Confirmed directly on a real Raspberry Pi staging install: all 5 live
units had ZERO of these directives, despite update.sh's own fresh-
install heredocs specifying a long list of them.

Fixed with a systemd drop-in override (<unit>.service.d/
10-security-hardening.conf) rather than rewriting the main unit file --
this only ever ADDS the hardening directives on top of whatever's
already there, never touching/risking any other line (ExecStart, a
sysop's own edit) in the main file. Gated on the main unit file still
missing `ProtectHome=` so a unit this same update.sh run just freshly
installed (which already has everything inline) doesn't get a
redundant drop-in.

A same-day follow-up (still 2026-10-24) found the ProtectHome=yes
directive itself -- not just this retrofit mechanism -- didn't work
the way it was assumed to for a /home-rooted install: ProtectHome=yes
+ ReadOnlyPaths=<whole tree> (carving a readable hole back through the
masked /home) was verified only via a manual mount-namespace
reproduction, which didn't match real systemd's behavior. A real
throwaway systemd unit on production (systemd 249, Ubuntu
249.11-0ubuntu3.23) confirmed ProtectHome=yes unconditionally blocks
exec under the carved-out path regardless of the carve-out -- causing
a real ~5 minute production outage. The fix, also empirically verified
against real systemd units rather than reasoned about: use
ProtectHome=read-only instead of ProtectHome=yes for a home-rooted
install (via the $PROTECTHOME_MODE variable this retrofit, and
install.sh/update.sh's own fresh-install heredocs, now share) -- read-
only mode never masks the tree in the first place, so no separate
ReadOnlyPaths= carve-out is needed at all.

These tests extract the REAL `_retrofit_hardening()` function and its
3 real call sites out of update.sh and run them in actual bash against
a throwaway fake "/etc/systemd/system" directory -- not a
reimplementation, and never against a real system path. The one
substitution made for testing (the literal `/etc/systemd/system` path)
is counted and asserted before use, so a future edit to the real path
reference would fail this test loudly rather than silently testing
nothing. Note this file's own limitation, the same one that let the
production outage happen: it only checks the TEXT written to a
drop-in, never whether a real systemd unit with that text actually
starts -- see tests/test_protecthome_systemd_unit_integration.py for
the real-unit-level check added after the outage to close that gap.
"""
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_BASH = shutil.which('bash') or '/bin/bash'

_BLOCK_RE = re.compile(
    r'^_retrofit_hardening\(\) \{.*?^unset _strict_svc$',
    re.DOTALL | re.MULTILINE,
)


def _extract_retrofit_block():
    content = (REPO_ROOT / 'update.sh').read_text()
    m = _BLOCK_RE.search(content)
    assert m, 'could not find _retrofit_hardening() block in update.sh'
    block = m.group(0)
    occurrences = block.count('/etc/systemd/system')
    assert occurrences == 1, (
        f'expected exactly one literal /etc/systemd/system reference to '
        f'retarget for testing, found {occurrences} -- update.sh changed '
        f'shape, this test needs a matching update')
    return block


class _Harness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.block = _extract_retrofit_block()

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.fake_root = Path(self.tmpdir.name) / 'etc-systemd-system'
        self.fake_root.mkdir()

    def _write_unit(self, svc, body='[Unit]\nDescription=fake\n'):
        (self.fake_root / f'{svc}.service').write_text(body)

    def _run(self, install_dir='/opt/anetbbs', rwpaths=None,
            protecthome_mode='yes'):
        if rwpaths is None:
            rwpaths = f'{install_dir}/data {install_dir}/logs'
        retargeted = self.block.replace(
            '/etc/systemd/system', str(self.fake_root))
        script = (
            'ok() { echo "OK: $*"; }\n'
            f'INSTALL_DIR={install_dir!r}\n'
            f'RWPATHS={rwpaths!r}\n'
            f'PROTECTHOME_MODE={protecthome_mode!r}\n'
            f'{retargeted}\n'
        )
        result = subprocess.run([_BASH, '-c', script],
                                capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, (
            f'retrofit block failed: {result.stderr}')
        return result

    def _dropin_path(self, svc):
        return self.fake_root / f'{svc}.service.d' / '10-security-hardening.conf'


class ExistingUnitGetsDropinTests(_Harness):
    def test_web_unit_without_hardening_gets_a_dropin(self):
        self._write_unit('anetbbs-web')
        self._run()
        dropin = self._dropin_path('anetbbs-web')
        self.assertTrue(dropin.exists())
        text = dropin.read_text()
        self.assertIn('ProtectHome=yes', text)
        self.assertIn('[Service]', text)

    def test_web_unit_omits_nonewprivileges_for_sudo_escalation(self):
        self._write_unit('anetbbs-web')
        self._run()
        text = self._dropin_path('anetbbs-web').read_text()
        self.assertNotIn('NoNewPrivileges', text)

    def test_web_unit_gets_protectsystem_full_and_broad_rwpaths(self):
        self._write_unit('anetbbs-web')
        self._run(install_dir='/home/stingray/anetbbs',
                  rwpaths='/home/stingray/anetbbs')
        text = self._dropin_path('anetbbs-web').read_text()
        self.assertIn('ProtectSystem=full', text)
        self.assertIn('ReadWritePaths=/home/stingray/anetbbs', text)

    def test_terminal_unit_gets_nonewprivileges_no_protectsystem(self):
        self._write_unit('anetbbs')
        self._run()
        text = self._dropin_path('anetbbs').read_text()
        self.assertIn('NoNewPrivileges=yes', text)
        self.assertNotIn('ProtectSystem', text)
        self.assertNotIn('MemoryDenyWriteExecute', text)
        self.assertNotIn('SystemCallFilter', text)

    def test_strict_units_get_full_strict_baseline(self):
        for svc in ('anetbbs-mrc-bridge', 'anetbbs-binkp', 'anetbbs-finger'):
            with self.subTest(svc=svc):
                self._write_unit(svc)
                self._run()
                text = self._dropin_path(svc).read_text()
                self.assertIn('ProtectSystem=strict', text)
                self.assertIn('MemoryDenyWriteExecute=yes', text)
                self.assertIn('SystemCallFilter=@system-service', text)


class HomeRootedInstallProtectHomeModeTests(_Harness):
    """ProtectHome=read-only for a home-rooted install, not the
    ProtectHome=yes+ReadOnlyPaths= carve-out the original fix tried
    first -- that combination was confirmed, via a real throwaway
    systemd unit on production (systemd 249), to unconditionally block
    exec regardless of the carve-out, causing a real ~5 minute
    production outage during v1.1.54 staging. ProtectHome=read-only
    needs no separate ReadOnlyPaths= carve-out at all -- the whole tree
    stays readable+executable by default in that mode."""

    def test_non_web_units_get_readonly_mode_when_home_rooted(self):
        self._write_unit('anetbbs')
        self._run(install_dir='/home/stingray/anetbbs',
                  protecthome_mode='read-only')
        text = self._dropin_path('anetbbs').read_text()
        self.assertIn('ProtectHome=read-only', text)
        self.assertNotIn('ReadOnlyPaths=', text)
        self.assertIn('ReadWritePaths=/home/stingray/anetbbs/data '
                      '/home/stingray/anetbbs/logs', text)

    def test_default_install_uses_yes_mode_and_no_readonlypaths_line(self):
        self._write_unit('anetbbs')
        self._run(install_dir='/opt/anetbbs', protecthome_mode='yes')
        text = self._dropin_path('anetbbs').read_text()
        self.assertIn('ProtectHome=yes', text)
        self.assertNotIn('ReadOnlyPaths=', text)

    def test_web_unit_never_gets_readonlypaths_even_when_home_rooted(self):
        self._write_unit('anetbbs-web')
        self._run(install_dir='/home/stingray/anetbbs',
                  rwpaths='/home/stingray/anetbbs',
                  protecthome_mode='read-only')
        text = self._dropin_path('anetbbs-web').read_text()
        self.assertIn('ProtectHome=read-only', text)
        self.assertNotIn('ReadOnlyPaths=', text)


class AlreadyHardenedUnitIsLeftAloneTests(_Harness):
    def test_unit_with_protecthome_already_present_gets_no_dropin(self):
        self._write_unit('anetbbs-web', body=(
            '[Unit]\nDescription=fake\n[Service]\nProtectHome=yes\n'))
        self._run()
        self.assertFalse(self._dropin_path('anetbbs-web').parent.exists(),
                         msg='a unit that already has ProtectHome= must not '
                             'get a redundant drop-in directory at all')


class MissingUnitFileIsSkippedTests(_Harness):
    def test_nonexistent_unit_produces_no_error_and_no_dropin(self):
        # Deliberately never call self._write_unit() for this one --
        # mirrors an install missing e.g. anetbbs-finger.service.
        result = self._run()
        self.assertEqual(result.returncode, 0)
        self.assertFalse(self._dropin_path('anetbbs-finger').parent.exists())


if __name__ == '__main__':
    unittest.main()
