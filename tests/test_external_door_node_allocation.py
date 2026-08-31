"""Regression tests for node allocation in door_runner.py's
play_rlogin_telnet / play_telnet_terminal.

Real gap found in a security/performance audit: unlike every locally-
spawned door type (play_door_game_telnet, via launch_door_game) and
unlike their own socketio-side twins (launch_rlogin_session /
launch_telnet_session, which already allocated correctly), these two
terminal-session bridges never enforced Game.max_nodes at all -- an
unbounded number of concurrent bridges to the same external server
(e.g. a TWGS instance, or another BBS's rlogin door) could always be
opened from real telnet/SSH sessions no matter what a sysop configured
max_nodes to. Fixed via the shared _allocate_external_node /
_release_external_node helpers.

Uses a real local TCP server (not a mock) standing in for the remote
door server, and a real Flask/SQLite app for GameSession/node
tracking -- matching this project's usual preference for exercising
the real wire/DB behavior over mocking it.
"""
import asyncio
import os
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path

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


class _HoldOpenServer:
    """A minimal local TCP server that accepts a connection and then
    holds it open, sending/receiving nothing, until told to release
    it -- stands in for a remote door server that's still running a
    session. Used to prove a node stays allocated for as long as the
    bridge is actually connected, not just for an instant."""

    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(('127.0.0.1', 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self._release = threading.Event()
        self._conn = None
        self._accepted = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        try:
            conn, _ = self.sock.accept()
        except OSError:
            return
        self._conn = conn
        self._accepted.set()
        self._release.wait(10)
        try:
            conn.close()
        except OSError:
            pass

    def wait_for_connection(self, timeout=3.0):
        if not self._accepted.wait(timeout):
            raise AssertionError('remote server never got a connection')

    async def async_wait_for_connection(self, timeout=3.0):
        """Cooperative version of wait_for_connection() -- the plain
        blocking wait() above would freeze the WHOLE event loop if
        called from inside a coroutine, starving the very
        play_telnet_terminal task it's waiting on (ensure_future only
        schedules a task, it doesn't run it until the loop is given
        back control), so the connection this waits for would never
        actually happen."""
        deadline = asyncio.get_event_loop().time() + timeout
        while not self._accepted.is_set():
            if asyncio.get_event_loop().time() >= deadline:
                raise AssertionError('remote server never got a connection')
            await asyncio.sleep(0.02)

    def release(self):
        """Close the held connection so the bridge's read() sees EOF."""
        self._release.set()

    def close(self):
        self._release.set()
        try:
            self.sock.close()
        except OSError:
            pass


class _RefusingPort:
    """A bound-then-closed port -- guarantees ECONNREFUSED on connect,
    for testing the connect-failure cleanup path deterministically."""

    def __init__(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(('127.0.0.1', 0))
        self.port = s.getsockname()[1]
        s.close()  # nothing listening here now


class _BlockingReader:
    """Stands in for session.reader -- read() never returns until
    close() is called, simulating a user who hasn't pressed anything
    yet. Lets a test hold play_telnet_terminal/play_rlogin_telnet open
    long enough to observe the node as allocated."""

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

    async def write(self, text):
        self.written.append(text)

    async def read_line(self, prompt=''):
        self.read_line_calls.append(prompt)
        return ''


class ExternalDoorNodeAllocationTests(unittest.TestCase):
    def setUp(self):
        import anetbbs.config as cfg_mod
        self._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        self.addCleanup(
            lambda: setattr(cfg_mod.TestingConfig,
                            'SQLALCHEMY_DATABASE_URI', self._orig_db_uri))
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _fresh_app(str(Path(self._tmp.name) / 'ext_door_nodes.db'))
        with self.app.app_context():
            from anetbbs.models import db, User, Game
            u = User(username='doorplayer', email='doorplayer@example.com',
                    password_hash='x')
            db.session.add(u)
            db.session.commit()
            self.user = {'id': u.id, 'username': 'doorplayer',
                        'display_name': 'Door Player'}

        from anetbbs.games import node_manager
        node_manager._active.clear()

    def _make_game(self, max_nodes, port, game_type='door_telnet'):
        """Must be called from within an already-pushed self.app
        app_context() that stays alive for the rest of the test --
        Flask-SQLAlchemy's scoped session is torn down when an app
        context pops, which would otherwise leave the returned Game
        instance detached (DetachedInstanceError on next attribute
        access) the moment a short-lived context created just for
        this call exits."""
        from anetbbs.models import db, Game
        g = Game(name='Test External Door', slug=f'test-ext-{port}',
                game_type=game_type, executable_path=f'127.0.0.1:{port}',
                max_nodes=max_nodes)
        db.session.add(g)
        db.session.commit()
        return g

    def test_node_is_allocated_while_connected_and_released_on_disconnect(self):
        from anetbbs.games.door_runner import play_telnet_terminal
        from anetbbs.games import node_manager

        server = _HoldOpenServer()
        self.addCleanup(server.close)
        session = _FakeSession()

        async def _drive():
            game = self._make_game(max_nodes=1, port=server.port)
            game_id = game.id
            task = asyncio.ensure_future(
                play_telnet_terminal(game, self.user, session))
            await server.async_wait_for_connection()
            # Give play_telnet_terminal's own event loop a couple of
            # scheduling opportunities to reach node allocation +
            # _drain_stale_session_input before we inspect state.
            for _ in range(20):
                await asyncio.sleep(0.05)
                if (game_id, 1) in node_manager._active:
                    break

            self.assertIn((game_id, 1), node_manager._active,
                          'node must be allocated while the bridge is live')
            from anetbbs.models import GameSession
            gs_id = GameSession.query.filter_by(game_id=game_id).first().id
            self.assertEqual(GameSession.query.get(gs_id).status, 'active')

            # Now let the remote side close -- the bridge's read()
            # sees EOF and the function should return normally.
            server.release()
            session.reader.close()
            result = await asyncio.wait_for(task, timeout=5)
            return result, game_id, gs_id

        with self.app.app_context():
            result, game_id, gs_id = asyncio.run(_drive())

        self.assertTrue(result)
        self.assertNotIn((game_id, 1), node_manager._active,
                         'node must be released once the bridge ends')
        with self.app.app_context():
            from anetbbs.models import GameSession
            gs = GameSession.query.get(gs_id)
            self.assertEqual(gs.status, 'completed')
            self.assertIsNotNone(gs.ended_at)

    def test_second_session_is_refused_when_max_nodes_is_exhausted(self):
        from anetbbs.games.door_runner import play_telnet_terminal
        from anetbbs.games import node_manager

        server = _HoldOpenServer()
        self.addCleanup(server.close)
        session1 = _FakeSession()
        session2 = _FakeSession()

        async def _drive():
            game = self._make_game(max_nodes=1, port=server.port)
            game_id = game.id
            task1 = asyncio.ensure_future(
                play_telnet_terminal(game, self.user, session1))
            await server.async_wait_for_connection()
            for _ in range(20):
                await asyncio.sleep(0.05)
                if (game_id, 1) in node_manager._active:
                    break

            # A second session against the same (max_nodes=1) game
            # must be refused outright -- no connection attempt, no
            # node consumed.
            result2 = await play_telnet_terminal(game, self.user, session2)

            server.release()
            session1.reader.close()
            result1 = await asyncio.wait_for(task1, timeout=5)
            return result1, result2

        with self.app.app_context():
            result1, result2 = asyncio.run(_drive())

        self.assertTrue(result1)
        self.assertFalse(result2)
        joined = ''.join(session2.written)
        self.assertIn('nodes for this door are currently in use', joined)

    def test_connect_failure_releases_the_node_and_marks_the_session_crashed(self):
        from anetbbs.games.door_runner import play_telnet_terminal
        from anetbbs.games import node_manager

        refused = _RefusingPort()
        session = _FakeSession()
        session.reader.close()  # nothing to read; connect fails immediately anyway

        async def _drive():
            game = self._make_game(max_nodes=1, port=refused.port)
            game_id = game.id
            result = await play_telnet_terminal(game, self.user, session)
            return result, game_id

        with self.app.app_context():
            result, game_id = asyncio.run(_drive())

        self.assertFalse(result)
        self.assertNotIn((game_id, 1), node_manager._active,
                         'a failed connect must not leave the node held')
        with self.app.app_context():
            from anetbbs.models import GameSession
            gs = GameSession.query.filter_by(game_id=game_id).first()
            self.assertIsNotNone(gs)
            self.assertEqual(gs.status, 'crashed')

    def test_connect_failure_releases_the_node_even_when_the_client_is_already_gone(self):
        """Real gap found in a security/performance audit: the
        connect-failure branches in play_telnet_terminal/
        play_rlogin_telnet used to call
        `await session.read_line(...)` BEFORE `_release_external_node(
        ...)` -- if the client is already disconnected at that exact
        moment (the same network blip that broke the remote connect,
        or the user simply closing their own client while looking at
        the error), read_line() raises CarrierLost and the release
        call is never reached, leaking the node slot. The sibling test
        above (test_connect_failure_releases_the_node_and_marks_the_
        session_crashed) never caught this because its _FakeSession.
        read_line() always returns cleanly instead of raising -- this
        test uses a session whose read_line() raises CarrierLost, the
        real exception class core/session.py's own read_line() raises
        on a dead connection, to reproduce the actual failure mode."""
        from anetbbs.games.door_runner import play_telnet_terminal
        from anetbbs.games import node_manager
        from anetbbs.core.session import CarrierLost

        class _AlreadyGoneSession(_FakeSession):
            async def read_line(self, prompt=''):
                self.read_line_calls.append(prompt)
                raise CarrierLost('client already disconnected')

        refused = _RefusingPort()
        session = _AlreadyGoneSession()
        session.reader.close()

        async def _drive():
            game = self._make_game(max_nodes=1, port=refused.port)
            game_id = game.id
            with self.assertRaises(CarrierLost):
                await play_telnet_terminal(game, self.user, session)
            return game_id

        with self.app.app_context():
            game_id = asyncio.run(_drive())

        self.assertNotIn((game_id, 1), node_manager._active,
                         'the node must be released even though read_line() '
                         'raised -- release must happen BEFORE read_line(), '
                         'not after')
        with self.app.app_context():
            from anetbbs.models import GameSession
            gs = GameSession.query.filter_by(game_id=game_id).first()
            self.assertIsNotNone(gs)
            self.assertEqual(gs.status, 'crashed')


if __name__ == '__main__':
    unittest.main()
