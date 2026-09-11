"""Regression test for a real Medium finding from a security/performance
audit (2026-09-10): features/login_modules.py's _run_shell() (backs the
'shell', 'door_native', and 'file_bulletin'-adjacent 'shell' logon/logoff
module types -- runs once per login/logoff for every session that
doesn't use fast-logon) left the child process completely unmanaged on
a timeout. asyncio.wait_for(proc.communicate(), timeout=...) only
abandons THIS function's own wait for the child -- it never kills the
child itself -- so a hung or runaway sysop-configured shell command
kept running indefinitely as a live child of the BBS process, unbounded
by the function's own timeout, one leaked process per timed-out login.

Fixed by killing the process and awaiting its exit (reaping it) in the
asyncio.TimeoutError handler.

Uses a fake create_subprocess_shell (no real child process spawned) so
this test doesn't depend on OS-level process-group/exec-optimization
behavior -- it verifies the CODE PATH (kill() + wait() both called on
timeout), which is what actually matters here.
"""
import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _FakeWriter:
    def __init__(self):
        self.written = bytearray()

    def write(self, data):
        self.written += data

    async def drain(self):
        pass


class _FakeSession:
    def __init__(self):
        self.user = {'username': 'tester'}
        self.writer = _FakeWriter()


class _FakeProc:
    """Stands in for asyncio.subprocess.Process -- communicate() hangs
    well past the shrunk test timeout; kill()/wait() record whether
    they were actually called."""

    def __init__(self):
        self.killed = False
        self.waited = False

    async def communicate(self):
        await asyncio.sleep(10)
        return (b'should never get here', None)

    def kill(self):
        self.killed = True

    async def wait(self):
        self.waited = True
        return 0


class LoginModulesShellTimeoutKillTests(unittest.TestCase):
    def test_timeout_kills_and_reaps_the_hung_child(self):
        from anetbbs.features import login_modules

        fake_proc = _FakeProc()

        async def _fake_create_subprocess_shell(*args, **kwargs):
            return fake_proc

        session = _FakeSession()
        old_timeout = login_modules._SHELL_MODULE_TIMEOUT_SECONDS
        login_modules._SHELL_MODULE_TIMEOUT_SECONDS = 0.05
        try:
            with patch('asyncio.create_subprocess_shell',
                      _fake_create_subprocess_shell):
                asyncio.run(login_modules._run_shell(
                    session, {'command': 'sleep 999'}))
        finally:
            login_modules._SHELL_MODULE_TIMEOUT_SECONDS = old_timeout

        self.assertTrue(fake_proc.killed,
                        'a timed-out login-module shell command must be '
                        'killed, not left running unmanaged')
        self.assertTrue(fake_proc.waited,
                        'the killed process must also be reaped (awaited)')


if __name__ == '__main__':
    unittest.main()
