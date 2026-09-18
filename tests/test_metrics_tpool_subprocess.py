"""Regression test for a real live bug: the Service Control Center's
per-PID metrics sampler (anetbbs/web/metrics.py) crash-looped every
~2 seconds under gunicorn+eventlet with "RuntimeError: Second
simultaneous read on fileno N detected" -- confirmed live via
journalctl. A plain subprocess.run() call, run once per known systemd
unit in a tight sequential loop, goes through eventlet's greened
subprocess module; any transient hiccup can leave that read's
fd-listener registered in the shared epoll hub past the point its fd
number gets recycled by the next subprocess.run() call, which then
collides with it -- crash-looping from that point on indefinitely.

Fixed by dispatching the blocking subprocess.run() call to a real
native OS thread via eventlet.tpool.execute(), which bypasses
eventlet's greened subprocess/os.read machinery (and its shared hub
bookkeeping) entirely -- the officially recommended eventlet pattern
for blocking calls like this.

Reported alongside a live "dosemu2 door games work fine over telnet/
SSH but show a black screen over the web UI" report -- this crash-loop
runs in the same gunicorn/eventlet worker process (and shares its one
epoll hub) as door_runner.py's own PTY/COM1 fd registrations for a
freshly-launched door game, a very plausible source of interference.
"""
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.web.metrics as metrics


class GetMainPidTpoolTests(unittest.TestCase):
    def _fake_run_result(self, pid=12345):
        r = MagicMock()
        r.stdout = f'MainPID={pid}\n'
        return r

    def test_uses_tpool_execute_when_available(self):
        fake_tpool_execute = MagicMock(return_value=self._fake_run_result())
        fake_eventlet = MagicMock()
        fake_eventlet.tpool.execute = fake_tpool_execute

        with patch.object(metrics, '_TPOOL_AVAILABLE', True), \
             patch.object(metrics, 'eventlet', fake_eventlet):
            pid = metrics._get_main_pid('anetbbs-web')

        self.assertEqual(pid, 12345)
        fake_tpool_execute.assert_called_once()
        # First positional arg to tpool.execute must be subprocess.run
        # itself -- this is what actually escapes eventlet's greened
        # subprocess module and its shared hub fd bookkeeping.
        self.assertIs(fake_tpool_execute.call_args.args[0], subprocess.run)

    def test_does_not_call_subprocess_run_directly_when_tpool_available(self):
        fake_tpool_execute = MagicMock(return_value=self._fake_run_result())
        fake_eventlet = MagicMock()
        fake_eventlet.tpool.execute = fake_tpool_execute

        with patch.object(metrics, '_TPOOL_AVAILABLE', True), \
             patch.object(metrics, 'eventlet', fake_eventlet), \
             patch('subprocess.run') as direct_run:
            metrics._get_main_pid('anetbbs-web')

        direct_run.assert_not_called()

    def test_falls_back_to_direct_call_when_tpool_unavailable(self):
        with patch.object(metrics, '_TPOOL_AVAILABLE', False), \
             patch('subprocess.run', return_value=self._fake_run_result(999)) as direct_run:
            pid = metrics._get_main_pid('anetbbs-web')

        direct_run.assert_called_once()
        self.assertEqual(pid, 999)

    def test_inactive_unit_returns_none(self):
        r = MagicMock()
        r.stdout = 'MainPID=0\n'
        with patch.object(metrics, '_TPOOL_AVAILABLE', False), \
             patch('subprocess.run', return_value=r):
            pid = metrics._get_main_pid('anetbbs-web')
        self.assertIsNone(pid)


class EnsureProcSkipsSystemctlWhenCachedTests(unittest.TestCase):
    """Regression test for a real live incident: _ensure_proc() used to
    call _get_main_pid() (a real `systemctl show` subprocess spawn)
    unconditionally on every sample tick, for every known unit, even
    when the cached psutil.Process handle was still perfectly valid --
    ~2.5 subprocess spawns/second, forever, regardless of host load.
    Confirmed live: repeated "systemctl show ... timed out after 5
    seconds" errors across a full day, correlated with a period of
    degrading server responsiveness. Fixed by checking the cached
    handle's cheap is_running() (no subprocess) first."""

    def setUp(self):
        self._orig_handles = dict(metrics._proc_handles)
        self._orig_pids = dict(metrics._pid_cache)
        metrics._proc_handles.clear()
        metrics._pid_cache.clear()
        self.addCleanup(self._restore)

    def _restore(self):
        metrics._proc_handles.clear()
        metrics._proc_handles.update(self._orig_handles)
        metrics._pid_cache.clear()
        metrics._pid_cache.update(self._orig_pids)

    def test_does_not_call_get_main_pid_when_cached_process_still_running(self):
        fake_proc = MagicMock()
        fake_proc.is_running.return_value = True
        metrics._proc_handles['anetbbs-web'] = fake_proc
        metrics._pid_cache['anetbbs-web'] = 111

        with patch.object(metrics, '_get_main_pid') as fake_get_pid, \
             patch.object(metrics, '_PSUTIL_AVAILABLE', True):
            result = metrics._ensure_proc('anetbbs-web')

        fake_get_pid.assert_not_called()
        self.assertIs(result, fake_proc)

    def test_falls_through_to_get_main_pid_when_cached_process_is_gone(self):
        fake_proc = MagicMock()
        fake_proc.is_running.return_value = False
        metrics._proc_handles['anetbbs-web'] = fake_proc
        metrics._pid_cache['anetbbs-web'] = 111

        with patch.object(metrics, '_get_main_pid', return_value=222) as fake_get_pid, \
             patch.object(metrics, '_PSUTIL_AVAILABLE', True), \
             patch.object(metrics, 'psutil') as fake_psutil_mod:
            new_proc = MagicMock()
            fake_psutil_mod.Process.return_value = new_proc
            result = metrics._ensure_proc('anetbbs-web')

        fake_get_pid.assert_called_once_with('anetbbs-web')
        self.assertIs(result, new_proc)

    def test_no_cached_handle_at_all_still_resolves_via_get_main_pid(self):
        with patch.object(metrics, '_get_main_pid', return_value=333) as fake_get_pid, \
             patch.object(metrics, '_PSUTIL_AVAILABLE', True), \
             patch.object(metrics, 'psutil') as fake_psutil_mod:
            new_proc = MagicMock()
            fake_psutil_mod.Process.return_value = new_proc
            result = metrics._ensure_proc('anetbbs-finger')

        fake_get_pid.assert_called_once_with('anetbbs-finger')
        self.assertIs(result, new_proc)


if __name__ == '__main__':
    unittest.main()
