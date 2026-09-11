"""Regression test for a real IDOR found in a security/performance
audit: the /game SocketIO namespace's `game_input`/`game_resize`
handlers (anetbbs/web/games.py) took an attacker-supplied
`session_id` straight from the client with NO ownership check
whatsoever. door_runner.send_input()/resize_terminal() are themselves
just a raw `session_id -> PTY` dict lookup with no authorization of
their own, so ANY authenticated user could inject keystrokes into, or
resize, ANY OTHER user's live door-game session simply by guessing or
incrementing `session_id` -- a small sequential integer
(GameSession.id), trivially enumerable.

Fixed by checking the client-supplied session_id against
`_socket_to_session[request.sid]`, the same sid -> session_id mapping
`handle_start_game()` already maintains for disconnect cleanup -- a
socket can only ever drive the door session IT started.

This test calls the socketio event handlers directly (no real
socketio transport needed) with a real Flask request context so
`current_user`/`request.sid` resolve exactly as they would for a real
socket event, matching the "call the handler function directly"
pattern already used by this suite's other socketio-adjacent tests.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class WebGameInputIdorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.web_game_input_idor_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User, Game, GameSession
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()
            owner = User(username='idor_owner', email='idor_owner@example.com',
                        is_active=True)
            owner.set_password('x')
            attacker = User(username='idor_attacker',
                            email='idor_attacker@example.com', is_active=True)
            attacker.set_password('x')
            db.session.add_all([owner, attacker])
            db.session.commit()

            game = Game(name='IDOR Test Door', slug='idor-test-door',
                       game_type='door_native', max_nodes=4)
            db.session.add(game)
            db.session.commit()

            victim_session = GameSession(game_id=game.id, user_id=owner.id,
                                         node_number=1, status='active')
            db.session.add(victim_session)
            db.session.commit()

            cls.owner_id = owner.id
            cls.attacker_id = attacker.id
            cls.victim_session_id = victim_session.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _call_as(self, user_id, sid, fn, payload):
        """Invoke a socketio event handler with a real Flask request
        context (so current_user resolves via flask_login) and a faked
        `request.sid` (the attribute flask-socketio itself stamps onto
        the request object for real socket events)."""
        from flask import request as flask_request
        from flask_login import login_user
        from anetbbs.models import User

        with self.app.test_request_context('/'):
            user = User.query.get(user_id)
            login_user(user)
            flask_request.sid = sid
            fn(payload)

    def test_game_input_blocked_when_session_not_owned_by_this_socket(self):
        from anetbbs.web import games as games_mod

        games_mod._socket_to_session.clear()
        # Attacker's socket never started (or owns) the victim's session.
        with patch('anetbbs.games.door_runner.send_input') as mock_send:
            self._call_as(
                self.attacker_id, 'attacker-sid', games_mod.handle_game_input,
                {'session_id': self.victim_session_id, 'input': 'hax0r'})
        mock_send.assert_not_called()

    def test_game_resize_blocked_when_session_not_owned_by_this_socket(self):
        from anetbbs.web import games as games_mod

        games_mod._socket_to_session.clear()
        with patch('anetbbs.games.door_runner.resize_terminal') as mock_resize:
            self._call_as(
                self.attacker_id, 'attacker-sid', games_mod.handle_game_resize,
                {'session_id': self.victim_session_id, 'rows': 1, 'cols': 1})
        mock_resize.assert_not_called()

    def test_game_input_allowed_when_this_socket_owns_the_session(self):
        from anetbbs.web import games as games_mod

        games_mod._socket_to_session.clear()
        games_mod._socket_to_session['owner-sid'] = self.victim_session_id
        with patch('anetbbs.games.door_runner.send_input') as mock_send:
            self._call_as(
                self.owner_id, 'owner-sid', games_mod.handle_game_input,
                {'session_id': self.victim_session_id, 'input': 'hello'})
        mock_send.assert_called_once_with(self.victim_session_id, 'hello')

    def test_game_resize_allowed_when_this_socket_owns_the_session(self):
        from anetbbs.web import games as games_mod

        games_mod._socket_to_session.clear()
        games_mod._socket_to_session['owner-sid'] = self.victim_session_id
        with patch('anetbbs.games.door_runner.resize_terminal') as mock_resize:
            self._call_as(
                self.owner_id, 'owner-sid', games_mod.handle_game_resize,
                {'session_id': self.victim_session_id, 'rows': 40, 'cols': 100})
        mock_resize.assert_called_once_with(self.victim_session_id, 40, 100)


if __name__ == '__main__':
    unittest.main()
