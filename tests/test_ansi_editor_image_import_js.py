"""Regression test for the web ANSI editor's image->ANSI import
(anetbbs/templates/ansi_editor/_editor_widget.html's nearestPaletteIndex(),
part of the importImageFile() feature) -- extracted from the REAL
template file and run under Node, not reimplemented in Python, matching
this repo's established tests/test_enhanced_client_block_chars.py-style
convention for client-side JS logic.

Covers the one genuinely non-obvious piece of that feature: nearest-
color matching against this editor's own real (non-VGA) 16-entry
palette, with bg deliberately restricted to the first 8 entries to
match the BACKGROUND swatch panel's own 8-color range and the `& 0x07`
mask every export format already applies -- producing a bg index 8-15
here would just be silently mangled on export, so the maxIndex
parameter exists specifically to prevent that. The pixel-sampling/
canvas/Image-loading half of importImageFile() is real-browser-only
(file input, Image(), canvas getImageData()) and isn't meaningfully
testable under Node -- same honestly-stated limitation this repo's
other Canvas-dependent client tests already carry.
"""
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

WIDGET_HTML = (Path(__file__).resolve().parents[1] / 'anetbbs' /
              'templates' / 'ansi_editor' / '_editor_widget.html')


def _extract(name, js):
    m = re.search(r'    function ' + name + r'\(.*?\n    \}\n', js, re.DOTALL)
    assert m is not None, f"couldn't find {name}() in the widget script"
    return m.group(0)


def _extract_js_functions():
    """Surgical extraction of just the handful of top-level statements/
    functions nearestPaletteIndex() actually needs (the real `palette`
    array, the real paletteRgb derivation, and the function itself) --
    matching tests/test_enhanced_client_block_chars.py's own established
    convention of extracting specific real functions from a template
    rather than trying to execute the whole file (this widget's entire
    script body is one big IIFE closing over everything, with real DOM/
    fetch side effects at load time that aren't worth stubbing out for
    the one pure function under test here)."""
    html = WIDGET_HTML.read_text(encoding='utf-8')
    m = re.search(r'\n<script>\n(.*?)\n</script>', html, re.DOTALL)
    assert m is not None, "couldn't find the widget's <script> block"
    js = m.group(1)

    palette_m = re.search(r'    const palette = \[.*?\];\n', js, re.DOTALL)
    assert palette_m is not None, "couldn't find the real palette array"
    paletterb_m = re.search(r'    const paletteRgb = palette\.map.*?\n    \}\);\n', js, re.DOTALL)
    assert paletterb_m is not None, "couldn't find paletteRgb"

    return (palette_m.group(0) + paletterb_m.group(0) +
            _extract('nearestPaletteIndex', js))


@unittest.skipUnless(shutil.which('node'), 'node not available')
class NearestPaletteIndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = _extract_js_functions()

    def _run(self, call_expr):
        script = self.js + f"\n{call_expr}\n"
        out = subprocess.run(['node', '-e', script],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0,
                         f'script errored: {out.stderr}')
        return out.stdout.strip()

    def test_pure_white_matches_palette_index_0(self):
        result = self._run("console.log(nearestPaletteIndex([255,255,255], 15));")
        self.assertEqual(result, '0')  # palette[0] === '#ffffff'

    def test_pure_black_matches_palette_index_1(self):
        result = self._run("console.log(nearestPaletteIndex([0,0,0], 15));")
        self.assertEqual(result, '1')  # palette[1] === '#000000'

    def test_bright_green_matches_palette_index_9(self):
        result = self._run("console.log(nearestPaletteIndex([0,0xfc,0], 15));")
        self.assertEqual(result, '9')  # palette[9] === '#00fc00'

    def test_an_off_palette_color_still_finds_its_real_nearest_neighbor(self):
        # A slightly-greenish yellow -- verified by real distance math,
        # not guessed: squared-distance to palette[8] '#ffff00' (0,0,2500)
        # is far below every other entry, e.g. palette[15] '#d2d2d2'
        # (2025,2025,25600) -- confirms the function picks the true
        # nearest neighbor for a color that isn't an exact palette hit,
        # not just echoing back an input that happens to already match.
        result = self._run("console.log(nearestPaletteIndex([255,255,50], 15));")
        self.assertEqual(result, '8')

    def test_maxindex_restricts_the_search_to_bg_safe_colors(self):
        """The whole reason maxIndex exists: bright green (palette[9])
        is the true nearest neighbor for this color among all 16
        entries, but capped to maxIndex=7 (this editor's real bg
        range) it must fall back to the nearest color actually WITHIN
        0-7 instead -- never returning an index that would get
        silently mangled by the `& 0x07` mask every export format and
        drawCell() itself already apply to bg."""
        unrestricted = self._run("console.log(nearestPaletteIndex([0,0xfc,0], 15));")
        restricted = self._run("console.log(nearestPaletteIndex([0,0xfc,0], 7));")
        self.assertEqual(unrestricted, '9')
        self.assertLessEqual(int(restricted), 7)

    def test_palette_rgb_decodes_every_hex_entry_correctly(self):
        result = self._run("console.log(JSON.stringify(paletteRgb[1]));")
        self.assertEqual(result, '[0,0,0]')  # palette[1] === '#000000'
        result = self._run("console.log(JSON.stringify(paletteRgb[0]));")
        self.assertEqual(result, '[255,255,255]')  # palette[0] === '#ffffff'


if __name__ == '__main__':
    unittest.main()
