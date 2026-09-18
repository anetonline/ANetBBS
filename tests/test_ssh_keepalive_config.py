"""Regression test for a real live idle-session bug: a session left
idle for 30+ minutes over an SSH client (an operator's iPad, switching
between two different SSH client apps) would go permanently silent and
never recover -- no error, no disconnect, nothing.

Root cause: nothing was writing to the session (so the drain()-timeout
fix in anetbbs/core/session.py, added for a separate write-heavy-chat
freeze, never engages), and the read() side has no timeout at all --
if the underlying network path silently dies (a carrier NAT/firewall
dropping an idle TCP mapping, the client OS suspending the app), the
server has no way to notice and the session just sits there forever.

Fix: enable asyncssh's own keepalive mechanism (keepalive_interval /
keepalive_count_max) on the SSH server. Confirmed against asyncssh's
real source (connection.py's _keepalive_timer_callback()): when
keepalive_count_max unanswered keepalives are reached, asyncssh itself
calls connection_lost(ConnectionLost(...)) -- which
_SshStreamReader.read() (see ssh_server.py) already catches and turns
into a clean EOF, already correctly handled by read_line()/read_raw()
as a CarrierLost disconnect. This test only confirms the wiring (the
right kwargs reach asyncssh.create_server()) -- actually exercising a
real 3-minute keepalive timeout end-to-end isn't practical in a fast
unit test; asyncssh's own test suite is the right place for that
mechanism's own correctness.
"""
import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.core import ssh_server


class SshKeepaliveConfigTests(unittest.TestCase):
    def test_start_ssh_server_enables_keepalive(self):
        async def _fake_create_server(*args, **kwargs):
            _fake_create_server.kwargs = kwargs
            return object()

        with mock.patch.object(ssh_server.asyncssh, 'create_server',
                               side_effect=_fake_create_server), \
             mock.patch.object(ssh_server, '_ensure_host_key',
                               return_value='/tmp/fake-key-path'):
            asyncio.run(ssh_server.start_ssh_server(
                '0.0.0.0', 2234, '/tmp/unused-key-file', bbs_config={}))

        kwargs = _fake_create_server.kwargs
        self.assertIn('keepalive_interval', kwargs)
        self.assertIn('keepalive_count_max', kwargs)
        # Must be non-zero -- 0 is asyncssh's own "disabled" sentinel
        # (see asyncssh's _DEFAULT_KEEPALIVE_INTERVAL = 0), which is
        # exactly the bug: leaving these unset/zero is what let a dead
        # connection go undetected forever.
        self.assertGreater(kwargs['keepalive_interval'], 0)
        self.assertGreater(kwargs['keepalive_count_max'], 0)


class ConnectionLostDiagnosticLoggingTests(unittest.TestCase):
    """Regression test for a real live diagnostic gap: connection_lost()
    used to log at DEBUG only, so the actual reason an SSH connection
    ended was invisible in normal production logs. An operator doing
    careful independent diagnosis of repeated ~5-minute SSH drops had
    nothing to go on beyond a bare "SSH session closed for (peer)" line
    -- no way to tell a keepalive-triggered close (the client never
    acknowledging asyncssh's own keepalive probes) apart from any other
    disconnect. Fixed by logging the real exception reason, and calling
    out the keepalive-specific case explicitly since asyncssh's own
    source (connection.py's _keepalive_timer_callback()) raises exactly
    that message text."""

    def _make_server(self, peer=('203.0.113.5', 51234)):
        server = ssh_server._BBSSshServer()
        conn = mock.Mock()
        conn.get_extra_info.return_value = peer
        server._conn = conn
        return server

    def test_keepalive_timeout_logs_at_warning_with_reason(self):
        server = self._make_server()
        with self.assertLogs(ssh_server.logger, level='WARNING') as cm:
            server.connection_lost(
                ssh_server.asyncssh.ConnectionLost(
                    'Client not responding to keepalive'))
        joined = '\n'.join(cm.output)
        self.assertIn('keepalive', joined)
        self.assertIn('203.0.113.5', joined)

    def test_ordinary_disconnect_logs_at_info_not_warning(self):
        server = self._make_server()
        with self.assertLogs(ssh_server.logger, level='INFO') as cm:
            server.connection_lost(ConnectionResetError('connection reset'))
        joined = '\n'.join(cm.output)
        self.assertIn('connection reset', joined)
        self.assertNotIn('WARNING', joined)

    def test_no_exception_logs_nothing(self):
        server = self._make_server()
        with self.assertRaises(AssertionError):
            # assertNoLogs isn't available on every supported Python
            # version here -- assertLogs itself raises AssertionError
            # when nothing was logged, which is exactly what a clean
            # (exc=None) connection_lost() call must do.
            with self.assertLogs(ssh_server.logger, level='INFO'):
                server.connection_lost(None)


if __name__ == '__main__':
    unittest.main()
