"""Regression tests for the terminal-side (ANView/ANEdit) twin of the two
bugs fixed in tests/test_render_msg_flat_art_crlf.py -- see that file's
docstring for the full live-bug story (a sysop-composed ~140-line CP437
art piece, every line under 80 visible columns, collapsed to almost
nothing).

render_message_body_lines() had the identical "strip line breaks for
dense flat block art" logic as anetbbs/web/render_msg.py, with the
identical two bugs: (1) only '\\n' was stripped, leaving '\\r' behind
(orphaned '\\r' resets column-to-0 without advancing the row in the
shared VT state machine, so later lines overwrote earlier ones); (2)
the gate deciding WHETHER to strip (`_avg_line > 70`, a body-wide raw
average including escape-code bytes) fired for real art whose actual
per-line VISIBLE width never came close to overflowing the 80-column
renderer -- stripping in that case doesn't lose characters but glues
multiple short source lines onto shared auto-wrapped rows, scrambling
the layout. Fixed by checking each line's real visible width against
80 directly, matching render_msg.py's fix.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anetbbs.features.anedit import render_message_body_lines


def _crlf_block_art(n_lines, glyphs_per_line):
    colors = [31, 32, 33, 34, 35, 36]
    lines = []
    for i in range(n_lines):
        color = colors[i % len(colors)]
        lines.append(f'\x1b[{color}m' + ('\xdb' * glyphs_per_line(i)))
    return '\r\n'.join(lines) + '\r\n'


def test_short_lines_keep_one_row_per_source_line():
    # Every line well under 80 columns -- matches the real groot.ans
    # shape (max real line width there was 77). Must NOT be stripped.
    n_lines = 15
    text = _crlf_block_art(n_lines, lambda i: i + 3)
    lines = render_message_body_lines(text)

    assert len(lines) == n_lines, (
        f"expected exactly {n_lines} rows (one per short source line, "
        f"none needing to be stripped), got {len(lines)} -- short lines "
        f"are being glued together onto shared auto-wrapped rows")

    rendered = '\n'.join(lines)
    total_glyphs = sum(i + 3 for i in range(n_lines))
    actual_glyphs = rendered.count('\xdb') + rendered.count('█')
    assert actual_glyphs == total_glyphs


def test_overflowing_lines_still_trigger_strip_without_losing_characters():
    # One line over 80 visible columns forces the strip path -- the
    # scenario stripping actually exists for.
    n_lines = 15
    text = _crlf_block_art(n_lines, lambda i: 60 + i * 2)  # last: 60+28=88 > 80
    expected_glyphs = sum(60 + i * 2 for i in range(n_lines))

    lines = render_message_body_lines(text)
    rendered = '\n'.join(lines)
    actual_glyphs = rendered.count('\xdb') + rendered.count('█')

    assert actual_glyphs == expected_glyphs, (
        f"expected all {expected_glyphs} block-art glyphs to survive "
        f"rendering, only {actual_glyphs} did -- rows are overwriting "
        f"each other (orphaned '\\r' from CRLF-stripping bug)")
