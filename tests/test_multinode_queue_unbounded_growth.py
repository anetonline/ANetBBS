"""Regression test for a real security/performance-audit finding:
anetbbs.features.multinode.NodeEntry.queue used to be a plain
`asyncio.Queue()` with no maxsize -- genuinely unbounded. broadcast() /
whisper() / kick_node() all already wrap their put_nowait() calls in
`except asyncio.QueueFull: pass`, which only means anything against a
bounded queue -- against the old unbounded queue, put_nowait() can never
raise QueueFull, so that handling was dead code.

Per multinode.py's own docstring, a node that's connected but not
currently `listening` (in the chat UI) queues broadcasts silently
without draining them. Any other logged-in user chatting a lot during
that window grew this one idle node's queue without limit -- a
memory-growth vector tied to how long the idle session stays connected,
not to anything an admin/rate-limit could cap.
"""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.features import multinode


class MultinodeQueueBoundedTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        multinode._NODES.clear()

    def tearDown(self):
        multinode._NODES.clear()

    async def test_node_queue_has_a_bounded_maxsize(self):
        entry = multinode.acquire_slot({'username': 'alice'}, 'telnet', '1.2.3.4', 8)
        self.assertGreater(
            entry.queue.maxsize, 0,
            "NodeEntry.queue must have a positive maxsize -- an "
            "unbounded (maxsize=0) queue lets a chatty node grow "
            "another idle node's backlog without limit")

    async def test_flooding_broadcasts_to_a_non_listening_node_does_not_grow_forever(self):
        # sender ('alice') and a victim node ('bob') that never enters
        # chat -- exactly the "connected but not listening" case the
        # docstring describes as queuing broadcasts silently.
        multinode.acquire_slot({'username': 'alice'}, 'telnet', '1.2.3.4', 8)
        entry_b = multinode.acquire_slot({'username': 'bob'}, 'telnet', '5.6.7.8', 8)
        self.assertFalse(entry_b.listening)

        flood_count = multinode._MAX_QUEUED_MESSAGES + 200
        for i in range(flood_count):
            multinode.broadcast('alice', f'message {i}', kind='msg')

        self.assertLessEqual(
            entry_b.queue.qsize(), multinode._MAX_QUEUED_MESSAGES,
            "an idle node's queue must never exceed the configured cap, "
            "no matter how many messages another node broadcasts")


if __name__ == '__main__':
    unittest.main()
