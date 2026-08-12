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


if __name__ == '__main__':
    unittest.main()
