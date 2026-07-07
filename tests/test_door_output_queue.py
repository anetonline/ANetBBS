"""Tests for the door-game output queue-marshaling fix in
anetbbs/web/games.py, built while investigating a known,
already-documented dead end: sixel image rendering has never worked
for Synchronet-compatible doors (DSR etc.) through the
gunicorn-spawned PTY chain, even though the frontend
(xterm-addon-image, sixelSupport: true) has working sixel rendering
capability.

Root-cause research (via a Plan-agent pass reading eventlet's actual
source) found the PTY-reader-thread's direct socketio.emit() call is a
smaller risk than first assumed (eventlet.monkey_patch() already makes
threading.Thread cooperative), but it's still not the documented
Flask-SocketIO pattern for emitting from outside a request/socket
handler context, and marshaling through a proper queue + background
task is purely additive (same order, same content, no behavior change
for the text output that already works) -- so it ships regardless of
whether it turns out to be THE root cause of the sixel bug specifically
(a stronger, more specific hypothesis -- 8-bit C1 control codes getting
silently remapped by the cp437 decode -- needs a real DSR test session
to confirm/refute, which isn't reproducible in a sandbox).

These tests call anetbbs/web/games.py's real handle_start_game()
directly (not via a real SocketIO client connection -- Flask-SocketIO's
global-singleton architecture doesn't support a second create_app()
call cleanly within one process, which every other test file in this
suite also calls, making a real socketio.test_client() connection
fragile/order-dependent when run as part of the full suite; confirmed
directly -- a bare second create_app() call, even with identical
config, breaks the connect handshake). Instead: a real Flask request
context + flask_login.login_user() for authentication, request.sid/
request.namespace set directly (normally populated by a real SocketIO
connection), and `emit`/`socketio.emit`/`join_room` patched to capture
calls. This still exercises the real _emit_output/_drain_queue/out_q
closures inside handle_start_game, just without the fragile transport
layer underneath.
"""
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class DoorOutputQueueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.door_output_queue_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app, socketio
        from anetbbs.models import db
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.socketio = socketio
        with cls.app.app_context():
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _make_user_and_game(self, username, slug):
        from anetbbs.models import db, User, Game
        with self.app.app_context():
            u = User.query.filter_by(username=username).first()
            if not u:
                u = User(username=username, email=f'{username}@example.com')
                u.set_password('x')
                db.session.add(u)
            g = Game.query.filter_by(slug=slug).first()
            if not g:
                g = Game(name=slug, slug=slug, game_type='door_rlogin',
                         executable_path='doortest', is_active=True)
                db.session.add(g)
            db.session.commit()
            return u.id

    def _run_start_game(self, username, slug, fake_launch, sid='fake-sid'):
        """Call the real handle_start_game() directly inside a Flask
        request context, with SocketIO's transport-layer pieces
        (emit/join_room/request.sid) patched/stubbed rather than going
        through a real connection. Returns the list of
        (event_name, args, kwargs) tuples captured from every emit()
        call, in call order."""
        from anetbbs.models import User
        from anetbbs.web import games as games_mod
        from flask_login import login_user
        from flask import request

        captured = []
        def fake_emit(event, *args, **kwargs):
            captured.append((event, args, kwargs))

        with self.app.test_request_context('/'):
            with self.app.app_context():
                u = User.query.filter_by(username=username).first()
                login_user(u)
                request.sid = sid
                request.namespace = '/game'
                with patch('anetbbs.web.games.socketio.emit', fake_emit), \
                     patch('anetbbs.web.games.emit', fake_emit), \
                     patch('anetbbs.web.games.join_room'), \
                     patch('anetbbs.games.door_runner.launch_rlogin_session', fake_launch):
                    games_mod.handle_start_game({'game_slug': slug})
                    # Let the socketio.start_background_task() drain
                    # greenthread actually run -- it's cooperative, so
                    # this needs to yield, not just wall-clock sleep.
                    self.socketio.sleep(0.3)
                    time.sleep(0.1)
                    self.socketio.sleep(0.2)
        return captured

    def test_small_chunks_delivered_in_order(self):
        """Regression coverage for the already-working case -- a few
        small emit_fn() calls (typical menu/nav text output) must still
        arrive in the exact order they were sent."""
        self._make_user_and_game('queuetest1', 'queuetestgame1')
        chunks = [b'Welcome to the door!\r\n', b'Menu:\r\n', b'1) Play\r\n']

        def fake_launch(game, user, emit_fn, bbs_name='ANetBBS'):
            for c in chunks:
                emit_fn(c)
            return 12345

        captured = self._run_start_game('queuetest1', 'queuetestgame1', fake_launch)

        outputs = [args[0]['output'] for name, args, kw in captured
                  if name == 'game_output']
        joined = ''.join(outputs)
        expected = ''.join(c.decode('cp437') for c in chunks)
        self.assertEqual(joined, expected)

    def test_large_multichunk_payload_reassembles_completely(self):
        """A large payload split across many 4KB-ish reads (simulating
        what a real sixel image would look like on the wire) must
        arrive complete, in order, with nothing dropped."""
        self._make_user_and_game('queuetest2', 'queuetestgame2')
        chunk_size = 4096
        total_chunks = 13
        chunks = []
        for i in range(total_chunks):
            marker = f'[CHUNK{i:03d}]'.encode('ascii')
            pad = b'x' * (chunk_size - len(marker))
            chunks.append(marker + pad)

        def fake_launch(game, user, emit_fn, bbs_name='ANetBBS'):
            for c in chunks:
                emit_fn(c)
            return 54321

        captured = self._run_start_game('queuetest2', 'queuetestgame2', fake_launch)

        outputs = [args[0]['output'] for name, args, kw in captured
                  if name == 'game_output']
        joined = ''.join(outputs)
        expected = ''.join(c.decode('cp437') for c in chunks)
        self.assertEqual(len(joined), len(expected))
        self.assertEqual(joined, expected)
        positions = [joined.index(f'[CHUNK{i:03d}]') for i in range(total_chunks)]
        self.assertEqual(positions, sorted(positions))

    def test_pre_join_buffer_flushes_before_direct_emits(self):
        """Output emitted before join_room() runs (the launcher calls
        emit_fn before handle_start_game gets to call join_room) must
        still arrive -- pre-join buffering, now routed through the same
        queue instead of emitting directly, must not regress."""
        self._make_user_and_game('queuetest3', 'queuetestgame3')

        def fake_launch(game, user, emit_fn, bbs_name='ANetBBS'):
            emit_fn(b'early welcome banner\r\n')
            return 99999

        captured = self._run_start_game('queuetest3', 'queuetestgame3', fake_launch)

        outputs = [args[0]['output'] for name, args, kw in captured
                  if name == 'game_output']
        self.assertTrue(any('early welcome banner' in o for o in outputs))

    def test_sixel_shaped_chunk_logs_diagnostic(self):
        """Confirms the diagnostic added to help confirm/refute the
        cp437/C1 hypothesis actually fires on sixel-shaped input (both
        7-bit ESC-P and 8-bit \\x90 framing) and stays silent for
        normal text -- this is the one piece of this fix that a real
        DSR test session will actually need to produce useful output
        from."""
        self._make_user_and_game('queuetest4', 'queuetestgame4')

        def fake_launch(game, user, emit_fn, bbs_name='ANetBBS'):
            emit_fn(b'normal menu text, no sixel here\r\n')
            emit_fn(b'\x1bPq#0;2;0;0;0#0!100~-\x1b\\')  # 7-bit DCS framing
            emit_fn(b'\x90q#0;2;0;0;0#0!100~-\x9c')       # 8-bit C1 framing
            return 22222

        with self.assertLogs('anetbbs.web.games', level='DEBUG') as log_ctx:
            self._run_start_game('queuetest4', 'queuetestgame4', fake_launch)

        debug_lines = [m for m in log_ctx.output if 'possible sixel/DCS' in m]
        self.assertEqual(len(debug_lines), 2)


if __name__ == '__main__':
    unittest.main()
