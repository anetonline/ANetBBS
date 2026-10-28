"""Regression test for the self-detach mechanism in
deploy/run_upgrade.sh, added after a second real live production
outage (2026-10-28) -- see tests/test_upgrade_spawn_cmd_detached.py's
module docstring for the full two-outage history.

Unlike install.sh/update.sh (which need real root + a real systemd
*system* instance and so only get structural source checks elsewhere
in this suite, see test_sudoers_tmp_file_permissions.py's own
docstring for that precedent), this ONE block only needs a working
systemd *user* instance, which IS available in a normal dev/CI sandbox
-- so this test actually EXECUTES the real, unmodified detach block
extracted verbatim from the real file, through a real
``systemd-run --wait`` round trip, rather than just asserting on its
text. A PATH shim transparently redirects the block's bare
``systemd-run`` call to ``systemd-run --user`` so the real script
needs no modification to run under a non-root test user.
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RUN_UPGRADE_SH = REPO_ROOT / 'deploy' / 'run_upgrade.sh'

_START_MARKER = "# ── Detach from the caller's cgroup"
_END_MARKER = '# ── Argument validation'


def _extract_detach_block():
    lines = RUN_UPGRADE_SH.read_text().splitlines()
    start = end = None
    for i, line in enumerate(lines):
        if start is None and line.startswith(_START_MARKER):
            start = i
        elif start is not None and line.startswith(_END_MARKER):
            end = i
            break
    if start is None or end is None:
        raise AssertionError(
            'could not find the detach block in deploy/run_upgrade.sh -- '
            'its section-header comments may have been renamed')
    return '\n'.join(lines[start:end])


def _systemd_user_available():
    try:
        r = subprocess.run(['systemctl', '--user', 'is-system-running'],
                           capture_output=True, text=True, timeout=5)
        # "running" or "degraded" both mean the user manager answers;
        # anything else (not found, can't connect) means skip.
        return r.returncode in (0, 1) and bool(r.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


_SCRIPT_TEMPLATE = """#!/usr/bin/env bash
set -eu -o pipefail
LOG_PREFIX="[upgrade]"
log()  { printf '%s %s\\n' "$LOG_PREFIX" "$*"; }
fail() { printf '%s FAIL: %s\\n' "$LOG_PREFIX" "$*" >&2; exit 1; }

__DETACH_BLOCK__

printf 'pid=%s cgroup=%s\\n' "$$" "$(cat /proc/self/cgroup)" >> "__MARKER__"
exit __EXITCODE__
"""


@unittest.skipUnless(_systemd_user_available(),
                     'no working systemd --user instance in this environment')
class RunUpgradeShSelfDetachTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.detach_block = _extract_detach_block()
        # Structural sanity before trusting it in a real exec below --
        # a clear assertion failure here beats a confusing runtime one.
        assert 'ANETBBS_UPGRADE_DETACHED' in cls.detach_block
        assert 'exec systemd-run' in cls.detach_block
        assert '--wait' in cls.detach_block
        assert '"$0" "$@"' in cls.detach_block
        assert '--unit=anetbbs-upgrade' in cls.detach_block

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp_path = Path(self._tmp.name)

        # PATH shim: forwards the real block's bare `systemd-run` call
        # to the system's --user instance, since this sandbox has no
        # root -- exercises the exact, unmodified detach block against
        # a real systemd-run --wait round trip.
        shim_dir = tmp_path / 'bin'
        shim_dir.mkdir()
        shim = shim_dir / 'systemd-run'
        shim.write_text('#!/usr/bin/env bash\nexec /usr/bin/systemd-run --user "$@"\n')
        shim.chmod(0o755)
        self.env = dict(os.environ)
        self.env['PATH'] = f"{shim_dir}:{self.env.get('PATH', '')}"
        self.marker = tmp_path / 'marker.txt'
        self.outer_cgroup = Path('/proc/self/cgroup').read_text()

    def _write_script(self, name, exit_code):
        body = (_SCRIPT_TEMPLATE
                .replace('__DETACH_BLOCK__', self.detach_block)
                .replace('__MARKER__', str(self.marker))
                .replace('__EXITCODE__', str(exit_code)))
        script = Path(self._tmp.name) / name
        script.write_text(body)
        script.chmod(0o755)
        return script

    def test_detaches_into_its_own_unit_and_relays_success_exit_code(self):
        script = self._write_script('probe-ok.sh', 0)
        result = subprocess.run([str(script)], env=self.env,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.marker.exists(), 'continuation never ran')
        marker_text = self.marker.read_text()
        self.assertIn('anetbbs-upgrade.service', marker_text,
                      'continuation did not run inside the detached unit')
        self.assertNotEqual(marker_text.split('cgroup=', 1)[1],
                            self.outer_cgroup,
                            'detached process landed in the same cgroup '
                            'as the caller -- not actually detached')

    def test_relays_a_real_failure_exit_code_too(self):
        script = self._write_script('probe-fail.sh', 7)
        result = subprocess.run([str(script)], env=self.env,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertTrue(self.marker.exists())

    def test_does_not_re_detach_when_already_detached(self):
        # Simulates the recursive invocation (ANETBBS_UPGRADE_DETACHED
        # already set, as systemd-run's own --setenv does) -- must skip
        # straight to the continuation without touching systemd-run
        # again at all.
        script = self._write_script('probe-already.sh', 0)
        env = dict(self.env)
        env['ANETBBS_UPGRADE_DETACHED'] = '1'
        result = subprocess.run([str(script)], env=env,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        marker_text = self.marker.read_text()
        # Ran in the CALLER's own cgroup this time -- never detached,
        # since the block's own guard should have skipped entirely.
        self.assertEqual(marker_text.split('cgroup=', 1)[1], self.outer_cgroup)


if __name__ == '__main__':
    unittest.main()
