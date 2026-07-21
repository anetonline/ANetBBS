"""Unit tests for anetbbs.features.petscii_codec -- the PETSCII byte
encoder used by PETSCII terminal-mode sessions (see
tests/test_petscii_session.py for the session.write() integration and
the PETSCII Terminal Support Phase 1 plan for context: PETSCII is not
just a different code page, it's a different control-code system
entirely, hence a dedicated codec rather than reusing the CP437 path).
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.features import petscii_codec as pc


class ControlCodeConstantsTests(unittest.TestCase):
    def test_screen_control_codes_match_real_c64_values(self):
        self.assertEqual(ord(pc.CLR_HOME), 0x93)
        self.assertEqual(ord(pc.HOME), 0x13)
        self.assertEqual(ord(pc.CURSOR_DOWN), 0x11)
        self.assertEqual(ord(pc.CURSOR_UP), 0x91)
        self.assertEqual(ord(pc.CURSOR_RIGHT), 0x1D)
        self.assertEqual(ord(pc.CURSOR_LEFT), 0x9D)
        self.assertEqual(ord(pc.REVERSE_ON), 0x12)
        self.assertEqual(ord(pc.REVERSE_OFF), 0x92)
        self.assertEqual(ord(pc.LOWERCASE_CHARSET), 0x0E)
        self.assertEqual(ord(pc.UPPERCASE_CHARSET), 0x8E)

    def test_all_16_colors_present_and_distinct(self):
        colors = [pc.COLOR_BLACK, pc.COLOR_WHITE, pc.COLOR_RED, pc.COLOR_CYAN,
                  pc.COLOR_PURPLE, pc.COLOR_GREEN, pc.COLOR_BLUE, pc.COLOR_YELLOW,
                  pc.COLOR_ORANGE, pc.COLOR_BROWN, pc.COLOR_LIGHT_RED,
                  pc.COLOR_DARK_GREY, pc.COLOR_GREY, pc.COLOR_LIGHT_GREEN,
                  pc.COLOR_LIGHT_BLUE, pc.COLOR_LIGHT_GREY]
        self.assertEqual(len(colors), 16)
        self.assertEqual(len(set(colors)), 16, 'all 16 color codes must be distinct bytes')
        for c in colors:
            self.assertTrue(pc.is_control_byte(ord(c)), f'{c!r} must be a control byte')


class EncodeTests(unittest.TestCase):
    def test_letters_are_case_swapped_digits_and_punctuation_are_not(self):
        # Confirmed on real hardware: in the "upper/lowercase" charset
        # (LOWERCASE_CHARSET, sent once at session start), PETSCII's
        # letter-case byte assignment is INVERTED from ASCII's -- sending
        # the ASCII-uppercase byte value displays as lowercase and vice
        # versa. A first hardware test showed the BBS's own correctly-
        # cased output ("Welcome" / "Login") rendering with every
        # letter's case flipped ("wELCOME" / "lOGIN"). encode() must
        # counteract this so the on-screen result matches the intended text.
        text = 'Hello, World! 123 ANetBBS'
        encoded = pc.encode(text)
        self.assertEqual(encoded, b'hELLO, wORLD! 123 anETbbs')

    def test_digits_and_punctuation_pass_through_identity(self):
        self.assertEqual(pc.encode('123!@#$%^&*()'), b'123!@#$%^&*()')

    def test_lowercase_letters_become_uppercase_bytes(self):
        self.assertEqual(pc.encode('abcxyz'), b'ABCXYZ')

    def test_uppercase_letters_become_lowercase_bytes(self):
        self.assertEqual(pc.encode('ABCXYZ'), b'abcxyz')

    def test_newline_normalized_to_carriage_return(self):
        self.assertEqual(pc.encode('line1\nline2'), b'LINE1\rLINE2')
        self.assertEqual(pc.encode('line1\r\nline2'), b'LINE1\rLINE2')

    def test_control_code_characters_pass_through_unchanged(self):
        text = f'{pc.CLR_HOME}Hi{pc.REVERSE_ON}there{pc.REVERSE_OFF}'
        encoded = pc.encode(text)
        self.assertEqual(encoded, bytes([0x93]) + b'hI' + bytes([0x12]) + b'THERE' + bytes([0x92]))

    def test_color_code_embedded_in_text(self):
        text = f'{pc.COLOR_YELLOW}Warning{pc.COLOR_WHITE}'
        encoded = pc.encode(text)
        self.assertEqual(encoded, bytes([0x9E]) + b'wARNING' + bytes([0x05]))

    def test_non_ascii_unicode_falls_back_to_question_mark(self):
        # CP437/ANSI box-drawing and accented characters have no PETSCII
        # equivalent -- must degrade to '?', never crash or emit a
        # multi-byte UTF-8 sequence onto a raw C64 client.
        self.assertEqual(pc.encode('café'), b'CAF?')
        self.assertEqual(pc.encode('█░'), b'??')  # block-drawing chars

    def test_empty_string(self):
        self.assertEqual(pc.encode(''), b'')

    def test_is_control_byte_boundaries(self):
        self.assertTrue(pc.is_control_byte(0x00))
        self.assertTrue(pc.is_control_byte(0x1F))
        self.assertFalse(pc.is_control_byte(0x20))  # space -- printable
        self.assertFalse(pc.is_control_byte(0x7E))  # '~' -- printable
        self.assertFalse(pc.is_control_byte(0x7F))  # not in either control range
        self.assertTrue(pc.is_control_byte(0x80))
        self.assertTrue(pc.is_control_byte(0x9F))
        self.assertFalse(pc.is_control_byte(0xA0))


class DecodeTests(unittest.TestCase):
    def test_decode_is_the_inverse_of_encode_for_letters(self):
        for text in ('Hello, World!', 'StingRay', 'ANetBBS', 'wanda123'):
            self.assertEqual(pc.decode(pc.encode(text)), text)

    def test_decode_char_swaps_case(self):
        self.assertEqual(pc.decode_char(ord('A')), 'a')
        self.assertEqual(pc.decode_char(ord('a')), 'A')
        self.assertEqual(pc.decode_char(ord('5')), '5')

    def test_decode_drops_control_bytes(self):
        data = bytes([0x93]) + b'Hi' + bytes([0x12])
        self.assertEqual(pc.decode(data), 'hI')  # letters still swapped

    def test_decode_unrepresentable_byte_becomes_question_mark(self):
        self.assertEqual(pc.decode(bytes([0xFF])), '?')


if __name__ == '__main__':
    unittest.main()
