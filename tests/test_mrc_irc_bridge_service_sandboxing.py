"""Regression test for a real Medium/High-severity finding from a
security/performance audit (2026-08-31):
deploy/anetbbs-mrc-irc-bridge@.service had none of the systemd
sandboxing directives (NoNewPrivileges, ProtectSystem, PrivateTmp,
etc.) that harden a unit against a compromised process escalating or
reaching outside its intended scope. It's a clean candidate for a
strict baseline: a pure network daemon (mrc_irc_bridge.py connects
outbound to IRC and to the local MRC bridge, and writes to the app DB)
with no subprocess spawning and no file logging of its own, unlike the
door-game-launching unit where a locked-down filesystem risks breaking
an arbitrary door binary's own I/O needs.

Checked via source inspection (parsing the unit file's key=value pairs
directly), with an additional `systemd-analyze verify` pass when that
binary is available on the test host -- confirms the file is
syntactically valid systemd unit syntax, not just that the expected
lines are present as text.
"""
import shutil
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
UNIT_FILE = REPO_ROOT / 'deploy' / 'anetbbs-mrc-irc-bridge@.service'


def _parse_unit(text):
    """Minimal key=value parser good enough for this flat unit file --
    real systemd unit files can repeat keys and have more structure,
    but every directive this test checks appears at most once here."""
    values = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#') or line.startswith('['):
            continue
        if '=' not in line:
            continue
        key, _, val = line.partition('=')
        values[key.strip()] = val.strip()
    return values


class MrcIrcBridgeSandboxingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = UNIT_FILE.read_text()
        cls.values = _parse_unit(cls.text)

    def test_no_new_privileges(self):
        self.assertEqual(self.values.get('NoNewPrivileges'), 'true')

    def test_filesystem_is_locked_down_with_an_explicit_writable_data_path(self):
        self.assertEqual(self.values.get('ProtectSystem'), 'strict')
        self.assertEqual(self.values.get('ProtectHome'), 'true')
        self.assertEqual(self.values.get('PrivateTmp'), 'true')
        # Must name a real writable path, not just be present -- an
        # empty or missing ReadWritePaths under ProtectSystem=strict
        # would make the unit unable to write its own database.
        rwp = self.values.get('ReadWritePaths', '')
        self.assertTrue(rwp, 'ReadWritePaths must be set so the bridge '
                         'can still write to its data directory under '
                         'ProtectSystem=strict')

    def test_kernel_and_privilege_hardening_directives_present(self):
        expected_true = (
            'ProtectKernelTunables', 'ProtectKernelModules',
            'ProtectKernelLogs', 'ProtectControlGroups', 'ProtectClock',
            'ProtectHostname', 'RestrictSUIDSGID', 'RestrictRealtime',
            'RestrictNamespaces', 'LockPersonality',
            'MemoryDenyWriteExecute',
        )
        missing = [d for d in expected_true if self.values.get(d) != 'true']
        self.assertEqual(missing, [],
                         f'expected these hardening directives set to '
                         f'"true": {missing}')

    def test_syscall_filter_present(self):
        self.assertEqual(self.values.get('SystemCallFilter'), '@system-service')

    @unittest.skipUnless(shutil.which('systemd-analyze'),
                        'systemd-analyze not available on this host')
    def test_unit_file_is_syntactically_valid_systemd_syntax(self):
        result = subprocess.run(
            ['systemd-analyze', 'verify', str(UNIT_FILE)],
            capture_output=True, text=True, timeout=15)
        # systemd-analyze verify exits non-zero here regardless (the
        # referenced /opt/anetbbs/venv/bin/python doesn't exist on a
        # bare test host) -- what matters is that the ONLY complaint is
        # about the missing binary/path, not a directive-syntax error,
        # which would show up as "Unknown key" or a parse failure.
        combined = (result.stdout + result.stderr).lower()
        self.assertNotIn('unknown key', combined, combined)
        self.assertNotIn('unknown lvalue', combined, combined)
        self.assertNotIn('failed to parse', combined, combined)


if __name__ == '__main__':
    unittest.main()
