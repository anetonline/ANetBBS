"""Regression test for a real live production outage (2026-10-27).

anetbbs/web/preflight.py's _check_sudo_escalation() used to detect
"unit hardening blocks sudo" by grepping the main unit file's raw text
for a literal `NoNewPrivileges=yes` line. On Jerry's own live box,
that text was never present anywhere -- not in the main unit file, not
in its `10-security-hardening.conf` drop-in -- yet the real running
process had the kernel's `no_new_privs` flag set anyway (confirmed via
`/proc/<MainPID>/status`), because systemd IMPLIES NoNewPrivileges=yes
from several of the OTHER hardening directives present in that drop-in
(SystemCallFilter=, RestrictNamespaces=, ProtectKernelTunables=,
ProtectKernelModules=, ProtectKernelLogs=, MemoryDenyWriteExecute=,
RestrictRealtime=, LockPersonality=), per systemd.exec(5), unless
NoNewPrivileges=no is explicitly set to override it.

The old text-only check therefore reported a false "ok" and let the
real `sudo -n -l` probe run anyway, which then failed with a confusing
runtime error instead of the actionable "unit hardening blocks sudo"
message this check exists to produce.

Fixed by querying systemd's own COMPUTED effective property
(`systemctl show anetbbs-web.service -p NoNewPrivileges --value`)
instead of (or in addition to, for CapabilityBoundingSet=) grepping
unit file text -- systemd itself already applies the implied-by-other-
directives rule when computing this value, so this check can't miss
it the way a text search can.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock, mock_open

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anetbbs.web import preflight


class _FakeCompletedProcess:
    def __init__(self, stdout='', returncode=0):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = ''


class SudoEscalationImpliedNoNewPrivilegesTests(unittest.TestCase):
    """The exact shape of the real bug: systemd reports the EFFECTIVE
    NoNewPrivileges as "yes" even though no unit file text says so."""

    def test_effective_yes_is_flagged_even_with_an_innocent_unit_file(self):
        def fake_run(cmd, **kwargs):
            if cmd[:2] == ['systemctl', 'show']:
                return _FakeCompletedProcess(stdout='yes\n')
            raise AssertionError(f'unexpected subprocess call: {cmd}')

        with patch('anetbbs.web.preflight.subprocess.run', side_effect=fake_run), \
             patch('builtins.open',
                  MagicMock(side_effect=OSError('no unit file for this test'))):
            result = preflight._check_sudo_escalation({'INSTALL_DIR': '/opt/anetbbs'})

        self.assertEqual(result['status'], 'fail', result)
        self.assertIn('NoNewPrivileges=yes', result['detail'])
        self.assertIn('effective', result['detail'])

    def test_effective_no_and_clean_unit_file_proceeds_to_the_sudo_probe(self):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[:2] == ['systemctl', 'show']:
                return _FakeCompletedProcess(stdout='no\n')
            if cmd[:1] == ['sudo']:
                return _FakeCompletedProcess(returncode=0)
            raise AssertionError(f'unexpected subprocess call: {cmd}')

        with patch('anetbbs.web.preflight.subprocess.run', side_effect=fake_run), \
             patch('builtins.open', MagicMock(side_effect=OSError('no file'))), \
             patch('os.path.isfile', return_value=True):
            result = preflight._check_sudo_escalation({'INSTALL_DIR': '/opt/anetbbs'})

        self.assertEqual(result['status'], 'ok', result)
        # Confirms the real sudo -n -l probe actually ran -- the fix
        # must not short-circuit on a correctly-configured unit.
        self.assertTrue(any(c[:1] == ['sudo'] for c in calls))

    def test_systemctl_unavailable_does_not_crash_and_falls_through(self):
        """If systemctl itself can't be queried (e.g. systemd not
        running), this check must degrade gracefully -- not raise --
        and still attempt the real sudo probe."""
        def fake_run(cmd, **kwargs):
            if cmd[:2] == ['systemctl', 'show']:
                raise FileNotFoundError('systemctl not found')
            if cmd[:1] == ['sudo']:
                return _FakeCompletedProcess(returncode=0)
            raise AssertionError(f'unexpected subprocess call: {cmd}')

        with patch('anetbbs.web.preflight.subprocess.run', side_effect=fake_run), \
             patch('builtins.open', MagicMock(side_effect=OSError('no file'))), \
             patch('os.path.isfile', return_value=True):
            result = preflight._check_sudo_escalation({'INSTALL_DIR': '/opt/anetbbs'})

        self.assertEqual(result['status'], 'ok', result)


class CapabilityBoundingSetStillDetectedTests(unittest.TestCase):
    """The CapabilityBoundingSet= text check is unaffected by the
    implied-NoNewPrivileges bug and fix -- still works via direct
    unit-file text, confirmed unchanged here."""

    def test_narrow_capabilitybounding_set_still_fails(self):
        unit_text = '[Service]\nCapabilityBoundingSet=CAP_NET_BIND_SERVICE\n'

        def fake_run(cmd, **kwargs):
            if cmd[:2] == ['systemctl', 'show']:
                return _FakeCompletedProcess(stdout='no\n')
            raise AssertionError(f'unexpected subprocess call: {cmd}')

        with patch('anetbbs.web.preflight.subprocess.run', side_effect=fake_run), \
             patch('builtins.open', mock_open(read_data=unit_text)):
            result = preflight._check_sudo_escalation({'INSTALL_DIR': '/opt/anetbbs'})

        self.assertEqual(result['status'], 'fail', result)
        self.assertIn('CapabilityBoundingSet', result['detail'])


if __name__ == '__main__':
    unittest.main()
