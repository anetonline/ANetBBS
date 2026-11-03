"""Regression test for a real live incident: a door game (a sysop's own
sixel-tv/video-streaming door) locked the whole box at 100% CPU, the
sysop restarted anetbbs.service to recover, and the GameSession row for
that door session was left permanently stuck 'active' -- the admin
"Disconnect" button did nothing, and the session kept showing as
connected.

Root cause: terminate_session()'s cross-process fallback (the owning
process isn't this one, so it os.killpg()s the recorded pid directly)
caught ProcessLookupError and just `pass`ed, relying on comment-only
reasoning that "the owning process's own watcher will finish cleanup."
That's only true if an owning process still exists. A `systemctl
restart` kills the entire process group -- there is no owning process
left, no _pty_reader/_waitpid_watcher anywhere ever observes this
child's exit, and the GameSession row sat 'active' until
node_manager.cleanup_stale_sessions()'s 1-hour backstop (keyed off
started_at, not any liveness check) eventually swept it.

This test reproduces the exact condition: a GameSession row whose pid
is for a process that has already exited AND been reaped (guaranteed
ProcessLookupError on killpg). Fixed code must finish the orphaned
cleanup itself -- flip status to 'completed', set ended_at, and release
the node -- instead of leaving it active indefinitely.
"""
import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.test_door_games_menu_layout import _fresh_app  # noqa: E402


class TerminateSessionDeadPidOrphanCleanupTests(unittest.TestCase):
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

        game = Game(name='Dead Pid Test', slug='dead-pid-test',
                    game_type='door_native')
        db.session.add(game)
        db.session.flush()
        self.game_id = game.id

        # A real process that we fully wait() on, guaranteeing it's both
        # exited AND reaped -- its pid is genuinely gone, not a zombie,
        # matching what killpg() sees after a `systemctl restart` killed
        # the old anetbbs process (and everything it would have reaped)
        # out from under a still-running door child.
        dead = subprocess.Popen([sys.executable, '-c', 'pass'],
                                start_new_session=True)
        dead.wait(timeout=5)
        self.dead_pid = dead.pid

        gs = GameSession(game_id=game.id, user_id=user.id, node_number=1,
                         status='active', pid=self.dead_pid)
        db.session.add(gs)
        db.session.commit()
        self.session_id = gs.id

        # Directly populate the in-memory allocation table to match the
        # GameSession row above -- allocate_node() itself can't be used
        # here since it refuses a node already reflected as 'active' in
        # the DB (which this row now is), the same real precondition
        # launch_door_game() avoids by calling it BEFORE inserting the
        # GameSession row.
        from anetbbs.games import node_manager
        node_manager._active[(self.game_id, 1)] = self.session_id

    def _restore_flask_env(self):
        if self._orig_flask_env is None:
            os.environ.pop('FLASK_ENV', None)
        else:
            os.environ['FLASK_ENV'] = self._orig_flask_env

    def test_dead_pid_session_is_cleaned_up_not_left_active_forever(self):
        from anetbbs.games import door_runner
        from anetbbs.models import GameSession

        with door_runner._sessions_lock:
            self.assertNotIn(self.session_id, door_runner._sessions)

        door_runner.terminate_session(self.session_id)

        gs = GameSession.query.get(self.session_id)
        self.assertEqual(gs.status, 'completed',
                         "a GameSession whose pid is already dead must be "
                         "cleaned up directly by terminate_session() -- "
                         "there is no other process left that ever will")
        self.assertIsNotNone(gs.ended_at)

    def test_dead_pid_session_releases_its_node(self):
        from anetbbs.games import door_runner
        from anetbbs.games.node_manager import active_node_count

        self.assertEqual(active_node_count(self.game_id), 1)
        door_runner.terminate_session(self.session_id)
        self.assertEqual(active_node_count(self.game_id), 0)


if __name__ == '__main__':
    unittest.main()
