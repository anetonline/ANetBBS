"""Regression test for a THIRD real live outage (2026-10-28, found
while verifying the second fix): deploy/run_upgrade.sh's detach block
used to hardcode `--property=StandardOutput=append:/tmp/anetbbs_upgrade.log`.
anetbbs-web.service has PrivateTmp=yes, so the file the WEB PROCESS
creates/truncates lives in that service's own private /tmp mount
namespace -- invisible to systemd/PID1, which resolves the
StandardOutput target against the REAL, system-wide /tmp when setting
up the detached unit. Confirmed live: `systemd[...]: Failed to set up
standard output: Permission denied`, exit 209/STDOUT.

Fixed by moving _UPGRADE_LOG_PATH off /tmp entirely, onto
INSTALL_DIR/data/anetbbs_upgrade.log -- a real, non-virtualized
directory every process agrees on regardless of any unit's PrivateTmp
setting (see deploy/run_upgrade.sh's own detach-block comment and
tests/test_run_upgrade_sh_self_detach.py's
test_standard_output_lands_in_data_dir_not_tmp for the bash-side half
of this fix).

This file covers the PYTHON side: _UPGRADE_LOG_PATH itself, and
_reset_upgrade_log()'s defensive handling of a file a PAST detached
root unit may have left behind with restrictive ownership (data/ is
owned by the service user, so remove+recreate always succeeds even
when the existing inode doesn't belong to us).
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.core  # noqa: F401 -- circular-import workaround
from anetbbs.web import upgrades


class UpgradeLogPathTests(unittest.TestCase):
    def test_log_path_is_under_data_not_tmp(self):
        self.assertNotIn('/tmp/', upgrades._UPGRADE_LOG_PATH)
        self.assertIn(os.path.join('data', 'anetbbs_upgrade.log'),
                      upgrades._UPGRADE_LOG_PATH)

    def test_log_path_is_two_parents_up_from_this_module_plus_data(self):
        expected_root = Path(upgrades.__file__).resolve().parent.parent.parent
        expected = str(expected_root / 'data' / 'anetbbs_upgrade.log')
        self.assertEqual(upgrades._UPGRADE_LOG_PATH, expected)


class ResetUpgradeLogTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / 'anetbbs_upgrade.log'
        self._patcher = patch.object(upgrades, '_UPGRADE_LOG_PATH', str(self.path))
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def test_creates_fresh_file_when_none_exists(self):
        self.assertFalse(self.path.exists())
        upgrades._reset_upgrade_log()
        self.assertTrue(self.path.exists())
        self.assertEqual(self.path.read_text(), '')

    def test_truncates_an_existing_writable_file(self):
        self.path.write_text('leftover progress text from a prior run\n')
        upgrades._reset_upgrade_log()
        self.assertEqual(self.path.read_text(), '')

    def test_sets_permissive_mode_so_a_root_owned_process_can_append_too(self):
        upgrades._reset_upgrade_log()
        mode = self.path.stat().st_mode & 0o777
        self.assertEqual(mode, 0o666, oct(mode))

    def test_falls_back_to_remove_and_recreate_on_permission_error(self):
        # Simulates the real scenario: a PAST detached root unit left
        # this file behind in a state the web process (non-root) can't
        # directly open-for-write, but CAN remove (data/ itself is
        # owned by the service user).
        self.path.write_text('left behind by a detached root unit\n')
        real_open = open
        call_count = {'n': 0}

        def flaky_open(path, mode='r', *a, **kw):
            if path == str(self.path) and mode == 'w':
                call_count['n'] += 1
                if call_count['n'] == 1:
                    raise PermissionError('simulated: file not writable by us')
            return real_open(path, mode, *a, **kw)

        with patch('builtins.open', side_effect=flaky_open):
            upgrades._reset_upgrade_log()

        self.assertTrue(self.path.exists())
        self.assertEqual(self.path.read_text(), '')

    def test_does_not_raise_when_the_directory_itself_is_unwritable(self):
        # Total failure (e.g. data/ itself missing) must not crash the
        # install()/rollback() route that calls this -- matches the
        # original bare try/except OSError: pass behavior.
        missing_dir_path = str(Path(self._tmp.name) / 'nonexistent' / 'anetbbs_upgrade.log')
        with patch.object(upgrades, '_UPGRADE_LOG_PATH', missing_dir_path):
            upgrades._reset_upgrade_log()  # must not raise


if __name__ == '__main__':
    unittest.main()
