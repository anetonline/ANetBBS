"""Regression test for adding the built-in Number Guessing game to the
PETSCII menu (Jerry: "we can just add the number guessing one for now
that is all text"). features.games.GameManager.play_number_guess() is
reused directly, not reimplemented -- it's pure session.write()/
read_line() with zero ANSI escape codes, so the session layer's
existing PETSCII encode-on-write path (core/session.py's write())
handles it transparently with no PETSCII-specific code needed at all.
"""
import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class _FakeSession:
    def __init__(self, user, responses):
        self.user = user
        self._responses = list(responses)
        self.written = []
        self._forced_width = 40

    async def write(self, text):
        self.written.append(text)

    async def read_line(self, prompt=''):
        if prompt:
            await self.write(prompt)
        if not self._responses:
            raise AssertionError(
                f'_FakeSession.read_line() called with prompt={prompt!r} but '
                'the scripted response queue is empty')
        return self._responses.pop(0)

    async def clear_screen(self):
        self.written.append('[CLR]')

    @property
    def petscii_width(self):
        return self._forced_width

    def transcript(self):
        return ''.join(self.written)


class PetsciiGamesMenuTests(unittest.TestCase):
    def test_games_menu_lists_number_guessing_and_quits(self):
        from anetbbs.features.petscii_ui import _games_menu
        session = _FakeSession({'id': 1, 'username': 'alice'}, ['Q'])
        asyncio.run(_games_menu(session))
        txt = session.transcript()
        self.assertIn('Number Guessing', txt)
        self.assertIn('Q. Back', txt)

    def test_playing_number_guess_via_petscii_menu_reaches_win(self):
        from anetbbs.features.petscii_ui import _games_menu
        # 1(games menu) -> guess 50 -> the real play_number_guess loop
        # reads guesses until correct -- patch random.randint so the
        # target is deterministic, then binary-search it in 7 guesses
        # max (100 -> 1 guess needed since we fix it to 50 directly),
        # ENTER after the win message, Q to leave the games menu.
        with patch('random.randint', return_value=50):
            session = _FakeSession({'id': 1, 'username': 'alice'},
                                   ['1', '50', '', 'Q'])
            asyncio.run(_games_menu(session))
        txt = session.transcript()
        self.assertIn('Congratulations! You got it in 1 tries!', txt)

    def test_number_guess_reuses_the_shared_gamemanager_not_a_duplicate(self):
        """The game logic itself must not be reimplemented in petscii_ui.py
        -- assert the real call reaches features.games.GameManager."""
        from anetbbs.features import petscii_ui
        from anetbbs.features.games import GameManager
        with patch.object(GameManager, 'play_number_guess',
                          return_value=None) as mock_play:
            session = _FakeSession({'id': 1, 'username': 'alice'}, ['1', 'Q'])
            asyncio.run(petscii_ui._games_menu(session))
        mock_play.assert_called_once()


if __name__ == '__main__':
    unittest.main()
