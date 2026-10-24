"""Real-systemd-unit integration test for the ProtectHome= mode fix
(2026-10-24 production outage, see test_protecthome_home_install_rwpaths.py
and test_update_sh_hardening_retrofit.py for the full incident writeup).

Every other test covering this fix -- including the ones that existed
BEFORE the outage -- only checks that the right TEXT landed in a unit
or drop-in file, or reasons about the mechanism via a hand-built mount-
namespace reproduction. Neither actually starts a real systemd unit
with the directive combination and confirms it starts. That gap is
exactly what let a confirmed-broken ProtectHome=yes+ReadOnlyPaths=
combination ship as "verified" and cause a real ~5 minute production
outage before being caught. This test closes that gap for real: it
creates an actual throwaway systemd unit (removed in tearDown either
way) and confirms via `systemctl start` whether exec/write actually
succeed or fail under each directive combination -- the same thing a
live service would experience, not a simulation of it.

Requires real root (writing a unit to /etc/systemd/system/ and running
`systemctl start` needs it) -- skipped everywhere else, same category
of environment-dependent limitation as the mount-namespace reproduction
this fix was originally (and insufficiently) verified with. Run this
manually with sudo after any future change to ProtectHome=/
ReadWritePaths=/ReadOnlyPaths= handling in install.sh or update.sh,
and before ever deploying such a change to a real host again.
"""
import os
import shutil
import stat
import subprocess
import unittest
import uuid
from pathlib import Path

_IS_ROOT = os.geteuid() == 0
_HAS_SYSTEMCTL = shutil.which('systemctl') is not None


@unittest.skipUnless(
    _IS_ROOT and _HAS_SYSTEMCTL,
    'requires real root and systemctl to create/start an actual throwaway '
    'systemd unit -- this exercises real systemd behavior, which a mount-'
    'namespace reproduction was already shown (2026-10-24) to NOT reliably '
    'predict; run manually with sudo after touching ProtectHome=/'
    'ReadWritePaths=/ReadOnlyPaths= handling, before deploying anywhere')
class ProtectHomeModeRealUnitTests(unittest.TestCase):
    def setUp(self):
        self.unit_name = f'anetbbs-protecthome-test-{uuid.uuid4().hex[:8]}'
        self.unit_path = Path(f'/etc/systemd/system/{self.unit_name}.service')
        self.addCleanup(self._cleanup_unit)

        # A throwaway directory UNDER the real $HOME -- ProtectHome=
        # only affects /home, /root, /run/user, so the scratch tree has
        # to actually live under /home for this test to exercise
        # anything real.
        home = Path(os.path.expanduser('~'))
        self.scratch = home / f'.protecthome_test_scratch_{uuid.uuid4().hex[:8]}'
        self.addCleanup(lambda: shutil.rmtree(self.scratch, ignore_errors=True))
        (self.scratch / 'bin').mkdir(parents=True)
        (self.scratch / 'data').mkdir()
        self.script = self.scratch / 'bin' / 'probe.sh'
        self.script.write_text('#!/bin/sh\nexit 0\n')
        self.script.chmod(self.script.stat().st_mode | stat.S_IEXEC)

    def _cleanup_unit(self):
        subprocess.run(['systemctl', 'stop', self.unit_name],
                       capture_output=True)
        self.unit_path.unlink(missing_ok=True)
        subprocess.run(['systemctl', 'daemon-reload'], capture_output=True)
        subprocess.run(['systemctl', 'reset-failed', self.unit_name],
                       capture_output=True)

    def _write_and_start(self, exec_start, extra_directives):
        self.unit_path.write_text(
            '[Unit]\n'
            'Description=ANetBBS ProtectHome mode test (throwaway)\n\n'
            '[Service]\n'
            'Type=oneshot\n'
            f'ExecStart={exec_start}\n'
            f'{extra_directives}\n')
        subprocess.run(['systemctl', 'daemon-reload'], check=True)
        subprocess.run(['systemctl', 'reset-failed', self.unit_name],
                       capture_output=True)
        return subprocess.run(['systemctl', 'start', self.unit_name],
                              capture_output=True, text=True)

    def test_protecthome_yes_blocks_exec_even_with_readonlypaths_carveout(self):
        """Pins the CONFIRMED-BROKEN combination the original fix
        shipped with -- ProtectHome=yes + ReadOnlyPaths=<scratch dir>
        should still fail to exec the script under it. If a future
        systemd version changes this, this test will start failing
        (i.e. start SUCCEEDING unexpectedly) and should be revisited,
        not silently left as dead documentation."""
        result = self._write_and_start(
            str(self.script),
            f'ProtectHome=yes\nReadOnlyPaths={self.scratch}\n'
            f'ReadWritePaths={self.scratch}/data')
        self.assertNotEqual(result.returncode, 0,
                            msg='expected this to FAIL (documents the '
                                'confirmed-broken combination) -- if it '
                                'now succeeds, systemd behavior changed '
                                'and this whole fix should be revisited')

    def test_protecthome_readonly_allows_exec_with_narrow_readwritepaths(self):
        """The actual fix: ProtectHome=read-only + narrow
        ReadWritePaths=data -- exec of the script must succeed."""
        result = self._write_and_start(
            str(self.script),
            f'ProtectHome=read-only\nReadWritePaths={self.scratch}/data')
        self.assertEqual(result.returncode, 0,
                         msg=f'expected SUCCESS: {result.stderr}')

    def test_protecthome_readonly_allows_exec_with_broad_readwritepaths(self):
        """Matches anetbbs-web's own shape: ProtectHome=read-only +
        ReadWritePaths over the WHOLE scratch tree -- exec must
        succeed too."""
        result = self._write_and_start(
            str(self.script),
            f'ProtectHome=read-only\nReadWritePaths={self.scratch}')
        self.assertEqual(result.returncode, 0,
                         msg=f'expected SUCCESS: {result.stderr}')

    def test_protecthome_readonly_without_carveout_still_blocks_writes(self):
        """Confirms read-only mode's write restriction is real, not
        just coincidentally permissive -- a write to data/ with NO
        ReadWritePaths= carve-out at all must fail."""
        marker = self.scratch / 'data' / '.write_test'
        result = self._write_and_start(
            f'/bin/sh -c "touch {marker}"', 'ProtectHome=read-only\n')
        self.assertNotEqual(result.returncode, 0,
                            msg='expected the write to FAIL -- read-only '
                                'mode without a carve-out must still '
                                'block writes')
        self.assertFalse(marker.exists())


if __name__ == '__main__':
    unittest.main()
