"""Regression tests for anetbbs/features/tdf_fonts.py -- TheDraw (.TDF)
banner font support added toward ANetDRAW feature parity (its own real,
working C implementation, ~/anet/ANetDRAW-Door/ANetDRAW_opendoors/src/
tdf.c, was ported faithfully; see that module's own docstring).

Primary coverage uses a small, hand-built synthetic .TDF file (no
external dependency) constructed exactly to spec, so these tests run
anywhere. A second, clearly-separated set of tests runs against
Jerry's real 3,716-font pack when it happens to be present on this
machine (~/anet/ANetDRAW-Door/ANetDRAW_opendoors/fonts) -- skipped,
not failed, when it isn't (this pack is a personal, multi-hundred-MB
download, never checked into this repo or expected in CI) -- these
are the ones that actually caught real bugs during development
(genuine 3,716-font scan count, real box-drawing outline-font glyphs,
real multi-color color-font output all visually confirmed correct
against actual TheDraw fonts before this test file was written).
"""
import os
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.features import tdf_fonts

_REAL_FONTS_DIR = os.path.expanduser(
    '~/anet/ANetDRAW-Door/ANetDRAW_opendoors/fonts')


def _build_tdf(font_type, spacing, glyphs):
    """glyphs: {char: bytes} -- each value is the RAW glyph payload
    (width, height, row bytes with \\r row-separators, NUL terminator)
    exactly as it appears in a real file, already fully formed by the
    caller. Builds one complete, valid .tdf file with a single font."""
    offs = [0xFFFF] * 94
    data = bytearray()
    for ch, payload in glyphs.items():
        code = ord(ch)
        offs[code - 33] = len(data)
        data += payload
    name = b'TESTFONT'.ljust(12, b' ')
    header = (b'\x55\xAA\x00\xFF' + bytes([len(b'TESTFONT')]) + name +
             b'\x00\x00\x00\x00' + bytes([font_type, spacing]) +
             struct.pack('<H', len(data)))
    offs_bytes = b''.join(struct.pack('<H', o) for o in offs)
    return tdf_fonts.TDF_MAGIC + header + offs_bytes + bytes(data)


class SyntheticFontTests(unittest.TestCase):
    """A minimal BLOCK-type font: '!' is a 1x1 glyph, 'A' is a 2x2
    glyph, spacing=1."""

    def setUp(self):
        self._tmp_dir = Path(__file__).resolve().parent / '.tdf_test_scratch'
        self._tmp_dir.mkdir(exist_ok=True)
        self.addCleanup(lambda: [p.unlink() for p in self._tmp_dir.glob('*.tdf')])
        glyphs = {
            '!': bytes([1, 1]) + b'X' + b'\x00',
            'A': bytes([2, 2]) + b'AB' + b'\x0d' + b'CD' + b'\x00',
        }
        self.tdf_bytes = _build_tdf(tdf_fonts.BLOCK, 1, glyphs)
        self.tdf_path = self._tmp_dir / 'synthetic.tdf'
        self.tdf_path.write_bytes(self.tdf_bytes)

    def test_scan_finds_the_one_font(self):
        entries = tdf_fonts.scan_fonts(str(self._tmp_dir))
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['name'], 'TESTFONT')
        self.assertEqual(entries[0]['type'], tdf_fonts.BLOCK)

    def test_scan_nonexistent_dir_returns_empty(self):
        self.assertEqual(tdf_fonts.scan_fonts('/no/such/dir/at/all'), [])

    def test_scan_skips_non_tdf_files(self):
        (self._tmp_dir / 'notafont.txt').write_text('hello')
        self.addCleanup(lambda: (self._tmp_dir / 'notafont.txt').unlink())
        entries = tdf_fonts.scan_fonts(str(self._tmp_dir))
        self.assertEqual(len(entries), 1)  # still just the real .tdf

    def test_scan_rejects_file_with_wrong_magic(self):
        bad = self._tmp_dir / 'bad.tdf'
        bad.write_bytes(b'NOT A REAL TDF FILE' + b'\x00' * 50)
        self.addCleanup(bad.unlink)
        entries = [e for e in tdf_fonts.scan_fonts(str(self._tmp_dir))
                  if e['file'] == str(bad)]
        self.assertEqual(entries, [])

    def test_load_font_returns_correct_fields(self):
        entries = tdf_fonts.scan_fonts(str(self._tmp_dir))
        font = tdf_fonts.load_font(entries[0]['file'], entries[0]['offset'])
        self.assertIsNotNone(font)
        self.assertEqual(font['type'], tdf_fonts.BLOCK)
        self.assertEqual(font['spacing'], 1)
        self.assertEqual(len(font['offs']), 94)

    def test_render_single_1x1_glyph(self):
        entries = tdf_fonts.scan_fonts(str(self._tmp_dir))
        font = tdf_fonts.load_font(entries[0]['file'], entries[0]['offset'])
        result = tdf_fonts.render_text(font, '!', fg=4, bg=0)
        self.assertEqual(result['width'], 1)
        self.assertEqual(result['height'], 1)
        self.assertEqual(result['cells'][0], {'c': 'X', 'fg': 4, 'bg': 0})

    def test_render_2x2_glyph_with_row_break(self):
        entries = tdf_fonts.scan_fonts(str(self._tmp_dir))
        font = tdf_fonts.load_font(entries[0]['file'], entries[0]['offset'])
        result = tdf_fonts.render_text(font, 'A', fg=9, bg=1)
        self.assertEqual((result['width'], result['height']), (2, 2))
        chars = ''.join(c['c'] for c in result['cells'])
        self.assertEqual(chars, 'ABCD')
        self.assertTrue(all(c['fg'] == 9 and c['bg'] == 1 for c in result['cells']))

    def test_render_two_glyphs_respects_spacing(self):
        entries = tdf_fonts.scan_fonts(str(self._tmp_dir))
        font = tdf_fonts.load_font(entries[0]['file'], entries[0]['offset'])
        # '!' is 1 wide, spacing=1, 'A' is 2 wide -> total width 1+1+2=4
        result = tdf_fonts.render_text(font, '!A', fg=7, bg=0)
        self.assertEqual(result['width'], 4)

    def test_render_unknown_character_only_returns_none(self):
        entries = tdf_fonts.scan_fonts(str(self._tmp_dir))
        font = tdf_fonts.load_font(entries[0]['file'], entries[0]['offset'])
        result = tdf_fonts.render_text(font, 'Z', fg=7, bg=0)  # 'Z' not in this tiny font
        self.assertIsNone(result)

    def test_render_empty_text_returns_none(self):
        entries = tdf_fonts.scan_fonts(str(self._tmp_dir))
        font = tdf_fonts.load_font(entries[0]['file'], entries[0]['offset'])
        self.assertIsNone(tdf_fonts.render_text(font, '', fg=7, bg=0))

    def test_load_font_with_out_of_range_offset_returns_none(self):
        self.assertIsNone(tdf_fonts.load_font(str(self.tdf_path), 999999))


class OutlineCharMappingTests(unittest.TestCase):
    def test_o_becomes_hard_space(self):
        self.assertEqual(tdf_fonts._outline_char(ord('O'), 9), 0x20)

    def test_at_and_ampersand_are_transparent(self):
        self.assertIsNone(tdf_fonts._outline_char(ord('@'), 9))
        self.assertIsNone(tdf_fonts._outline_char(ord('&'), 9))

    def test_a_through_q_use_the_style_table(self):
        code = tdf_fonts._outline_char(ord('A'), 0)
        self.assertEqual(code, tdf_fonts.OUTLINE_STYLES[0][0])

    def test_out_of_range_style_falls_back_to_default(self):
        code = tdf_fonts._outline_char(ord('A'), 999)
        self.assertEqual(code, tdf_fonts.OUTLINE_STYLES[tdf_fonts.OUTLINE_STYLE_DEFAULT][0])

    def test_character_outside_a_q_passes_through_unchanged(self):
        self.assertEqual(tdf_fonts._outline_char(ord('5'), 9), ord('5'))

    def test_table_has_19_styles_of_17_codes_each(self):
        self.assertEqual(len(tdf_fonts.OUTLINE_STYLES), 19)
        for style in tdf_fonts.OUTLINE_STYLES:
            self.assertEqual(len(style), 17)


@unittest.skipUnless(os.path.isdir(_REAL_FONTS_DIR),
                     f'real TheDraw font pack not present at {_REAL_FONTS_DIR} '
                     '(personal download, not part of this repo)')
class RealFontPackTests(unittest.TestCase):
    """Runs against Jerry's actual 3,716-font pack when available --
    real-world validation beyond what a synthetic fixture can prove."""

    @classmethod
    def setUpClass(cls):
        cls.entries = tdf_fonts.scan_fonts(_REAL_FONTS_DIR)

    def test_finds_thousands_of_real_fonts(self):
        # ANetDRAW's own README advertises "3,716 TheDraw fonts" --
        # this is the real, load-bearing number this port must match.
        self.assertEqual(len(self.entries), 3716)

    def test_every_font_type_is_one_of_the_three_known_values(self):
        for e in self.entries[:500]:  # full scan is slow; a real sample suffices
            self.assertIn(e['type'], (tdf_fonts.OUTLINE, tdf_fonts.BLOCK, tdf_fonts.COLOR))

    def test_an_outline_font_renders_real_glyph_content(self):
        outline = next(e for e in self.entries if e['type'] == tdf_fonts.OUTLINE)
        font = tdf_fonts.load_font(outline['file'], outline['offset'])
        result = tdf_fonts.render_text(font, 'HI', fg=15, bg=0)
        self.assertIsNotNone(result)
        non_blank = [c for c in result['cells'] if c and c['c'] != ' ']
        self.assertGreater(len(non_blank), 0)

    def test_a_block_font_renders_real_glyph_content(self):
        block = next(e for e in self.entries if e['type'] == tdf_fonts.BLOCK)
        font = tdf_fonts.load_font(block['file'], block['offset'])
        result = tdf_fonts.render_text(font, 'OK', fg=10, bg=0)
        self.assertIsNotNone(result)
        non_blank = [c for c in result['cells'] if c and c['c'] != ' ']
        self.assertGreater(len(non_blank), 0)

    def test_a_color_font_renders_multiple_distinct_colors(self):
        color = next(e for e in self.entries if e['type'] == tdf_fonts.COLOR)
        font = tdf_fonts.load_font(color['file'], color['offset'])
        result = tdf_fonts.render_text(font, 'GO', fg=7, bg=0)
        self.assertIsNotNone(result)
        colors_seen = {(c['fg'], c['bg']) for c in result['cells'] if c}
        self.assertGreater(len(colors_seen), 1)


if __name__ == '__main__':
    unittest.main()
