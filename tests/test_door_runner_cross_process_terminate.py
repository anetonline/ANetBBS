"""Regression test for a real gap found in a security/performance audit
of anetbbs/games/door_runner.py: terminate_session() only ever checked
THIS PROCESS's own in-memory `_sessions` dict.

ANetBBS runs the web (SocketIO), telnet, and SSH surfaces as separate
OS processes (deploy/anetbbs-web.service, deploy/anetbbs-telnet.service,
deploy/anetbbs-ssh.service) -- each with its own independent copy of
door_runner's module-level `_sessions` dict, matching the identical
cross-process split node_manager.py's own docstring already documents
for node counting. A door launched over telnet is invisible to
`_sessions` in the web process, and vice versa -- but every caller of
terminate_session() (the web admin "Disconnect" button, the sysop cfg
tool's games section, the terminal sysop-games menu) lists sessions
from the SHARED GameSession table, so a sysop routinely sees, and can
click "disconnect" on, a session that isn't running in their own
process.

Before the fix: door_session came back None from the local `_sessions`
lookup, so the actual kill silently no-opped -- but _cleanup_session()
still unconditionally flipped GameSession.status to 'completed' and
released the node, falsely reporting the session terminated while the
real subprocess kept running untracked and the freed node number
became available for a colliding new session.

This test reproduces the exact condition: a real child process (its
own process group, matching how launch_door_game()'s fork always
os.setsid()s the child) with its pid recorded on a GameSession row,
but with NO entry in door_runner._sessions (simulating "a different
process launched this door"). It verifies two things the fix
guarantees that the old code did not:

  1. The child process actually receives SIGTERM (proving the
     cross-process PID-targeted killpg() fallback fires).
  2. GameSession.status is NOT flipped to 'completed' by this call --
     that's left to the (simulated-absent) owning process's own
     waitpid watcher, exactly like any other disconnect path.
"""
import os
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.test_door_games_menu_layout import _fresh_app  # noqa: E402


class CrossProcessTerminateSessionTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _fresh_app(str(Path(self._tmp.name) / 'a.db'))

        self._orig_flask_env = os.environ.get('FLASK_ENV')
        os.environ['FLASK_ENV'] = 'testing'
        self.addCleanup(self._restore_flask_env)

        self._ctx = self.app.app_context()
        self._ctx.push()
        self.addCleanup(self._ctx.pop)

        from anetbbs.models import db, Game, User, GameSession
        db.create_all()

        user = User(username='tester', email='t@example.com')
        user.set_password('x')
        db.session.add(user)
        db.session.flush()

        game = Game(name='Cross Process Test', slug='cross-proc-test',
                    game_type='door_native')
        db.session.add(game)
        db.session.flush()

        # A real child process in its own process group -- same shape as
        # launch_door_game()'s forked child (os.setsid() in the child, so
        # pid == pgid, matching what os.killpg(gs.pid, ...) targets).
        self.child = subprocess.Popen(
            [sys.executable, '-c', 'import time; time.sleep(60)'],
            start_new_session=True)
        self.addCleanup(self._reap_child)

        gs = GameSession(game_id=game.id, user_id=user.id, node_number=1,
                         status='active', pid=self.child.pid)
        db.session.add(gs)
        db.session.commit()
        self.session_id = gs.id

    def _reap_child(self):
        try:
            os.killpg(self.child.pid, signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass
        try:
            self.child.wait(timeout=5)
        except Exception:
            pass

    def _restore_flask_env(self):
        if self._orig_flask_env is None:
            os.environ.pop('FLASK_ENV', None)
        else:
            os.environ['FLASK_ENV'] = self._orig_flask_env

    def test_cross_process_session_child_is_actually_killed(self):
        from anetbbs.games import door_runner
        # Sanity: this process never registered the session (simulates
        # it having been launched by a DIFFERENT OS process).
        with door_runner._sessions_lock:
            self.assertNotIn(self.session_id, door_runner._sessions)
        self.assertIsNone(self.child.poll(), 'child should still be alive before terminate_session')

        door_runner.terminate_session(self.session_id)

        try:
            self.child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.fail('child process was not sent SIGTERM by '
                     'terminate_session() for a session not owned by '
                     'this process')
        self.assertIsNotNone(self.child.returncode)

    def test_cross_process_session_does_not_falsely_mark_completed(self):
        """The owning process's own watcher is responsible for flipping
        GameSession status once it observes the SIGTERM'd exit -- doing
        it here too (as the old code did, unconditionally, regardless of
        whether the kill actually reached anything) would falsely report
        success and could release the node for reuse while the door's
        actual teardown (fds, temp files) hasn't happened anywhere."""
        from anetbbs.games import door_runner
        from anetbbs.models import GameSession

        door_runner.terminate_session(self.session_id)
        # Give the (never-started, in this test) owning-process watcher
        # no chance to interfere -- there isn't one. Status must still
        # read 'active' right after the call.
        gs = GameSession.query.get(self.session_id)
        self.assertEqual(gs.status, 'active')
        self.assertIsNone(gs.ended_at)


if __name__ == '__main__':
    unittest.main()
