"""Regression test for a real live incident: a user's handle stayed
visible in an MRC room (and in data/mrc/sessions.json) days after they
had actually disconnected, surviving multiple bridge restarts in
between -- but who's-online (an entirely separate, unrelated system)
correctly showed them not logged into the BBS at all.

Root cause: mrc/bridge/db.py's BridgeDB loaded sessions.json straight
off disk with no liveness check at all, and none of main.py's
keepalive_loop/_rejoin_all_sessions/periodic userlist+stats refreshers
(nor _sessions_for_user()/_sessions_in_room(), used to decide who a PM
or room broadcast should reach) checked a session's ws_id against
self.websockets before treating it as real. A session mid-chat at the
exact moment the bridge process restarts (deploy, crash, `systemctl
restart`) never runs its graceful-disconnect cleanup, so it survives
in the file and gets re-announced to the upstream hub forever --
invisible locally (messages to a dead ws_id are silently dropped by
_send_to_session) but visibly "present" to every other BBS on the
network.

Two-part fix: (1) BridgeDB.discard_stale_sessions(), called once by
BridgeApp.__init__ (a real bridge process construction, not the many
test-only BridgeDB()/object.__new__(BridgeApp) constructions that
legitimately need to round-trip session data within one process) --
any session loaded from a PREVIOUS process's file can never be live in
this one. (2) BridgeApp._live_sessions(), a single chokepoint now used
by every session-iterating call site in main.py, as defense in depth
for any OTHER way a session's websocket could go away without its db
row being cleaned up in lockstep.
"""
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mrc.bridge.main import BridgeApp
from mrc.bridge.db import BridgeDB


def _run(coro):
    return asyncio.run(coro)


class _FakeWs:
    def __init__(self):
        self.sent = []

    async def send_json(self, obj):
        self.sent.append(obj)


class DiscardStaleSessionsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_discard_clears_in_memory_and_on_disk(self):
        db = BridgeDB(data_dir=self._tmp.name)
        _run(db.save_session_async('12345', {'handle': 'vanny', 'in_room': True, 'room': 'ddial'}))
        self.assertIsNotNone(db.get_session('12345'))

        db.discard_stale_sessions()
        self.assertEqual(db.list_sessions(), {})

        # Persisted, not just cleared in memory -- a fresh reload must
        # also come back empty.
        reloaded = BridgeDB(data_dir=self._tmp.name)
        self.assertEqual(reloaded.list_sessions(), {})

    def test_discard_is_a_no_op_when_nothing_was_ever_saved(self):
        db = BridgeDB(data_dir=self._tmp.name)
        db.discard_stale_sessions()  # must not raise, must not create sessions.json
        self.assertFalse(db.sessions_file.exists())

    def test_profiles_are_never_touched(self):
        db = BridgeDB(data_dir=self._tmp.name)
        _run(db.save_profile_async('vanny', {'password_hash': 'x'}))
        _run(db.save_session_async('12345', {'handle': 'vanny', 'in_room': True}))

        db.discard_stale_sessions()

        self.assertIsNotNone(db.get_profile('vanny'))
        self.assertEqual(db.list_sessions(), {})

    def test_ordinary_round_trip_within_one_process_is_unaffected(self):
        # discard_stale_sessions() is NOT called from BridgeDB.__init__
        # itself -- reconstructing a BridgeDB against the same data_dir
        # (the exact pattern tests elsewhere in this suite use to
        # verify save/delete round-trips) must still see real data.
        db = BridgeDB(data_dir=self._tmp.name)
        _run(db.save_session_async('999', {'handle': 'alice', 'in_room': True}))
        fresh = BridgeDB(data_dir=self._tmp.name)
        self.assertIsNotNone(fresh.get_session('999'))


class BridgeAppStartupReconciliationTests(unittest.TestCase):
    def test_real_bridgeapp_construction_discards_a_ghost_session_left_by_a_prior_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = str(Path(tmp) / 'data')
            # Simulate a PREVIOUS bridge process leaving a session
            # behind in sessions.json (e.g. it was mid-chat when the
            # process was killed/restarted).
            prior_process_db = BridgeDB(data_dir=data_dir)
            _run(prior_process_db.save_session_async(
                '424242', {'handle': 'vanny', 'in_room': True, 'room': 'ddial'}))

            cfg_path = Path(tmp) / 'config.json'
            cfg_path.write_text('{"mrc_host": "example.invalid", "mrc_port": 5000, '
                                 f'"bridge_bbs": "TestBBS", "data_dir": {data_dir!r}}}'
                                 .replace("'", '"'))

            app = BridgeApp(config_path=str(cfg_path))
            self.assertEqual(app.db.list_sessions(), {},
                             'a real BridgeApp() construction must discard whatever a '
                             'prior process left in sessions.json')


class LiveSessionsFilterTests(unittest.TestCase):
    """_live_sessions() as defense in depth: even without a process
    restart, a session whose ws_id has no live websocket must be
    excluded from every session-iterating operation."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = object.__new__(BridgeApp)
        self.app.config = {"bridge_bbs": "TestBBS"}
        self.app.db = BridgeDB(self._tmp.name)
        self.app.websockets = {}
        self.app.mrc_tcp_clients = {}
        self.app.mrc = AsyncMock()
        self.app.mrc.connected = True
        self.app.join_packet_delay_ms = 0

    def _seed(self, ws_id, handle, room='ddial', in_room=True, live=True):
        _run(self.app.db.save_session_async(
            str(ws_id), {'handle': handle, 'room': room, 'in_room': in_room}))
        if live:
            self.app.websockets[ws_id] = _FakeWs()

    def test_ghost_session_excluded_live_session_included(self):
        self._seed(1, 'vanny', live=False)   # ghost: no websocket
        self._seed(2, 'alice', live=True)    # real: has a websocket

        live = self.app._live_sessions()
        self.assertNotIn('1', live)
        self.assertIn('2', live)

    def test_sessions_for_user_ignores_ghost(self):
        self._seed(1, 'vanny', live=False)
        self.assertEqual(self.app._sessions_for_user('vanny'), set())

    def test_sessions_for_user_finds_live_match(self):
        self._seed(2, 'alice', live=True)
        self.assertEqual(self.app._sessions_for_user('alice'), {'2'})

    def test_sessions_in_room_ignores_ghost(self):
        self._seed(1, 'vanny', room='ddial', live=False)
        self.assertEqual(self.app._sessions_in_room('ddial'), set())

    def test_sessions_in_room_finds_live_match(self):
        self._seed(2, 'alice', room='ddial', live=True)
        self.assertEqual(self.app._sessions_in_room('ddial'), {'2'})

    def test_rooms_with_active_sessions_ignores_ghost(self):
        self._seed(1, 'vanny', room='ddial', live=False)
        self.assertEqual(self.app._rooms_with_active_sessions(), set())

    def test_rooms_with_active_sessions_finds_live_room(self):
        self._seed(2, 'alice', room='ddial', live=True)
        self.assertEqual(self.app._rooms_with_active_sessions(), {'ddial'})

    def test_keepalive_loop_never_announces_a_ghost(self):
        # A single iteration's worth of work, not the real infinite
        # loop -- exercise the body directly via _live_sessions() the
        # same way keepalive_loop() itself does.
        self._seed(1, 'vanny', room='ddial', live=False)
        self._seed(2, 'alice', room='ddial', live=True)

        announced = []
        for _, sess in self.app._live_sessions().items():
            if sess.get('in_room'):
                announced.append(self.app._session_effective_nick(sess))
        self.assertEqual(announced, ['alice'])


if __name__ == '__main__':
    unittest.main()
