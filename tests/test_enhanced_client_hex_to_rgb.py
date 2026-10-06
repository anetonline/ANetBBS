"""Regression test for _hexToRgb() in anetbbs/enhanced_client/index.html
-- the pure-logic piece of the pixel-perfect-font fix (see
_drawRunPixelPerfect()'s own docstring: a real live bug where the
browser's font rasterizer anti-aliased glyph edges even at the font's
native pixel size, requiring a hard alpha-threshold pass that needs
the run's foreground color as RGB to rebuild each opaque pixel).

Extracted from the real file and run under Node (not reimplemented),
matching this repo's established convention (see
test_mrc_web_multiline_split_parity.py) -- most of
_drawRunPixelPerfect() is Canvas/DOM-dependent and can't be
meaningfully unit tested outside a real browser, but this one function
is pure string-to-numbers logic.
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


def _extract_hex_to_rgb_js():
    html = CLIENT_HTML.read_text()
    m = re.search(r"function _hexToRgb\(hex\) \{.*?\n  \}\n", html, re.DOTALL)
    assert m is not None, "couldn't find _hexToRgb() in index.html"
    return m.group(0)


@unittest.skipUnless(shutil.which('node'), 'node not available')
class HexToRgbTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js_fn = _extract_hex_to_rgb_js()

    def _call(self, hex_str):
        script = (
            self.js_fn +
            "\nconsole.log(JSON.stringify(_hexToRgb(process.argv[1])));\n"
        )
        out = subprocess.run(['node', '-e', script, hex_str],
                             capture_output=True, text=True, check=True)
        return json.loads(out.stdout)

    def test_six_digit_hex(self):
        self.assertEqual(self._call('#39d0d8'), [0x39, 0xd0, 0xd8])

    def test_pure_colors(self):
        self.assertEqual(self._call('#ff0000'), [255, 0, 0])
        self.assertEqual(self._call('#00ff00'), [0, 255, 0])
        self.assertEqual(self._call('#0000ff'), [0, 0, 255])

    def test_three_digit_shorthand_expands_correctly(self):
        self.assertEqual(self._call('#f0a'), [0xff, 0x00, 0xaa])

    def test_black_and_white(self):
        self.assertEqual(self._call('#000000'), [0, 0, 0])
        self.assertEqual(self._call('#ffffff'), [255, 255, 255])


class EnhancedClientNotLabeledTestVersionTests(unittest.TestCase):
    """Real audit finding: the Enhanced Client overlay carried a
    leftover "(TEST)"/"TEST VERSION" label in its title, status line,
    and connected-message text from its original internal testing
    phase -- stale now that it's a real shipped, publicly-downloadable
    add-on (see docs/37-enhanced-client.md and Admin -> Add-ons)."""

    def test_no_test_labeling_remains_in_shipped_markup(self):
        html = CLIENT_HTML.read_text()
        self.assertNotIn('(TEST)', html)
        self.assertNotIn('TEST VERSION', html)


if __name__ == '__main__':
    unittest.main()
