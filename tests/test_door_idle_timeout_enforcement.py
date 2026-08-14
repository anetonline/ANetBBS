"""Regression test for a real gap found in a security audit:
play_door_game_telnet() (anetbbs/games/door_runner.py) tells the user
"{N}s of zero activity will auto-abort the door" for EVERY door type,
but the actual enforcement only ever existed for door_dos (its own TCP
bridge idle_timeout, tied to bytes on that specific socket). A hung
door_native/door_mystic/door_synchronet process that keeps the PTY
open with no more output, and a user who never types anything either,
previously ran forever with zero server-side enforcement.

Fixed by tracking last_activity (updated by either the output pump
receiving door output, or the input pump forwarding a real keystroke)
and checking it against DOOR_IDLE_TIMEOUT in the same polling loop
that already checks for abort/disconnect.

Uses the same launch_door_game-mocking technique already proven in
test_door_session_disconnect_cleanup.py, but with a fake session whose
reader NEVER returns (simulating a genuinely idle, still-connected
user) instead of one that returns immediately (simulating a dropped
connection) -- isolating idle-timeout as the only thing that can end
the loop.
"""
import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class _NeverActiveSession:
    """Fake BBSSession that's genuinely connected but produces no
    input at all -- reader.read() never returns within the test's
    lifetime, matching a real idle user (as opposed to
    test_door_session_disconnect_cleanup.py's _DisconnectedSession,
    whose read() returns b'' immediately to simulate a dropped
    connection)."""

    def __init__(self):
        self.written = []
        self.encoding = 'cp437'
        self.reader = self

    async def read(self, n):
        await asyncio.sleep(3600)
        return b''  # pragma: no cover -- never actually reached in the test

    async def write(self, data):
        self.written.append(data)

    async def read_line(self, prompt=''):
        if prompt:
            await self.write(prompt)
        return ''

    def transcript(self):
        return ''.join(self.written)


class DoorIdleTimeoutEnforcementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.door_idle_timeout_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User, Game
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()
            user = User(username='idletest', email='idletest@example.com',
                       password_hash='x', access_level=100, is_admin=True)
            db.session.add(user)
            game = Game(name='Idle Test Door', slug='idle-test-door',
                       game_type='door_synchronet')
            db.session.add(game)
            db.session.commit()
            cls.user_id = user.id
            cls.game_id = game.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def test_hung_door_with_idle_user_is_auto_aborted_within_timeout(self):
        from anetbbs.games import door_runner
        from anetbbs.games.door_runner import (
            play_door_game_telnet, DoorSession, _sessions, _sessions_lock)
        from anetbbs.models import Game

        fake_sid = 999002

        def _fake_launch(game, user, emit_fn, bbs_name='ANetBBS',
                         minutes_remaining=60):
            # A door that forked successfully, produces no more output,
            # and never exits on its own -- exactly the "hung door"
            # scenario the idle-timeout message is supposed to cover.
            ds = DoorSession(fake_sid, master_fd=-1, pid=999999998)
            with _sessions_lock:
                _sessions[fake_sid] = ds
            return fake_sid

        old_timeout_env = os.environ.get('DOOR_IDLE_TIMEOUT')
        os.environ['DOOR_IDLE_TIMEOUT'] = '1'  # keep the test fast
        try:
            with self.app.app_context():
                game = Game.query.get(self.game_id)
                session = _NeverActiveSession()

                with patch.object(door_runner, '_build_command',
                                  return_value=(['true'], '/tmp')), \
                     patch.object(door_runner, 'launch_door_game', _fake_launch), \
                     patch.object(door_runner, 'terminate_session') as mock_terminate:
                    try:
                        result = asyncio.run(asyncio.wait_for(
                            play_door_game_telnet(
                                game, {'id': self.user_id, 'username': 'idletest'},
                                session, bbs_name='TestBBS', minutes_remaining=60),
                            timeout=10))
                    except asyncio.TimeoutError:
                        self.fail(
                            'play_door_game_telnet() did not return within 10s of '
                            'a genuinely idle session with DOOR_IDLE_TIMEOUT=1 -- '
                            'idle-timeout enforcement has regressed (nothing is '
                            'stopping the loop for a hung door + idle user)')

            self.assertTrue(result)
            mock_terminate.assert_called_once_with(fake_sid)
            self.assertIn('auto-aborted', session.transcript(),
                          'user must be told the door was auto-aborted on idle timeout')
            self.assertIn('zero activity', session.transcript())
        finally:
            if old_timeout_env is None:
                os.environ.pop('DOOR_IDLE_TIMEOUT', None)
            else:
                os.environ['DOOR_IDLE_TIMEOUT'] = old_timeout_env
            with _sessions_lock:
                _sessions.pop(fake_sid, None)


if __name__ == '__main__':
    unittest.main()
