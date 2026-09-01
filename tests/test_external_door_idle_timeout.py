"""Regression test for a real live freeze Jerry reported (2026-09-01):
"I was playing a door game on A-Net game server and it froze on the
ANetBBS side, I had to manually back out on the A-Net Game Server
side." Root cause: play_rlogin_telnet()'s and play_telnet_terminal()'s
own `_output_pump()` coroutines read from the remote socket with
`await reader.read(4096)` and NO TIMEOUT AT ALL -- if the remote
server went quiet without ever closing the TCP connection, that read
blocked forever, and since the surrounding
`asyncio.wait(..., FIRST_COMPLETED)` only unblocks once either pump
finishes, the whole bridged session sat frozen until something on the
REMOTE end closed the socket -- exactly what "backing out on the game
server side" did. Every other door-launch path in door_runner.py
already enforces DOOR_IDLE_TIMEOUT (see play_door_game_telnet /
launch_telnet_session); these two rlogin/telnet terminal-bridge paths
were the two gaps.

Uses the same _HoldOpenServer/_FakeSession harness as
test_external_door_node_allocation.py -- a real local TCP server that
accepts a connection and then holds it open, sending nothing, standing
in for a remote door that's gone silent without disconnecting.
"""
import asyncio
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _fresh_app(db_path):
    import anetbbs.config as cfg_mod
    if os.path.exists(db_path):
        os.remove(db_path)
    cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path}'
    os.environ['FLASK_ENV'] = 'testing'
    from anetbbs.web_app import create_app
    app = create_app('testing')
    app.config['TESTING'] = True
    return app


class _SilentHoldOpenServer:
    """Accepts a connection and holds it open forever -- reads whatever
    the bridge sends (the rlogin handshake, keystrokes) into a black
    hole, but never writes anything back and never closes. Simulates a
    remote door server that's hung but hasn't dropped the TCP
    connection -- exactly the scenario that froze ANetBBS live."""

    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(('127.0.0.1', 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self._accepted = threading.Event()
        self._stop = threading.Event()
        self._conn = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        try:
            conn, _ = self.sock.accept()
        except OSError:
            return
        self._conn = conn
        self._accepted.set()
        # Keep reading (and discarding) so the client's writes/drain()
        # calls never block on a full TCP send buffer -- this test is
        # specifically about the READ side going silent, not a second,
        # unrelated write-side stall.
        try:
            while not self._stop.is_set():
                data = conn.recv(4096)
                if not data:
                    break
        except OSError:
            pass

    async def async_wait_for_connection(self, timeout=3.0):
        deadline = asyncio.get_event_loop().time() + timeout
        while not self._accepted.is_set():
            if asyncio.get_event_loop().time() >= deadline:
                raise AssertionError('remote server never got a connection')
            await asyncio.sleep(0.02)

    def close(self):
        self._stop.set()
        try:
            if self._conn:
                self._conn.close()
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


class _BlockingReader:
    """Stands in for session.reader -- read() never returns until
    close() is called, simulating a user who isn't typing anything."""

    def __init__(self):
        self._closed = asyncio.Event()

    async def read(self, n=1):
        await self._closed.wait()
        return b''

    def close(self):
        self._closed.set()


class _FakeSession:
    def __init__(self):
        self.written = []
        self.reader = _BlockingReader()
        self.read_line_calls = []
        self.encoding = 'cp437'

    async def write(self, text):
        self.written.append(text)

    async def read_line(self, prompt=''):
        self.read_line_calls.append(prompt)
        return ''


class ExternalDoorIdleTimeoutTests(unittest.TestCase):
    def setUp(self):
        import anetbbs.config as cfg_mod
        self._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        self.addCleanup(
            lambda: setattr(cfg_mod.TestingConfig,
                            'SQLALCHEMY_DATABASE_URI', self._orig_db_uri))
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _fresh_app(str(Path(self._tmp.name) / 'idle_timeout.db'))
        with self.app.app_context():
            from anetbbs.models import db, User
            u = User(username='doorplayer', email='doorplayer2@example.com',
                    password_hash='x')
            db.session.add(u)
            db.session.commit()
            self.user = {'id': u.id, 'username': 'doorplayer',
                        'display_name': 'Door Player'}

        from anetbbs.games import node_manager
        node_manager._active.clear()

    def _make_rlogin_game(self, port):
        from anetbbs.models import db, Game
        g = Game(name='Test Rlogin Door', slug=f'test-rlogin-idle-{port}',
                game_type='door_rlogin', executable_path=f'127.0.0.1:{port}',
                command_line_args='@USER@ testpass', max_nodes=1)
        db.session.add(g)
        db.session.commit()
        return g

    def _make_telnet_game(self, port):
        from anetbbs.models import db, Game
        g = Game(name='Test Telnet Door', slug=f'test-telnet-idle-{port}',
                game_type='door_telnet', executable_path=f'127.0.0.1:{port}',
                max_nodes=1)
        db.session.add(g)
        db.session.commit()
        return g

    def test_rlogin_bridge_does_not_hang_forever_when_remote_goes_silent(self):
        from anetbbs.games.door_runner import play_rlogin_telnet
        from anetbbs.games import node_manager

        server = _SilentHoldOpenServer()
        self.addCleanup(server.close)
        session = _FakeSession()

        async def _drive():
            game = self._make_rlogin_game(server.port)
            game_id = game.id
            with patch.dict(os.environ, {'DOOR_IDLE_TIMEOUT': '1'}):
                task = asyncio.ensure_future(
                    play_rlogin_telnet(game, self.user, session))
                await server.async_wait_for_connection()
                # Must complete on its own within a small bound -- the
                # OLD behavior was to hang forever here (only test
                # timeout or the "remote" closing would ever end it).
                result = await asyncio.wait_for(task, timeout=5)
            return result, game_id

        start = time.monotonic()
        with self.app.app_context():
            result, game_id = asyncio.run(_drive())
        elapsed = time.monotonic() - start

        self.assertTrue(result)
        self.assertLess(elapsed, 4.5,
                        'the bridge must self-terminate on idle timeout, '
                        'not hang until the outer test timeout fires')
        joined = ''.join(session.written)
        self.assertIn('No data from the remote', joined)
        self.assertNotIn((game_id, 1), node_manager._active,
                         'the node must be released once the idle bridge ends')

    def test_telnet_bridge_does_not_hang_forever_when_remote_goes_silent(self):
        from anetbbs.games.door_runner import play_telnet_terminal
        from anetbbs.games import node_manager

        server = _SilentHoldOpenServer()
        self.addCleanup(server.close)
        session = _FakeSession()

        async def _drive():
            game = self._make_telnet_game(server.port)
            game_id = game.id
            with patch.dict(os.environ, {'DOOR_IDLE_TIMEOUT': '1'}):
                task = asyncio.ensure_future(
                    play_telnet_terminal(game, self.user, session))
                await server.async_wait_for_connection()
                result = await asyncio.wait_for(task, timeout=5)
            return result, game_id

        start = time.monotonic()
        with self.app.app_context():
            result, game_id = asyncio.run(_drive())
        elapsed = time.monotonic() - start

        self.assertTrue(result)
        self.assertLess(elapsed, 4.5)
        joined = ''.join(session.written)
        self.assertIn('No data from the remote', joined)
        self.assertNotIn((game_id, 1), node_manager._active)

    def test_rlogin_bridge_with_active_remote_does_not_idle_out_early(self):
        """Sanity check the fix doesn't make a perfectly healthy,
        actively-chatty connection time out early -- a remote that
        keeps sending data must NOT trigger the idle path."""
        from anetbbs.games.door_runner import play_rlogin_telnet

        server = _SilentHoldOpenServer()
        self.addCleanup(server.close)
        session = _FakeSession()

        async def _drive():
            game = self._make_rlogin_game(server.port)
            game_id = game.id
            with patch.dict(os.environ, {'DOOR_IDLE_TIMEOUT': '1'}):
                task = asyncio.ensure_future(
                    play_rlogin_telnet(game, self.user, session))
                await server.async_wait_for_connection()
                # Keep the connection "alive" by having the remote send
                # data faster than the idle timeout, well past when an
                # unfixed/bugged idle check would have fired.
                for _ in range(4):
                    await asyncio.sleep(0.4)
                    try:
                        server._conn.sendall(b'still here\r\n')
                    except OSError:
                        break
                self.assertFalse(task.done(),
                                 'an actively-chatty remote must not be '
                                 'treated as idle')
                session.reader.close()
                server.close()
                result = await asyncio.wait_for(task, timeout=5)
            return result, game_id

        with self.app.app_context():
            result, game_id = asyncio.run(_drive())
        self.assertTrue(result)
        joined = ''.join(session.written)
        self.assertNotIn('No data from the remote', joined)


if __name__ == '__main__':
    unittest.main()
