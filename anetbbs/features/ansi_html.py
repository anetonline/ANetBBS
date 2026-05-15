# anetbbs/features/ansi_html.py
"""
Minimal ANSI / CP437 → HTML converter for in-browser display of ANSI art
and ANSI-formatted board posts / echomail. Handles SGR colour codes
(30-37, 40-47, 90-97, 100-107, plus 1=bold, 0=reset). Strips other
escape sequences quietly. CP437 high-bytes are decoded to Unicode using
``codecs.decode(bytes, 'cp437')``.
"""
import re
from html import escape

# 16-color ANSI palette (DOS-ish): regular + bright variants.
_FG_BASE = {
    30: '#000000', 31: '#aa0000', 32: '#00aa00', 33: '#aa5500',
    34: '#0000aa', 35: '#aa00aa', 36: '#00aaaa', 37: '#aaaaaa',
    90: '#555555', 91: '#ff5555', 92: '#55ff55', 93: '#ffff55',
    94: '#5555ff', 95: '#ff55ff', 96: '#55ffff', 97: '#ffffff',
}
_BG_BASE = {
    40: '#000000', 41: '#aa0000', 42: '#00aa00', 43: '#aa5500',
    44: '#0000aa', 45: '#aa00aa', 46: '#00aaaa', 47: '#aaaaaa',
    100: '#555555', 101: '#ff5555', 102: '#55ff55', 103: '#ffff55',
    104: '#5555ff', 105: '#ff55ff', 106: '#55ffff', 107: '#ffffff',
}

_CSI_RE = re.compile(r'\x1b\[([0-9;]*)([A-Za-z])')


def to_html(data):
    """Convert ANSI-encoded text or bytes to a safe HTML <pre>-style fragment.

    Returns an HTML string with <span> elements for each colour change.
    Caller is responsible for wrapping in <pre> with monospace + CRT styling.
    """
    if isinstance(data, bytes):
        # Strip SAUCE trailer if present (last 128 bytes after 0x1A).
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

    out = []
    fg = '#aaaaaa'
    bg = None
    bold = False
    span_open = False

    def open_span():
        nonlocal span_open
        styles = []
        # Apply bold by lifting fg into bright range when applicable
        eff_fg = fg
        if bold and eff_fg.startswith('#') and eff_fg in _FG_BASE.values():
            for code, hexv in _FG_BASE.items():
                if hexv == eff_fg and code < 90:
                    eff_fg = _FG_BASE.get(code + 60, eff_fg)
                    break
        styles.append(f'color:{eff_fg}')
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
        # Append literal text before this escape
        chunk = text[pos:m.start()]
        if chunk:
            out.append(escape(chunk).replace('\n', '<br>'))
        params = m.group(1)
        cmd = m.group(2)
        pos = m.end()
        if cmd == 'm':
            close_span()
            codes = [int(p) for p in params.split(';') if p.isdigit()] or [0]
            for c in codes:
                if c == 0:
                    fg = '#aaaaaa'; bg = None; bold = False
                elif c == 1:
                    bold = True
                elif c == 22:
                    bold = False
                elif c in _FG_BASE:
                    fg = _FG_BASE[c]
                elif c in _BG_BASE:
                    bg = _BG_BASE[c]
            open_span()
        # Other CSI commands (cursor moves, clears) silently dropped — we're
        # rendering static art, not driving a terminal.
    # Tail
    chunk = text[pos:]
    if chunk:
        out.append(escape(chunk).replace('\n', '<br>'))
    close_span()
    return ''.join(out)
