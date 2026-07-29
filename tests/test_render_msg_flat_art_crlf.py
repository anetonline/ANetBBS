"""Regression tests for two real bugs found live in the same incident: a
sysop-composed CP437/ANSI art piece (groot.ans, ~140 lines, pure SGR
color codes, no cursor-positioning, every line comfortably under 80
visible columns) rendered as almost nothing in the web UI.

Bug 1 (fixed first): render_msg_body()'s "flat block art" path strips
line breaks entirely so the VT emulator's own 80-column auto-wrap owns
row advancement -- but it only ever did `.replace('\\n', '')`, leaving
every '\\r' from CRLF-terminated art behind. The VT emulator treats a
bare '\\r' as "column := 0" WITHOUT advancing the row, so each source
line overwrote the previous one at the same row instead of moving to a
new one. Confirmed live: HTML output collapsed from what should have
been ~30,000 characters down to ~500.

Bug 2 (found testing bug 1's own fix against the real live message):
fixing the '\\r' orphan wasn't enough -- the "strip line breaks" logic
itself was firing unconditionally for ANY flat block art with no
cursor-positioning, regardless of whether any line actually needed it.
Stripping is only correct when a line is wider than the VT emulator's
80-column width (the ONLY case where an explicit break conflicts with
the emulator's own auto-wrap). groot.ans's widest line is 77 columns,
so it never needed stripping at all -- every one of its lines should
just keep its real '\\r\\n' and let the VT emulator's ordinary '\\r'/'\\n'
handling lay it out one row per source line, exactly as authored.
Confirmed live: keeping line breaks produced 69 correctly-laid-out rows
for the real message; the (bug-1-fixed but still bug-2-broken) stripped
version produced 55 wrong ones -- multiple short source lines glued
onto shared auto-wrapped rows, scrambling the whole picture even though
no characters were lost anymore.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anetbbs.web.render_msg import render_msg_body


def _crlf_block_art(n_lines, glyphs_per_line):
    """n_lines of distinct, uniquely-colored block-art rows, CRLF-
    terminated -- the exact shape real DOS/Windows-authored ANSI art
    takes."""
    colors = [31, 32, 33, 34, 35, 36]
    lines = []
    for i in range(n_lines):
        color = colors[i % len(colors)]
        lines.append(f'\x1b[{color}m' + ('\xdb' * glyphs_per_line(i)))
    return '\r\n'.join(lines) + '\r\n'


# -- Bug 2: short lines (groot.ans's actual shape) must keep their real
#    row layout, not get glued together by unnecessary stripping --------

def test_short_lines_keep_one_row_per_source_line():
    # Every line well under 80 columns -- matches groot.ans exactly
    # (max real line width there was 77). Must NOT be stripped: each
    # source line should land on its own row.
    n_lines = 20
    text = _crlf_block_art(n_lines, lambda i: i + 3)  # 3..22 chars/line
    html = str(render_msg_body(text, 'CP437 2'))

    assert html.count('<br>') == n_lines - 1, (
        f"expected exactly {n_lines - 1} line breaks (one row per short "
        f"source line, none of them needing to be stripped), got "
        f"{html.count('<br>')} -- short lines are being glued together "
        f"onto shared auto-wrapped rows")

    total_glyphs = sum(i + 3 for i in range(n_lines))
    assert html.count('█') == total_glyphs


# -- Bug 1: when a line DOES overflow 80 cols (stripping legitimately
#    needed), the leftover '\r' must not cause row-overwriting ----------

def test_overflowing_lines_still_trigger_strip_without_losing_characters():
    # One line over 80 visible columns forces the strip path -- this is
    # the scenario stripping actually exists for. Rows are built with
    # strictly increasing length so that under the '\r'-orphan bug, only
    # the LAST (longest) row's full extent would survive.
    n_lines = 20
    text = _crlf_block_art(n_lines, lambda i: 60 + i * 2)  # last line: 60+38=98 > 80
    expected_glyphs = sum(60 + i * 2 for i in range(n_lines))
    html = str(render_msg_body(text, 'CP437 2'))
    actual_glyphs = html.count('█')
    assert actual_glyphs == expected_glyphs, (
        f"expected all {expected_glyphs} block-art glyphs to survive "
        f"rendering, only {actual_glyphs} did -- rows are overwriting "
        f"each other (orphaned '\\r' from CRLF-stripping bug)")


def test_cursor_positioned_art_still_keeps_its_line_breaks():
    # Cursor-pos art must NOT go through the strip path at all (a
    # pre-existing, still-correct behavior) -- confirm the fix didn't
    # change that branch.
    from anetbbs.features.ansi_html import _HAS_CURSOR_POS
    text = '\x1b[1;1H\x1b[31m\xdb\xdb\xdb\r\n\x1b[2;1H\x1b[32m\xdb\xdb\xdb\r\n'
    assert _HAS_CURSOR_POS.search(text)
    html = str(render_msg_body(text, 'CP437 2'))
    assert '█' in html
