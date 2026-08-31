"""Regression test for a real Medium-severity finding from a security/
performance audit (2026-08-31): BBSSession.start()'s multinode slot-
acquisition block used to wrap acquire_slot() AND everything after it
(broadcast(), _open_node_activity()) in ONE try/except that reset
`self._node_entry = None` on ANY exception. broadcast() and
_open_node_activity() are both best-effort and not expected to raise,
but aren't guaranteed not to -- and if either did, the reset would
clobber an ALREADY-successfully-acquired node_entry back to None.
start()'s own finally: block only releases the multinode slot `if
self._node_entry is not None`, so that reset permanently leaks the
slot until process restart (multinode.py's _NODES dict has no
independent stale-session backstop the way node_manager.py's _active
does).

start() is a large, monolithic async method with real network/DB I/O
threaded all the way through it -- not practically drivable end-to-end
in a unit test just to reach this one block (same reasoning
test_presence_alerts.py and test_time_budget_enforcement.py's own
"cancelled on teardown" tests already document for similarly deep code
in this same method). This test instead verifies the actual fix
structurally via AST: the try/except that can reset
self._node_entry = None must contain ONLY the acquire_slot() call (not
broadcast()/_open_node_activity()), so nothing after a successful
acquisition can un-acquire it.
"""
import ast
import inspect
import sys
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class SessionNodeEntryNotClobberedTests(unittest.TestCase):
    def test_node_entry_reset_try_block_only_covers_acquire_slot(self):
        from anetbbs.core.session import BBSSession
        source = textwrap.dedent(inspect.getsource(BBSSession.start))
        tree = ast.parse(source)

        found_reset_handler = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            body_src = '\n'.join(ast.unparse(stmt) for stmt in node.body)
            if 'acquire_slot(' not in body_src:
                continue
            # Any Try whose body calls acquire_slot() and whose except
            # handler sets self._node_entry = None is the block this
            # fix is about.
            resets_node_entry = any(
                'self._node_entry = None' in ast.unparse(h)
                for h in node.handlers)
            if not resets_node_entry:
                continue
            found_reset_handler = True
            self.assertNotIn(
                'broadcast(', body_src,
                'broadcast() must not be inside the same try whose except '
                'resets self._node_entry -- a broadcast() exception must '
                'not be able to clobber an already-acquired node slot')
            self.assertNotIn(
                '_open_node_activity(', body_src,
                '_open_node_activity() must not be inside the same try '
                'whose except resets self._node_entry, for the same reason')

        self.assertTrue(
            found_reset_handler,
            'expected to find the try/except around acquire_slot() that '
            'resets self._node_entry = None on failure')

    def test_broadcast_and_open_node_activity_are_still_called(self):
        """Confirms the refactor didn't accidentally drop these calls
        entirely while narrowing the try block -- they must still run,
        just in their own separately-guarded try/except now."""
        from anetbbs.core.session import BBSSession
        source = inspect.getsource(BBSSession.start)
        self.assertIn('broadcast(', source)
        self.assertIn('self._open_node_activity(proto)', source)


if __name__ == '__main__':
    unittest.main()
