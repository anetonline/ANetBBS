"""Regression tests for the Meadowlark Valley web-game migration
(anetbbs/web/meadowlark.py, anetbbs/models.py's MeadowlarkSave, the
anetbbs/games/web_games.py registry entry, and the /mlv-coop SocketIO
namespace).

Covers: the Game row auto-seeds correctly, the lobby lists it, the play
page renders the migrated template (and does NOT still have the
removed import/export buttons or the un-prefixed .modal/.toast classes
that would collide with Bootstrap), the per-user save API round-trips
and isolates slots between users, and the co-op room-relay handlers
(host/join/leave/state_sync/action) update the in-memory room registry
and emit the right events -- called directly (patched emit/join_room/
leave_room, manual request.sid) the same way test_door_output_queue.py
tests SocketIO handlers without a live connection.
"""
import os
import sys
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_DATA_DIR = Path(__file__).resolve().parents[1] / 'data'


def _snapshot_data_dir():
    if not _DATA_DIR.is_dir():
        return set()
    return set(_DATA_DIR.iterdir())


class MeadowlarkMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._data_dir_before = _snapshot_data_dir()

        import anetbbs.config as cfg_mod
        cls._dbfile = str(Path(__file__).resolve().parent / '.meadowlark_migration_test.db')
        for suffix in ('', '-wal', '-shm'):
            path = cls._dbfile + suffix
            if os.path.exists(path):
                os.remove(path)
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._dbfile}'

        from anetbbs.web_app import create_app, socketio
        from anetbbs.models import db, User

        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        cls.socketio = socketio

        with cls.app.app_context():
            db.create_all()
            for uname in ('mlv_alice', 'mlv_bob'):
                u = User(username=uname, email=f'{uname}@example.com', is_admin=False)
                u.set_password('password123')
                db.session.add(u)
            db.session.commit()

        # One /auth/login POST per user, reused (as a logged-in session)
        # across every test method below, instead of each test method
        # logging in its own fresh client -- the login rate limiter
        # (anetbbs/features/rate_limit.py's `_buckets`) is a process-wide
        # in-memory dict keyed by IP, never reset between test files, so
        # a full `pytest tests/` run accumulates login attempts across
        # every test file that hits /auth/login from the same 127.0.0.1
        # test-client IP. Minimizing this file's own login count keeps it
        # from being the straw that tips a shared, already-fragile bucket
        # over the "10 attempts / 5 min" threshold for whatever unrelated
        # test file happens to run after it.
        cls.alice = cls.app.test_client()
        cls.bob = cls.app.test_client()
        for client, uname in ((cls.alice, 'mlv_alice'), (cls.bob, 'mlv_bob')):
            resp = client.post('/auth/login', data={'username': uname, 'password': 'password123'}, follow_redirects=True)
            assert resp.status_code == 200, f'login failed for {uname}'

    @classmethod
    def tearDownClass(cls):
        import anetbbs.config as cfg_mod
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri

        for suffix in ('', '-wal', '-shm'):
            path = cls._dbfile + suffix
            if os.path.exists(path):
                os.remove(path)
        import shutil
        for entry in _snapshot_data_dir() - cls._data_dir_before:
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)

    def setUp(self):
        # Co-op room state is a module-level in-memory registry -- clear
        # it between tests so they don't see each other's rooms.
        from anetbbs.web import meadowlark as mlv_mod
        mlv_mod._rooms.clear()
        mlv_mod._sid_room.clear()

    # -- Game registry / lobby / template -----------------------------

    def test_game_row_seeded(self):
        from anetbbs.models import Game
        with self.app.app_context():
            game = Game.query.filter_by(slug='meadowlark-valley').first()
            self.assertIsNotNone(game)
            self.assertEqual(game.game_type, 'builtin_web')
            self.assertEqual(game.web_game_module, 'meadowlark_valley')
            self.assertEqual(game.category, 'strategy')

    def test_lobby_lists_meadowlark(self):
        resp = self.alice.get('/games/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('Meadowlark Valley', resp.get_data(as_text=True))

    def test_play_page_markup(self):
        resp = self.alice.get('/games/meadowlark-valley/play')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn('id="mlv-app"', body)
        self.assertIn('id="gameCanvas"', body)
        self.assertIn('js/meadowlark/main.js', body)
        self.assertIn('id="btnCoop"', body)
        # Import/export were removed as part of the migration (server-side
        # save replaces the localStorage-era file round-trip).
        self.assertNotIn('btnExport', body)
        self.assertNotIn('btnImport', body)
        # .modal/.toast (unprefixed) collide with real Bootstrap component
        # classes loaded site-wide by base.html -- must stay mlv-prefixed.
        self.assertIn('class="mlv-modal hidden"', body)
        self.assertIn('id="mlvToasts"', body)

    # -- Save API -------------------------------------------------------

    def test_save_api_round_trip_and_isolation(self):
        alice = self.alice
        bob = self.bob

        resp = alice.get('/games/meadowlark/saves')
        self.assertEqual(resp.status_code, 200)
        saves = resp.get_json()['saves']
        self.assertEqual(len(saves), 3)
        self.assertTrue(all(s['empty'] for s in saves))

        resp = alice.get('/games/meadowlark/state/1')
        self.assertEqual(resp.status_code, 404)

        fake_state = '{"grid":[],"cash":1500,"day":1,"season":0,"population":0,"happiness":70,"taxRate":9,"speed":1,"unlocked":[0],"milestonesSeen":[],"totalPopEver":0,"stormDaysLeft":0,"droughtDaysLeft":0,"history":[]}'
        resp = alice.post('/games/meadowlark/state/1', json={'state_json': fake_state, 'day': 5, 'population': 12, 'season': 0})
        self.assertEqual(resp.status_code, 200)

        resp = alice.get('/games/meadowlark/state/1')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()['state_json'], fake_state)

        resp = alice.get('/games/meadowlark/saves')
        slot1 = [s for s in resp.get_json()['saves'] if s['slot'] == 1][0]
        self.assertFalse(slot1['empty'])
        self.assertEqual(slot1['day'], 5)
        self.assertEqual(slot1['population'], 12)

        # Bob can't see Alice's slot 1 save.
        resp = bob.get('/games/meadowlark/state/1')
        self.assertEqual(resp.status_code, 404)

        resp = alice.delete('/games/meadowlark/state/1')
        self.assertEqual(resp.status_code, 200)
        resp = alice.get('/games/meadowlark/state/1')
        self.assertEqual(resp.status_code, 404)

    def test_save_api_rejects_bad_slot(self):
        resp = self.alice.get('/games/meadowlark/state/4')
        self.assertEqual(resp.status_code, 400)
        resp = self.alice.get('/games/meadowlark/state/0')
        self.assertEqual(resp.status_code, 400)

    # -- Co-op SocketIO handlers (direct-call pattern, no live socket;
    #    same approach as test_door_output_queue.py) --------------------

    def _call_handler(self, username, sid, fn, *args, **kwargs):
        """Run a /mlv-coop handler inside a real request/app context with
        current_user + request.sid set, capturing every emit() call as
        (event, args, kwargs) tuples in call order. join_room/leave_room
        are stubbed (they need a live SocketIO server-side session this
        test harness doesn't have) -- only the room-registry bookkeeping
        and emitted events are under test here."""
        from anetbbs.models import User
        from anetbbs.web import meadowlark as mlv_mod
        from flask_login import login_user
        from flask import request

        captured = []
        def fake_emit(event, *a, **kw):
            captured.append((event, a, kw))

        with self.app.test_request_context('/'):
            with self.app.app_context():
                u = User.query.filter_by(username=username).first()
                login_user(u)
                request.sid = sid
                request.namespace = '/mlv-coop'
                with patch('anetbbs.web.meadowlark.emit', fake_emit), \
                     patch('anetbbs.web.meadowlark.join_room'), \
                     patch('anetbbs.web.meadowlark.leave_room'):
                    fn(*args, **kwargs)
        return captured

    def test_host_then_join_then_action_relay(self):
        from anetbbs.web import meadowlark as mlv_mod

        hosted = self._call_handler('mlv_alice', 'sid-alice', mlv_mod.coop_host_room, {})
        self.assertEqual(len(hosted), 1)
        event, args, kwargs = hosted[0]
        self.assertEqual(event, 'room_hosted')
        code = args[0]['code']
        self.assertIn(code, mlv_mod._rooms)
        self.assertEqual(mlv_mod._rooms[code]['host_sid'], 'sid-alice')

        joined = self._call_handler('mlv_bob', 'sid-bob', mlv_mod.coop_join_room, {'code': code})
        events = [e for e, a, kw in joined]
        self.assertIn('room_joined', events)
        self.assertIn('member_joined', events)
        self.assertEqual(mlv_mod._rooms[code]['members']['sid-bob'], 'mlv_bob')

        # Bob (a member, not the host) relays a build action -- should be
        # accepted and relayed (not silently dropped).
        action_result = self._call_handler(
            'mlv_bob', 'sid-bob', mlv_mod.coop_action,
            {'code': code, 'type': 'place', 'payload': {'x': 5, 'y': 5, 'tool': 'road'}})
        self.assertEqual(len(action_result), 1)
        event, args, kwargs = action_result[0]
        self.assertEqual(event, 'action')
        self.assertEqual(args[0]['type'], 'place')
        self.assertEqual(kwargs.get('room'), code)
        self.assertFalse(kwargs.get('include_self', True))

    def test_state_sync_rejected_from_non_host(self):
        from anetbbs.web import meadowlark as mlv_mod

        self._call_handler('mlv_alice', 'sid-alice', mlv_mod.coop_host_room, {})
        code = next(iter(mlv_mod._rooms))
        self._call_handler('mlv_bob', 'sid-bob', mlv_mod.coop_join_room, {'code': code})

        # A guest (non-host) sending state_sync must be silently ignored --
        # otherwise any room member could overwrite everyone's town state.
        result = self._call_handler(
            'mlv_bob', 'sid-bob', mlv_mod.coop_state_sync,
            {'code': code, 'state_json': '{"cash":999999}'})
        self.assertEqual(result, [])

        # The real host's state_sync IS relayed.
        result = self._call_handler(
            'mlv_alice', 'sid-alice', mlv_mod.coop_state_sync,
            {'code': code, 'state_json': '{"cash":1500}'})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][0], 'state_sync')

    def test_join_nonexistent_room_errors(self):
        from anetbbs.web import meadowlark as mlv_mod

        result = self._call_handler('mlv_alice', 'sid-alice', mlv_mod.coop_join_room, {'code': 'NOPE1'})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][0], 'room_error')

    def test_host_leaving_tears_down_room(self):
        from anetbbs.web import meadowlark as mlv_mod

        self._call_handler('mlv_alice', 'sid-alice', mlv_mod.coop_host_room, {})
        code = next(iter(mlv_mod._rooms))
        self._call_handler('mlv_bob', 'sid-bob', mlv_mod.coop_join_room, {'code': code})

        result = self._call_handler('mlv_alice', 'sid-alice', mlv_mod.coop_leave_room)
        events = [e for e, a, kw in result]
        self.assertIn('host_left', events)
        self.assertNotIn(code, mlv_mod._rooms)


if __name__ == '__main__':
    unittest.main()
