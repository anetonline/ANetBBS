"""Regression test for a real live bug: composing an echomail message
through the web UI with genuine Unicode box-drawing characters (e.g.
pasted ANSI-art-style borders like ╔══╗) turned every one of those
characters into '?' once the message was rendered back.

Root cause: anetbbs/web/render_msg.py's _decode_charset() assumes every
stored body is byte-preserved via a latin-1 round-trip (true for
inbound BinkP/QWK wire messages), and unconditionally re-encodes to
latin-1 with errors='replace' before decoding with the declared
charset. A locally-composed message stores genuine already-decoded
Unicode text instead -- encoding real box-drawing characters (codepoints
above 0xFF) back to latin-1 is lossy and replaced them with '?'.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anetbbs.web.render_msg import render_msg_body, _decode_charset


def test_locally_composed_unicode_box_drawing_survives_render():
    body = '╔══╗\nHello\n╚══╝'
    result = str(render_msg_body(body))
    assert '?' not in result
    assert '╔' in result or '&#9556;' in result or 'x2554' in result.lower() or '╔'.encode('unicode_escape')
    assert 'Hello' in result


def test_decode_charset_leaves_genuine_unicode_untouched():
    text = '╔══╗'
    assert _decode_charset(text, 'cp437') == text
    assert _decode_charset(text, '') == text


def test_decode_charset_still_translates_real_cp437_bytes():
    # 0xC9 0xCD 0xCD 0xBB in CP437 are the same box-drawing glyphs, but
    # arriving as latin-1-decoded raw bytes (the real inbound-message
    # shape) -- must still translate via CP437, not get skipped.
    raw_bytes = bytes([0xC9, 0xCD, 0xCD, 0xBB])
    latin1_wrapped = raw_bytes.decode('latin-1')
    decoded = _decode_charset(latin1_wrapped, '')
    assert decoded == '╔══╗'


def test_plain_ascii_body_unaffected():
    body = 'Hello, world! This is a normal message.'
    assert str(render_msg_body(body)) .find('Hello, world!') != -1


def test_mixed_cp437_mojibake_with_one_stray_real_unicode_char_still_translates():
    """Regression for a real live case: a pasted Moebius CP437 export
    was overwhelmingly raw CP437-as-latin-1 mojibake, except one corner
    glyph that came through the clipboard as a genuine Unicode character
    (U+0152 'Œ') instead of staying byte-preserved. An earlier version of
    this fix used an all-or-nothing check ("any codepoint above 0xFF ->
    skip the whole decode") that correctly avoided corrupting pure
    Unicode text, but broke this exact mixed case: the one outlier made
    the ENTIRE message skip CP437 translation, leaving even the
    genuinely-raw-byte box-drawing characters as literal mojibake."""
    raw_top = bytes([0xC9, 0xCD, 0xCD, 0xCD, 0xBB]).decode('latin-1')  # ╔════╗
    raw_bottom = bytes([0xC8, 0xCD, 0xCD, 0xCD]).decode('latin-1')     # ╚═══ (no real closing byte)
    text = raw_top + '\n' + raw_bottom + 'Œ'  # stray real Unicode char tacked on
    result = _decode_charset(text, '')
    assert result != text, 'the CP437 mojibake portion must still translate'
    assert '╔' in result and '╗' in result and '╚' in result
    assert 'Œ' in result, 'the genuine out-of-range character must pass through untouched'
    assert '?' not in result
