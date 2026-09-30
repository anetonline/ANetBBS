"""Regression tests for the web ANSI editor's Phase 2 block operations
added toward ANetDRAW feature parity (swap colors, replace color,
center, insert/delete row/column) -- anetbbs/templates/ansi_editor/
_editor_widget.html.

Same extract-and-run-under-Node pattern as
tests/test_ansi_editor_new_tools_js.py -- swapFgBg, replaceFgColor, and
computeCenterShift are pure and extracted directly. insertRowAt/
deleteRowAt/insertColAt/deleteColAt aren't pure (they touch the
module-scope `cells`/W/H/drawCell/pushHistory/dirty/saveStatus), so the
harness stubs those exact names before evaluating the real function
body -- still the real code, not a reimplementation, just with its
outer-scope dependencies supplied instead of a real DOM.
"""
import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = REPO_ROOT / 'anetbbs' / 'templates' / 'ansi_editor' / '_editor_widget.html'


def _extract(pattern):
    html = TEMPLATE_PATH.read_text()
    m = re.search(pattern, html, re.DOTALL)
    assert m is not None, f"couldn't find pattern {pattern!r} in {TEMPLATE_PATH}"
    return m.group(0)


def _run_node(js_body, call_expr):
    script = js_body + f"\nconsole.log(JSON.stringify({call_expr}));\n"
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


@unittest.skipUnless(shutil.which("node"), "node not available")
class SwapFgBgTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = _extract(r"function swapFgBg\(.*?\n    \}\n")

    def _swap(self, cell):
        return _run_node(self.js, f"swapFgBg({json.dumps(cell)})")

    def test_swaps_fg_and_bg(self):
        self.assertEqual(self._swap({'c': 'X', 'fg': 4, 'bg': 2}),
                         {'c': 'X', 'fg': 2, 'bg': 4})

    def test_bright_fg_masked_into_valid_bg_range(self):
        # fg can be 8-15 (bright), but this editor's bg palette only
        # has 8 entries -- swapping a bright fg into bg must mask it
        # down rather than produce an out-of-range value.
        result = self._swap({'c': 'X', 'fg': 14, 'bg': 1})
        self.assertEqual(result['fg'], 1)
        self.assertEqual(result['bg'], 14 & 0x07)

    def test_character_is_unaffected(self):
        self.assertEqual(self._swap({'c': '#', 'fg': 0, 'bg': 0})['c'], '#')


@unittest.skipUnless(shutil.which("node"), "node not available")
class ReplaceFgColorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = _extract(r"function replaceFgColor\(.*?\n    \}\n")

    def _replace(self, cell, from_fg, to_fg):
        return _run_node(self.js, f"replaceFgColor({json.dumps(cell)}, {from_fg}, {to_fg})")

    def test_matching_fg_is_replaced(self):
        result = self._replace({'c': 'X', 'fg': 4, 'bg': 0}, 4, 9)
        self.assertEqual(result, {'c': 'X', 'fg': 9, 'bg': 0})

    def test_non_matching_fg_is_left_alone(self):
        result = self._replace({'c': 'X', 'fg': 4, 'bg': 0}, 5, 9)
        self.assertEqual(result, {'c': 'X', 'fg': 4, 'bg': 0})

    def test_bg_untouched_by_fg_replacement(self):
        result = self._replace({'c': 'X', 'fg': 4, 'bg': 3}, 4, 9)
        self.assertEqual(result['bg'], 3)


@unittest.skipUnless(shutil.which("node"), "node not available")
class ComputeCenterShiftTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = _extract(r"function computeCenterShift\(.*?\n    \}\n")

    def _shift(self, occ_min, occ_max, c0, width):
        return _run_node(self.js, f"computeCenterShift({occ_min}, {occ_max}, {c0}, {width})")

    def test_already_centered_needs_no_shift(self):
        # 80-wide region, content occupies cols 35-44 (10 wide, exactly centered)
        self.assertEqual(self._shift(35, 44, 0, 80), 0)

    def test_left_aligned_content_shifts_right(self):
        # 80-wide region, a 10-wide block sitting at the far left (0-9)
        # should shift right by 35 to land at 35-44.
        self.assertEqual(self._shift(0, 9, 0, 80), 35)

    def test_right_aligned_content_shifts_left(self):
        self.assertEqual(self._shift(70, 79, 0, 80), -35)

    def test_empty_region_returns_zero(self):
        # occupiedMaxCol < occupiedMinCol signals "nothing occupied".
        self.assertEqual(self._shift(10, 5, 0, 80), 0)

    def test_respects_a_non_zero_region_start(self):
        # A selection starting at column 20, 10 wide (20-29), content
        # occupying its own leftmost 4 columns (20-23) -- should center
        # within the SELECTION's own width (10), not the whole canvas.
        self.assertEqual(self._shift(20, 23, 20, 10), 3)


@unittest.skipUnless(shutil.which("node"), "node not available")
class RowColumnRippleOpsTests(unittest.TestCase):
    """insertRowAt/deleteRowAt/insertColAt/deleteColAt aren't pure --
    stub their outer-scope dependencies (cells/W/H/drawCell/
    pushHistory/document) and run the REAL function bodies against a
    real small grid."""

    HARNESS = """
    let W = 4, H = 3;
    let cells = [];
    let dirty = false;
    const drawnIndexes = [];
    function drawCell(i) { drawnIndexes.push(i); }
    function pushHistory() {}
    const document = { getElementById: () => ({ set textContent(v) {} }) };
    """

    @classmethod
    def setUpClass(cls):
        cls.insert_row = _extract(r"function insertRowAt\(.*?\n    \}\n")
        cls.delete_row = _extract(r"function deleteRowAt\(.*?\n    \}\n")
        cls.insert_col = _extract(r"function insertColAt\(.*?\n    \}\n")
        cls.delete_col = _extract(r"function deleteColAt\(.*?\n    \}\n")

    def _grid_of_letters(self):
        # 3 rows x 4 cols, each cell's char is a distinct letter so a
        # shift is trivially visible: row0="ABCD", row1="EFGH", row2="IJKL"
        letters = 'ABCDEFGHIJKL'
        return [{'c': ch, 'fg': 7, 'bg': 0} for ch in letters]

    def _run(self, fn_js, call_expr):
        script = (
            self.HARNESS + f"\ncells = {json.dumps(self._grid_of_letters())};\n" +
            fn_js + f"\n{call_expr};\n" +
            "console.log(JSON.stringify(cells.map(c => c.c)));\n"
        )
        out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
        return json.loads(out.stdout)

    def test_insert_row_at_1_shifts_row1_and_row2_down_dropping_the_last_row(self):
        result = self._run(self.insert_row, "insertRowAt(1)")
        # row0 unchanged, row1 becomes blank, row2 becomes old row1,
        # old row2 ("IJKL") is dropped off the bottom entirely.
        self.assertEqual(result[0:4], ['A', 'B', 'C', 'D'])
        self.assertEqual(result[4:8], [' ', ' ', ' ', ' '])
        self.assertEqual(result[8:12], ['E', 'F', 'G', 'H'])

    def test_delete_row_at_0_shifts_everything_up_blanking_the_last_row(self):
        result = self._run(self.delete_row, "deleteRowAt(0)")
        self.assertEqual(result[0:4], ['E', 'F', 'G', 'H'])
        self.assertEqual(result[4:8], ['I', 'J', 'K', 'L'])
        self.assertEqual(result[8:12], [' ', ' ', ' ', ' '])

    def test_insert_col_at_1_shifts_right_dropping_the_last_column(self):
        result = self._run(self.insert_col, "insertColAt(1)")
        # row0 "ABCD" -> "A DC"? No -- col1 becomes blank, old col1
        # shifts to col2, old col2 to col3, old col3 ("D") dropped.
        self.assertEqual(result[0:4], ['A', ' ', 'B', 'C'])
        self.assertEqual(result[4:8], ['E', ' ', 'F', 'G'])

    def test_delete_col_at_0_shifts_left_blanking_the_last_column(self):
        result = self._run(self.delete_col, "deleteColAt(0)")
        self.assertEqual(result[0:4], ['B', 'C', 'D', ' '])
        self.assertEqual(result[4:8], ['F', 'G', 'H', ' '])


if __name__ == '__main__':
    unittest.main()
