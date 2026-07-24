"""Regression tests for a real bug found live: outbound BinkP/QWK
message encoding used a blanket text.encode('latin-1', errors='replace'),
which silently corrupted any genuine Unicode character (e.g. box-drawing
art pasted through the web compose UI) into '?' before relaying it to
another BBS -- even though the SAME message displayed correctly on this
install's own web UI (fixed separately in web/render_msg.py).
encode_body_cp437() is the outbound counterpart of that fix.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anetbbs.features.wire_encoding import encode_body_cp437


def test_byte_representable_chars_pass_through_as_their_exact_byte_value():
    # The storage convention: chr(0xC9) means "the raw byte 0xC9",
    # not "the Unicode character U+00C9". Must go out unchanged.
    text = ''.join(chr(b) for b in (0xC9, 0xCD, 0xCD, 0xBB))
    assert encode_body_cp437(text) == bytes([0xC9, 0xCD, 0xCD, 0xBB])


def test_genuine_unicode_box_drawing_encodes_to_correct_cp437_byte():
    # Real box-drawing double-line corner/rule characters, pasted
    # directly (the exact live bug scenario).
    text = '╔═╗'
    encoded = encode_body_cp437(text)
    assert encoded == bytes([0xC9, 0xCD, 0xBB])
    # Round-trip sanity: decoding those bytes back via cp437 recovers
    # the same characters.
    assert encoded.decode('cp437') == text


def test_plain_ascii_is_unaffected():
    text = 'Hello, world!'
    assert encode_body_cp437(text) == b'Hello, world!'


def test_mixed_raw_bytes_and_genuine_unicode_both_encode_correctly():
    # A message with some already-byte-representable content (a normal
    # composed line) and a pasted real Unicode box character in the
    # same body -- both must come out correct, not just one or the
    # other.
    text = 'Notes:\n' + '╔' + chr(0xCD) * 3 + '╗'
    encoded = encode_body_cp437(text)
    assert encoded == b'Notes:\n' + bytes([0xC9, 0xCD, 0xCD, 0xCD, 0xBB])


def test_unmappable_unicode_falls_back_to_question_mark():
    text = '你好'  # CP437 has no representation for these
    encoded = encode_body_cp437(text)
    assert encoded == b'??'


def test_empty_and_none_input():
    assert encode_body_cp437('') == b''


if __name__ == '__main__':
    import unittest
    unittest.main()
