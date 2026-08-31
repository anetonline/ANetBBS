"""Regression test for a real High-severity finding from a security/
performance audit (2026-08-31): launch_door_game()'s parent-side
post-fork path had a bare `gs.pid = pid; db.session.commit()` -- unlike
every EARLIER failure path in this same function (build-command
failure, PTY-open failure, fork failure), which is wrapped in try/
except with a real gs.status='crashed' + release_node() cleanup.

A transient DB commit failure here (SQLite "database is locked" under
concurrent write load) would propagate out of the function AFTER the
child was already execvp()'d and running: DoorSession is never
created, _sessions[gs.id] is never set, the waitpid watcher never
starts, master_fd is never closed -- an untracked, never-reaped child
process plus a leaked fd, bounded only by node_manager.py's 1-hour
stale-session backstop.

launch_door_game() itself isn't directly unit-tested anywhere in this
codebase (no existing test forks a real child through it -- too heavy/
fragile for a unit test, matching the restraint this whole test suite
already shows around anything that calls os.fork()). This test follows
the same source-inspection precedent already established for a
similarly deep, hard-to-isolate fix in this codebase (see
tests/test_time_budget_enforcement.py's own
test_budget_task_is_cancelled_on_session_teardown): it confirms the
commit is now wrapped in try/except, AND that the code path immediately
after it (DoorSession construction, the actual object needed for
_sessions/waitpid-watcher publishing) is NOT nested inside that
except block -- i.e. a commit failure can no longer prevent the rest
of session-publishing from running.
"""
import inspect
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class DoorLaunchPostForkCommitResilienceTests(unittest.TestCase):
    def test_pid_persist_commit_is_wrapped_in_try_except(self):
        from anetbbs.games.door_runner import launch_door_game
        source = inspect.getsource(launch_door_game)
        self.assertIn('gs.pid = pid', source)
        idx = source.index('gs.pid = pid')
        # The commit() call and a try: guarding it must appear shortly
        # after gs.pid is set, in that order (generous window -- the
        # real fix has a long explanatory comment in between).
        nearby = source[idx:idx + 2000]
        self.assertIn('try:', nearby)
        self.assertIn('db.session.commit()', nearby)
        self.assertIn('except', nearby)

        # The except block guarding this specific commit() must NOT
        # `return` -- that's exactly the bug: an early return here
        # would abort session-publishing (DoorSession/_sessions/waitpid
        # watcher) after the child was already execvp()'d and running.
        # Isolate just that except block's body via AST rather than
        # fragile string/indentation slicing.
        import ast
        tree = ast.parse(inspect.getsource(launch_door_game))
        found_commit_handler = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Try):
                if not any('db.session.commit()' in ast.unparse(stmt)
                          for stmt in node.body):
                    continue
                found_commit_handler = True
                for handler in node.handlers:
                    handler_src = ast.unparse(handler)
                    self.assertNotIn(
                        'return', handler_src,
                        'the except block around this commit() must not '
                        'return early -- session-publishing (DoorSession, '
                        '_sessions, the waitpid watcher) must still run '
                        'even when the commit fails')
        self.assertTrue(found_commit_handler,
                        'expected to find a try/except whose try body '
                        'contains db.session.commit() (the gs.pid persist)')

        # DoorSession construction must still appear later in the
        # function body (i.e. genuinely reachable, not dead code after
        # an early return this test already ruled out above).
        self.assertIn('DoorSession(', source[idx:])


if __name__ == '__main__':
    unittest.main()
