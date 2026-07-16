"""Regression test: the Service Control Center panel (anetbbs/web/
control.py) calls subprocess.run() directly for systemctl/journalctl/
supervisorctl -- once per KNOWN_UNITS entry, every time a sysop's
browser polls /admin/control/status.json (every 5s while the panel is
open). That is the same tight-sequential-subprocess.run()-under-
eventlet shape that was found and fixed in metrics.py's sampler
(v1.0b2.131, see test_metrics_tpool_subprocess.py): under
gunicorn+eventlet, a plain subprocess.run() goes through eventlet's
greened subprocess module and can crash-loop with "Second simultaneous
read on fileno N detected" once a transient hiccup leaves a read's
fd-listener registered in the shared epoll hub past the point its fd
number gets recycled by the next call. control.py never got the same
fix -- this test verifies the mirrored _run_subprocess() helper routes
through eventlet.tpool.execute() the same way.
"""
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.core  # noqa: F401 -- avoid a control<->session circular import
from anetbbs.web import control


class RunSubprocessTpoolTests(unittest.TestCase):
    def _fake_run_result(self, out='ActiveState=active\n'):
        r = MagicMock()
        r.returncode = 0
        r.stdout = out
        r.stderr = ''
        return r

    def test_uses_tpool_execute_when_available(self):
        fake_tpool_execute = MagicMock(return_value=self._fake_run_result())
        fake_eventlet = MagicMock()
        fake_eventlet.tpool.execute = fake_tpool_execute

        with patch.object(control, '_TPOOL_AVAILABLE', True), \
             patch.object(control, 'eventlet', fake_eventlet):
            ok, out, err = control._systemctl_read('show', 'anetbbs')

        self.assertTrue(ok)
        fake_tpool_execute.assert_called_once()
        self.assertIs(fake_tpool_execute.call_args.args[0], subprocess.run)

    def test_does_not_call_subprocess_run_directly_when_tpool_available(self):
        fake_tpool_execute = MagicMock(return_value=self._fake_run_result())
        fake_eventlet = MagicMock()
        fake_eventlet.tpool.execute = fake_tpool_execute

        with patch.object(control, '_TPOOL_AVAILABLE', True), \
             patch.object(control, 'eventlet', fake_eventlet), \
             patch('subprocess.run') as direct_run:
            control._systemctl_read('show', 'anetbbs')

        direct_run.assert_not_called()

    def test_falls_back_to_direct_call_when_tpool_unavailable(self):
        with patch.object(control, '_TPOOL_AVAILABLE', False), \
             patch('subprocess.run',
                   return_value=self._fake_run_result()) as direct_run:
            ok, out, err = control._systemctl_read('show', 'anetbbs')

        direct_run.assert_called_once()
        self.assertTrue(ok)

    def test_journal_read_also_routes_through_tpool(self):
        fake_tpool_execute = MagicMock(
            return_value=self._fake_run_result(out='log line\n'))
        fake_eventlet = MagicMock()
        fake_eventlet.tpool.execute = fake_tpool_execute

        with patch.object(control, '_TPOOL_AVAILABLE', True), \
             patch.object(control, 'eventlet', fake_eventlet):
            log = control._read_journal_systemd('anetbbs', lines=50)

        self.assertEqual(log, 'log line\n')
        fake_tpool_execute.assert_called_once()
        self.assertIs(fake_tpool_execute.call_args.args[0], subprocess.run)


if __name__ == '__main__':
    unittest.main()
