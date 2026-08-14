"""Regression test for a real gap found in a security audit:
DoorSession.close() (anetbbs/games/door_runner.py) was the ONLY
termination path for every door type except door_dos -- a single
SIGTERM, no confirmation, no SIGKILL fallback. A door process that
ignores/blocks SIGTERM survived indefinitely as an orphaned process
after a user aborted or disconnected -- the same class of resource
leak as the v1.0.21 production incident, just via processes instead
of a cached object.

Fixed by mirroring the proven pattern door_dos's own
_on_bridge_close() already used: killpg(SIGTERM) immediately, then a
background thread sends killpg(SIGKILL) 2 seconds later if the
process group still exists. Tested here against a REAL child process
that deliberately ignores SIGTERM, in its own process group (matching
the real setsid() launch_door_game() always does), proving it's
actually dead within the fallback window rather than orphaned forever.
"""
import os
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class DoorSessionSigkillFallbackTests(unittest.TestCase):
    def test_process_that_ignores_sigterm_is_sigkilled_within_the_fallback_window(self):
        from anetbbs.games.door_runner import DoorSession

        proc = subprocess.Popen(
            [sys.executable, '-c',
             'import signal, time; '
             'signal.signal(signal.SIGTERM, signal.SIG_IGN); '
             'time.sleep(30)'],
            start_new_session=True,  # real process-group leader, like launch_door_game()'s setsid()
        )
        self.addCleanup(lambda: proc.poll() is None and
                        os.killpg(proc.pid, signal.SIGKILL) if _pid_alive(proc.pid) else None)

        # Give the child a moment to actually install the SIG_IGN handler
        # before we test against it.
        time.sleep(0.3)
        self.assertTrue(_pid_alive(proc.pid), 'test child should be alive before close()')

        session = DoorSession(session_id=1, master_fd=-1, pid=proc.pid)
        session.close()

        # Immediately after close(), SIGTERM was sent but the child
        # ignores it -- must still be alive right away...
        self.assertTrue(_pid_alive(proc.pid),
                        'child ignores SIGTERM -- must still be alive immediately after close()')

        # ...but the background force-kill thread must SIGKILL it within
        # the ~2s fallback window. Poll with headroom rather than a bare
        # sleep(2) to avoid a flaky boundary-timing failure. Uses
        # proc.poll() (which reaps via waitpid(WNOHANG) under the hood),
        # not the raw _pid_alive() os.kill(pid, 0) check used above --
        # SIGKILL genuinely kills the child, but as OUR subprocess.Popen
        # child it becomes a zombie until we, its parent, reap it, and a
        # zombie still answers os.kill(pid, 0) as "existing".
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and proc.poll() is None:
            time.sleep(0.1)
        self.assertIsNotNone(proc.poll(),
                             'process that ignores SIGTERM must be SIGKILLed within the fallback window')

    def test_process_that_terminates_cleanly_on_sigterm_still_works(self):
        """Regression guard: killpg() instead of kill() (needed for the
        process-group SIGKILL sweep) must not break the normal, common
        case of a door that DOES respond to SIGTERM immediately."""
        from anetbbs.games.door_runner import DoorSession

        proc = subprocess.Popen(
            [sys.executable, '-c', 'import time; time.sleep(30)'],
            start_new_session=True,
        )
        time.sleep(0.2)
        self.assertTrue(_pid_alive(proc.pid))

        session = DoorSession(session_id=2, master_fd=-1, pid=proc.pid)
        session.close()

        proc.wait(timeout=2)  # default SIGTERM handling should exit promptly
        self.assertFalse(_pid_alive(proc.pid))


if __name__ == '__main__':
    unittest.main()
