"""Regression test for a merge-blocking gap flagged in PR #32's review:
password-change session revocation (`_start_kick_watchdog`'s auth_version
check, added in PR32) closes the terminal transport, but nothing confirmed
that closing the transport actually reaps an ACTIVE door-game subprocess --
on any transport, and especially not on SSH, where the close/EOF mechanism
(asyncssh channel, via _SshStreamReader/_SshStreamWriter) is a different
code path from the raw asyncio StreamReader/StreamWriter telnet and rlogin
share (confirmed by reading both: ssh_server.py wraps asyncssh's per-
direction process streams, main.py/rlogin_server.py hand BBSSession the
same raw asyncio reader/writer pair).

Both tests below exercise a REAL socket pair -- a real TCP loopback for the
telnet/rlogin case, a real local asyncssh server+client loopback for the SSH
case -- not stubbed reader/writer objects, so a regression in either
close/EOF mechanism actually fails this test instead of being hidden by a
mock. door_runner.play_door_game_telnet() is itself transport-agnostic
(named for telnet only for historical reasons -- it only ever touches
session.reader/session.writer/session.write/session.read_line, confirmed by
reading it), so the same door-launch stub technique already established by
test_door_session_disconnect_cleanup.py is reused for both transports here,
layered under the REAL `_start_kick_watchdog()` (not a hand-rolled stand-in
for it) so the actual PR32 code path is what's under test.
"""
import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncssh

import anetbbs.config as cfg_mod


def _fake_door_launch(fake_sid, compat_path):
    """launch_door_game() stand-in that registers a DoorSession kept in
    _sessions exactly like a real still-running door process -- same
    convention test_door_session_disconnect_cleanup.py already established.
    """
    from anetbbs.games.door_runner import DoorSession, _sessions, _sessions_lock

    def _fake_launch(game, user, emit_fn, bbs_name='ANetBBS',
                     minutes_remaining=60, window_size=None):
        ds = DoorSession(fake_sid, master_fd=-1, pid=999999999)
        ds.temp_files = [compat_path]
        with _sessions_lock:
            _sessions[fake_sid] = ds
        return fake_sid
    return _fake_launch


class DoorCleanupOnPasswordRevocationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent
                          / '.door_cleanup_revocation_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'
        os.environ['DATABASE_URL'] = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User, Game
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()
            user = User(username='doorrevoke', email='doorrevoke@example.com')
            user.set_password('original-password')
            user.auth_version = None
            db.session.add(user)
            game = Game(name='Test Door', slug='test-door-revoke',
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

    def _replace_password(self, password='replacement-password'):
        from anetbbs.models import User, db
        with self.app.app_context():
            user = db.session.get(User, self.user_id)
            user.set_password(password)
            db.session.commit()

    async def _run_door_vs_revocation(self, reader, writer):
        """Shared core: run a REAL door task plus the REAL kick watchdog
        concurrently against the given (real) reader/writer pair, flip
        auth_version mid-flight via a genuine DB commit, and confirm the
        door task actually reaps -- not just that the watchdog closed the
        writer (test_terminal_password_session_revocation.py already
        covers that half with a fake writer; this covers the half it
        can't: whether that close reliably unblocks the door's own
        pending reader.read(1))."""
        from anetbbs.games import door_runner
        from anetbbs.core.session import BBSSession
        from anetbbs.models import Game

        fake_sid = 999501 + id(reader) % 1000
        fake_compat = tempfile.NamedTemporaryFile(
            mode='w', suffix='_synchronet_compat.js', prefix='anetbbs_',
            delete=False)
        fake_compat.write('// test compat script')
        fake_compat.close()
        compat_path = fake_compat.name

        with self.app.app_context():
            game = Game.query.get(self.game_id)

        session = object.__new__(BBSSession)
        session.reader = reader
        session.writer = writer
        session.encoding = 'cp437'
        session.window_size = None
        session.write = AsyncMock()
        session.read_line = AsyncMock(return_value='')
        session.user = {'id': self.user_id, 'username': 'doorrevoke',
                        'auth_version': None}
        session._node_activity_id = None

        with patch.object(door_runner, '_build_command',
                          return_value=(['true'], '/tmp')), \
             patch.object(door_runner, 'launch_door_game',
                          _fake_door_launch(fake_sid, compat_path)):
            door_task = asyncio.ensure_future(door_runner.play_door_game_telnet(
                game, session.user, session, bbs_name='TestBBS',
                minutes_remaining=60))

            # Let the door actually start and its input pump start
            # blocking on reader.read(1) before anything changes.
            await asyncio.sleep(0.2)

            # Real _start_kick_watchdog() polls every 5s (no patching of
            # asyncio.sleep here -- anetbbs.core.session does a plain
            # `import asyncio`, so that module object IS the same global
            # asyncio module this test file also uses; patching its
            # .sleep would also slow down every await asyncio.sleep(...)
            # in this test's own code, including the ones below). The
            # password change happens immediately after starting the
            # watchdog, well before its first 5s sleep elapses, so the
            # watchdog's very first auth_version check already sees the
            # new value.
            with patch('anetbbs.features.bbs_ui._app', return_value=self.app):
                session._start_kick_watchdog()
                self._replace_password()

                try:
                    result = await asyncio.wait_for(door_task, timeout=10)
                except asyncio.TimeoutError:
                    door_task.cancel()
                    self.fail(
                        "play_door_game_telnet() did not reap the door "
                        "within 10s of a password-change revocation -- "
                        "closing the transport did not unblock the input "
                        "pump's reader.read(1) the way it does for a "
                        "plain dropped connection")

                session._kick_task.cancel()
                try:
                    await session._kick_task
                except (asyncio.CancelledError, Exception):
                    pass

        from anetbbs.games.door_runner import _sessions, _sessions_lock
        self.assertTrue(result)
        with _sessions_lock:
            self.assertNotIn(
                fake_sid, _sessions,
                'door session should have been reaped once the kick '
                'watchdog closed the transport')
        self.assertFalse(
            os.path.exists(compat_path),
            'the orphaned-door temp-file cleanup (DoorSession.close()) '
            'should still run on this revocation path too')

    def test_raw_stream_door_reaps_on_password_revocation(self):
        """Telnet/rlogin case: a real asyncio TCP loopback pair."""
        async def scenario():
            server_side = {}
            got_connection = asyncio.Event()

            async def handle(reader, writer):
                server_side['reader'] = reader
                server_side['writer'] = writer
                got_connection.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    pass

            server = await asyncio.start_server(handle, '127.0.0.1', 0)
            port = server.sockets[0].getsockname()[1]
            _client_reader, client_writer = await asyncio.open_connection(
                '127.0.0.1', port)
            await asyncio.wait_for(got_connection.wait(), timeout=3)

            try:
                await self._run_door_vs_revocation(
                    server_side['reader'], server_side['writer'])
            finally:
                client_writer.close()
                server.close()
                await server.wait_closed()

        asyncio.run(asyncio.wait_for(scenario(), timeout=25))

    def test_ssh_stream_door_reaps_on_password_revocation(self):
        """SSH case: a real local asyncssh server+client loopback, using
        the actual production _SshStreamReader/_SshStreamWriter adapters
        (ssh_server.py) -- the one genuinely different close/EOF mechanism
        the PR32 review flagged as unverified. Auth is disabled server-side
        (begin_auth returns False) so this test exercises the channel
        close/EOF mechanics directly, without needing to drive the full
        interactive BBS login screen first -- that UI flow is shared,
        already-tested, generic terminal code untouched by PR32."""
        async def scenario():
            from anetbbs.core.ssh_server import _SshStreamReader, _SshStreamWriter

            host_key = asyncssh.generate_private_key('ssh-ed25519')
            server_side = {}
            got_process = asyncio.Event()

            class _OpenServer(asyncssh.SSHServer):
                def begin_auth(self, username):
                    return False

            async def handle_process(process):
                peer = process.get_extra_info('peername')
                server_side['reader'] = _SshStreamReader(process.stdin)
                server_side['writer'] = _SshStreamWriter(process.stdout, peer)
                got_process.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    pass

            server = await asyncssh.create_server(
                _OpenServer, host='127.0.0.1', port=0,
                server_host_keys=[host_key],
                process_factory=handle_process,
                encoding=None)
            port = server.sockets[0].getsockname()[1]

            conn = await asyncssh.connect(
                '127.0.0.1', port, username='doorrevoke-ssh',
                known_hosts=None, encoding=None)
            client_process = await conn.create_process(encoding=None)
            await asyncio.wait_for(got_process.wait(), timeout=5)

            try:
                await self._run_door_vs_revocation(
                    server_side['reader'], server_side['writer'])
            finally:
                try:
                    client_process.close()
                except Exception:
                    pass
                try:
                    conn.close()
                except Exception:
                    pass
                server.close()
                await server.wait_closed()

        asyncio.run(asyncio.wait_for(scenario(), timeout=25))


if __name__ == '__main__':
    unittest.main()
