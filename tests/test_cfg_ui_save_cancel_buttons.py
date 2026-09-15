"""Regression test for the on-screen [Save]/[Cancel] rows added to
run_form(), following up a real bug report (2026-09-15, screen-reader
user on an SSH client) where F2 apparently arrived as a bare ESC
instead of a recognized F2 sequence, silently discarding ~3 hours of
edits with no save and no warning.

A keyboard-shortcut-only fix (Ctrl-Z was the original ask) has the
same class of problem -- still depends on the terminal correctly
delivering a specific byte, and Ctrl-Z specifically collides with the
terminal driver's own job-control SUSPEND character (confirmed via a
real PTY test during triage: in this tool's current curses input mode,
Ctrl-Z never reaches getch() as a literal keystroke at all -- the
kernel line discipline intercepts it first). Explicit, always-
focusable [Save]/[Cancel] rows only need arrow keys + Enter, which the
form already depends on for every other kind of navigation, sidestepping
the whole class of terminal-encoding problem.

F2 (save) and Esc (cancel) must both still work unchanged -- this is
purely additive.
"""
import curses
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class _FakeStdscr:
    """Minimal curses stdscr stand-in -- records every addstr() call,
    same pattern as test_cfg_ui_help_text_wrap.py's own copy and
    test_cfg_ui_safe_curs_set.py's _FakeWin."""

    def __init__(self, height=24, width=80):
        self._h = height
        self._w = width
        self.addstr_calls = []
        self._getch_queue = [27]

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


_FIELDS = [
    {"key": "name", "label": "Name", "kind": "text"},
    {"key": "active", "label": "Active", "kind": "bool"},
]
_VALUES = {"name": "Original Name", "active": True}


class SaveCancelButtonTests(unittest.TestCase):
    def setUp(self):
        curses.LINES = 24
        curses.COLS = 80
        self._orig_curs_set = curses.curs_set
        self._orig_has_colors = curses.has_colors
        curses.curs_set = lambda *_a, **_k: None
        curses.has_colors = lambda: False

    def tearDown(self):
        del curses.LINES
        del curses.COLS
        curses.curs_set = self._orig_curs_set
        curses.has_colors = self._orig_has_colors

    def test_save_row_is_rendered(self):
        from anetbbs.cfg import ui
        fake = _FakeStdscr()
        fake._getch_queue = [27]  # Esc immediately, just render + exit
        ui.run_form(fake, 'Test Form', _FIELDS, dict(_VALUES))
        rendered = [text for (_y, _x, text, _attr) in fake.addstr_calls]
        self.assertTrue(any('Save' in t for t in rendered))
        self.assertTrue(any('Cancel' in t for t in rendered))

    def test_navigating_down_to_save_and_pressing_enter_saves(self):
        from anetbbs.cfg import ui
        fake = _FakeStdscr()
        # 2 fields -> KEY_DOWN x2 lands on [Save], then Enter.
        fake._getch_queue = [curses.KEY_DOWN, curses.KEY_DOWN, 10]
        result = ui.run_form(fake, 'Test Form', _FIELDS, dict(_VALUES))
        self.assertEqual(result, _VALUES)

    def test_navigating_down_to_cancel_and_pressing_enter_discards(self):
        from anetbbs.cfg import ui
        fake = _FakeStdscr()
        # 2 fields -> KEY_DOWN x3 lands on [Cancel], then Enter.
        fake._getch_queue = [curses.KEY_DOWN, curses.KEY_DOWN, curses.KEY_DOWN, 10]
        result = ui.run_form(fake, 'Test Form', _FIELDS, dict(_VALUES))
        self.assertIsNone(result)

    def test_navigation_wraps_around_through_the_action_rows(self):
        """Up from the first field must wrap to [Cancel] (the last
        navigable row), not throw an IndexError -- confirms nav_items
        (fields + 2 action rows) is what idx wraps against, not just
        len(fields)."""
        from anetbbs.cfg import ui
        fake = _FakeStdscr()
        fake._getch_queue = [curses.KEY_UP, 10]  # wrap to [Cancel], activate
        result = ui.run_form(fake, 'Test Form', _FIELDS, dict(_VALUES))
        self.assertIsNone(result)

    def test_f2_still_saves_immediately_unchanged(self):
        from anetbbs.cfg import ui
        fake = _FakeStdscr()
        fake._getch_queue = [curses.KEY_F2]
        result = ui.run_form(fake, 'Test Form', _FIELDS, dict(_VALUES))
        self.assertEqual(result, _VALUES)

    def test_esc_still_cancels_immediately_unchanged(self):
        from anetbbs.cfg import ui
        fake = _FakeStdscr()
        fake._getch_queue = [27]
        result = ui.run_form(fake, 'Test Form', _FIELDS, dict(_VALUES))
        self.assertIsNone(result)

    def test_enter_on_a_real_field_still_edits_not_saves(self):
        """Enter while idx is on an actual field (not an action row)
        must not be swallowed by the new action-row handling -- the
        bool field's own toggle-on-Enter behavior must still fire."""
        from anetbbs.cfg import ui
        fake = _FakeStdscr()
        # idx starts at 0 ("Name", a text field) -> KEY_DOWN to "Active"
        # (idx=1, a bool field) -> Enter toggles it -> Esc to exit
        # without saving (we only care whether the toggle happened,
        # inspectable via the next render's checkbox glyph).
        fake._getch_queue = [curses.KEY_DOWN, 10, 27]
        ui.run_form(fake, 'Test Form', _FIELDS, dict(_VALUES))
        rendered = [text for (_y, _x, text, _attr) in fake.addstr_calls]
        # Active started True ("[X]"); after the toggle the LAST render
        # before Esc must show "[ ]".
        checkbox_states = [t for t in rendered if t in ('[X]', '[ ]')]
        self.assertEqual(checkbox_states[-1], '[ ]',
                         'Enter on the Active field should have toggled it, '
                         'not been intercepted as a Save/Cancel activation')


if __name__ == '__main__':
    unittest.main()
