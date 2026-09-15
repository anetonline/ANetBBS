"""Regression test for a real, live-reported bug (2026-09-15, from a
screen-reader user on an 80-column SSH client): anetbbs-cfg's
run_form() rendered each help_lines entry via a single _safe_addstr()
call, which silently hard-truncates text wider than the terminal --
login_modules.py's "Params by type: ..." HELP string (well over 200
characters, one unbroken line) got clipped dead at column 79 with no
indication anything was cut off. Confirmed the same shape hits at
least three other sections too (events.py, games.py, menu.py all have
a HELP/GAME_HELP/ITEM_HELP literal over 75 chars).

Fixed at the shared anetbbs/cfg/ui.py:run_form() chokepoint (a new
_wrap_help_line() helper) rather than patching login_modules.py alone,
so every section's help text benefits, not just the one reported.
"""
import curses
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class _FakeStdscr:
    """Minimal curses stdscr stand-in -- records every addstr() call
    so a test can inspect exactly what would have been drawn, same
    pattern as test_cfg_ui_safe_curs_set.py's _FakeWin."""

    def __init__(self, height=24, width=80):
        self._h = height
        self._w = width
        self.addstr_calls = []
        self._getch_queue = [27]  # Esc -- exit run_form after one render

    def erase(self):
        pass

    def refresh(self):
        pass

    def keypad(self, _flag):
        pass

    def getmaxyx(self):
        return (self._h, self._w)

    def addstr(self, y, x, text, attr=0):
        self.addstr_calls.append((y, x, text, attr))

    def getch(self):
        return self._getch_queue.pop(0) if self._getch_queue else 27


class HelpTextWrapTests(unittest.TestCase):
    def setUp(self):
        curses.LINES = 24
        curses.COLS = 80
        # run_form() calls curses.curs_set()/has_colors() along the
        # way -- neither works without a real initscr() session, which
        # this unit test deliberately doesn't set up (same reasoning
        # test_cfg_ui_safe_curs_set.py's own tests already apply to
        # curs_set specifically).
        self._orig_curs_set = curses.curs_set
        self._orig_has_colors = curses.has_colors
        curses.curs_set = lambda *_a, **_k: None
        curses.has_colors = lambda: False

    def tearDown(self):
        del curses.LINES
        del curses.COLS
        curses.curs_set = self._orig_curs_set
        curses.has_colors = self._orig_has_colors

    def test_wrap_help_line_splits_long_text_to_fit(self):
        from anetbbs.cfg.ui import _wrap_help_line
        long_line = 'x ' * 100  # 200 chars
        wrapped = _wrap_help_line(long_line, 76)
        self.assertGreater(len(wrapped), 1)
        for w in wrapped:
            self.assertLessEqual(len(w), 76)

    def test_wrap_help_line_splits_on_embedded_newlines_first(self):
        from anetbbs.cfg.ui import _wrap_help_line
        wrapped = _wrap_help_line('short one\nshort two', 76)
        self.assertEqual(wrapped, ['short one', 'short two'])

    def test_run_form_does_not_truncate_login_modules_help_text(self):
        """Drive the real run_form() with the actual reported HELP
        content, at the actual reported terminal size (80 cols), and
        confirm the full text is rendered across multiple lines
        instead of being cut off after one."""
        from anetbbs.cfg import ui
        from anetbbs.cfg.sections import login_modules

        fake = _FakeStdscr(height=24, width=80)
        result = ui.run_form(fake, 'New Login Module', login_modules.FIELDS,
                             dict(login_modules.NEW_DEFAULTS),
                             help_lines=login_modules.HELP)
        self.assertIsNone(result)  # Esc was pressed -- cancelled

        help_text_rendered = ' '.join(
            text for (_y, _x, text, attr) in fake.addstr_calls
            if attr == ui._attr(3, curses.A_DIM))
        original_help = login_modules.HELP[0]

        # Every word from the original (previously-truncated) help
        # string must appear somewhere in what got rendered.
        for word in original_help.split():
            self.assertIn(word, help_text_rendered,
                          f'{word!r} from the real HELP text is missing from '
                          f'the rendered output -- still being truncated')

        # No single rendered help row may be wider than the screen.
        for (_y, _x, text, attr) in fake.addstr_calls:
            if attr == ui._attr(3, curses.A_DIM):
                self.assertLessEqual(len(text), 80,
                                     f'rendered help row {text!r} still '
                                     f'overflows the 80-col terminal')

    def test_run_form_wraps_across_multiple_rows_not_one(self):
        from anetbbs.cfg import ui
        from anetbbs.cfg.sections import login_modules

        fake = _FakeStdscr(height=24, width=80)
        ui.run_form(fake, 'New Login Module', login_modules.FIELDS,
                   dict(login_modules.NEW_DEFAULTS),
                   help_lines=login_modules.HELP)

        help_rows = [text for (_y, _x, text, attr) in fake.addstr_calls
                    if attr == ui._attr(3, curses.A_DIM)]
        self.assertGreater(len(help_rows), 1,
                           'a >200-char HELP string rendered as only one row '
                           '-- word-wrap is not actually happening')


if __name__ == '__main__':
    unittest.main()
