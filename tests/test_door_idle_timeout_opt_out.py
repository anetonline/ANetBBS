"""Regression tests for Game.idle_timeout_enabled -- a real live report:
a sysop got auto-kicked out of an MRC-style chat door (uMRC) for simply
sitting idle reading/waiting, the exact behavior play_door_game_telnet's
idle-timeout enforcement (tests/test_door_idle_timeout_enforcement.py)
exists to apply to OTHER (non-chat) doors, but is actively wrong for a
door whose entire point is sitting there not typing anything.

Game.idle_timeout_enabled (new column, default True) lets a sysop opt
specific games OUT. Reuses the exact same launch_door_game-mocking /
_NeverActiveSession harness as
tests/test_door_idle_timeout_enforcement.py, but asserts the OPPOSITE
outcome when the flag is off: the door must NOT be auto-aborted even
after the idle threshold has clearly elapsed.
"""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class _NeverActiveSession:
    """Same fake session as test_door_idle_timeout_enforcement.py's own
    -- genuinely connected, produces no input at all."""

    def __init__(self):
        self.written = []
        self.encoding = 'cp437'
        self.reader = self

    async def read(self, n):
        await asyncio.sleep(3600)
        return b''  # pragma: no cover

    async def write(self, data):
        self.written.append(data)

    async def read_line(self, prompt=''):
        if prompt:
            await self.write(prompt)
        return ''

    def transcript(self):
        return ''.join(self.written)


class DoorIdleTimeoutOptOutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.door_idle_opt_out_test.db')
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
            user = User(username='idleoptouttest', email='iot@example.com',
                       password_hash='x', access_level=100, is_admin=True)
            db.session.add(user)
            chat_door = Game(name='uMRC-style Chat Door', slug='chat-door-test',
                             game_type='door_synchronet',
                             idle_timeout_enabled=False)
            normal_door = Game(name='Normal Door', slug='normal-door-test',
                               game_type='door_synchronet')
            db.session.add(chat_door)
            db.session.add(normal_door)
            db.session.commit()
            cls.user_id = user.id
            cls.chat_door_id = chat_door.id
            cls.normal_door_id = normal_door.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def test_new_games_default_to_idle_timeout_enabled(self):
        """The column default itself -- a game created without
        explicitly setting the flag (like every game before this
        column existed) must keep today's exact behavior."""
        from anetbbs.models import Game
        with self.app.app_context():
            g = Game.query.get(self.normal_door_id)
            self.assertTrue(g.idle_timeout_enabled)

    def test_launch_message_omits_auto_abort_line_when_disabled(self):
        """Fast, direct check of the message the launch banner shows --
        doesn't require waiting out any real idle timer."""
        from anetbbs.games import door_runner
        from anetbbs.games.door_runner import (
            play_door_game_telnet, DoorSession, _sessions, _sessions_lock)
        from anetbbs.models import Game

        fake_sid = 999101

        def _fake_launch(game, user, emit_fn, bbs_name='ANetBBS',
                         minutes_remaining=None, window_size=None):
            ds = DoorSession(fake_sid, master_fd=-1, pid=999999901)
            with _sessions_lock:
                _sessions[fake_sid] = ds
            return fake_sid

        with self.app.app_context():
            game = Game.query.get(self.chat_door_id)
            session = _NeverActiveSession()

            with patch.object(door_runner, '_build_command',
                              return_value=(['true'], '/tmp')), \
                 patch.object(door_runner, 'launch_door_game', _fake_launch), \
                 patch.object(door_runner, 'terminate_session'):
                async def _quick_abort():
                    # Don't actually run the door loop out -- just let
                    # the launch banner get written, then simulate a
                    # user-initiated abort (Ctrl+]q) so this returns
                    # immediately instead of needing a real timeout.
                    task = asyncio.ensure_future(play_door_game_telnet(
                        game, {'id': self.user_id, 'username': 'idleoptouttest'},
                        session, bbs_name='TestBBS'))
                    await asyncio.sleep(0.2)
                    with _sessions_lock:
                        _sessions.pop(fake_sid, None)
                    return await asyncio.wait_for(task, timeout=5)

                asyncio.run(_quick_abort())

        transcript = session.transcript()
        self.assertNotIn('zero activity will auto-abort', transcript)

    def test_disabled_door_is_not_auto_aborted_past_the_idle_threshold(self):
        """The real end-to-end proof: with DOOR_IDLE_TIMEOUT set very
        short, a normal door (flag on) gets auto-aborted well within
        that window (test_door_idle_timeout_enforcement.py already
        covers this) -- this test's door (flag off) must NOT, even
        after that same window has clearly elapsed."""
        from anetbbs.games import door_runner
        from anetbbs.games.door_runner import (
            play_door_game_telnet, DoorSession, _sessions, _sessions_lock)
        from anetbbs.models import Game

        fake_sid = 999102

        def _fake_launch(game, user, emit_fn, bbs_name='ANetBBS',
                         minutes_remaining=None, window_size=None):
            ds = DoorSession(fake_sid, master_fd=-1, pid=999999902)
            with _sessions_lock:
                _sessions[fake_sid] = ds
            return fake_sid

        old_timeout_env = os.environ.get('DOOR_IDLE_TIMEOUT')
        os.environ['DOOR_IDLE_TIMEOUT'] = '1'
        try:
            with self.app.app_context():
                game = Game.query.get(self.chat_door_id)
                session = _NeverActiveSession()

                with patch.object(door_runner, '_build_command',
                                  return_value=(['true'], '/tmp')), \
                     patch.object(door_runner, 'launch_door_game', _fake_launch), \
                     patch.object(door_runner, 'terminate_session'):
                    # A genuinely idle-timeout-disabled door must still
                    # be running well past the 1s DOOR_IDLE_TIMEOUT --
                    # asserting THAT it times out (the call never
                    # returns within 3s) is the actual proof here,
                    # inverting test_door_idle_timeout_enforcement.py's
                    # own assertion. (asyncio.wait_for's own timeout
                    # cancels the coroutine, which legitimately runs
                    # its normal cleanup/terminate_session path as part
                    # of that cancellation -- that's an artifact of
                    # this test's own harness, not evidence either way
                    # about idle-timeout enforcement, so it's not
                    # asserted on here.)
                    with self.assertRaises(asyncio.TimeoutError):
                        asyncio.run(asyncio.wait_for(
                            play_door_game_telnet(
                                game, {'id': self.user_id, 'username': 'idleoptouttest'},
                                session, bbs_name='TestBBS'),
                            timeout=3))
        finally:
            if old_timeout_env is None:
                os.environ.pop('DOOR_IDLE_TIMEOUT', None)
            else:
                os.environ['DOOR_IDLE_TIMEOUT'] = old_timeout_env
            with _sessions_lock:
                _sessions.pop(fake_sid, None)


if __name__ == '__main__':
    unittest.main()
