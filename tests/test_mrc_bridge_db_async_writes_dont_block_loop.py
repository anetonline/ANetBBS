"""Regression test for a real HIGH-severity finding from a security/
performance audit (2026-08-31): mrc/bridge/main.py's aiohttp WebSocket
handlers called BridgeDB's save_profile()/save_session()/
delete_session() directly -- synchronous, blocking disk I/O executed
right on the asyncio event loop. Every write is also a FULL rewrite of
the entire profiles.json/sessions.json file (cost grows with the total
number of stored profiles/sessions, not just the one being changed),
so a blocking write on a long-running bridge stalls EVERY other
concurrently-connected MRC client for a duration that only grows the
longer the bridge has been running.

Fixed by adding async, executor-offloaded counterparts
(save_profile_async/save_session_async/delete_session_async) and
converting every call site in main.py's async handlers to use them.
The in-memory dict mutation still happens immediately/synchronously
(cheap); only the actual file write is offloaded to a thread pool, and
a snapshot (shallow copy) is handed to the executor rather than the
live dict, since a concurrent coroutine mutating the dict while the
executor thread iterates it for json.dump() would otherwise risk a
"dictionary changed size during iteration" crash.

The real regression guard here is behavioral, not just "does the
method exist": a genuinely blocking implementation would starve every
other scheduled coroutine for the full duration of the write (asyncio
only switches tasks at await points, and a synchronous call has none);
an executor-offloaded one lets other ready tasks run while the write
happens in the background thread.
"""
import asyncio
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mrc.bridge.db import BridgeDB


class BridgeDbAsyncWriteRoundTripTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = BridgeDB(data_dir=self._tmp.name)

    def test_save_profile_async_round_trips(self):
        asyncio.run(self.db.save_profile_async('alice', {'nick': 'alice', 'color': 'red'}))
        fresh = BridgeDB(data_dir=self._tmp.name)
        profile = fresh.get_profile('alice')
        self.assertEqual(profile['nick'], 'alice')
        self.assertEqual(profile['color'], 'red')

    def test_save_session_async_round_trips(self):
        asyncio.run(self.db.save_session_async('sess1', {'nick': 'bob', 'in_room': True}))
        fresh = BridgeDB(data_dir=self._tmp.name)
        sess = fresh.get_session('sess1')
        self.assertEqual(sess['nick'], 'bob')
        self.assertTrue(sess['in_room'])

    def test_delete_session_async_round_trips(self):
        asyncio.run(self.db.save_session_async('sess2', {'nick': 'carol'}))
        asyncio.run(self.db.delete_session_async('sess2'))
        fresh = BridgeDB(data_dir=self._tmp.name)
        self.assertIsNone(fresh.get_session('sess2'))

    def test_delete_session_async_on_missing_key_is_a_no_op(self):
        # Must not raise or write a file for a session that was never
        # there -- mirrors the sync delete_session()'s own guard.
        asyncio.run(self.db.delete_session_async('never-existed'))
        self.assertIsNone(self.db.get_session('never-existed'))


class BridgeDbAsyncWriteDoesNotBlockEventLoopTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = BridgeDB(data_dir=self._tmp.name)

    def test_slow_write_does_not_starve_other_scheduled_coroutines(self):
        """The actual regression guard: a slow (simulated) disk write
        must not prevent an unrelated, concurrently-scheduled coroutine
        from making progress during that window. asyncio only switches
        between tasks at await points -- a genuinely synchronous,
        blocking write has none, so a concurrently-scheduled task could
        not possibly complete before it under the old (unfixed) code."""
        order = []
        real_save_json = self.db._save_json

        def slow_save_json(*args, **kwargs):
            time.sleep(0.2)  # a real, wall-clock blocking "disk write"
            return real_save_json(*args, **kwargs)

        self.db._save_json = slow_save_json

        async def do_save():
            order.append('save_start')
            await self.db.save_profile_async('slowuser', {'nick': 'slowuser'})
            order.append('save_end')

        async def other_task():
            order.append('other_start')
            await asyncio.sleep(0)
            order.append('other_end')

        async def run_both():
            t1 = asyncio.create_task(do_save())
            t2 = asyncio.create_task(other_task())
            await asyncio.gather(t1, t2)

        asyncio.run(run_both())

        self.assertIn('other_end', order)
        self.assertIn('save_end', order)
        self.assertLess(
            order.index('other_end'), order.index('save_end'),
            f'other_task must complete WHILE the slow write is still in '
            f'flight (offloaded to a thread), not only after it finishes '
            f'-- actual order: {order}. If this fails, the write is '
            'blocking the event loop again.')


if __name__ == '__main__':
    unittest.main()
