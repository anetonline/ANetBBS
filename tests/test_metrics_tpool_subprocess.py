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


if __name__ == '__main__':
    unittest.main()
