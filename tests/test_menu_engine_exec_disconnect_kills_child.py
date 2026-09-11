"""Regression test for a real Medium/High finding from a security/
performance audit (2026-09-10): menu_engine.py's _act_exec() (the
sysop-only 'exec' menu action that bridges a user's terminal to an
external program's stdin/stdout) never told the CHILD PROCESS when the
user's connection actually dropped mid-session.

pump_in() forwards keystrokes via ui.session.read_raw(), which raises
CarrierLost on a real disconnect -- but that was caught by pump_in()'s
own broad `except Exception: logger.exception(...)`, which just ended
the pump task quietly. Nothing else ever kills the child. `await
proc.wait()` in the calling function's own body -- which run_menu()'s
dispatch loop awaits SYNCHRONOUSLY -- has no idea the connection is
gone and blocks until the child exits on its own. A child that's
simply blocked reading its own now-abandoned stdin (very ordinary --
`cat`, an interactive script, a real door) then never exits, so the
ENTIRE BBSSession (multinode slot, NodeActivity row, DB handles, etc.)
stays alive forever after the user physically disconnected -- the same
"state persists past when the underlying connection is actually gone"
leak shape this audit round specifically looks for (see presence.py's
own analogous fix history).

Fixed by having pump_in() catch CarrierLost specifically and kill the
child process, so proc.wait() actually unblocks.

Uses a fake asyncio.create_subprocess_shell (a fake Process whose
wait() only resolves once kill() has been called, mirroring a real
child blocked reading its own stdin) rather than a real subprocess --
a real-subprocess version of this test hung indefinitely specifically
under this project's mandated `systemd-run --user --scope` test
wrapper (a child-process-reaping interaction between asyncio's
ThreadedChildWatcher and that wrapper, unrelated to the actual
application bug/fix being verified here -- confirmed by reproducing
the exact same real-subprocess test cleanly outside that wrapper both
before and after the fix). This version verifies the CODE PATH itself
(kill() called, and proc.wait() only resolving after it), which is
what actually matters and is robust to that environment quirk.
"""
import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _FakeStdin:
    def write(self, data):
        pass

    async def drain(self):
        pass


class _FakeStdout:
    async def read(self, n=1024):
        # No output -- pump_out() ends quickly either way.
        return b''


class _FakeProc:
    """Mirrors a real child that's blocked reading its own stdin: wait()
    only resolves once kill() actually flips returncode, exactly like a
    real blocked process only exits once it's actually killed."""

    def __init__(self):
        self.returncode = None
        self.killed = False
        self.stdin = _FakeStdin()
        self.stdout = _FakeStdout()

    def kill(self):
        self.killed = True
        self.returncode = -9

    def terminate(self):
        # _act_exec()'s own finally: block calls terminate() too --
        # harmless once already killed.
        if self.returncode is None:
            self.returncode = -15

    async def wait(self):
        while self.returncode is None:
            await asyncio.sleep(0.01)
        return self.returncode


class _FakeExecSession:
    """Minimal ui.session stand-in for _act_exec(): admin gate passes,
    read_raw() raises CarrierLost on its very first call (simulating an
    already-dropped connection), write() just records output."""

    def __init__(self):
        self.user = {'is_admin': True, 'id': 1, 'username': 'sysop'}
        self.writes = []

    async def write(self, text):
        self.writes.append(text)

    async def read_raw(self, n=64):
        from anetbbs.core.session import CarrierLost
        raise CarrierLost('client disconnected')


class _FakeUI:
    def __init__(self, session):
        self.session = session


class MenuEngineExecDisconnectKillsChildTests(unittest.TestCase):
    def test_disconnect_during_exec_kills_the_child_process(self):
        from anetbbs.features import menu_engine

        fake_proc = _FakeProc()

        async def _fake_create_subprocess_shell(*args, **kwargs):
            return fake_proc

        ui = _FakeUI(_FakeExecSession())

        async def _run():
            await asyncio.wait_for(menu_engine._act_exec(ui, 'irrelevant'),
                                   timeout=5)

        with patch('asyncio.create_subprocess_shell',
                  _fake_create_subprocess_shell):
            try:
                asyncio.run(_run())
            except asyncio.TimeoutError:
                self.fail(
                    "_act_exec() never returned -- the child process was "
                    "not killed after the simulated disconnect, so "
                    "proc.wait() blocked forever (the real bug this test "
                    "guards against)")

        self.assertTrue(fake_proc.killed,
                        'pump_in() must kill the child once it detects a '
                        'real disconnect (CarrierLost from read_raw())')


if __name__ == '__main__':
    unittest.main()
