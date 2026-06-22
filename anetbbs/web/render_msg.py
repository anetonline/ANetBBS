"""
Message body renderer: CP437 + ANSI SGR -> HTML.

Echomail and BBS post bodies arrive as latin-1-decoded strings (each byte
becomes the same Unicode codepoint 0..255). The bytes themselves are
either CP437 (the default for FTN/QWK), UTF-8 (when @CHRS says so), or
plain ASCII. We translate CP437 high-bytes to their proper Unicode
glyphs (box drawing, blocks, etc.) and convert ANSI escape sequences
to colored HTML spans so terminal art renders the way it does in a
classic ANSI viewer.

This is intentionally a small, dependency-free implementation. Synchronet
Ctrl-A codes have already been stripped from QWK bodies upstream — what
remains is genuine ANSI escape sequences and CP437 glyphs.
"""
import re
from markupsafe import Markup, escape
from ..features.ansi_html import (to_html as _vt_to_html,
                                   _HAS_CURSOR_POS as _HAS_CPOS,
                                   _HAS_BLOCK_ART)


# Standard 16-color VGA palette, matches the colors a DOS BBS would draw.
_FG = {
    30: '#000000', 31: '#aa0000', 32: '#00aa00', 33: '#aa5500',
    34: '#0000aa', 35: '#aa00aa', 36: '#00aaaa', 37: '#aaaaaa',
    90: '#555555', 91: '#ff5555', 92: '#55ff55', 93: '#ffff55',
    94: '#5555ff', 95: '#ff55ff', 96: '#55ffff', 97: '#ffffff',
}
_BG = {
    40: '#000000', 41: '#aa0000', 42: '#00aa00', 43: '#aa5500',
    44: '#0000aa', 45: '#aa00aa', 46: '#00aaaa', 47: '#aaaaaa',
    100: '#555555', 101: '#ff5555', 102: '#55ff55', 103: '#ffff55',
    104: '#5555ff', 105: '#ff55ff', 106: '#55ffff', 107: '#ffffff',
}

# CSI sequence: ESC [ <params> <final-byte>
_CSI_RE = re.compile(r'\x1b\[([0-9;?]*)([@-~])')

# Synchronet/MRC pipe color codes — |NN where NN is 00-15 = foreground.
# Mystic, SBBSecho, Renegade, and most modern BBS software accept these
# in echomail/netmail bodies. Translate to the equivalent ANSI SGR so the
# downstream _ansi_to_html pass renders them as colored spans.
_PIPE_FG = {
    '00': '30', '01': '34', '02': '32', '03': '36',
    '04': '31', '05': '35', '06': '33', '07': '37',
    '08': '90', '09': '94', '10': '92', '11': '96',
    '12': '91', '13': '95', '14': '93', '15': '97',
}
# Pipe background colors |16..|23 (rare but seen in Renegade/Mystic art)
_PIPE_BG = {
    '16': '40', '17': '44', '18': '42', '19': '46',
    '20': '41', '21': '45', '22': '43', '23': '47',
}
_PIPE_RE = re.compile(r'\|(\d{2})')


def _pipe_to_ansi(s: str) -> str:
    """Translate Synchronet-style |NN pipe codes to ANSI SGR escape
    sequences. Codes outside the known FG/BG range pass through untouched
    so e.g. literal "|99" doesn't become a stray escape."""
    if not s or '|' not in s:
        return s
    def sub(m):
        c = m.group(1)
        sgr = _PIPE_FG.get(c) or _PIPE_BG.get(c)
        return f'\x1b[{sgr}m' if sgr else m.group(0)
    return _PIPE_RE.sub(sub, s)


def _decode_charset(text: str, chrs: str = '') -> str:
    """Re-decode a latin-1-decoded body using the declared charset.

    Bodies enter the DB as latin-1 (1:1 byte->codepoint), so we round-trip
    back to bytes and decode with the right codec.
    """
    if not text:
        return text
    raw = text.encode('latin-1', errors='replace')
    chrs_upper = (chrs or '').upper()
    if chrs_upper.startswith(('UTF-8', 'UTF8')):
        return raw.decode('utf-8', errors='replace')
    if chrs_upper.startswith('LATIN-1') or chrs_upper.startswith('ISO-8859'):
        return raw.decode('latin-1', errors='replace')
    # Default: CP437 (FTN/QWK standard, also what Synchronet emits).
    # CP437 maps bytes 0x01–0x1F to graphic Unicode points (smileys, arrows,
    # etc.) — in particular 0x1B → U+2190 (←) instead of U+001B (ESC).
    # That breaks _ansi_to_html's \x1b regex so every ANSI sequence leaks
    # through as visible text.  Restore the original byte value for any
    # control character; CP437 is a single-byte codec so len(raw)==len(decoded).
    decoded = raw.decode('cp437', errors='replace')
    return ''.join(chr(b) if 0x01 <= b <= 0x1F else c
                   for b, c in zip(raw, decoded))


def _ansi_to_html(text: str) -> str:
    """Convert ANSI SGR escape sequences in *text* to HTML spans.

    Non-SGR CSI sequences (cursor moves, clear screen, etc.) are dropped.
    HTML metacharacters in the visible text are escaped.
    """
    out = []
    fg = None
    bg = None
    bold = False
    span_open = False

    def open_span():
        nonlocal span_open
        f = fg
        if bold and f is not None and 30 <= f <= 37:
            f = f + 60  # bright variant
        styles = []
        if f is not None and f in _FG:
            styles.append(f'color:{_FG[f]}')
        if bg is not None and bg in _BG:
            styles.append(f'background-color:{_BG[bg]}')
        if bold and not (fg is not None and 30 <= fg <= 37):
            styles.append('font-weight:bold')
        if styles:
            out.append('<span style="' + ';'.join(styles) + '">')
            span_open = True

    def close_span():
        nonlocal span_open
        if span_open:
            out.append('</span>')
            span_open = False

    pos = 0
    for m in _CSI_RE.finditer(text):
        if m.start() > pos:
            out.append(str(escape(text[pos:m.start()])))
        params, final = m.group(1), m.group(2)
        if final == 'm':
            close_span()
            if not params:
                params = '0'
            for p in params.split(';'):
                try:
                    n = int(p)
                except ValueError:
                    continue
                if n == 0:
                    fg, bg, bold = None, None, False
                elif n == 1:
                    bold = True
                elif n == 22:
                    bold = False
                elif n == 7:
                    fg, bg = bg if bg is not None else 30, fg if fg is not None else 47
                elif 30 <= n <= 37 or 90 <= n <= 97:
                    fg = n
                elif 40 <= n <= 47 or 100 <= n <= 107:
                    bg = n
                elif n == 39:
                    fg = None
                elif n == 49:
                    bg = None
            open_span()
        # Drop non-SGR CSI sequences (cursor positioning, erase, etc.)
        pos = m.end()
    if pos < len(text):
        out.append(str(escape(text[pos:])))
    close_span()
    return ''.join(out)


def render_msg_body(text, chrs: str = '') -> Markup:
    """Jinja filter: render a message body as HTML with CP437 + ANSI support.
    Also translates Synchronet/MRC `|NN` pipe color codes to ANSI before
    the ANSI-to-HTML pass — Mystic-originated echomail and netmail commonly
    embed these in signatures and ANSI art blocks."""
    if not text:
        return Markup('')
    decoded = _decode_charset(str(text), chrs)
    # QWK 0xE3 separators can land anywhere inside a CSI sequence (between
    # ESC and '[', or anywhere in the parameter string).  Strip \n from any
    # position within an escape sequence so the renderer's regex matches.
    decoded = re.sub(r'\x1b\n?\[[0-9;?\n]*[@-~]',
                     lambda m: m.group(0).replace('\n', ''), decoded)
    decoded = _pipe_to_ansi(decoded)
    # Strip \n only for pure flat block art (no cursor-pos sequences).
    # Cursor-pos art keeps \n: stripping collapses flat header sections
    # (e.g. full-screen logos) that use \n for row breaks, causing them to
    # overflow past col 80 and disappear.  Cursor-pos sequences use absolute
    # row/col so artifact \n between them have no visual effect on those rows.
    if '\x1b' in decoded and _HAS_BLOCK_ART.search(decoded) and not _HAS_CPOS.search(decoded):
        decoded = decoded.replace('\n', '')
    return Markup(_vt_to_html(decoded))


_IMG_URL_RE = re.compile(
    r'(https?://[^\s<>"\']+\.(?:jpg|jpeg|png|gif|webp|svg))',
    re.IGNORECASE)


def render_msg_body_rich(text, chrs: str = '') -> Markup:
    """Like render_msg_body but also embeds image URLs as <img> tags.

    Used for board posts where users paste image links and also occasionally
    paste ANSI art / CP437 box-drawing.
    """
    if not text:
        return Markup('')
    decoded = _decode_charset(str(text), chrs)
    decoded = re.sub(r'\x1b\n?\[[0-9;?\n]*[@-~]',
                     lambda m: m.group(0).replace('\n', ''), decoded)
    decoded = _pipe_to_ansi(decoded)
    if '\x1b' in decoded and _HAS_BLOCK_ART.search(decoded) and not _HAS_CPOS.search(decoded):
        decoded = decoded.replace('\n', '')
    out = []
    last = 0
    for m in _IMG_URL_RE.finditer(decoded):
        out.append(_vt_to_html(decoded[last:m.start()]))
        url = m.group(1)
        out.append('<br><img src="' + str(escape(url)) +
                   '" alt="" style="max-width:100%;max-height:600px;'
                   'border:1px solid var(--theme-border);"><br>')
        last = m.end()
    out.append(_vt_to_html(decoded[last:]))
    return Markup(''.join(out))
