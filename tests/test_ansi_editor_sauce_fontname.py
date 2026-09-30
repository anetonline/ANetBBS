"""Regression test for a real completeness gap in ansi_editor.py's
_build_sauce(): TInfoS (FontName, the last 22 bytes of a SAUCE record)
was always left as 22 spaces, declaring no font at all, even though
this editor always knows the answer -- everything it renders/decodes
is CP437. Found by comparing against ANetDRAW's own SAUCE writer
(Jerry's reference implementation for the site's in-browser demo),
which correctly sets this field to "IBM VGA". TFlags is deliberately
NOT copied from that reference -- it sets the iCE-color bit, which
would be wrong metadata here: this editor's palette only ever produces
classic 8-color (non-iCE) backgrounds.
"""
import sys
import types
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _fake_art(**overrides):
    defaults = dict(name='Test Art', width=80, height=25, ansi_text='hello',
                    created_by=None, updated_at=datetime(2026, 1, 1),
                    created_at=datetime(2026, 1, 1))
    defaults.update(overrides)
    return types.SimpleNamespace(**defaults)


class SauceFontNameTests(unittest.TestCase):
    def test_font_name_field_is_ibm_vga_not_blank(self):
        from anetbbs.web.ansi_editor import _build_sauce
        record = _build_sauce(_fake_art())
        self.assertEqual(len(record), 128)
        font_name_field = record[106:128]
        self.assertTrue(font_name_field.startswith(b'IBM VGA'))
        # NUL-padded after the name, not space-padded -- matches the
        # real SAUCE spec convention for an ASCIIZ field.
        self.assertEqual(font_name_field[7:], b'\x00' * 15)

    def test_tflags_byte_stays_zero_no_ice_color_claim(self):
        # This editor's palette can't actually produce iCE-color art
        # (only 8 background colors) -- the flags byte must not claim
        # otherwise.
        from anetbbs.web.ansi_editor import _build_sauce
        record = _build_sauce(_fake_art())
        self.assertEqual(record[105], 0)

    def test_record_is_still_exactly_128_bytes(self):
        from anetbbs.web.ansi_editor import _build_sauce
        record = _build_sauce(_fake_art(name='x' * 100))
        self.assertEqual(len(record), 128)

    def test_id_and_version_unchanged(self):
        from anetbbs.web.ansi_editor import _build_sauce
        record = _build_sauce(_fake_art())
        self.assertEqual(record[:7], b'SAUCE00')


if __name__ == '__main__':
    unittest.main()
