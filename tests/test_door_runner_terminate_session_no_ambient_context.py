"""Regression test for a real, live-reported bug: terminate_session()
(and the _cleanup_session() it calls for the "owned by this process"
branch) assumed SOME Flask app context was already pushed by the
caller -- true for its web-admin-route caller (a real Flask request
always has one), but NOT for games/door_runner.py's own
play_door_game_telnet()'s idle-timeout/user-abort `finally:` block,
which calls terminate_session(sid) well after that function's own
`with transient_app_context(app):` (scoped only around the earlier
launch/validation section) has already exited.

Confirmed via a real operator's own captured server logs, 2026-09-16:
22 occurrences of "RuntimeError: Working outside of application
context" across both of terminate_session()'s own DB-access
branches --
  File "anetbbs/games/door_runner.py", line 2039, in _cleanup_session
    gs = GameSession.query.get(session_id)
and
  File "anetbbs/games/door_runner.py", line 2130, in terminate_session
    gs = GameSession.query.get(session_id)
-- both logged and swallowed by their own broad `except Exception`,
meaning the GameSession row was silently NEVER marked completed and
the node NEVER released for any door whose idle-timeout auto-abort
fired (a routine, expected occurrence once Game.idle_timeout_enabled
shipped, not a rare edge case).

The existing tests/test_door_runner_cross_process_terminate.py always
pushes a real app context in its own setUp() (self._ctx.push()) --
exactly why it never caught this: every real-world trigger of this bug
is specifically the case where NO app context is ambient at all.

Fixed by making terminate_session() itself has_app_context()-gated
(same pattern anetbbs/games/door_runner.py's own
_write_msgbase_area_modopts() already used elsewhere in this file):
reuse a real ambient context when one exists, build a throwaway one
only when genuinely needed.
"""
import os
import signal
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.test_door_games_menu_layout import _fresh_app  # noqa: E402


class TerminateSessionNoAmbientContextTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _fresh_app(str(Path(self._tmp.name) / 'a.db'))

        self._orig_flask_env = os.environ.get('FLASK_ENV')
        os.environ['FLASK_ENV'] = 'testing'
        self.addCleanup(self._restore_flask_env)

        # Seed data with a context pushed, matching the real incident's
        # own shape (a context existed during launch/setup) -- then POP
        # it before the actual terminate_session() call under test, so
        # that call genuinely has nothing ambient, exactly like
        # play_door_game_telnet()'s idle-timeout finally: block.
        with self.app.app_context():
            from anetbbs.models import db, Game, User, GameSession
            db.create_all()

            user = User(username='noambienttester', email='nat@example.com')
            user.set_password('x')
            db.session.add(user)
            db.session.flush()

            game = Game(name='No Ambient Context Test', slug='no-ambient-ctx-test',
                       game_type='door_native')
            db.session.add(game)
            db.session.flush()

            gs = GameSession(game_id=game.id, user_id=user.id, node_number=1,
                             status='active')
            db.session.add(gs)
            db.session.commit()
            self.session_id = gs.id
            self.game_id = game.id

    def _restore_flask_env(self):
        if self._orig_flask_env is None:
            os.environ.pop('FLASK_ENV', None)
        else:
            os.environ['FLASK_ENV'] = self._orig_flask_env

    def _assert_no_ambient_context(self):
        from flask import has_app_context
        self.assertFalse(
            has_app_context(),
            'test setup bug: an app context is still ambient -- this '
            'would hide the real bug, which only reproduces with none')

    def test_found_locally_branch_does_not_raise_with_no_ambient_context(self):
        """The exact branch and exact traceback confirmed live
        (_cleanup_session, door_runner.py:2039 in the report)."""
        from anetbbs.games import door_runner
        from anetbbs.games.door_runner import DoorSession, _sessions, _sessions_lock

        # Register this session as "owned by this process" (found_locally
        # branch) -- a minimal DoorSession stand-in, no real PTY needed
        # since .close() just needs to not blow up.
        # A clearly fake, non-existent pid -- matching
        # test_door_idle_timeout_enforcement.py's own established
        # convention. NEVER pass os.getpid() here: DoorSession.close()
        # (triggered by terminate_session() below) sends a REAL
        # os.killpg(self.pid, SIGTERM), followed by a background
        # thread's SIGKILL 2s later if the process is still alive --
        # passing this test process's own pid would SIGTERM/SIGKILL
        # the test runner itself.
        ds = DoorSession(self.session_id, master_fd=-1, pid=999999997)
        with _sessions_lock:
            _sessions[self.session_id] = ds
        self.addCleanup(lambda: _sessions.pop(self.session_id, None))

        self._assert_no_ambient_context()

        # Must not raise -- before the fix, this exact call sequence
        # threw "RuntimeError: Working outside of application context"
        # out of GameSession.query.get() inside _cleanup_session().
        door_runner.terminate_session(self.session_id)

        # And the real fix criterion: the DB actually got updated, not
        # just "didn't crash" -- before the fix the exception was
        # caught by _cleanup_session's own broad except and swallowed,
        # so the session stayed 'active' forever (the node never
        # released) even though nothing propagated to the caller.
        with self.app.app_context():
            from anetbbs.models import GameSession
            gs = GameSession.query.get(self.session_id)
            self.assertEqual(gs.status, 'completed',
                            'GameSession must actually be marked completed '
                            '-- the original bug silently left it active '
                            'forever, never releasing the node')

    def test_not_found_locally_branch_does_not_raise_with_no_ambient_context(self):
        """The second confirmed live traceback (terminate_session
        itself, door_runner.py:2130) -- the cross-process PID-kill
        branch, reusing test_door_runner_cross_process_terminate.py's
        own real-child-process technique."""
        child = subprocess.Popen(
            [sys.executable, '-c', 'import time; time.sleep(60)'],
            start_new_session=True)

        def _reap():
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass
            try:
                child.wait(timeout=5)
            except Exception:
                pass
        self.addCleanup(_reap)

        with self.app.app_context():
            from anetbbs.models import db, GameSession
            gs = GameSession.query.get(self.session_id)
            gs.pid = child.pid
            db.session.commit()

        self._assert_no_ambient_context()

        from anetbbs.games import door_runner
        with door_runner._sessions_lock:
            self.assertNotIn(self.session_id, door_runner._sessions)

        # Must not raise -- before the fix, GameSession.query.get()
        # inside terminate_session()'s own "not found locally" branch
        # threw the same RuntimeError.
        door_runner.terminate_session(self.session_id)

        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.fail('child process was not sent SIGTERM -- '
                     'terminate_session() must have raised before '
                     'reaching the killpg() call')
        self.assertIsNotNone(child.returncode)


if __name__ == '__main__':
    unittest.main()
