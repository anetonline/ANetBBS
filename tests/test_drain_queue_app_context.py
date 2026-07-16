"""Regression test for a real live bug found investigating a
"dosemu2 door games show a black screen over the web UI" report:
handle_start_game()'s _drain_queue() background task (anetbbs/web/
games.py) has no Flask request/app context of its own. Its idle-
timeout status check (out_q.get(timeout=5) raising queue.Empty, then
GameSession.query.get(sid) to see if the session is still active)
needs one -- and didn't have it.

The very first time a door produced no output for 5+ seconds (a
completely normal boot/loading pause -- dosemu2 in particular takes
long enough to boot that this is nearly guaranteed), that query raised
RuntimeError: Working outside of application context. The except
clause around it logged the error and `break`, treating a plain bug
as "the session must be gone" -- permanently ending _drain_queue for
the rest of that browser session. Everything the door rendered after
that point was still queued (out_q.put() from _emit_output kept
working fine) but never actually reached socketio.emit(), so the
browser never received it again: a stuck/black screen with the
backend still correctly running the door underneath the whole time.

This reproduces the exact failure path (a real 5+ second idle gap on
out_q) rather than mocking around the timeout, then confirms output
sent AFTER that idle gap still reaches the browser -- which the old
code could never do once the gap had already occurred once.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class DrainQueueAppContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.drain_queue_app_context_test.db')
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

    def test_output_after_a_real_five_second_idle_gap_still_reaches_the_browser(self):
        from anetbbs.models import db, User, Game, GameSession
        from anetbbs.web import games as games_mod
        from flask_login import login_user
        from flask import request

        username, slug = 'drainqtest1', 'drainqtestgame1'
        with self.app.app_context():
            u = User.query.filter_by(username=username).first()
            if not u:
                u = User(username=username, email=f'{username}@example.com')
                u.set_password('x')
                db.session.add(u)
                db.session.commit()
            g = Game.query.filter_by(slug=slug).first()
            if not g:
                g = Game(name=slug, slug=slug, game_type='door_rlogin',
                         executable_path='doortest', is_active=True)
                db.session.add(g)
                db.session.commit()
            # A real, active GameSession row -- _drain_queue's status
            # check must find this and see status='active' to know it
            # should keep draining, exactly like a real live game.
            gs = GameSession(id=88888, game_id=g.id, user_id=u.id,
                             node_number=1, status='active')
            db.session.merge(gs)
            db.session.commit()
            user_id = u.id

        captured = []
        def fake_emit(event, *args, **kwargs):
            captured.append((event, args, kwargs))

        def fake_launch(game, user, emit_fn, bbs_name='ANetBBS'):
            def _delayed_emit():
                # Past _drain_queue's 5s out_q.get(timeout=5) -- forces
                # the real idle-timeout status-check code path to run
                # for real, not simulated.
                self.socketio.sleep(6)
                emit_fn(b'output after the idle gap\r\n')
            self.socketio.start_background_task(_delayed_emit)
            return 88888

        with self.app.test_request_context('/'):
            with self.app.app_context():
                u = User.query.get(user_id)
                login_user(u)
                request.sid = 'fake-sid-drainq'
                request.namespace = '/game'
                with patch('anetbbs.web.games.socketio.emit', fake_emit), \
                     patch('anetbbs.web.games.emit', fake_emit), \
                     patch('anetbbs.web.games.join_room'), \
                     patch('anetbbs.games.door_runner.launch_rlogin_session', fake_launch):
                    games_mod.handle_start_game({'game_slug': slug})
                    # Long enough for the real 5s idle timeout AND the
                    # delayed 6s emit to both actually happen.
                    self.socketio.sleep(7.5)

        outputs = [args[0]['output'] for name, args, kw in captured
                  if name == 'game_output']
        joined = ''.join(outputs)
        self.assertIn('output after the idle gap', joined,
                      '_drain_queue must still be alive and emitting '
                      'after a real 5+ second idle gap on the queue -- '
                      'the old code silently killed the drain loop the '
                      'first time this ever happened, exactly matching '
                      'the live "black screen" report')


if __name__ == '__main__':
    unittest.main()
