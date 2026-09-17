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


if __name__ == '__main__':
    unittest.main()
