"""Regression test for a real live incident: when a door auto-aborts
for inactivity but the session's connection is ALSO already dead by
that point (confirmed live -- an operator's idle SSH session, its
underlying connection gone silent for 30+ minutes), the final
"[Door auto-aborted -- Ns of zero activity]" write can now raise
CarrierLost (see WRITE_DRAIN_TIMEOUT_SECONDS in core/session.py --
write() used to always silently swallow a stuck drain(), now raises
after a bounded timeout instead of hanging forever). That write sat
unwrapped in play_door_game_telnet(), so the CarrierLost escaped the
function entirely instead of being treated the same quiet way every
other post-loop teardown path here already handles a dead connection.

Reuses the exact fixture/mocking technique already proven in
tests/test_door_idle_timeout_enforcement.py (a hung door + a genuinely
idle-but-connected fake session), just with a session whose write()
raises CarrierLost specifically for the auto-abort message, matching
the real incident where the connection was gone by the time that
message was sent.
"""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class _NeverActiveSessionDeadOnAbortWrite:
    """Like test_door_idle_timeout_enforcement.py's _NeverActiveSession,
    but write() raises CarrierLost for any call whose text mentions
    the auto-abort message -- simulating a connection that's already
    dead by the time the idle-timeout teardown tries to tell the user
    about it."""

    def __init__(self):
        self.written = []
        self.encoding = 'cp437'
        self.reader = self

    async def read(self, n):
        await asyncio.sleep(3600)
        return b''  # pragma: no cover -- never actually reached

    async def write(self, data):
        from anetbbs.core.session import CarrierLost
        if 'auto-aborted' in data:
            raise CarrierLost('write drain timed out')
        self.written.append(data)

    async def read_line(self, prompt=''):
        if prompt:
            await self.write(prompt)
        return ''

    def transcript(self):
        return ''.join(self.written)


class DoorIdleAbortWriteCarrierLostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.door_idle_abort_carrier_lost_test.db')
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
            user = User(username='idleabortcltest', email='idleabortcltest@example.com',
                       password_hash='x', access_level=100, is_admin=True)
            db.session.add(user)
            game = Game(name='Idle Abort CL Test Door', slug='idle-abort-cl-test-door',
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

    def test_carrier_lost_on_auto_abort_write_does_not_propagate(self):
        from anetbbs.games import door_runner
        from anetbbs.games.door_runner import (
            play_door_game_telnet, DoorSession, _sessions, _sessions_lock)
        from anetbbs.models import Game

        fake_sid = 999003

        def _fake_launch(game, user, emit_fn, bbs_name='ANetBBS',
                         minutes_remaining=60, window_size=None):
            ds = DoorSession(fake_sid, master_fd=-1, pid=999999997)
            with _sessions_lock:
                _sessions[fake_sid] = ds
            return fake_sid

        old_timeout_env = os.environ.get('DOOR_IDLE_TIMEOUT')
        os.environ['DOOR_IDLE_TIMEOUT'] = '1'  # keep the test fast
        try:
            with self.app.app_context():
                game = Game.query.get(self.game_id)
                session = _NeverActiveSessionDeadOnAbortWrite()

                with patch.object(door_runner, '_build_command',
                                  return_value=(['true'], '/tmp')), \
                     patch.object(door_runner, 'launch_door_game', _fake_launch), \
                     patch.object(door_runner, 'terminate_session') as mock_terminate:
                    try:
                        result = asyncio.run(asyncio.wait_for(
                            play_door_game_telnet(
                                game, {'id': self.user_id, 'username': 'idleabortcltest'},
                                session, bbs_name='TestBBS', minutes_remaining=60),
                            timeout=10))
                    except asyncio.TimeoutError:
                        self.fail(
                            'play_door_game_telnet() did not return within 10s -- '
                            'a CarrierLost from the auto-abort write must not hang '
                            'or otherwise break the teardown path')
                    except Exception as exc:  # pylint: disable=broad-except
                        self.fail(
                            'CarrierLost from the auto-abort write must not '
                            f'propagate out of play_door_game_telnet(): {exc!r}')

            self.assertTrue(result)
            mock_terminate.assert_called_once_with(fake_sid)
        finally:
            if old_timeout_env is None:
                os.environ.pop('DOOR_IDLE_TIMEOUT', None)
            else:
                os.environ['DOOR_IDLE_TIMEOUT'] = old_timeout_env
            with _sessions_lock:
                _sessions.pop(fake_sid, None)


if __name__ == '__main__':
    unittest.main()
