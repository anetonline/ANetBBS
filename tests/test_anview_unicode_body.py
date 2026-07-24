"""Regression test for a real live bug: a message composed through the
web UI with genuine Unicode box-drawing characters (pasted CP437 art)
displayed correctly on the web, but showed as a wall of '?' when read
via a telnet/SSH terminal client (ANView, features/anedit.py).

Root cause: render_message_body_lines() used the same unconditional
`body.encode('latin-1', errors='replace')` bug as web/render_msg.py's
now-fixed _decode_charset() -- a genuine Unicode character (codepoint
above 0xFF, which real latin-1-wrapped bytes could never produce) is
lossy to encode as latin-1 and got silently replaced with '?'.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anetbbs.features.anedit import render_message_body_lines


def test_genuine_unicode_box_drawing_survives_terminal_render():
    body = '╔══╗\r\nHello\r\n╚══╝'
    lines = render_message_body_lines(body)
    joined = '\n'.join(lines)
    assert '?' not in joined
    assert '╔' in joined and '╗' in joined and '╚' in joined
    assert 'Hello' in joined


def test_real_cp437_bytes_still_translate_correctly():
    raw_bytes = bytes([0xC9, 0xCD, 0xCD, 0xBB])
    latin1_wrapped = raw_bytes.decode('latin-1')
    lines = render_message_body_lines(latin1_wrapped)
    joined = '\n'.join(lines)
    assert '╔══╗'[:1] in joined  # at least the corner survived
    assert '?' not in joined


def test_plain_ascii_body_unaffected():
    lines = render_message_body_lines('Hello, world!')
    assert any('Hello, world!' in l for l in lines)
