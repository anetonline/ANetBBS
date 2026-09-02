# anetbbs/features/irc_format.py
"""
mIRC formatting code parser — used by the web IRC client (HTML output).

The telnet/SSH/rlogin IRC client (features/anetirc2.py) does NOT use
this module's ANSI rendering -- it strips mIRC formatting codes
outright via its own dedicated `_MIRC_RE`, rather than rendering them
as ANSI escapes. A real to_ansi() ANSI-rendering counterpart to
to_html() below existed here once (found dead -- zero callers anywhere
in the codebase -- in a security/performance audit, 2026-09-02) and
was removed rather than kept unreachable; if terminal IRC color
rendering is ever wanted, re-derive it from to_html()'s parsing logic
rather than resurrecting the removed code verbatim (git history has it
if needed as a reference).

mIRC formatting codes (de-facto IRC standard):
    \x02            bold
    \x1d            italic
    \x1f            underline
    \x1e            strikethrough
    \x16            reverse / inverse
    \x0f            reset (cancel all formatting)
    \x03            colour reset
    \x03NN          foreground color NN  (00-15, 16-99 added later)
    \x03NN,BB       foreground NN, background BB
    \x04RRGGBB      24-bit hex color (rare; treated like reset for now)

Color palette (16 classic + 16 newer = 32, we map to nearest ANSI):

    00  white       08  yellow
    01  black       09  light green
    02  blue        10  cyan
    03  green       11  light cyan
    04  red         12  light blue
    05  brown       13  pink
    06  magenta     14  grey
    07  orange      15  light grey
"""
import re

# ---------------------------------------------------------------------------
# HTML rendering (web IRC client)
# ---------------------------------------------------------------------------

# CSS-friendly hex colors for the 16-color mIRC palette.
_HTML_COLORS = {
    0: '#ffffff',  1: '#000000',  2: '#00007f',  3: '#009300',
    4: '#ff0000',  5: '#7f0000',  6: '#9c009c',  7: '#fc7f00',
    8: '#ffff00',  9: '#00fc00', 10: '#009393', 11: '#00ffff',
    12: '#0000fc', 13: '#ff00ff', 14: '#7f7f7f', 15: '#d2d2d2',
}


def _safe_html(s):
    return (s.replace('&', '&amp;')
             .replace('<', '&lt;')
             .replace('>', '&gt;')
             .replace('"', '&quot;')
             .replace("'", '&#39;'))


def to_html(text):
    """Convert IRC-formatted text to safe HTML with inline color spans.

    Returns an HTML-safe string (already escaped). Caller should NOT escape
    again. Unknown / future control bytes are stripped."""
    if not text:
        return ''

    out = []
    open_spans = 0      # how many <span> we currently have open
    bold = italic = underline = reverse = False
    cur_fg = cur_bg = None

    def reopen():
        nonlocal open_spans
        # Close any currently-open span and reopen with current style.
        out.append('</span>' * open_spans)
        open_spans = 0
        styles = []
        if bold:       styles.append('font-weight:bold')
        if italic:     styles.append('font-style:italic')
        if underline:  styles.append('text-decoration:underline')
        if reverse:
            styles.append(f'background-color:{_HTML_COLORS.get(cur_fg or 0, "#fff")};'
                          f'color:{_HTML_COLORS.get(cur_bg or 1, "#000")}')
        else:
            if cur_fg is not None:
                styles.append(f'color:{_HTML_COLORS.get(cur_fg, "#000")}')
            if cur_bg is not None:
                styles.append(f'background-color:{_HTML_COLORS.get(cur_bg, "#fff")}')
        if styles:
            out.append('<span style="' + ';'.join(styles) + '">')
            open_spans += 1

    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == '\x02':
            bold = not bold; reopen(); i += 1
        elif c == '\x1d':
            italic = not italic; reopen(); i += 1
        elif c == '\x1f':
            underline = not underline; reopen(); i += 1
        elif c == '\x16':
            reverse = not reverse; reopen(); i += 1
        elif c == '\x0f':
            bold = italic = underline = reverse = False
            cur_fg = cur_bg = None
            reopen(); i += 1
        elif c == '\x03':
            # Color code: \x03 [FG[,BG]]
            j = i + 1
            fg_digits = ''
            while j < n and text[j].isdigit() and len(fg_digits) < 2:
                fg_digits += text[j]; j += 1
            bg_digits = ''
            if j < n and text[j] == ',' and j + 1 < n and text[j + 1].isdigit():
                j += 1   # consume comma
                while j < n and text[j].isdigit() and len(bg_digits) < 2:
                    bg_digits += text[j]; j += 1
            if fg_digits == '' and bg_digits == '':
                # Bare \x03 -> reset color only
                cur_fg = cur_bg = None
            else:
                if fg_digits:
                    cur_fg = int(fg_digits) % 16
                if bg_digits:
                    cur_bg = int(bg_digits) % 16
            reopen()
            i = j
        elif c == '\x04':
            # 24-bit hex color — we just skip 6 hex chars and reset.
            i += 1
            skipped = 0
            while i < n and skipped < 6 and text[i] in '0123456789abcdefABCDEF':
                i += 1; skipped += 1
        elif c < ' ':
            # Other control bytes — drop silently
            i += 1
        else:
            out.append(_safe_html(c))
            i += 1

    out.append('</span>' * open_spans)
    return ''.join(out)


def strip_codes(text):
    """Remove all mIRC formatting codes — used when the destination can't
    display color (logging, etc.)."""
    if not text:
        return ''
    return re.sub(
        r'(\x03(\d{1,2}(,\d{1,2})?)?|\x04[0-9a-fA-F]{6}|[\x02\x0f\x16\x1d\x1e\x1f])',
        '', text)
