"""Regression tests for the new web ANSI editor drawing tools added
toward ANetDRAW feature parity (ellipse, shading brush, half-block
pixel mode) -- anetbbs/templates/ansi_editor/_editor_widget.html.

Same pattern as tests/test_mrc_web_multiline_split_parity.py: extract
the REAL function bodies from the actual template with a regex and run
them under Node, rather than reimplementing the logic in Python and
risking silent drift between the two (exactly the bug class that
established this pattern in the first place). These three functions
(ellipsePoints, nextShadeChar, computeHalfBlockCell) were specifically
split out of their DOM-touching callers (drawEllipse, shadeBrush,
paintHalfBlock) so they'd be extractable and testable this way at all.
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


def _ellipse_points_js():
    return _extract(r"function ellipsePoints\(.*?\n    \}\n")


def _shade_js():
    return _extract(r"const SHADE_SEQUENCE = .*?\n.*?function nextShadeChar\(.*?\n    \}\n")


def _half_block_js():
    return _extract(r"function halfBlockColors\(.*?\n    \}\n\n    .*?function computeHalfBlockCell\(.*?\n    \}\n")


def _run_node(js_body, call_expr):
    script = js_body + f"\nconsole.log(JSON.stringify({call_expr}));\n"
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


@unittest.skipUnless(shutil.which("node"), "node not available")
class EllipsePointsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = _ellipse_points_js()

    def _points(self, r0, c0, r1, c1):
        # ellipsePoints returns a Set of "r,c" strings -- serialize via
        # Array.from for JSON.
        return _run_node(self.js, f"Array.from(ellipsePoints({r0},{c0},{r1},{c1}))")

    def test_reaches_all_four_edges_of_bounding_box(self):
        pts = self._points(0, 0, 9, 19)
        rows = [int(p.split(',')[0]) for p in pts]
        cols = [int(p.split(',')[1]) for p in pts]
        self.assertLessEqual(min(rows), 1)
        self.assertGreaterEqual(max(rows), 8)
        self.assertLessEqual(min(cols), 1)
        self.assertGreaterEqual(max(cols), 18)

    def test_degenerate_single_point_does_not_crash(self):
        pts = self._points(5, 5, 5, 5)
        self.assertGreater(len(pts), 0)

    def test_no_duplicate_points_from_dense_angle_sampling(self):
        pts = self._points(0, 0, 3, 3)
        self.assertEqual(len(pts), len(set(pts)))


@unittest.skipUnless(shutil.which("node"), "node not available")
class ShadeSequenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = _shade_js()

    def _next(self, ch):
        return _run_node(self.js, f"nextShadeChar({json.dumps(ch)})")

    def test_blank_advances_to_light_shade(self):
        self.assertEqual(self._next(' '), '░')

    def test_full_progression_ends_at_solid(self):
        ch = ' '
        seen = [ch]
        for _ in range(6):
            ch = self._next(ch)
            seen.append(ch)
        self.assertEqual(seen[-1], '█')
        self.assertEqual(seen[-2], '█')  # caps out, doesn't wrap

    def test_unrelated_character_treated_as_blank(self):
        self.assertEqual(self._next('X'), '░')


@unittest.skipUnless(shutil.which("node"), "node not available")
class HalfBlockPixelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = _half_block_js()

    def _compute(self, cell, which, color):
        return _run_node(
            self.js,
            f"computeHalfBlockCell({json.dumps(cell)}, {json.dumps(which)}, {color})")

    def test_painting_top_of_blank_cell_preserves_bg(self):
        cell = self._compute({'c': ' ', 'fg': 7, 'bg': 0}, 'top', 4)
        self.assertEqual(cell, {'c': '▀', 'fg': 4, 'bg': 0})

    def test_painting_bottom_after_top_preserves_top_color(self):
        cell = self._compute({'c': ' ', 'fg': 7, 'bg': 0}, 'top', 4)
        cell = self._compute(cell, 'bottom', 9)
        self.assertEqual(cell, {'c': '▀', 'fg': 4, 'bg': 9})

    def test_same_color_both_halves_collapses_to_solid_block(self):
        cell = self._compute({'c': ' ', 'fg': 7, 'bg': 0}, 'top', 12)
        cell = self._compute(cell, 'bottom', 12)
        self.assertEqual(cell['c'], '█')
        self.assertEqual(cell['fg'], 12)

    def test_reads_existing_lower_half_block_character_correctly(self):
        # A cell already using ▄ (e.g. picked from the character
        # palette, or loaded from existing art) has its colors SWAPPED
        # relative to ▀ -- painting its top half must not silently
        # corrupt the bottom.
        cell = self._compute({'c': '▄', 'fg': 5, 'bg': 2}, 'top', 8)
        # Before: ▄ fg=5 bg=2 means top=bg(2) bottom=fg(5).
        # After painting top=8: top=8, bottom stays 5 -- re-encoded as
        # ▀ fg=8 bg=5 (▀ encodes fg=top/bg=bottom).
        self.assertEqual(cell, {'c': '▀', 'fg': 8, 'bg': 5})


if __name__ == '__main__':
    unittest.main()
