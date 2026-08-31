"""Regression test for a real bug found live: launching anetbbs-cfg
through the terminal Sysop Menu (see tests/test_terminal_sysop_menu.py
and tests/test_anetbbs_cfg_door_seed.py) crashed immediately with
`_curses.error: curs_set() returned ERR`. Root cause: doors launched
via door_runner.py inherit `TERM=ansi` (a minimal terminfo entry meant
for doors that emit raw ANSI escapes directly -- every other door this
launch path has ever run). `ansi` has no civis/cnorm capability, so
any bare `curses.curs_set()` call raises there. anetbbs-cfg is the
first curses-based program to go through this launch path, so the bug
never surfaced before. `anetbbs/cfg/ui.py`'s new `safe_curs_set()`
wraps every call site (app.py's own main-menu entry included) so a
missing cursor-visibility capability degrades to "cursor stays
visible" instead of crashing the whole tool.
"""
import curses
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class SafeCursSetTests(unittest.TestCase):
    def test_swallows_curses_error(self):
        from anetbbs.cfg import ui

        def _raises(_visibility):
            raise curses.error("curs_set() returned ERR")

        orig = curses.curs_set
        curses.curs_set = _raises
        try:
            ui.safe_curs_set(0)  # must not raise
        finally:
            curses.curs_set = orig

    def test_still_calls_through_on_success(self):
        from anetbbs.cfg import ui

        calls = []
        orig = curses.curs_set
        curses.curs_set = calls.append
        try:
            ui.safe_curs_set(1)
        finally:
            curses.curs_set = orig
        self.assertEqual(calls, [1])

    def test_does_not_swallow_unrelated_exceptions(self):
        # Only curses.error is a legitimate "no civis/cnorm capability"
        # signal -- anything else (a real bug in a caller, say) must
        # still propagate normally.
        from anetbbs.cfg import ui

        def _raises(_visibility):
            raise ValueError("not a curses.error")

        orig = curses.curs_set
        curses.curs_set = _raises
        try:
            with self.assertRaises(ValueError):
                ui.safe_curs_set(0)
        finally:
            curses.curs_set = orig

    def test_no_bare_curses_curs_set_calls_remain_in_ui_module(self):
        """Guards against a future edit reintroducing an unwrapped call
        site -- every curs_set() call in ui.py must go through the safe
        wrapper (or be the wrapper's own definition)."""
        import inspect
        from anetbbs.cfg import ui
        source = inspect.getsource(ui)
        # The wrapper's own body legitimately contains "curses.curs_set(" once.
        self.assertEqual(source.count('curses.curs_set('), 1)


class ConfirmBoundsClampingTests(unittest.TestCase):
    """Regression test for a real Low-severity finding from a security/
    performance audit (2026-08-31): confirm()'s modal window dimensions
    (h/w) were never bounds-clamped against the actual screen size,
    unlike every other curses call in this module (safe_curs_set() /
    _safe_addstr(), same class of gap, already fixed there).
    curses.newwin() raises curses.error whenever a requested window
    doesn't fit the terminal (a long confirmation string on a narrow
    PTY, or a mid-session resize) -- previously unhandled, crashing the
    whole anetbbs-cfg tool. Fixed by clamping h/w to the current screen
    size and failing soft (returning the caller's default answer) if
    even a minimal window still can't be created."""

    def setUp(self):
        curses.LINES = 24
        curses.COLS = 80

    def tearDown(self):
        del curses.LINES
        del curses.COLS

    def test_newwin_failure_returns_the_safe_default_no(self):
        from anetbbs.cfg import ui

        def _raises(*_args, **_kwargs):
            raise curses.error("newwin() returned ERR")

        orig = curses.newwin
        curses.newwin = _raises
        try:
            result = ui.confirm(None, "Are you sure?", default_no=True)
        finally:
            curses.newwin = orig
        self.assertFalse(result,
                         'a window-creation failure must fail soft to the '
                         "caller's default answer, not crash")

    def test_newwin_failure_returns_the_safe_default_yes(self):
        from anetbbs.cfg import ui

        def _raises(*_args, **_kwargs):
            raise curses.error("newwin() returned ERR")

        orig = curses.newwin
        curses.newwin = _raises
        try:
            result = ui.confirm(None, "Continue?", default_no=False)
        finally:
            curses.newwin = orig
        self.assertTrue(result)

    def test_window_dimensions_never_exceed_the_screen(self):
        """A very long confirmation text (more lines/width than the
        24x80 screen configured above) must still produce a newwin()
        call whose h/w fit on screen, not the raw unclamped size."""
        from anetbbs.cfg import ui

        captured = {}

        class _FakeWin:
            def box(self):
                pass

            def refresh(self):
                pass

            def getch(self):
                return ord('n')

            def getmaxyx(self):
                return (captured['h'], captured['w'])

            def addstr(self, *a, **k):
                pass

        def _fake_newwin(h, w, y, x):
            captured['h'] = h
            captured['w'] = w
            return _FakeWin()

        long_text = '\n'.join(f'line {i} ' + 'x' * 200 for i in range(100))
        orig = curses.newwin
        curses.newwin = _fake_newwin
        try:
            ui.confirm(None, long_text, default_no=True)
        finally:
            curses.newwin = orig

        self.assertLessEqual(captured['h'], curses.LINES)
        self.assertLessEqual(captured['w'], curses.COLS)


if __name__ == '__main__':
    unittest.main()
