"""Regression tests for the rlogin direct-door-launch feature (Jerry's
ask, 2026-09-01): "we can rlogin into a game server and pass username,
password, and xtrn= to go to a game directly... How hard would it be
to do the same for ANetBBS? ... if they do [pass xtrn=], it goes
straight to the game, and then when exiting the game the connection
hangs up, just like when I rlogin to my synchronet game server."

ANetBBS's own outbound rlogin_bridge.py already speaks this exact
convention (Synchronet-style reversed handshake fields: client-user-
name = PASSWORD, server-user-name = BBS username, terminal/speed =
'xtrn=<slug>/<speed>' for a direct launch) to Jerry's A-Net Game
Server -- this feature makes the INBOUND rlogin listener
(anetbbs/core/rlogin_server.py) understand the same convention, so
ANetBBS can act as a game-server target too.

Three layers tested:
  1. _parse_xtrn_slug() -- pure parsing of the terminal/speed field.
  2. RloginServer.handle_connection() -- wires the parsed password/slug
     into BBSSession's constructor (mirrors the existing
     test_rlogin_probe_logging.py prefill_username wiring test).
  3. BBSSession._launch_direct_door() -- resolves a slug to a Game row
     and launches it, or reports why it couldn't.
  4. BBSSession.start()'s own direct-door branch, verified structurally
     (AST) since start() is a large, monolithic async method with real
     network/DB I/O threaded all the way through -- same reasoning
     test_session_node_entry_not_clobbered.py already documents for
     other deep logic in this same method.
"""
import ast
import asyncio
import inspect
import os
import shutil
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.core.rlogin_server import RloginServer, _parse_xtrn_slug


class ParseXtrnSlugTests(unittest.TestCase):
    def test_xtrn_with_speed_suffix(self):
        self.assertEqual(_parse_xtrn_slug('xtrn=LORD408/57600'), 'LORD408')

    def test_bare_xtrn_no_speed_suffix(self):
        self.assertEqual(_parse_xtrn_slug('xtrn=lord'), 'lord')

    def test_case_insensitive_prefix(self):
        self.assertEqual(_parse_xtrn_slug('XTRN=lord/57600'), 'lord')

    def test_ordinary_terminal_type_returns_none(self):
        self.assertIsNone(_parse_xtrn_slug('xterm/57600'))
        self.assertIsNone(_parse_xtrn_slug('vt100/9600'))

    def test_empty_or_missing_field_returns_none(self):
        self.assertIsNone(_parse_xtrn_slug(''))
        self.assertIsNone(_parse_xtrn_slug(None))

    def test_xtrn_with_nothing_after_equals_returns_none(self):
        self.assertIsNone(_parse_xtrn_slug('xtrn=/57600'))
        self.assertIsNone(_parse_xtrn_slug('xtrn='))


class _FakeWriter:
    def __init__(self, peer=('127.0.0.1', 12345)):
        self._peer = peer
        self.written = bytearray()
        self._closing = False

    def get_extra_info(self, key):
        return self._peer if key == 'peername' else None

    def write(self, data):
        self.written += data

    async def drain(self):
        pass

    def is_closing(self):
        return self._closing

    def close(self):
        self._closing = True

    async def wait_closed(self):
        pass


class _RealClientReader:
    """A game-server-style handshake: client_user=PASSWORD,
    server_user=BBS username, terminal_speed=xtrn=<slug>/<speed>."""
    def __init__(self, payload=b'secretpass\x00stingray\x00xtrn=LORD408/57600\x00'):
        self._first = True
        self._payload = payload

    async def readexactly(self, n):
        if self._first:
            self._first = False
            return b'\x00'
        raise asyncio.IncompleteReadError(b'', n)

    async def read(self, n):
        if self._payload:
            chunk, self._payload = self._payload, b''
            return chunk
        return b''


class RloginServerHandshakeWiringTests(unittest.TestCase):
    def test_xtrn_handshake_passes_password_and_slug_through(self):
        server = RloginServer(config={})
        with patch('anetbbs.core.rlogin_server.BBSSession') as MockSession:
            MockSession.return_value.start = AsyncMock()
            asyncio.run(server.handle_connection(_RealClientReader(), _FakeWriter()))
        _, kwargs = MockSession.call_args
        self.assertEqual(kwargs.get('prefill_username'), 'stingray')
        self.assertEqual(kwargs.get('prefill_password'), 'secretpass')
        self.assertEqual(kwargs.get('direct_door_slug'), 'LORD408')

    def test_plain_terminal_type_yields_no_direct_door_slug(self):
        server = RloginServer(config={})
        payload = b'alice\x00bob\x00vt100/9600\x00'
        with patch('anetbbs.core.rlogin_server.BBSSession') as MockSession:
            MockSession.return_value.start = AsyncMock()
            asyncio.run(server.handle_connection(
                _RealClientReader(payload), _FakeWriter()))
        _, kwargs = MockSession.call_args
        self.assertEqual(kwargs.get('prefill_username'), 'bob')
        # A plain interactive rlogin client sends its own local OS
        # username here, not a BBS password -- still passed through
        # (login_screen() safely falls back to an interactive prompt
        # on a bad guess, the same as SSH already does), but there
        # must be no direct-door slug for an ordinary terminal type.
        self.assertEqual(kwargs.get('prefill_password'), 'alice')
        self.assertIsNone(kwargs.get('direct_door_slug'))


_DATA_DIR = Path(__file__).resolve().parents[1] / 'data'


def _snapshot_data_dir():
    if not _DATA_DIR.is_dir():
        return set()
    return set(_DATA_DIR.iterdir())


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


class LaunchDirectDoorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._data_dir_before = _snapshot_data_dir()
        import anetbbs.config as cfg_mod
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._orig_flask_env = os.environ.get('FLASK_ENV')

    @classmethod
    def tearDownClass(cls):
        import anetbbs.config as cfg_mod
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        if cls._orig_flask_env is None:
            os.environ.pop('FLASK_ENV', None)
        else:
            os.environ['FLASK_ENV'] = cls._orig_flask_env
        for entry in _snapshot_data_dir() - cls._data_dir_before:
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _fresh_app(str(Path(self._tmp.name) / 'a.db'))
        from anetbbs.models import db
        self._ctx = self.app.app_context()
        self._ctx.push()
        self.addCleanup(self._ctx.pop)
        db.create_all()

    def _make_session(self, user_access_level=10):
        from anetbbs.core.session import BBSSession
        session = BBSSession(reader=None, writer=_FakeWriter(), config={})
        session.user = {'id': 1, 'username': 'tester',
                        'access_level': user_access_level}
        session.games._launch = AsyncMock()
        written = []
        async def _write(text):
            written.append(text)
        session.write = _write
        session._written = written
        return session

    def test_unknown_slug_returns_false_and_reports_error(self):
        session = self._make_session()
        result = asyncio.run(session._launch_direct_door('NOSUCHGAME'))
        self.assertFalse(result)
        self.assertTrue(any('Unknown game code' in t for t in session._written))
        session.games._launch.assert_not_called()

    def test_inactive_game_is_treated_as_unknown(self):
        from anetbbs.models import db, Game
        db.session.add(Game(name='Retired Door', slug='RETIRED',
                            game_type='door_native', is_active=False))
        db.session.commit()
        session = self._make_session()
        result = asyncio.run(session._launch_direct_door('RETIRED'))
        self.assertFalse(result)
        session.games._launch.assert_not_called()

    def test_access_gated_game_is_refused_with_a_clear_message(self):
        from anetbbs.models import db, Game
        db.session.add(Game(name='VIP Door', slug='VIPDOOR',
                            game_type='door_native', is_active=True,
                            min_access_level=50))
        db.session.commit()
        session = self._make_session(user_access_level=10)
        result = asyncio.run(session._launch_direct_door('VIPDOOR'))
        self.assertFalse(result)
        self.assertTrue(any("don't have access" in t for t in session._written))
        session.games._launch.assert_not_called()

    def test_valid_accessible_game_is_launched_with_the_right_dict(self):
        from anetbbs.models import db, Game
        db.session.add(Game(name='Legend of the Red Dragon', slug='LORD408',
                            game_type='door_native', is_active=True,
                            min_access_level=0,
                            web_game_module='some.module:launch'))
        db.session.commit()
        session = self._make_session(user_access_level=10)
        result = asyncio.run(session._launch_direct_door('LORD408'))
        self.assertTrue(result)
        session.games._launch.assert_awaited_once()
        (game_dict,), _ = session.games._launch.call_args
        self.assertEqual(game_dict['name'], 'Legend of the Red Dragon')
        self.assertEqual(game_dict['game_type'], 'door_native')
        self.assertEqual(game_dict['web_game_module'], 'some.module:launch')

    def test_slug_lookup_is_case_sensitive_to_the_stored_slug(self):
        # Game.slug is stored/compared as-is (matching how it's created
        # via /admin/games/) -- this just documents current behavior so
        # a future change here is deliberate, not accidental.
        from anetbbs.models import db, Game
        db.session.add(Game(name='Case Door', slug='CaseDoor',
                            game_type='door_native', is_active=True))
        db.session.commit()
        session = self._make_session()
        result = asyncio.run(session._launch_direct_door('casedoor'))
        self.assertFalse(result)


class StartDirectDoorBranchStructureTests(unittest.TestCase):
    """start() is a large, monolithic async method with real network/DB
    I/O threaded all the way through it -- not practically drivable
    end-to-end in a unit test just to reach this one branch (same
    reasoning test_session_node_entry_not_clobbered.py already
    documents for other deep logic in this same method). Verified
    structurally instead."""

    def test_direct_door_branch_calls_helper_and_returns_on_success(self):
        from anetbbs.core.session import BBSSession
        source = textwrap.dedent(inspect.getsource(BBSSession.start))
        self.assertIn('self._launch_direct_door(self._direct_door_slug)', source)

        tree = ast.parse(source)
        found = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.If):
                continue
            test_src = ast.unparse(node.test)
            if test_src != 'self._direct_door_slug':
                continue
            found = True
            body_src = '\n'.join(ast.unparse(stmt) for stmt in node.body)
            self.assertIn('_launch_direct_door', body_src,
                          'the self._direct_door_slug branch must call '
                          '_launch_direct_door()')
            self.assertIn('return', body_src,
                          'a successful direct-door launch must return '
                          'from start() (skipping the normal menu) so the '
                          'connection hangs up when the door exits, '
                          'matching a real Synchronet game-server target')
        self.assertTrue(found, 'expected an `if self._direct_door_slug:` '
                                'branch in start()')

    def test_direct_door_branch_runs_after_presence_is_established(self):
        """The branch must come after `self.presence = presence` -- games.py's
        _launch() calls presence.set_page(...), which would crash with an
        AttributeError on a bare/unset self.presence if the branch ran too
        early (e.g. before multinode/presence setup)."""
        from anetbbs.core.session import BBSSession
        source = inspect.getsource(BBSSession.start)
        presence_idx = source.index('self.presence = presence')
        branch_idx = source.index('if self._direct_door_slug:')
        self.assertGreater(
            branch_idx, presence_idx,
            'the direct-door branch must run after self.presence is set, '
            'not before')


if __name__ == '__main__':
    unittest.main()
