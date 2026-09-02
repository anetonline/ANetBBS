"""Regression test for a real Medium finding from a security/
performance audit (2026-09-02): anetbbs/games/door_runner.py's
play_door_game_telnet() fed out_queue from the PTY-reader background
THREAD via loop.call_soon_threadsafe() -- there's no guarantee that
scheduled callback has actually run and delivered the door's last
chunk of output into out_queue (and from there into _output_pump()'s
own pending out_queue.get()) by the instant the outer polling loop
notices the door process has exited and reaches the finally: block.
The finally: block used to cancel out_task immediately with no drain
grace period -- the exact same bug shape just fixed in
menu_engine.py's _act_exec() (see
test_menu_engine_exec_admin_gate.py's
test_exec_output_is_not_dropped_when_the_last_stdout_read_is_still_
pending_at_exit), just never ported to this call site.

play_door_game_telnet() is a large function with real PTY/subprocess/
background-thread machinery threaded all the way through -- not
practically drivable end-to-end in a unit test just to force this
exact scheduling race (same reasoning
test_rlogin_direct_door_launch.py's StartDirectDoorBranchStructureTests
already documents for other hard-to-drive logic in this codebase).
Verified structurally instead: the finally: block must give out_task a
bounded grace window via asyncio.wait_for() BEFORE the cancellation
loop, catching the timeout rather than letting it propagate and skip
cancellation.
"""
import ast
import inspect
import sys
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.games.door_runner import play_door_game_telnet


class DoorRunnerOutputDrainBeforeCancelTests(unittest.TestCase):
    def test_out_task_is_given_a_grace_window_before_the_cancel_loop(self):
        source = textwrap.dedent(inspect.getsource(play_door_game_telnet))
        wait_for_idx = source.index("await asyncio.wait_for(out_task")
        cancel_loop_idx = source.index(
            "for t in (out_task, in_task):\n            if not t.done():\n                t.cancel()")
        self.assertLess(
            wait_for_idx, cancel_loop_idx,
            'out_task must be given a drain grace window via '
            'asyncio.wait_for() BEFORE the unconditional cancel loop, '
            'not after')

    def test_the_grace_wait_is_inside_the_finally_block(self):
        source = textwrap.dedent(inspect.getsource(play_door_game_telnet))
        tree = ast.parse(source)
        found_try = None
        for node in ast.walk(tree):
            if isinstance(node, ast.Try) and node.finalbody:
                final_src = '\n'.join(ast.unparse(s) for s in node.finalbody)
                if 'out_task' in final_src and 'cancel' in final_src:
                    found_try = node
                    break
        self.assertIsNotNone(found_try, 'expected a try/finally whose '
                             'finally: block cancels out_task')
        final_src = '\n'.join(ast.unparse(s) for s in found_try.finalbody)
        self.assertIn('wait_for', final_src,
                      'the finally: block must wait_for() out_task before '
                      'cancelling it')

    def test_a_timeout_from_the_grace_wait_is_caught_not_left_to_propagate(self):
        source = textwrap.dedent(inspect.getsource(play_door_game_telnet))
        # The wait_for(out_task, ...) call must be inside a try/except
        # that catches TimeoutError -- otherwise a real timeout (the
        # expected, normal case, since out_task has no natural
        # completion signal) would raise out of the finally: block and
        # skip the cancellation loop entirely, leaking the task.
        idx = source.index("await asyncio.wait_for(out_task")
        surrounding = source[max(0, idx - 200):idx + 400]
        self.assertIn('except', surrounding)
        self.assertIn('TimeoutError', surrounding)


if __name__ == '__main__':
    unittest.main()
