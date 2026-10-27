"""Regression test for a real live production outage (2026-10-27,
second half): the web-triggered auto-update button could pass
preflight (sudo escalation confirmed working) and still silently die
partway through, with the box left on the old version and no error in
the UI.

Root cause: `_spawn_upgrade()` ran the privileged wrapper via
``systemd-run --scope``, which -- confirmed via systemd-run(1), not
guessed -- makes ``systemd-run`` itself the PARENT process of the
wrapper ("the command will inherit the execution environment of the
caller"). That means the wrapper was still a descendant of whatever
spawned it (gunicorn, inside anetbbs-web's own cgroup) the whole time.
When ``update.sh``'s own Step 3 stops anetbbs-web, systemd can still
kill the wrapper as collateral damage, confirmed live via journalctl
showing the wrapper's sudo process SIGKILLed mid-upgrade.

Fixed by dropping ``--scope`` entirely. The DEFAULT (non-scope)
``systemd-run`` mode runs the command as a transient *service* with
the service manager (PID 1) as its parent -- never a descendant of the
caller at all, so it's unconditionally immune to anetbbs-web's cgroup
being killed, regardless of timing. ``--wait`` restores completion
tracking by monitoring the unit via the service manager (not by being
part of its process tree), and the StandardOutput=/StandardError=
properties redirect the unit's output to the upgrade log file, since a
plain service's output goes to the journal by default rather than
inheriting systemd-run's own stdout the way --scope mode did.

This test asserts the exact `cmd` list `_spawn_upgrade()` builds --
not threading/subprocess mechanics, which are mocked out entirely --
and separately asserts deploy/sudoers.anetbbs's ANETBBS_UPGRADE grant
contains the exact same flags, so the two can never drift out of sync
the way a sudoers-grant mismatch would silently break this again.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.core  # noqa: F401 -- circular-import workaround
from anetbbs.web import upgrades

REPO_ROOT = Path(__file__).resolve().parent.parent


class _SyncThread:
    """Runs the target synchronously instead of actually threading --
    makes capturing _spawn_upgrade()'s internal subprocess call
    deterministic instead of racing a real background thread."""

    def __init__(self, target=None, daemon=None, name=None, **kw):
        self._target = target

    def start(self):
        if self._target:
            self._target()


class SpawnUpgradeCommandShapeTests(unittest.TestCase):
    def setUp(self):
        upgrades._install_state['running'] = False

    def _capture_cmd(self):
        fake_proc = MagicMock()
        fake_proc.wait.return_value = None
        fake_proc.returncode = 0
        captured = {}

        def _fake_popen(cmd, **kwargs):
            captured['cmd'] = cmd
            return fake_proc

        with patch.object(upgrades, 'threading') as mock_threading, \
             patch.object(upgrades.subprocess, 'Popen', side_effect=_fake_popen), \
             patch('builtins.open', MagicMock()):
            mock_threading.Thread = _SyncThread
            upgrades._spawn_upgrade('/opt/anetbbs/deploy/run_upgrade.sh',
                                    'v1.1.58',
                                    'https://example.com/ANetBBS-v1.1.58.tar.gz',
                                    'a' * 64, label='upgrade')
        return captured['cmd']

    def test_does_not_use_scope(self):
        # The exact bug: --scope makes systemd-run the wrapper's own
        # parent, leaving it entangled with the caller's cgroup.
        cmd = self._capture_cmd()
        self.assertNotIn('--scope', cmd)

    def test_uses_a_named_unit_and_waits_for_completion(self):
        cmd = self._capture_cmd()
        self.assertIn('--unit=anetbbs-upgrade', cmd)
        self.assertIn('--wait', cmd)

    def test_redirects_unit_output_to_the_upgrade_log(self):
        # A plain (non-scope) transient service's output goes to the
        # journal by default, not to our pipe -- must be explicitly
        # redirected or the web UI's log tail goes silent.
        cmd = self._capture_cmd()
        joined = ' '.join(cmd)
        self.assertIn(f'--property=StandardOutput=append:{upgrades._UPGRADE_LOG_PATH}',
                      joined)
        self.assertIn(f'--property=StandardError=append:{upgrades._UPGRADE_LOG_PATH}',
                      joined)

    def test_still_invokes_via_sudo_n_systemd_run(self):
        cmd = self._capture_cmd()
        self.assertEqual(cmd[0], 'sudo')
        self.assertEqual(cmd[1], '-n')
        self.assertEqual(cmd[2], '/usr/bin/systemd-run')

    def test_wrapper_and_args_still_passed_through(self):
        cmd = self._capture_cmd()
        self.assertIn('/opt/anetbbs/deploy/run_upgrade.sh', cmd)
        self.assertIn('v1.1.58', cmd)
        self.assertIn('https://example.com/ANetBBS-v1.1.58.tar.gz', cmd)
        self.assertIn('a' * 64, cmd)


class SudoersGrantMatchesSpawnCommandTests(unittest.TestCase):
    """Cross-checks deploy/sudoers.anetbbs against what _spawn_upgrade()
    actually builds, so the two files can't silently drift apart --
    exactly the shape of bug that would reintroduce a permission
    denial with zero code-level test coverage otherwise."""

    def setUp(self):
        self.sudoers_text = (REPO_ROOT / 'deploy' / 'sudoers.anetbbs').read_text()

    def _upgrade_cmd_tokens(self):
        fake_proc = MagicMock()
        fake_proc.wait.return_value = None
        fake_proc.returncode = 0
        captured = {}

        def _fake_popen(cmd, **kwargs):
            captured['cmd'] = cmd
            return fake_proc

        upgrades._install_state['running'] = False
        with patch.object(upgrades, 'threading') as mock_threading, \
             patch.object(upgrades.subprocess, 'Popen', side_effect=_fake_popen), \
             patch('builtins.open', MagicMock()):
            mock_threading.Thread = _SyncThread
            upgrades._spawn_upgrade('/opt/anetbbs/deploy/run_upgrade.sh',
                                    'v1.1.58', 'https://example.com/x.tar.gz',
                                    'a' * 64, label='upgrade')
        # Drop sudo/-n (the sudoers file itself is what grants those)
        # and everything from the wrapper path onward (the per-call
        # wrapper/version/url/sha256 args, matched by the trailing `*`
        # glob in the sudoers rule, not literal tokens).
        cmd = captured['cmd']
        wrapper_idx = cmd.index('/opt/anetbbs/deploy/run_upgrade.sh')
        return cmd[2:wrapper_idx]

    def test_every_fixed_flag_appears_in_the_sudoers_grant(self):
        for token in self._upgrade_cmd_tokens():
            self.assertIn(token, self.sudoers_text,
                          f'_spawn_upgrade() builds {token!r} but '
                          'deploy/sudoers.anetbbs does not grant it -- '
                          'the auto-update button would fail sudo again')

    def test_sudoers_grant_does_not_still_use_scope(self):
        upgrade_line = next(
            (line for line in self.sudoers_text.splitlines()
             if 'systemd-run' in line and 'run_upgrade.sh' in line), None)
        self.assertIsNotNone(upgrade_line, 'no systemd-run grant line found')
        self.assertNotIn('--scope', upgrade_line)


if __name__ == '__main__':
    unittest.main()
