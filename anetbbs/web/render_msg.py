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

    Real bug found live: that byte-preserving round-trip is only true for
    messages that arrived over the wire (BinkP/QWK) -- a message composed
    directly through the web/terminal UI stores genuine already-decoded
    Unicode text, never latin-1-wrapped bytes. A first fix skipped the
    whole round-trip whenever ANY codepoint was above 0xFF (proof it
    can't be raw latin-1-wrapped bytes, which top out at 0xFF) -- correct
    for a purely-Unicode message, but a real pasted Moebius CP437 export
    demonstrated the flaw: it was overwhelmingly raw CP437-as-latin-1
    mojibake, EXCEPT one corner glyph that came through the clipboard as
    a genuine Unicode character (U+0152 Œ) instead of staying
    byte-preserved. That one outlier made the all-or-nothing check skip
    translating the entire message, including every other, genuinely
    raw-byte box-drawing character. Decode per character instead: only
    characters that are actually byte-representable (0-0xFF) go through
    the round-trip; anything already outside that range passes through
    untouched, whatever the rest of the message needs.
    """
    if not text:
        return text
    chrs_upper = (chrs or '').upper()
    if chrs_upper.startswith(('UTF-8', 'UTF8')):
        # Multi-byte codec -- unlike CP437/latin-1 this can't be split
        # per character (a single decoded character can span several
        # raw-byte positions), so this path keeps the original
        # all-or-nothing check. It's specific to @CHRS-declared wire
        # messages (a genuinely byte-oriented source), not the locally-
        # composed/mixed-content case the fix above addresses.
        if any(ord(c) > 0xFF for c in text):
            return text
        raw = text.encode('latin-1', errors='replace')
        return raw.decode('utf-8', errors='replace')
    if chrs_upper.startswith('LATIN-1') or chrs_upper.startswith('ISO-8859'):
        # Identity for any byte-representable char; genuine Unicode
        # chars pass through unchanged either way.
        return text
    # Default: CP437 (FTN/QWK standard, also what Synchronet emits).
    # CP437 maps bytes 0x01–0x1F to graphic Unicode points (smileys, arrows,
    # etc.) — in particular 0x1B → U+2190 (←) instead of U+001B (ESC).
    # That breaks _ansi_to_html's \x1b regex so every ANSI sequence leaks
    # through as visible text. Restore the original byte value for any
    # control character (real ANSI escapes in a pasted message must
    # survive this untouched).
    out = []
    for c in text:
        b = ord(c)
        if b > 0xFF:
            out.append(c)
            continue
        if 0x01 <= b <= 0x1F:
            out.append(c)
            continue
        out.append(bytes([b]).decode('cp437', errors='replace'))
    return ''.join(out)


# Any of these mark a line as NOT plain wrapped prose -- box-drawing,
# block/shade art, or a horizontal-rule-style run of repeated symbol
# chars. Reflow must never touch these (real regression risk: this is
# exactly the kind of content project history has broken before).
_ART_CHARS_RE = re.compile(
    r'[─-▟\xb0\xb1\xb2\xdb\xdc\xdd\xde\xdf]')
_RULE_LINE_RE = re.compile(r'^\s*([=\-*#~^_])\1{4,}\s*$')
_LIST_OR_QUOTE_RE = re.compile(r'^\s*(\d+[.)]|[-*•>|])\s')
_REFLOW_MIN_LEN = 60  # a line at least this long plausibly hit a wrap column
_TRAILING_WORD_RE = re.compile(r"[A-Za-z']+$")
_LEADING_WORD_RE = re.compile(r"^[A-Za-z']+")

_reflow_spellchecker = None
_reflow_spellchecker_tried = False


def _get_reflow_spellchecker():
    """Same lazy, degrade-silently pattern as features/anedit.py's spell
    check -- pyspellchecker bundles its own dictionary, no network/file."""
    global _reflow_spellchecker, _reflow_spellchecker_tried
    if not _reflow_spellchecker_tried:
        _reflow_spellchecker_tried = True
        try:
            from spellchecker import SpellChecker
            _reflow_spellchecker = SpellChecker()
        except ImportError:
            _reflow_spellchecker = None
    return _reflow_spellchecker


def _looks_like_art(line: str) -> bool:
    return bool(_ART_CHARS_RE.search(line) or _RULE_LINE_RE.match(line))


def _join_wrap_point(cur: str, nxt: str) -> str:
    """Join `cur` and `nxt` at a hard-wrap point, deciding whether the
    original wrap fell mid-word (no space belongs) or between two words
    (a space belongs) -- genuinely ambiguous from the bytes alone (e.g.
    "...and al" / "l things..." could be the word "al" followed by "l",
    or "all" split mid-word). Disambiguate the only way that's actually
    reliable: try both readings and prefer whichever is a real
    dictionary word. Falls back to a space (the far more common case,
    and the safe default when the spellchecker package is unavailable
    or the merged word isn't recognized -- e.g. informal words like
    "putzing" that a standard dictionary doesn't carry)."""
    cur_m = _TRAILING_WORD_RE.search(cur)
    nxt_m = _LEADING_WORD_RE.search(nxt)
    if cur_m and nxt_m:
        sp = _get_reflow_spellchecker()
        if sp is not None:
            merged = cur_m.group(0) + nxt_m.group(0)
            if not sp.unknown([merged]):
                prefix = cur[:cur_m.start()]
                suffix = nxt[nxt_m.end():]
                return prefix + merged + suffix
    return cur.rstrip() + ' ' + nxt.lstrip()


def reflow_hard_wrapped_body(text: str) -> str:
    """Rejoin lines that look like a fixed-column hard-wrap artifact
    rather than an intentional break.

    Real bug reported live: a message from a real Synchronet/SBBSecho
    peer hard-wraps long paragraphs at a fixed column (~79 chars) using
    real line-break bytes with no word-boundary awareness and no
    soft-wrap marker -- FTS-0001 compliant, but every one of those
    breaks renders as a real paragraph break here (correctly, per spec),
    producing choppy, sometimes mid-word-split output for any sender
    whose wrap width differs from ours.

    Conservative on purpose: only joins a line to the next when the
    current line is close to a typical wrap width AND neither line looks
    like art, a rule, a list item, or a quote -- ASCII-art borders,
    origin lines, and quoted replies must never be touched.
    """
    if not text or '\x1b' in text:
        return text
    lines = text.split('\n')
    out = []
    i = 0
    while i < len(lines):
        cur = lines[i]
        while (i + 1 < len(lines)
               and len(cur) >= _REFLOW_MIN_LEN
               and cur.strip() != ''
               and lines[i + 1].strip() != ''
               and not _looks_like_art(cur)
               and not _looks_like_art(lines[i + 1])
               and not lines[i + 1][:1].isspace()
               and not _LIST_OR_QUOTE_RE.match(lines[i + 1])):
            cur = _join_wrap_point(cur, lines[i + 1])
            i += 1
        out.append(cur)
        i += 1
    return '\n'.join(out)


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


_URL_RE = re.compile(r'(https?://[^\s<>"\']+)')
_IMG_EXT_RE = re.compile(r'\.(?:jpg|jpeg|png|gif|webp|svg)$', re.IGNORECASE)
_URL_TRAIL = '.,;:!?)]}\'"'


def _linkify(decoded: str, embed_images: bool) -> str:
    """Split *decoded* on bare https?:// URLs, running everything else
    through the normal ANSI/CP437-to-HTML pass and turning URLs into
    clickable <a> tags (or <img> tags for image URLs, when embed_images
    is set — board posts only, see render_msg_body_rich).

    Trailing sentence punctuation ("see https://x.com." or a URL closing
    a parenthetical) is kept out of the link target/text, matching the
    same heuristic used for terminal OSC 8 hyperlinks in bbs_ui.py."""
    out = []
    last = 0
    for m in _URL_RE.finditer(decoded):
        out.append(_vt_to_html(decoded[last:m.start()]))
        url = m.group(1)
        trail = ''
        while url and url[-1] in _URL_TRAIL:
            trail = url[-1] + trail
            url = url[:-1]
        last = m.end() - len(trail)
        if not url:
            continue
        if embed_images and _IMG_EXT_RE.search(url):
            out.append('<br><img src="' + str(escape(url)) +
                       '" alt="" style="max-width:100%;max-height:600px;'
                       'border:1px solid var(--theme-border);"><br>')
        else:
            esc = str(escape(url))
            out.append(f'<a href="{esc}" target="_blank" '
                       f'rel="noopener noreferrer">{esc}</a>')
    out.append(_vt_to_html(decoded[last:]))
    return ''.join(out)


def _any_line_exceeds_vt_width(decoded: str, width: int = 80) -> bool:
    """True when at least one line of *decoded* (escape sequences excluded
    from the width count) is wider than the VT emulator's column width.

    This is the ONLY scenario where explicit line breaks actually conflict
    with the emulator's own auto-wrap: a line over `width` visible columns
    triggers the natural column wrap mid-line, and its own trailing break
    then advances the row AGAIN, doubling up. For any line at or under
    `width`, keeping the real break produces the exact intended row -- no
    conflict, nothing to strip.

    Real bug found live: the callers of this function used to strip line
    breaks for ANY flat block-art content with no cursor-positioning,
    regardless of actual line width -- correct for content shaped like the
    "full-screen logo overflowing col 80" case this logic was built for,
    but wrong for real-world art (confirmed: a real ~140-line piece, every
    line comfortably under 80 columns) where each line is its own
    intentional row. Stripping the breaks doesn't lose characters (that
    was the separate, now-fixed '\\r'-orphaning bug) but glues multiple
    short source lines onto one auto-wrapped row apiece, scrambling the
    layout: keeping breaks produced 69 correctly-laid-out rows for that
    piece; stripping them produced 55 wrong ones. Only strip when a line
    genuinely would overflow.
    """
    for line in re.split(r'\r\n|\r|\n', _CSI_RE.sub('', decoded)):
        if len(line) > width:
            return True
    return False


def render_msg_body(text, chrs: str = '') -> Markup:
    """Jinja filter: render a message body as HTML with CP437 + ANSI support.
    Also translates Synchronet/MRC `|NN` pipe color codes to ANSI before
    the ANSI-to-HTML pass — Mystic-originated echomail and netmail commonly
    embed these in signatures and ANSI art blocks. Bare https:// URLs are
    turned into clickable links (see _linkify)."""
    if not text:
        return Markup('')
    decoded = _decode_charset(str(text), chrs)
    decoded = reflow_hard_wrapped_body(decoded)
    # QWK 0xE3 separators can land anywhere inside a CSI sequence (between
    # ESC and '[', or anywhere in the parameter string).  Strip \n from any
    # position within an escape sequence so the renderer's regex matches.
    decoded = re.sub(r'\x1b\n?\[[0-9;?\n]*[@-~]',
                     lambda m: m.group(0).replace('\n', ''), decoded)
    decoded = _pipe_to_ansi(decoded)
    # Strip line breaks only for flat block art (no cursor-pos sequences)
    # that actually has a line wider than the VT emulator's 80-column
    # width -- see _any_line_exceeds_vt_width's docstring for the full
    # story on why "has block art, no cursor-pos" alone is NOT enough to
    # decide this. Cursor-pos art always keeps its breaks: cursor-pos
    # sequences use absolute row/col, so a break between them has no
    # visual effect on those rows either way.
    if ('\x1b' in decoded and _HAS_BLOCK_ART.search(decoded)
            and not _HAS_CPOS.search(decoded)
            and _any_line_exceeds_vt_width(decoded)):
        decoded = decoded.replace('\r\n', '').replace('\n', '').replace('\r', '')
    return Markup(_linkify(decoded, embed_images=False))  # nosec B704 -- _linkify() escape()s all literal text and URLs


def render_msg_body_rich(text, chrs: str = '') -> Markup:
    """Like render_msg_body but also embeds image URLs as <img> tags
    instead of a plain link.

    Used for board posts where users paste image links and also occasionally
    paste ANSI art / CP437 box-drawing.
    """
    if not text:
        return Markup('')
    # Local board posts are stored as proper Unicode (no latin-1/CP437 round-trip
    # needed). Only apply charset decoding when an explicit charset is declared
    # (echomail/netmail carries one via the CHRS kludge line).
    decoded = _decode_charset(str(text), chrs) if chrs else str(text)
    decoded = re.sub(r'\x1b\n?\[[0-9;?\n]*[@-~]',
                     lambda m: m.group(0).replace('\n', ''), decoded)
    decoded = _pipe_to_ansi(decoded)
    # See render_msg_body's identical check (and _any_line_exceeds_vt_
    # width's docstring) for the full explanation of both why '\r' and
    # '\n' must be stripped together, not just '\n', AND why that
    # stripping must only happen when a line actually overflows 80
    # columns -- not for any flat block art unconditionally.
    if ('\x1b' in decoded and _HAS_BLOCK_ART.search(decoded)
            and not _HAS_CPOS.search(decoded)
            and _any_line_exceeds_vt_width(decoded)):
        decoded = decoded.replace('\r\n', '').replace('\n', '').replace('\r', '')
    return Markup(_linkify(decoded, embed_images=True))  # nosec B704 -- _linkify() escape()s all literal text and URLs
