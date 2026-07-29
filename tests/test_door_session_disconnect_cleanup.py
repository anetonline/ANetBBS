"""Regression test for a real leak found live: orphaned Synchronet-JS door
compat scripts (write_compat_script(), suffix _synchronet_compat.js in
anetbbs/games/synchronet_compat.py) piling up in /tmp on a production
install -- 11 files found, none ever cleaned up.

Root cause: play_door_game_telnet()'s outer polling loop only broke on
(a) the user pressing Ctrl+]q to abort, or (b) the door PROCESS itself
exiting (session removed from `_sessions` via PTY EOF). It never checked
whether the *user's connection* had dropped while the door process was
still running -- door_dos games get idle-timeout protection from their
TCP bridge, but Synchronet-JS doors (Node + PTY, no bridge) have no way
to learn the user is gone, so the door (and its DoorSession.temp_files,
including the compat script) just sat there forever.

Fix: check `in_task.done()` in the polling loop -- _input_pump() already
detects a dropped connection immediately (session.reader.read(1) raises
or returns empty), this just needed to be watched by the outer loop too.

This test simulates an instant disconnect (fake session.reader.read()
returns b'' right away) against a door session that's still "running"
(manually kept in `_sessions`, matching a door process that never exits
on its own) and asserts termination + temp-file cleanup happen promptly
-- not that the process eventually cleans up some other way. Wrapped in
asyncio.wait_for() with a hard timeout: if the fix regresses, the outer
loop goes back to polling `still_active` forever with nothing to ever
make it false, and this test must fail loudly instead of hanging the
suite (see feedback_test_fake_defaults_infinite_loop.md).
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


class _DisconnectedSession:
    """Fake BBSSession whose reader looks exactly like a dropped
    connection: the very first read() returns b'' (EOF), matching what
    asyncio's StreamReader does when the peer has closed the socket."""

    def __init__(self):
        self.written = []
        self.encoding = 'cp437'
        self.reader = self

    async def read(self, n):
        return b''

    async def write(self, data):
        self.written.append(data)

    async def read_line(self, prompt=''):
        if prompt:
            await self.write(prompt)
        return ''


class DoorSessionDisconnectCleanupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.door_disconnect_cleanup_test.db')
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
            user = User(username='doortest', email='doortest@example.com',
                       password_hash='x', access_level=100, is_admin=True)
            db.session.add(user)
            game = Game(name='Test Door', slug='test-door',
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

    def test_dropped_connection_terminates_session_and_cleans_up_temp_files(self):
        from anetbbs.games import door_runner
        from anetbbs.games.door_runner import (
            play_door_game_telnet, DoorSession, _sessions, _sessions_lock)
        from anetbbs.models import Game

        # A temp file standing in for write_compat_script()'s output --
        # the exact thing that was found orphaned live.
        fake_compat = tempfile.NamedTemporaryFile(
            mode='w', suffix='_synchronet_compat.js', prefix='anetbbs_',
            delete=False)
        fake_compat.write('// test compat script')
        fake_compat.close()
        compat_path = fake_compat.name
        self.assertTrue(os.path.exists(compat_path))

        fake_sid = 999001

        def _fake_launch(game, user, emit_fn, bbs_name='ANetBBS',
                         minutes_remaining=60):
            # Stands in for a door process that forked successfully and is
            # still running -- kept in `_sessions` exactly like the real
            # launch path does, with the leaking temp file attached.
            ds = DoorSession(fake_sid, master_fd=-1, pid=999999999)
            ds.temp_files = [compat_path]
            with _sessions_lock:
                _sessions[fake_sid] = ds
            return fake_sid

        with self.app.app_context():
            game = Game.query.get(self.game_id)
            session = _DisconnectedSession()

            with patch.object(door_runner, '_build_command',
                              return_value=(['true'], '/tmp')), \
                 patch.object(door_runner, 'launch_door_game', _fake_launch):
                try:
                    result = asyncio.run(asyncio.wait_for(
                        play_door_game_telnet(
                            game, {'id': self.user_id, 'username': 'doortest'},
                            session, bbs_name='TestBBS', minutes_remaining=60),
                        timeout=10))
                except asyncio.TimeoutError:
                    self.fail(
                        'play_door_game_telnet() did not return within 10s of '
                        'a dropped connection -- the disconnect-detection fix '
                        'has regressed (outer loop is polling forever with '
                        'nothing to ever make it stop)')

            self.assertTrue(result)
            with _sessions_lock:
                self.assertNotIn(
                    fake_sid, _sessions,
                    'session should have been removed from _sessions once '
                    'the dropped connection was detected')
            self.assertFalse(
                os.path.exists(compat_path),
                'the Synchronet compat temp file should have been unlinked '
                'by DoorSession.close() once the dropped connection was '
                'detected -- this is the exact leak found live')


if __name__ == '__main__':
    unittest.main()
