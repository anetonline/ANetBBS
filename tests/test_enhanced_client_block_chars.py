"""Regression test for _drawBlockChar()/_drawShade() in
anetbbs/enhanced_client/index.html -- the real fix for a live bug
confirmed via a side-by-side SyncTerm comparison (Pi3 test,
2026-10-02): SyncTerm rendered a robot pixel-art screen as perfectly
solid, gap-free color regions; the Enhanced Client showed visible
hairline gaps between adjacent full-block (U+2588) characters for the
EXACT SAME content. Root cause: the IBM VGA font's own glyph outline
for block-element characters, as rasterized by the browser's font
engine, doesn't fill its cell edge-to-edge (unlike box-drawing/text
glyphs, which DO render cleanly through the font -- confirmed via the
lightbar/login menu screenshots). An earlier native-size-render +
hard-alpha-threshold fix (aimed at an antialiasing theory) had ZERO
effect on this specific gap, which was itself evidence the real cause
was different -- no amount of alpha cleanup fixes a glyph that's
honestly smaller than its cell by the font's own design.

Fixed by never asking the font to draw CP437 Block Elements
(U+2580-2593) at all -- drawn as direct geometric ctx.fillRect() calls
instead, the same way real terminal emulators (SyncTerm included)
handle these specific characters.

Unlike the rest of the pixel-perfect-font fix (which is fundamentally
Canvas/DOM/font-rasterizer-dependent and can't be meaningfully unit
tested outside a real browser -- see test_enhanced_client_hex_to_rgb.py's
own docstring), THIS piece is pure geometry with no font involved at
all, so it can be verified for real: extracted from the actual file and
run under Node (not reimplemented) against a fake ctx that records
every fillRect() call, confirming each block character's coverage is
gap-free and exactly matches its cell -- not approximated, not
reasoned about, actually computed and checked.
"""
import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CLIENT_HTML = Path(__file__).resolve().parents[1] / 'anetbbs' / 'enhanced_client' / 'index.html'


def _extract(name, html):
    m = re.search(r"function " + name + r"\(.*?\n  \}\n", html, re.DOTALL)
    assert m is not None, f"couldn't find {name}() in index.html"
    return m.group(0)


def _extract_block_fns():
    html = CLIENT_HTML.read_text()
    return _extract('_drawBlockChar', html) + '\n' + _extract('_drawShade', html)


_HARNESS = """
const NATIVE_CW = 9, NATIVE_CH = 16;
const calls = [];
const ctx = {
  fillStyle: null,
  fillRect: function(x, y, w, h) { calls.push([x, y, w, h]); },
};
"""


@unittest.skipUnless(shutil.which('node'), 'node not available')
class DrawBlockCharTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js_fns = _extract_block_fns()

    def _run(self, call_expr):
        script = (
            _HARNESS + self.js_fns +
            f"\n{call_expr}\n"
            "console.log(JSON.stringify(calls));\n"
        )
        out = subprocess.run(['node', '-e', script],
                             capture_output=True, text=True, check=True)
        return json.loads(out.stdout)

    def test_full_block_is_one_fillrect_covering_the_whole_cell_no_gap(self):
        calls = self._run("_drawBlockChar('\\u2588', 100, 200, 18, 32, '#ff0000');")
        self.assertEqual(calls, [[100, 200, 18, 32]])

    def test_upper_half_block_covers_exactly_the_top_half(self):
        calls = self._run("_drawBlockChar('\\u2580', 0, 0, 18, 32, '#fff');")
        self.assertEqual(calls, [[0, 0, 18, 16]])

    def test_lower_half_block_covers_exactly_the_bottom_half(self):
        calls = self._run("_drawBlockChar('\\u2584', 0, 0, 18, 32, '#fff');")
        self.assertEqual(calls, [[0, 16, 18, 16]])

    def test_left_and_right_half_blocks_together_cover_the_whole_cell_no_overlap_no_gap(self):
        left = self._run("_drawBlockChar('\\u258c', 0, 0, 18, 32, '#fff');")
        right = self._run("_drawBlockChar('\\u2590', 0, 0, 18, 32, '#fff');")
        self.assertEqual(left, [[0, 0, 9, 32]])
        self.assertEqual(right, [[9, 0, 9, 32]])
        # Together: 0-9 and 9-18, exactly the full 18px width, no gap/overlap.
        self.assertEqual(left[0][0] + left[0][2], right[0][0])
        self.assertEqual(right[0][0] + right[0][2], 18)

    def test_non_block_character_is_not_handled_here_no_fillrect_called(self):
        calls = self._run("var handled = _drawBlockChar('X', 0, 0, 18, 32, '#fff'); "
                          "calls.push(['handled', handled]);")
        self.assertEqual(calls, [['handled', False]])

    def test_shade_characters_produce_increasing_coverage_light_to_dark(self):
        def _coverage(level_char):
            calls = self._run(f"_drawBlockChar('{level_char}', 0, 0, 18, 32, '#fff');")
            return sum(c[2] * c[3] for c in calls)

        light = _coverage('\\u2591')
        medium = _coverage('\\u2592')
        dark = _coverage('\\u2593')
        cell_area = 18 * 32
        self.assertLess(light, medium)
        self.assertLess(medium, dark)
        self.assertLess(dark, cell_area)   # never a secretly-full block
        self.assertGreater(light, 0)       # never literally nothing

    def test_shade_dither_pattern_tiles_identically_across_adjacent_cells(self):
        """Adjacent shade-character cells must produce the SAME local
        dither pattern (each cell's pattern is computed from its own
        local 0..NATIVE_CW/NATIVE_CH coordinates) -- otherwise a run
        of shade characters would look inconsistent/patchy rather than
        a uniform texture."""
        cell_a = self._run("_drawBlockChar('\\u2592', 0, 0, 18, 32, '#fff');")
        cell_b = self._run("_drawBlockChar('\\u2592', 180, 0, 18, 32, '#fff');")
        # Same shape/count of fills, just offset by the cell's own x position.
        self.assertEqual(len(cell_a), len(cell_b))
        offsets_a = [c[0] for c in cell_a]
        offsets_b = [c[0] - 180 for c in cell_b]
        self.assertEqual(sorted(offsets_a), sorted(offsets_b))


if __name__ == '__main__':
    unittest.main()
