"""Regression test for a real live bug reported by Jerry (2026-09-01):
"the delete key is not working in the webUI when connecting to/playing
games. I tried to backspace to make a spelling correction and it does
nothing." Root cause: xterm.js sends DEL (0x7f) for the Backspace key
by default, but classic BBS door software (and remote rlogin/telnet
game servers like A-Net Online's, which is what Jerry was actually
testing) was written for the traditional BS (0x08) convention --
matching what a real terminal client normally sends, and what
ANetBBS's own telnet/SSH terminal client already treats as backspace
(core/session.py's is_backspace checks accept either byte on the way
IN; this is the same normalization applied on the way OUT to whatever
the web player is bridged to, in anetbbs/templates/games/
play_terminal.html's term.onData handler).

No JS test runner in this codebase -- this reads the template source
directly and asserts the translation is present, the same "verify
against actual current source" discipline used elsewhere for static/
config-shaped content that has no dedicated test framework.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_TEMPLATE = (Path(__file__).resolve().parents[1]
            / 'anetbbs' / 'templates' / 'games' / 'play_terminal.html')


class WebGameTerminalBackspaceTests(unittest.TestCase):
    def setUp(self):
        self.src = _TEMPLATE.read_text(encoding='utf-8')

    def test_ondata_translates_del_to_bs_before_emitting(self):
        # Isolate the onData handler body so this doesn't just match
        # some unrelated \x7f/\x08 reference anywhere else in the file.
        start = self.src.index('term.onData(function')
        end = self.src.index('});', start)
        handler = self.src[start:end]
        self.assertIn(r'\x7f', handler,
                      'onData handler must reference DEL (0x7f), the '
                      'byte xterm.js sends for Backspace by default')
        self.assertIn(r'\x08', handler,
                      'onData handler must reference BS (0x08), the '
                      'byte classic BBS door software expects')
        self.assertIn('.replace(', handler,
                      'must actually translate the byte, not just '
                      'reference both constants without connecting them')

    def test_game_input_emit_uses_the_translated_variable_not_raw_data(self):
        start = self.src.index('term.onData(function')
        end = self.src.index('});', start)
        handler = self.src[start:end]
        emit_line = next(
            (line for line in handler.splitlines() if 'game_input' in line),
            None)
        self.assertIsNotNone(emit_line, 'expected a game_input emit call '
                             'inside the onData handler')
        self.assertNotIn('input: data', emit_line,
                         'the emitted input must be the translated value, '
                         'not the raw untranslated xterm.js data')


if __name__ == '__main__':
    unittest.main()
