"""Regression test for TWO real live production outages in this
subsystem, now both closed by the same structural fix.

Outage 1 (2026-10-27, second half): `_spawn_upgrade()` ran the
privileged wrapper via ``systemd-run --scope``, which -- confirmed via
systemd-run(1), not guessed -- makes ``systemd-run`` itself the PARENT
process of the wrapper, so the wrapper stayed a descendant of
anetbbs-web's own cgroup and could be killed as collateral damage when
``update.sh`` restarted that service mid-upgrade. Fixed in v1.1.59 by
switching to a named, detached ``systemd-run --unit=... --wait``
service instead -- but that fix put the exact detachment flags INSIDE
the sudo-gated command, which meant deploy/sudoers.anetbbs had to
change in lockstep on every existing install.

Outage 2 (2026-10-28): on one real box, that sudoers refresh (inside
``update.sh``) silently failed on a past upgrade while the rest of
that upgrade still succeeded -- leaving the box running v1.1.59+ code
that built the new systemd-run command, against a stale pre-v1.1.59
sudoers grant that still only permitted the OLD ``--scope`` form.
"sudo: a password is required", confirmed live. The auto-update button
is the one thing that can never fix a sudoers problem via itself, so
this exact class of bug could recur indefinitely every time the
detachment mechanism's flags changed.

Structural fix: ALL detachment logic moved out of the sudo-gated
command entirely, into deploy/run_upgrade.sh itself (see
test_run_upgrade_sh_self_detach.py for that half). `_spawn_upgrade()`
now only ever builds `sudo -n <wrapper> <args>` -- nothing that can
ever need to change again regardless of how detachment works
internally -- and deploy/sudoers.anetbbs's grant is just as simple.
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

    def test_command_is_exactly_sudo_n_wrapper_and_three_args(self):
        # The whole point of the fix: NOTHING else is in this command --
        # no systemd-run, no --scope, no --unit, no --property flags.
        # Detachment is entirely deploy/run_upgrade.sh's own problem now.
        cmd = self._capture_cmd()
        self.assertEqual(cmd, [
            'sudo', '-n', '/opt/anetbbs/deploy/run_upgrade.sh',
            'v1.1.58', 'https://example.com/ANetBBS-v1.1.58.tar.gz',
            'a' * 64,
        ])

    def test_does_not_mention_systemd_run_at_all(self):
        cmd = self._capture_cmd()
        joined = ' '.join(cmd)
        self.assertNotIn('systemd-run', joined)
        self.assertNotIn('--scope', joined)
        self.assertNotIn('--unit=', joined)
        self.assertNotIn('--property=', joined)


class SudoersGrantMatchesSpawnCommandTests(unittest.TestCase):
    """Cross-checks deploy/sudoers.anetbbs against what _spawn_upgrade()
    actually builds, so the two files can't silently drift apart --
    exactly the shape of bug that caused both outages above."""

    def setUp(self):
        sudoers_path = REPO_ROOT / 'deploy' / 'sudoers.anetbbs'
        if not sudoers_path.exists():
            # .dockerignore deliberately excludes this file from the
            # Docker image (it's a bare-metal-only concern -- see its
            # own comment there), so the Docker-build CI workflow runs
            # this suite without it present. Nothing to cross-check in
            # that environment.
            self.skipTest('deploy/sudoers.anetbbs not present in this build '
                          '(excluded from the Docker image by .dockerignore)')
        self.sudoers_text = sudoers_path.read_text()

    def _upgrade_lines(self):
        return [ln for ln in self.sudoers_text.splitlines()
               if 'run_upgrade.sh' in ln
               and not ln.strip().startswith('#')
               and 'Cmnd_Alias' not in ln]

    def _upgrade_line(self):
        lines = self._upgrade_lines()
        self.assertEqual(len(lines), 1, lines)
        return lines[0]

    def test_grant_is_the_plain_wrapper_with_no_extra_flags(self):
        # Deliberately simple: this must stay just "<wrapper> *" forever,
        # with nothing version/mechanism-specific baked into it. If a
        # future change ever reintroduces a flag here, it has reopened
        # exactly the class of bug this test exists to catch.
        line = self._upgrade_line().strip().rstrip(',').strip()
        self.assertTrue(line.endswith('/deploy/run_upgrade.sh *'), line)
        self.assertNotIn('systemd-run', line)
        self.assertNotIn('--', line)

    def test_only_one_upgrade_grant_alternative_exists(self):
        # Outage 1's fix added a second ("direct invocation")
        # alternative alongside the systemd-run one; now that
        # detachment moved into the script, there's no reason for more
        # than one invocation form to exist at all.
        self.assertEqual(len(self._upgrade_lines()), 1, self._upgrade_lines())


if __name__ == '__main__':
    unittest.main()
