"""N-9: two nodes launching the same dosemu door must not swap drop files.

Each launch writes DOOR.SYS into the game's shared directory just before
the door reads it. Launches of one game are now serialized for a short hold
window, without blocking the terminal server's event loop.
"""
import asyncio
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from anetbbs.games import dosemu_launch_slot as slot


class DosemuLaunchSlot(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='dslot-')
        p = mock.patch('anetbbs.games.node_paths.temp_root', return_value=self.tmp)
        p.start()
        self.addCleanup(p.stop)

    async def test_second_launch_waits_and_loop_stays_responsive(self):
        game = SimpleNamespace(slug='tw2002')
        self.assertTrue(await slot.take_slot_async(game, hold=0.6))
        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                ticks += 1
                await asyncio.sleep(0.05)
        t = asyncio.create_task(ticker())
        start = time.monotonic()
        self.assertTrue(await slot.take_slot_async(game, hold=0.1))
        waited = time.monotonic() - start
        t.cancel()
        self.assertGreaterEqual(waited, 0.45)
        self.assertGreater(ticks, 5)          # other sessions kept running

    async def test_other_games_not_delayed(self):
        await slot.take_slot_async(SimpleNamespace(slug='lord'), hold=1.0)
        start = time.monotonic()
        await slot.take_slot_async(SimpleNamespace(slug='bre'), hold=0.1)
        self.assertLess(time.monotonic() - start, 0.2)

    async def test_gives_up_after_wait_limit(self):
        game = SimpleNamespace(slug='stuck')
        await slot.take_slot_async(game, hold=5)
        self.assertFalse(await slot.take_slot_async(game, hold=0.1, wait=0.3))

    def test_sync_path_for_web(self):
        game = SimpleNamespace(slug='web-door')
        self.assertTrue(slot.take_slot(game, hold=0.4))
        start = time.monotonic()
        self.assertTrue(slot.take_slot(game, hold=0.1))
        self.assertGreaterEqual(time.monotonic() - start, 0.3)


if __name__ == '__main__':
    unittest.main()
