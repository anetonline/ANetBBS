"""Regression tests for local/multinode chat, added 2026-07-04.

Jerry reported: "in terminal mode - chat - option 1 is local chat...
you can only talk to yourself" -- logged onto 2 nodes and couldn't see
what the other session typed. Root cause: ChatManager.local_chat()
(anetbbs/features/chat.py) was a stub that only ever echoed the
sender's own message back to themselves -- it never called into
anetbbs/features/multinode.py's broadcast()/NodeEntry.queue machinery
at all, even though that machinery was already fully built and working
(just never wired into a default menu, under a *different*,
unreachable 'multinode' action_type). Fixed by extracting the working
chat loop into multinode.run_chat_session() and having both the
main-menu multinode action AND ChatManager.local_chat() call it.

These tests use fake session objects (no real BBSSession/asyncio
transport needed) since multinode.py's contract is just
`await session.write(text)` / `await session.read_line(prompt)`.
"""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.features import multinode


class FakeSession:
    def __init__(self, username, scripted_inputs, read_delay=0.01):
        self.user = {'username': username}
        self._node_entry = None
        self.written = []
        self._inputs = list(scripted_inputs)
        self.read_delay = read_delay

    async def write(self, text):
        self.written.append(text)

    async def read_line(self, prompt=''):
        # Yields control back to the event loop so the OTHER session's
        # pump task gets a chance to run before this one proceeds --
        # without this, a fake read_line that never awaits anything
        # would let one coroutine race straight through to /quit
        # before ever giving a broadcast a chance to be delivered.
        await asyncio.sleep(self.read_delay)
        if self._inputs:
            return self._inputs.pop(0)
        return '/q'


class MultinodeBroadcastTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        multinode._NODES.clear()

    def tearDown(self):
        multinode._NODES.clear()

    async def test_broadcast_reaches_the_other_nodes_queue(self):
        entry_a = multinode.acquire_slot({'username': 'alice'}, 'telnet', '1.2.3.4', 8)
        entry_b = multinode.acquire_slot({'username': 'bob'}, 'telnet', '5.6.7.8', 8)
        multinode.broadcast('alice', 'hello bob', kind='msg')

        msg = entry_b.queue.get_nowait()
        self.assertEqual(msg['from'], 'alice')
        self.assertEqual(msg['text'], 'hello bob')

    async def test_sender_does_not_receive_their_own_broadcast(self):
        entry_a = multinode.acquire_slot({'username': 'alice'}, 'telnet', '1.2.3.4', 8)
        multinode.acquire_slot({'username': 'bob'}, 'telnet', '5.6.7.8', 8)
        multinode.broadcast('alice', 'hello bob', kind='msg')
        self.assertTrue(entry_a.queue.empty())

    async def test_same_username_on_two_nodes_still_hears_each_other(self):
        # The actual bug Jerry hit: log the SAME account into two nodes
        # (a natural thing to do when quickly testing a feature) and
        # send a message from one. The old self-exclusion check
        # compared by username only, so it matched BOTH nodes (since
        # they share a username) and silently dropped the message for
        # everyone, not just the real sender. sender_slot fixes this by
        # identifying the specific sending node instead.
        entry_1 = multinode.acquire_slot({'username': 'admin'}, 'telnet', '1.1.1.1', 8)
        entry_2 = multinode.acquire_slot({'username': 'admin'}, 'telnet', '2.2.2.2', 8)
        multinode.broadcast('admin', 'hi other me', kind='msg', sender_slot=entry_1.slot)

        self.assertTrue(entry_1.queue.empty(), 'sender node should not get its own message')
        msg = entry_2.queue.get_nowait()
        self.assertEqual(msg['text'], 'hi other me')

    async def test_same_username_without_sender_slot_falls_back_to_old_behavior(self):
        # Documents the fallback: callers that don't pass sender_slot
        # (there are none left in this codebase, but the parameter is
        # optional) get the old, username-only exclusion -- which is
        # exactly the behavior that caused the bug for same-username
        # nodes. This test exists so the fallback's behavior is
        # intentional and visible, not a silent trap for a future caller.
        entry_1 = multinode.acquire_slot({'username': 'admin'}, 'telnet', '1.1.1.1', 8)
        entry_2 = multinode.acquire_slot({'username': 'admin'}, 'telnet', '2.2.2.2', 8)
        multinode.broadcast('admin', 'hi other me', kind='msg')
        self.assertTrue(entry_2.queue.empty())


class RunChatSessionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        multinode._NODES.clear()

    def tearDown(self):
        multinode._NODES.clear()

    async def test_a_message_typed_by_one_session_reaches_the_other(self):
        session_a = FakeSession('alice', ['hello bob'], read_delay=0.01)
        session_b = FakeSession('bob', [], read_delay=0.2)
        session_a._node_entry = multinode.acquire_slot(
            {'username': 'alice'}, 'telnet', '1.2.3.4', 8, session=session_a)
        session_b._node_entry = multinode.acquire_slot(
            {'username': 'bob'}, 'telnet', '5.6.7.8', 8, session=session_b)

        await asyncio.gather(
            multinode.run_chat_session(session_a),
            multinode.run_chat_session(session_b),
        )

        joined = ''.join(session_b.written)
        self.assertIn('hello bob', joined)
        self.assertIn('alice', joined)
        # session_a should NOT see its own message echoed via the
        # broadcast path (no self-talk-back-to-self loop)
        self.assertNotIn('<alice> hello bob', ''.join(session_a.written))

    async def test_same_username_on_two_nodes_reaches_the_other_end_to_end(self):
        # Reproduces Jerry's exact report: the same account logged onto
        # two nodes at once ("I logged on 2 nodes and could not see
        # what the other 'person' is typing"). This exercises the real
        # run_chat_session() path end to end, confirming sender_slot
        # actually gets threaded through correctly, not just tested at
        # the lower-level broadcast() unit.
        session_1 = FakeSession('admin', ['hi other me'], read_delay=0.01)
        session_2 = FakeSession('admin', [], read_delay=0.2)
        session_1._node_entry = multinode.acquire_slot(
            {'username': 'admin'}, 'telnet', '1.1.1.1', 8, session=session_1)
        session_2._node_entry = multinode.acquire_slot(
            {'username': 'admin'}, 'telnet', '2.2.2.2', 8, session=session_2)

        await asyncio.gather(
            multinode.run_chat_session(session_1),
            multinode.run_chat_session(session_2),
        )

        self.assertIn('hi other me', ''.join(session_2.written))

    async def test_list_command_shows_both_nodes(self):
        session_a = FakeSession('alice', ['/list'], read_delay=0.01)
        session_b = FakeSession('bob', [], read_delay=0.2)
        session_a._node_entry = multinode.acquire_slot(
            {'username': 'alice'}, 'telnet', '1.2.3.4', 8, session=session_a)
        session_b._node_entry = multinode.acquire_slot(
            {'username': 'bob'}, 'telnet', '5.6.7.8', 8, session=session_b)

        await asyncio.gather(
            multinode.run_chat_session(session_a),
            multinode.run_chat_session(session_b),
        )

        joined = ''.join(session_a.written)
        self.assertIn('alice', joined)
        self.assertIn('bob', joined)

    async def test_no_node_entry_shows_a_clear_message_instead_of_crashing(self):
        session = FakeSession('alice', [])
        session._node_entry = None
        await multinode.run_chat_session(session)
        self.assertIn('chat unavailable', ''.join(session.written))


class LocalChatDelegatesToRealBroadcastTests(unittest.IsolatedAsyncioTestCase):
    """Regression guard for the exact bug Jerry reported: ChatManager's
    "Local Chat" menu option used to be a stub that only echoed back to
    the sender. This confirms it now actually broadcasts."""

    def setUp(self):
        multinode._NODES.clear()

    def tearDown(self):
        multinode._NODES.clear()

    async def test_local_chat_broadcasts_between_two_sessions(self):
        # anetbbs.features.chat <-> anetbbs.core.session have a circular
        # import (session.py imports ChatManager for its own use);
        # importing anetbbs.core first resolves it the same way it's
        # worked around elsewhere in this project's tests.
        import anetbbs.core  # noqa: F401
        from anetbbs.features.chat import ChatManager

        session_a = FakeSession('alice', ['hello bob'], read_delay=0.01)
        session_b = FakeSession('bob', [], read_delay=0.2)
        session_a._node_entry = multinode.acquire_slot(
            {'username': 'alice'}, 'telnet', '1.2.3.4', 8, session=session_a)
        session_b._node_entry = multinode.acquire_slot(
            {'username': 'bob'}, 'telnet', '5.6.7.8', 8, session=session_b)

        await asyncio.gather(
            ChatManager(session_a).local_chat(),
            ChatManager(session_b).local_chat(),
        )

        self.assertIn('hello bob', ''.join(session_b.written))


if __name__ == '__main__':
    unittest.main()
