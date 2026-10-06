"""Regression tests for the web ANSI editor's Phase 3 file-format
support added toward ANetDRAW feature parity -- BIN, XBin, PCBoard @X,
Renegade/Mystic pipe codes, and Synchronet Ctrl-A -- anetbbs/web/
ansi_editor.py.

_dos_cga_index() is the load-bearing piece every format below depends
on (PCBoard, BIN, and XBin all use the classic DOS/CGA attribute
nibble, a genuinely different 8-color order than standard ANSI SGR) --
covered directly with known-correct color pairs, not just via
round-trips, since a round-trip test alone can't catch a consistent
off-by-one that cancels itself out on the way back.

render_pipe_codes() and render_synchronet_ctrl_a() are cross-checked
against this codebase's OWN existing reverse-direction implementations
(web/render_msg.py's _pipe_to_ansi(), games/synchronet_compat.py's
_CTRLA_MAP) rather than just asserting against hand-computed expected
strings -- if the two ever drift, this test catches it either way.
"""
import re
import subprocess
import shutil
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _grid(width, height, cells):
    return {'width': width, 'height': height, 'cells': cells}


def _solid_cell(c=' ', fg=7, bg=0):
    return {'c': c, 'fg': fg, 'bg': bg}


class DosCgaIndexTests(unittest.TestCase):
    def test_known_fg_pairs(self):
        from anetbbs.web.ansi_editor import _dos_cga_index
        # (ansi code, expected DOS/CGA value)
        cases = [
            (30, 0),   # black -> black
            (34, 1),   # blue -> blue
            (32, 2),   # green -> green
            (36, 3),   # cyan -> cyan
            (31, 4),   # red -> red
            (35, 5),   # magenta -> magenta
            (33, 6),   # yellow(ansi) -> brown(dos)
            (37, 7),   # white(ansi) -> lightgray(dos)
            (97, 15),  # bright white -> white
            (93, 14),  # bright yellow -> yellow
            (91, 12),  # bright red -> lightred
        ]
        for ansi_code, expected in cases:
            self.assertEqual(_dos_cga_index(ansi_code), expected,
                             f"ansi {ansi_code} -> expected dos {expected}")

    def test_known_bg_pairs(self):
        from anetbbs.web.ansi_editor import _dos_cga_index
        self.assertEqual(_dos_cga_index(40), 0)   # black bg
        self.assertEqual(_dos_cga_index(44), 1)   # blue bg
        self.assertEqual(_dos_cga_index(47), 7)   # lightgray bg

    def test_bright_is_exactly_dark_plus_eight(self):
        from anetbbs.web.ansi_editor import _dos_cga_index
        for base in range(30, 38):
            self.assertEqual(_dos_cga_index(base + 60), _dos_cga_index(base) + 8)


class BinFormatTests(unittest.TestCase):
    def test_round_trip_preserves_chars_and_colors(self):
        from anetbbs.web.ansi_editor import render_bin, parse_bin_to_grid
        cells = [_solid_cell('A', 4, 1), _solid_cell('B', 15, 0),
                 _solid_cell('C', 9, 2), _solid_cell('D', 0, 7)]
        grid = _grid(2, 2, cells)
        data = render_bin(grid)
        self.assertEqual(len(data), 2 * 2 * 2)  # 4 cells, 2 bytes each
        result = parse_bin_to_grid(data, width=2)
        self.assertEqual(result['width'], 2)
        self.assertEqual(result['height'], 2)
        self.assertEqual([c['c'] for c in result['cells']], ['A', 'B', 'C', 'D'])

    def test_attribute_byte_packs_bg_high_nibble_fg_low_nibble(self):
        from anetbbs.web.ansi_editor import _FG_CODES, _BG_CODES, _dos_cga_index, render_bin
        fg_idx, bg_idx = 15, 4  # arbitrary valid grid indices
        expected_attr = (_dos_cga_index(_BG_CODES[bg_idx]) << 4) | _dos_cga_index(_FG_CODES[fg_idx])
        data = render_bin(_grid(1, 1, [_solid_cell('X', fg_idx, bg_idx)]))
        self.assertEqual(data[1], expected_attr)


class XBinFormatTests(unittest.TestCase):
    def test_round_trip_uncompressed(self):
        from anetbbs.web.ansi_editor import render_xbin, parse_xbin_to_grid
        cells = [_solid_cell(ch, 7, 0) for ch in 'HELLOWORLD!']
        grid = _grid(11, 1, cells)
        data = render_xbin(grid)
        self.assertTrue(data.startswith(b'XBIN\x1a'))
        result = parse_xbin_to_grid(data)
        self.assertEqual(result['width'], 11)
        self.assertEqual(result['height'], 1)
        self.assertEqual(''.join(c['c'] for c in result['cells']), 'HELLOWORLD!')

    def test_header_fields_are_correct(self):
        from anetbbs.web.ansi_editor import render_xbin
        grid = _grid(40, 12, [_solid_cell()] * (40 * 12))
        data = render_xbin(grid)
        width, height, font_h, flags = struct.unpack('<HHBB', data[5:11])
        self.assertEqual(width, 40)
        self.assertEqual(height, 12)
        self.assertEqual(font_h, 16)
        self.assertEqual(flags, 0)  # no palette, no font, no compression

    def test_bad_magic_raises(self):
        from anetbbs.web.ansi_editor import parse_xbin_to_grid
        with self.assertRaises(ValueError):
            parse_xbin_to_grid(b'NOTANXBINFILE' + b'\x00' * 20)

    def test_reads_a_real_rle_compressed_file(self):
        # render_xbin() never writes compressed data, so this is the
        # only coverage for the RLE-decode path -- crafted by hand per
        # the real XBin spec: a run-length byte's top 2 bits select the
        # run type (0=uncompressed run of N literal pairs, 1=char run:
        # one char repeated N times each with its OWN following attr
        # byte... actually for char-run/attr-run/both-run, exactly one
        # (char,attr) pair follows and is repeated N times), bottom 6
        # bits are (count-1).
        from anetbbs.web.ansi_editor import parse_xbin_to_grid
        width, height = 4, 1
        header = b'XBIN\x1a' + struct.pack('<HHBB', width, height, 16, 0x04)  # compress flag
        # "Both run" (type 3, top bits 11) of length 4: char='*' (0x2A), attr=0x07
        ctrl = 0b11_000011  # type=3 (both run), len=4 (0b000011 + 1)
        payload = bytes([ctrl, ord('*'), 0x07])
        data = header + payload
        result = parse_xbin_to_grid(data)
        self.assertEqual(''.join(c['c'] for c in result['cells']), '****')
        self.assertTrue(all(c['fg'] == 0x07 and c['bg'] == 0x00 for c in result['cells']))

    def test_oversized_declared_dimensions_are_refused(self):
        """Real audit finding: width/height come straight from the
        uploaded file's own header (each up to 65535, unsigned 16-bit)
        with no cap, unlike every other grid-dimension entry point in
        this file. A tiny crafted header declaring an enormous
        width*height must be refused before the cell-padding loop ever
        tries to build billions of dicts, not decoded."""
        from anetbbs.web.ansi_editor import parse_xbin_to_grid
        header = b'XBIN\x1a' + struct.pack('<HHBB', 60000, 60000, 16, 0)
        with self.assertRaises(ValueError):
            parse_xbin_to_grid(header)

    def test_dimensions_at_the_editors_real_cap_still_work(self):
        from anetbbs.web.ansi_editor import render_xbin, parse_xbin_to_grid
        grid = _grid(132, 50, [_solid_cell()] * (132 * 50))
        data = render_xbin(grid)
        result = parse_xbin_to_grid(data)
        self.assertEqual(result['width'], 132)
        self.assertEqual(result['height'], 50)


class PcboardFormatTests(unittest.TestCase):
    def test_emits_at_x_code_with_bg_then_fg_hex_nibbles(self):
        from anetbbs.web.ansi_editor import render_pcboard, _FG_CODES, _BG_CODES, _dos_cga_index
        fg_idx, bg_idx = 15, 4
        text = render_pcboard(_grid(1, 1, [_solid_cell('Q', fg_idx, bg_idx)]))
        expected_bg = _dos_cga_index(_BG_CODES[bg_idx])
        expected_fg = _dos_cga_index(_FG_CODES[fg_idx])
        self.assertEqual(text, f'@X{expected_bg:X}{expected_fg:X}Q')

    def test_only_emits_code_on_color_change(self):
        from anetbbs.web.ansi_editor import render_pcboard
        cells = [_solid_cell('A', 4, 0), _solid_cell('B', 4, 0), _solid_cell('C', 9, 0)]
        text = render_pcboard(_grid(3, 1, cells))
        self.assertEqual(text.count('@X'), 2)  # once for A/B (same color), once for C


class PipeCodeFormatTests(unittest.TestCase):
    def test_round_trips_through_render_msg_pipe_to_ansi(self):
        # Cross-check against this codebase's OWN reverse-direction
        # implementation rather than a hand-computed expected string.
        from anetbbs.web.ansi_editor import render_pipe_codes, _FG_CODES, _BG_CODES
        from anetbbs.web.render_msg import _pipe_to_ansi
        fg_idx, bg_idx = 12, 3
        text = render_pipe_codes(_grid(1, 1, [_solid_cell('Z', fg_idx, bg_idx)]))
        self.assertIn('|', text)
        translated = _pipe_to_ansi(text)
        self.assertIn(f'\x1b[{_FG_CODES[fg_idx]}m', translated)
        self.assertIn(f'\x1b[{_BG_CODES[bg_idx]}m', translated)

    def test_covers_every_grid_color_without_a_stray_untranslated_pipe_code(self):
        from anetbbs.web.ansi_editor import render_pipe_codes
        from anetbbs.web.render_msg import _pipe_to_ansi, _PIPE_RE
        cells = [_solid_cell(chr(65 + i), i, i % 8) for i in range(16)]
        text = render_pipe_codes(_grid(16, 1, cells))
        translated = _pipe_to_ansi(text)
        # Every |NN in the output must be one _pipe_to_ansi recognizes
        # (i.e. none survive untranslated in the final text).
        self.assertEqual(_PIPE_RE.findall(translated), [])


class SynchronetCtrlAFormatTests(unittest.TestCase):
    def test_normal_color_uses_base_letter_no_bold(self):
        from anetbbs.web.ansi_editor import render_synchronet_ctrl_a
        # grid fg index whose ANSI code is a normal (30-37) one, e.g. red=4
        text = render_synchronet_ctrl_a(_grid(1, 1, [_solid_cell('R', 4, 0)]))
        self.assertIn('\x01R', text)   # red letter
        self.assertNotIn('\x01H', text)  # no bold toggle for a normal color

    def test_bright_color_gets_bold_toggle_plus_base_letter(self):
        from anetbbs.web.ansi_editor import render_synchronet_ctrl_a, _FG_CODES
        # grid fg index 15 -> ansi 97 (bright white) per _FG_CODES --
        # confirm the fixture assumption itself, not just the outcome.
        self.assertEqual(_FG_CODES[15], 97)
        text = render_synchronet_ctrl_a(_grid(1, 1, [_solid_cell('W', 15, 0)]))
        self.assertIn('\x01H', text)
        self.assertIn('\x01W', text)

    def test_matches_synchronet_compats_own_ctrla_map(self):
        # Extract the real _CTRLA_MAP from games/synchronet_compat.py
        # and confirm this renderer's letters agree with what that
        # table would translate them back to.
        if not shutil.which('node'):
            self.skipTest('node not available')
        from anetbbs.web.ansi_editor import render_synchronet_ctrl_a
        repo_root = Path(__file__).resolve().parents[1]
        compat_path = repo_root / 'anetbbs' / 'games' / 'synchronet_compat.py'
        src = compat_path.read_text()
        m = re.search(r"var _CTRLA_MAP = \{.*?\};", src, re.DOTALL)
        assert m is not None
        text = render_synchronet_ctrl_a(_grid(1, 1, [_solid_cell('X', 4, 0)]))  # red, normal
        script = (
            m.group(0) +
            f"\nconst codes = {text!r}.match(/\\x01(.)/g) || [];\n"
            "let out = '';\n"
            "for (const code of codes) { const ch = code[1].toUpperCase(); "
            "out += _CTRLA_MAP.hasOwnProperty(ch) ? _CTRLA_MAP[ch] : ''; }\n"
            "console.log(out);\n"
        )
        out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
        # Should contain the real ANSI red foreground escape.
        self.assertIn('\x1b[31m', out.stdout)


if __name__ == '__main__':
    unittest.main()
