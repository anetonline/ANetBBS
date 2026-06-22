# anetbbs/features/ansi_html.py
"""
ANSI / CP437 → HTML converter.  Two render modes:

* Streaming (``_to_html_streaming``): fast linear pass — color codes only.
  Used for plain-text messages with no cursor movement.

* Virtual-terminal (``_to_html_vt``): full 80-column screen buffer.
  Handles cursor-positioning sequences (``\x1b[r;cH``, ``\x1b[A/B/C/D``,
  ``\x1b[2J``, ``\x1b[K``, etc.) so ANSI art renders in the correct 2-D
  layout rather than as a scrambled linear stream.

``to_html`` detects cursor-positioning sequences and routes accordingly.
"""
import re
from collections import defaultdict
from html import escape

# 16-color ANSI palette (DOS / CGA).
_FG_BASE = {
    30: '#000000', 31: '#aa0000', 32: '#00aa00', 33: '#aa5500',
    34: '#0000aa', 35: '#aa00aa', 36: '#00aaaa', 37: '#aaaaaa',
    90: '#555555', 91: '#ff5555', 92: '#55ff55', 93: '#ffff55',
    94: '#5555ff', 95: '#ff55ff', 96: '#55ffff', 97: '#ffffff',
}
_BG_BASE = {
    40: '#000000', 41: '#aa0000', 42: '#00aa00', 43: '#aa5500',
    44: '#0000aa', 45: '#aa00aa', 46: '#00aaaa', 47: '#aaaaaa',
   100: '#555555',101: '#ff5555',102: '#55ff55',103: '#ffff55',
   104: '#5555ff',105: '#ff55ff',106: '#55ffff',107: '#ffffff',
}

# Detect cursor-positioning or screen-erase sequences.
_HAS_CURSOR_POS = re.compile(r'\x1b\[[\d;]*[HABCDGJKfsur]')

# Detect CP437 block/half-block chars in either latin-1 mojibake or proper Unicode.
# Mojibake: 0xB0-0xB2 (░▒▓), 0xDB-0xDF (█▄▌▐▀) stored as U+00B0-B2, U+00DB-DF.
# Unicode:  U+2591-2593 (░▒▓), U+2588 (█), U+2584 (▄), U+2580 (▀), U+258C/258D/258E/258F (▌).
_HAS_BLOCK_ART = re.compile(
    r'[\xb0\xb1\xb2\xdb\xdc\xdd\xde\xdf'
    r'█▄▀▌▍▎▏░▒▓]'
)

_CSI_RE = re.compile(r'\x1b\[([0-9;]*)([A-Za-z])')

# Reverse-map hex → SGR code for terminal ANSI output.
_HEX_TO_FG = {v: k for k, v in _FG_BASE.items()}
_HEX_TO_BG = {v: k for k, v in _BG_BASE.items()}


def _bold_fg(fg: str, bold: bool) -> str:
    """Apply bold by lifting fg to its bright variant when possible."""
    if not bold:
        return fg
    for code, hexv in _FG_BASE.items():
        if hexv == fg and code < 90:
            return _FG_BASE.get(code + 60, fg)
    return fg


def _to_html_streaming(text: str) -> str:
    """Fast linear pass — SGR colors only, no cursor positioning."""
    out = []
    fg = '#aaaaaa'
    bg = None
    bold = False
    span_open = False

    def open_span():
        nonlocal span_open
        styles = [f'color:{_bold_fg(fg, bold)}']
        if bg:
            styles.append(f'background-color:{bg}')
        out.append(f'<span style="{";".join(styles)}">')
        span_open = True

    def close_span():
        nonlocal span_open
        if span_open:
            out.append('</span>')
            span_open = False

    open_span()
    pos = 0
    for m in _CSI_RE.finditer(text):
        chunk = text[pos:m.start()]
        if chunk:
            out.append(escape(chunk).replace('\n', '<br>'))
        params = m.group(1)
        cmd    = m.group(2)
        pos    = m.end()
        if cmd == 'm':
            close_span()
            codes = [int(p) for p in params.split(';') if p.isdigit()] or [0]
            for c in codes:
                if c == 0:   fg = '#aaaaaa'; bg = None; bold = False
                elif c == 1: bold = True
                elif c == 22: bold = False
                elif c in _FG_BASE: fg = _FG_BASE[c]
                elif c in _BG_BASE: bg = _BG_BASE[c]
            open_span()
    chunk = text[pos:]
    if chunk:
        out.append(escape(chunk).replace('\n', '<br>'))
    close_span()
    return ''.join(out)


def _run_vt(text: str):
    """Run text through the VT state machine.

    Returns ``(cells, max_row)`` where
    ``cells[(row, col)] = (char, fg_hex, bg_hex_or_None, bold_bool)``.
    ``max_row`` is -1 when the output is empty.

    Handles the same escape sequences as _to_html_vt.
    """
    MAX_ROWS = 300
    WIDTH    = 80

    cells: dict = {}
    cur_row = cur_col = 0
    fg = '#aaaaaa'; bg = None; bold = False
    sv_row = sv_col = 0

    def _put(ch: str):
        nonlocal cur_row, cur_col
        if 0 <= cur_row < MAX_ROWS and 0 <= cur_col < WIDTH:
            cells[(cur_row, cur_col)] = (ch, fg, bg, bold)
        cur_col += 1
        if cur_col >= WIDTH:
            cur_col = 0
            cur_row = min(MAX_ROWS - 1, cur_row + 1)

    def _sgr(ps: list):
        nonlocal fg, bg, bold
        for p in (ps or [0]):
            if p == 0:    fg = '#aaaaaa'; bg = None; bold = False
            elif p == 1:  bold = True
            elif p == 22: bold = False
            elif p in _FG_BASE: fg = _FG_BASE[p]
            elif p in _BG_BASE: bg = _BG_BASE[p]

    i = 0; n = len(text)
    while i < n:
        ch = text[i]
        if ch == '\x1b' and i + 1 < n:
            if text[i + 1] == '[':
                j = i + 2
                while j < n and (text[j].isdigit() or text[j] in ';?'):
                    j += 1
                if j < n:
                    cmd = text[j]
                    raw = text[i + 2:j]
                    ps  = [int(x) for x in raw.split(';') if x.isdigit()]
                    p1  = ps[0] if ps else 0
                    p2  = ps[1] if len(ps) > 1 else 0
                    if cmd in ('H', 'f'):
                        cur_row = max(0, (p1 or 1) - 1)
                        cur_col = max(0, (p2 or 1) - 1)
                    elif cmd == 'A': cur_row = max(0, cur_row - (p1 or 1))
                    elif cmd == 'B': cur_row = min(MAX_ROWS - 1, cur_row + (p1 or 1))
                    elif cmd == 'C': cur_col = min(WIDTH - 1, cur_col + (p1 or 1))
                    elif cmd == 'D': cur_col = max(0, cur_col - (p1 or 1))
                    elif cmd == 'G': cur_col = max(0, (p1 or 1) - 1)
                    elif cmd == 'J' and p1 == 2:
                        cells.clear(); cur_row = cur_col = 0
                    elif cmd == 'K' and p1 == 0:
                        for c in range(cur_col, WIDTH): cells.pop((cur_row, c), None)
                    elif cmd == 's': sv_row, sv_col = cur_row, cur_col
                    elif cmd == 'u': cur_row, cur_col = sv_row, sv_col
                    elif cmd == 'm': _sgr(ps)
                    i = j + 1; continue
                i += 2; continue
            i += 2; continue
        if ch == '\n':
            cur_row = min(MAX_ROWS - 1, cur_row + 1); cur_col = 0
        elif ch == '\r':
            cur_col = 0
        elif ch == '\x08':
            cur_col = max(0, cur_col - 1)
        elif ch >= ' ' or ch in '\t':
            _put(ch if ch != '\t' else ' ')
        i += 1

    max_row = max((r for r, _ in cells), default=-1)
    return cells, max_row


def _to_html_vt(text: str) -> str:
    """Virtual-terminal renderer — builds a 2-D cell grid, renders to HTML."""
    cells, max_row = _run_vt(text)
    if max_row < 0:
        return ''

    by_row: dict = defaultdict(dict)
    for (r, c), v in cells.items():
        by_row[r][c] = v

    WIDTH = 80
    out = []
    for r in range(max_row + 1):
        row_cells = by_row.get(r)
        if not row_cells:
            out.append('<br>')
            continue

        cur_fg = cur_bg = cur_bold = None
        span_open = False
        row_out = []

        for c in range(WIDTH):
            cell = row_cells.get(c)
            if cell is None:
                char = ' '; cfg = '#aaaaaa'; cbg = None; cb = False
            else:
                char, cfg, cbg, cb = cell

            if cfg != cur_fg or cbg != cur_bg or cb != cur_bold:
                if span_open:
                    row_out.append('</span>')
                styles = [f'color:{_bold_fg(cfg, cb)}']
                if cbg:
                    styles.append(f'background-color:{cbg}')
                row_out.append(f'<span style="{";".join(styles)}">')
                span_open = True
                cur_fg = cfg; cur_bg = cbg; cur_bold = cb

            row_out.append(escape(char))

        if span_open:
            row_out.append('</span>')
        out.append(''.join(row_out))
        if r < max_row:
            out.append('<br>')

    return ''.join(out)


def to_ansi_lines(text: str, width: int = 80) -> list:
    """Convert ANSI/CP437 text to terminal-ready display lines via VT renderer.

    Returns a list of strings (one per screen row) containing ANSI SGR color
    codes.  Suitable for feeding into ANView for scrollable display.

    Call after stripping record-boundary \\n for art bodies; for plain text
    leave \\n intact so the VT renderer advances rows normally.
    """
    cells, max_row = _run_vt(text)
    if max_row < 0:
        return text.splitlines() or ['']

    by_row: dict = defaultdict(dict)
    for (r, c), v in cells.items():
        by_row[r][c] = v

    lines = []
    for r in range(max_row + 1):
        row_cells = by_row.get(r, {})
        parts = []
        cur_fg = cur_bg = None

        for c in range(width):
            cell = row_cells.get(c)
            if cell is None:
                char, cfg, cbg, cb = ' ', '#aaaaaa', None, False
            else:
                char, cfg, cbg, cb = cell

            actual_fg = _bold_fg(cfg, cb)

            if actual_fg != cur_fg or cbg != cur_bg:
                sgr = ['0']
                fg_code = _HEX_TO_FG.get(actual_fg)
                if fg_code and fg_code != 37:
                    sgr.append(str(fg_code))
                bg_code = _HEX_TO_BG.get(cbg) if cbg else None
                if bg_code:
                    sgr.append(str(bg_code))
                parts.append(f'\x1b[{";".join(sgr)}m')
                cur_fg = actual_fg
                cur_bg = cbg

            parts.append(char)

        parts.append('\x1b[0m')
        lines.append(''.join(parts))

    return lines


def to_html(data):
    """
    Convert ANSI-encoded text or bytes to a safe HTML fragment.

    Routes to the virtual-terminal renderer when cursor-positioning
    sequences are detected, and to the streaming renderer otherwise.

    Caller should wrap in ``<pre>`` with monospace + CRT styling.
    """
    if isinstance(data, bytes):
        if b'\x1a' in data:
            try:
                eof = data.rindex(b'\x1aSAUCE')
                data = data[:eof]
            except ValueError:
                pass
        try:
            text = data.decode('cp437')
        except UnicodeDecodeError:
            text = data.decode('cp437', errors='replace')
    else:
        text = data or ''
        # Strip SAUCE record for string input (bytes path already handles this).
        # 0x1A (Ctrl+Z) marks the end of art content; everything after is
        # binary metadata that looks like random ANSI to any renderer.
        sa = text.find('\x1a')
        if sa >= 0:
            text = text[:sa]

    # Use the VT renderer when cursor-positioning or block-art chars are
    # present; the streaming renderer handles plain coloured text.
    if _HAS_CURSOR_POS.search(text) or (
            '\x1b' in text and _HAS_BLOCK_ART.search(text)):
        return _to_html_vt(text)
    return _to_html_streaming(text)


__all__ = [
    'to_html', 'to_ansi_lines',
    '_HAS_CURSOR_POS', '_HAS_BLOCK_ART',
    '_HEX_TO_FG', '_HEX_TO_BG',
]
