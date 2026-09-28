"""Regression test for a real bug reported live (2026-09-27): the
anetbbs-cfg terminal admin tool's forms (e.g. editing/managing a door
game -- enough conditional fields, especially for door games, to run
well past row 24) had NO scrolling at all. run_list() already scrolled
correctly (top/body_h windowing); run_form() drew every field at a
fixed absolute row with no bounds check against the real terminal
height, so anything past the visible area -- including the [Save] row
itself -- just silently never rendered. A caller on a real 80x24 SSH
session (not a resizable local terminal) had no way to reach it at all.

Fixed by giving run_form() (and, proactively, run_menu() -- the exact
same "fixed row, no bounds check" shape, not yet hit today but a latent
trap for the next long menu) the same top/visible_h scroll-into-view
logic run_list() already had.

Uses the same _FakeStdscr pattern test_cfg_ui_save_cancel_buttons.py
already established.
"""
import curses
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class _FakeStdscr:
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


def _rendered_texts(fake):
    return [text for (_y, _x, text, _attr) in fake.addstr_calls]


class _TrackingStdscr(_FakeStdscr):
    """Same fake, but marks each frame boundary (on erase(), called once
    per render at the top of every run_form()/run_menu() loop
    iteration) so a test can inspect exactly one frame's render calls
    instead of the whole accumulated session log."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.frame_bounds = []

    def erase(self):
        # Called exactly once per render, right before that frame's own
        # addstr() calls -- so each call here marks the START index of
        # the frame about to be drawn (not a duplicate of frame 0's
        # start, which pre-seeding this list with [0] would cause).
        self.frame_bounds.append(len(self.addstr_calls))

    def frame(self, i):
        """Render calls belonging to frame i (0-indexed)."""
        start = self.frame_bounds[i]
        end = (self.frame_bounds[i + 1] if i + 1 < len(self.frame_bounds)
              else len(self.addstr_calls))
        return [t for (_y, _x, t, _a) in self.addstr_calls[start:end]]


class FormScrollingTests(unittest.TestCase):
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

    def _fields(self, n):
        return [{"key": f"f{i}", "label": f"Field {i}", "kind": "bool"}
                for i in range(n)]

    def test_save_row_is_reachable_and_rendered_on_a_short_terminal(self):
        # A real 80x24-ish tight window (height=10) with more fields
        # than fit -- exactly the reported shape (door game form,
        # standard SSH session, can't resize). Before the fix, [Save]
        # would never appear in addstr_calls at all no matter how far
        # down you navigated.
        from anetbbs.cfg import ui
        fields = self._fields(12)
        values = {f["key"]: False for f in fields}
        fake = _FakeStdscr(height=10, width=80)
        # 12 fields, idx starts at 0 -- exactly len(fields) KEY_DOWNs
        # lands on [Save] (nav index 12), then Enter activates it.
        fake._getch_queue = [curses.KEY_DOWN] * len(fields) + [10]
        result = ui.run_form(fake, 'Long Form', fields, dict(values))
        self.assertEqual(result, values)
        rendered = _rendered_texts(fake)
        self.assertTrue(any('Save' in t for t in rendered),
                        '[Save] never got rendered even after scrolling all '
                        'the way down to it -- the real bug this guards')

    def test_early_fields_scroll_out_of_view_once_scrolled_past(self):
        from anetbbs.cfg import ui
        fields = self._fields(12)
        values = {f["key"]: False for f in fields}
        fake = _TrackingStdscr(height=10, width=80)
        # Scroll down past the first several fields, then bail via Esc.
        fake._getch_queue = [curses.KEY_DOWN] * 10 + [27]
        ui.run_form(fake, 'Long Form', fields, dict(values))
        # The LAST rendered frame (right before Esc is handled) must
        # NOT still show Field 0 -- it scrolled off the top ticks ago.
        last_frame = fake.frame(len(fake.frame_bounds) - 1)
        self.assertFalse(any('Field 0' in t for t in last_frame),
                         'Field 0 was still being rendered in the final '
                         'frame even after scrolling 10 rows past it -- '
                         'scroll-out-of-view is not working')
        # And it's genuinely a SCROLL (not just "form got smaller") --
        # confirm Field 0 legitimately WAS visible in the very first
        # frame, before any scrolling happened.
        first_frame = fake.frame(0)
        self.assertTrue(any('Field 0' in t for t in first_frame))

    def test_selected_field_stays_visible_while_navigating_down(self):
        """Every intermediate frame while stepping down one row at a
        time must actually render the currently-selected field --
        i.e. the view scrolls to follow the selection, not just
        eventually catch up at the end."""
        from anetbbs.cfg import ui
        fields = self._fields(12)
        values = {f["key"]: False for f in fields}

        fake = _TrackingStdscr(height=8, width=80)
        # 10 KEY_DOWNs -> idx=10 ("Field 10") is the 11th frame (frame 0
        # is the initial render at idx=0, frame N is drawn with idx=N).
        fake._getch_queue = [curses.KEY_DOWN] * 10 + [27]
        ui.run_form(fake, 'Long Form', fields, dict(values))
        texts = fake.frame(10)
        self.assertTrue(any('Field 10' in t for t in texts),
                        'the currently-selected field must always be '
                        'scrolled into view, not just the final selection')

    def test_no_scrolling_needed_behaves_exactly_as_before(self):
        """A short form on a normal 24-row terminal must render
        identically to the pre-scrolling behavior -- this fix must be a
        no-op when nothing needs to scroll."""
        from anetbbs.cfg import ui
        fields = [{"key": "name", "label": "Name", "kind": "text"},
                 {"key": "active", "label": "Active", "kind": "bool"}]
        values = {"name": "Original Name", "active": True}
        fake = _FakeStdscr()
        fake._getch_queue = [27]
        ui.run_form(fake, 'Test Form', fields, dict(values))
        rendered = _rendered_texts(fake)
        self.assertTrue(any('Name' in t for t in rendered))
        self.assertTrue(any('Save' in t for t in rendered))
        self.assertTrue(any('Cancel' in t for t in rendered))


class MenuScrollingTests(unittest.TestCase):
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

    def test_last_menu_item_is_reachable_and_rendered_on_a_short_terminal(self):
        from anetbbs.cfg import ui
        items = [(f"k{i}", f"Item {i}") for i in range(15)]
        fake = _FakeStdscr(height=8, width=80)
        fake._getch_queue = [curses.KEY_DOWN] * 14 + [10]
        result = ui.run_menu(fake, 'Long Menu', items)
        self.assertEqual(result, "k14")
        rendered = _rendered_texts(fake)
        self.assertTrue(any('Item 14' in t for t in rendered),
                        'the last menu item never got rendered even after '
                        'scrolling all the way down to it')


if __name__ == '__main__':
    unittest.main()
