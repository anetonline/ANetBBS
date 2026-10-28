"""Regression test for a real live finding (2026-10-28, reported live
by the sysop right after a clean `sudo bash update.sh` run):
preflight.py's `_check_sudoers()` flagged the sudoers fragment's own
freshly-set, CORRECT permissions (0440) as "too open", then suggested
fixing it by running `chmod 0440` -- the exact permission it had just
called insufficient.

Root cause: the check's bitmask was `0o077`, which covers every
group AND other permission bit, including the group-READ bit that
0440 itself sets (owner r--, group r--, other ---) -- install.sh and
update.sh both always chmod this file to exactly 0440, matching what
visudo itself expects for a sudoers.d fragment. A correctly-permissioned
file could never pass this check.

Fixed by narrowing the mask to 0o037 (group write/execute + any other
access at all) -- group-read is fine and expected; the real risk is
group/other being able to WRITE or EXECUTE, or OTHER having any access
whatsoever.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.web import preflight


class SudoersPermissionsCheckTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / 'anetbbs'
        self.path.write_text('# fake sudoers fragment\n')

    def _check_with_mode(self, mode):
        self.path.chmod(mode)
        real_stat = os.stat
        with patch('anetbbs.web.preflight.os.path.exists', return_value=True), \
             patch('anetbbs.web.preflight.os.stat',
                   side_effect=lambda p: real_stat(self.path)):
            return preflight._check_sudoers()

    def test_the_exact_permission_the_scripts_set_is_not_flagged(self):
        # install.sh/update.sh always chmod to exactly this -- the real
        # live bug was this exact, correct value getting flagged.
        result = self._check_with_mode(0o440)
        self.assertEqual(result['status'], 'ok', result)

    def test_group_write_is_still_flagged(self):
        result = self._check_with_mode(0o460)
        self.assertEqual(result['status'], 'warn', result)
        self.assertIn('too open', result['detail'])

    def test_group_execute_is_still_flagged(self):
        result = self._check_with_mode(0o450)
        self.assertEqual(result['status'], 'warn', result)

    def test_other_read_is_still_flagged(self):
        result = self._check_with_mode(0o444)
        self.assertEqual(result['status'], 'warn', result)

    def test_other_write_is_still_flagged(self):
        result = self._check_with_mode(0o442)
        self.assertEqual(result['status'], 'warn', result)

    def test_world_writable_is_still_flagged(self):
        result = self._check_with_mode(0o666)
        self.assertEqual(result['status'], 'warn', result)

    def test_owner_only_is_not_flagged(self):
        result = self._check_with_mode(0o400)
        self.assertEqual(result['status'], 'ok', result)


if __name__ == '__main__':
    unittest.main()
