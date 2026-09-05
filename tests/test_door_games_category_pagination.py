"""Regression test for a real live UX bug reported by Jerry (2026-09-01,
with a SyncTerm screenshot): a category with more games than fit on one
screen -- the A-Net Game Server bulk import routinely produces
categories with 100+ games -- just kept writing every single one, no
pagination, running off the bottom of a fixed-height terminal with no
way to see the rest short of the client's own scrollback.

GameManager._show_category_submenu() is exercised directly with a
synthetic games list (plain dicts, the same shape show_door_menu()
already builds) rather than seeding 50 real DB rows -- it takes
`games` as a plain argument, no DB access of its own.
"""
import asyncio
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _FakeWriter:
    def __init__(self, sink):
        self._sink = sink

    def write(self, data):
        self._sink.append(data.decode('latin-1', errors='replace'))

    async def drain(self):
        pass


class _FakeSession:
    def __init__(self, responses, width=80, height=24):
        self.user = None
        self._responses = list(responses)
        self.written = []
        self.window_size = (width, height)
        self.term_mode = 'ansi'
        self.writer = _FakeWriter(self.written)

    async def write(self, text):
        self.written.append(text)

    async def clear_screen(self):
        await self.write('\x1b[2J\x1b[H\x1b[0m')

    async def read_line(self, prompt=''):
        if prompt:
            await self.write(prompt)
        if not self._responses:
            raise AssertionError(
                f'_FakeSession.read_line() called with prompt={prompt!r} but '
                'the scripted response queue is empty')
        return self._responses.pop(0)

    def transcript(self):
        return ''.join(self.written)


def _strip_ansi(text):
    return re.sub(r'\x1b\[[0-9;]*m', '', text)


def _make_games(n):
    return [{'id': i, 'name': f'Synthetic Door {i:03d}', 'game_type': 'door_native'}
           for i in range(1, n + 1)]


class CategoryPaginationTests(unittest.TestCase):
    def test_small_category_shows_no_pagination_controls(self):
        from anetbbs.features.games import GameManager
        games = _make_games(5)
        session = _FakeSession(['B'])
        asyncio.run(GameManager(session)._show_category_submenu('t', 'Test', games))
        txt = _strip_ansi(session.transcript())
        self.assertNotIn('Next page', txt)
        self.assertNotIn('Prev page', txt)
        self.assertNotIn('page 1/', txt)
        for g in games:
            self.assertIn(g['name'], txt)

    def test_large_category_paginates_and_shows_next_control_only(self):
        # window_size=(80,24) -> rows_per_page=16, page_size=32.
        from anetbbs.features.games import GameManager
        games = _make_games(50)
        session = _FakeSession(['B'])
        asyncio.run(GameManager(session)._show_category_submenu('t', 'Test', games))
        txt = _strip_ansi(session.transcript())
        self.assertIn('page 1/2', txt)
        self.assertIn('Next page', txt)
        self.assertNotIn('Prev page', txt)
        self.assertIn(games[0]['name'], txt, 'first game must appear on page 1')
        self.assertIn(games[31]['name'], txt, 'last game of page 1 (#32) must appear')
        self.assertNotIn(games[32]['name'], txt, 'first game of page 2 must NOT appear on page 1')

    def test_next_then_prev_navigation_shows_correct_games(self):
        from anetbbs.features.games import GameManager
        games = _make_games(50)
        session = _FakeSession(['N', 'P', 'B'])
        asyncio.run(GameManager(session)._show_category_submenu('t', 'Test', games))
        stripped = _strip_ansi(session.transcript())
        self.assertIn('page 2/2', stripped)
        self.assertIn(games[49]['name'], stripped, 'last game (#50) must appear once on page 2')
        self.assertIn('Prev page', stripped)

    def test_page_2_hides_next_control_and_shows_prev(self):
        from anetbbs.features.games import GameManager
        games = _make_games(50)
        session = _FakeSession(['N', 'B'])
        asyncio.run(GameManager(session)._show_category_submenu('t', 'Test', games))
        txt = _strip_ansi(session.transcript())
        # Two renders happened (page 1, then page 2) -- isolate
        # everything from page 2's own "page 2/2" marker onward.
        self.assertIn('page 2/2', txt)
        page2_block = txt.split('page 2/2', 1)[1]
        self.assertIn('Prev page', page2_block)
        self.assertNotIn('Next page', page2_block)

    def test_selecting_a_game_by_absolute_number_on_page_2_launches_it(self):
        from anetbbs.features.games import GameManager
        games = _make_games(50)
        gm = GameManager(_FakeSession(['N', '40', 'B']))
        gm._launch = AsyncMock()
        asyncio.run(gm._show_category_submenu('t', 'Test', games))
        gm._launch.assert_awaited_once_with(games[39])

    def test_quitting_from_any_page_returns_without_launching(self):
        from anetbbs.features.games import GameManager
        games = _make_games(50)
        gm = GameManager(_FakeSession(['N', 'Q']))
        gm._launch = AsyncMock()
        asyncio.run(gm._show_category_submenu('t', 'Test', games))
        gm._launch.assert_not_called()

    def test_next_on_last_page_and_prev_on_first_page_are_ignored(self):
        """N/P are only meaningful when there's somewhere to go -- on a
        single-page category (or already at an edge) they should just
        be treated as an invalid choice, not crash or loop forever."""
        from anetbbs.features.games import GameManager
        games = _make_games(5)
        session = _FakeSession(['N', 'P', 'B'])
        asyncio.run(GameManager(session)._show_category_submenu('t', 'Test', games))
        txt = _strip_ansi(session.transcript())
        self.assertIn('Invalid choice', txt)

    def test_shorter_terminal_yields_a_smaller_page_size(self):
        """A 20-row terminal has less room than the default 24 --
        rows_per_page = max(3, 20-8) = 12, page_size = 24. Confirms the
        page size actually scales with ui_height(), not hardcoded."""
        from anetbbs.features.games import GameManager
        games = _make_games(50)
        session = _FakeSession(['B'], height=20)
        asyncio.run(GameManager(session)._show_category_submenu('t', 'Test', games))
        txt = _strip_ansi(session.transcript())
        self.assertIn('1-24 of 50', txt)


if __name__ == '__main__':
    unittest.main()
