"""Regression test for anetbbs/features/anet_game_import.py's ANSI/
control-byte injection fix.

Real vulnerability found in a security/performance audit: scrape_games()
returns name/code/category straight from the remote A-Net Game Server
page's HTML with no sanitization. Those values get persisted as
Game.name / Game.category / GameCategory.name (via web/games_admin.py's
anet_import_confirm route) and rendered UNESCAPED into every telnet/SSH
caller's real terminal every time anyone opens the Door Games menu
(features/games.py's show_door_menu(), which writes g['name'] directly
into an ANSI-colored f-string). Unlike an ephemeral chat line, this is
PERSISTENT data rendered on every future visit to that menu, so a raw
CSI/OSC escape sequence baked into a scraped name/category (a
compromised remote page, or just a scraping bug picking up the wrong
span) would inject escape codes into every caller's session from then
on -- same vulnerability class as wall.py's InterBBS-synced posts (see
that module's own _strip_untrusted() docstring).

Fixed by stripping well-formed CSI/OSC sequences and the whole C0
control range (+ DEL) from name/code/category inside scrape_games()
itself, before any of them are ever returned -- same two-pass fix shape
already used in wall.py/mrc_chat.py/menu_engine.py/bbs_ui.py.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.features.anet_game_import import scrape_games

_EVIL_HTML = """
<html><body>
<div class="code-category">
  <h3>\x1b[2J\x1b[HArcade</h3>
  <ul class="flex-list">
    <li>Evil\x1b[31mGame\x07 - <span class="door-code">EVIL\x1b]0;pwn\x07CODE</span></li>
  </ul>
</div>
</body></html>
"""


class ScrapeGamesAnsiInjectionTests(unittest.TestCase):
    def _mock_get(self, text):
        resp = Mock()
        resp.text = text
        resp.raise_for_status = Mock()
        return resp

    def test_category_is_stripped_of_escape_sequences(self):
        with patch('anetbbs.features.anet_game_import.requests.get',
                   return_value=self._mock_get(_EVIL_HTML)):
            games = scrape_games()
        self.assertEqual(len(games), 1)
        category = games[0]['category']
        self.assertNotIn('\x1b', category)
        self.assertIn('Arcade', category)

    def test_code_is_stripped_of_escape_sequences(self):
        with patch('anetbbs.features.anet_game_import.requests.get',
                   return_value=self._mock_get(_EVIL_HTML)):
            games = scrape_games()
        code = games[0]['code']
        self.assertNotIn('\x1b', code)
        self.assertNotIn('\x07', code)
        self.assertIn('EVIL', code)
        self.assertIn('CODE', code)

    def test_name_is_stripped_of_escape_sequences(self):
        with patch('anetbbs.features.anet_game_import.requests.get',
                   return_value=self._mock_get(_EVIL_HTML)):
            games = scrape_games()
        name = games[0]['name']
        self.assertNotIn('\x1b', name)
        self.assertNotIn('\x07', name)


if __name__ == '__main__':
    unittest.main()
