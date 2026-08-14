"""Unit tests for the door_played/door_exited and chat_entered/
chat_exited instrumentation added to games.py's GameManager._launch()
and mrc_chat.py's MRCChat._connect_and_chat() -- the finer-grained
half of the activity-log trail Jerry described ("played Lord, exited
Lord ... chatted in mrc for 2 hours"). Both call session._log_activity()
directly (no DB access needed here) so a simple recording fake is
enough to verify the right events fire at the right times, without
needing a real app context.
"""
import asyncio
import sys
import unittest
from unittest import mock


class _FakeActivityLog:
    def __init__(self):
        self.calls = []

    def _log_activity(self, activity_type, details=None):
        self.calls.append((activity_type, details))


class _FakeSession(_FakeActivityLog):
    def __init__(self):
        super().__init__()
        self.user = {'id': 1, 'username': 'tester'}

    async def write(self, text):
        pass

    async def read_line(self, prompt=''):
        return ''


class DoorActivityLogTests(unittest.TestCase):
    def test_builtin_door_logs_played_then_exited(self):
        from anetbbs.features.games import GameManager

        session = _FakeSession()
        gm = GameManager(session)

        async def _fake_launch(sess, username):
            # door_played must already be logged by the time the game
            # actually runs.
            self.assertEqual(session.calls[-1][0], 'door_played')

        fake_module = type(sys)('fake_activity_game_module')
        fake_module.launch = _fake_launch
        sys.modules['fake_activity_game_module'] = fake_module
        try:
            asyncio.run(gm._launch({
                'id': 1, 'name': 'Lord', 'game_type': 'builtin_python',
                'web_game_module': 'fake_activity_game_module',
            }))
        finally:
            del sys.modules['fake_activity_game_module']

        types = [c[0] for c in session.calls]
        self.assertEqual(types, ['door_played', 'door_exited'])
        self.assertIn('Lord', session.calls[0][1])
        self.assertIn('Lord', session.calls[1][1])

    def test_door_exited_logs_even_when_the_launcher_raises(self):
        """finally: must fire door_exited even on a crashed door, so the
        activity trail doesn't silently stop mid-session."""
        from anetbbs.features.games import GameManager

        session = _FakeSession()
        gm = GameManager(session)

        async def _raising_launch(sess, username):
            raise RuntimeError('boom')

        fake_module = type(sys)('fake_activity_game_module_2')
        fake_module.launch = _raising_launch
        sys.modules['fake_activity_game_module_2'] = fake_module
        try:
            asyncio.run(gm._launch({
                'id': 2, 'name': 'Tradewars', 'game_type': 'builtin_python',
                'web_game_module': 'fake_activity_game_module_2',
            }))
        finally:
            del sys.modules['fake_activity_game_module_2']

        types = [c[0] for c in session.calls]
        self.assertEqual(types, ['door_played', 'door_exited'])


class ChatActivityLogTests(unittest.TestCase):
    def test_failed_bridge_connect_logs_neither_event(self):
        """A connect failure never reaches the chat loop at all -- must
        not log a spurious chat_entered/chat_exited pair for a chat
        session that never actually happened."""
        import anetbbs.core  # noqa: F401  (resolves a circular import if mrc_chat is imported first)
        from anetbbs.features.mrc_chat import MRCChat

        session = _FakeSession()
        chat = MRCChat(session)

        class _FailingClientSession:
            def ws_connect(self, url, **kwargs):
                raise OSError('connection refused')

            async def close(self):
                pass

        async def _drive():
            with mock.patch('aiohttp.ClientSession', _FailingClientSession):
                await chat._connect_and_chat('ws://bad-host:1/mrcws', 'tester', 'lobby')

        asyncio.run(_drive())

        self.assertEqual(session.calls, [],
                         'a failed bridge connect must not log chat_entered/exited')


if __name__ == '__main__':
    unittest.main()
