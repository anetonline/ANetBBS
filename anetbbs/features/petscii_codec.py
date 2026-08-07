# anetbbs/features/petscii_codec.py
"""
PETSCII encoding + C64 control-code constants for the PETSCII terminal
mode (see anetbbs/core/session.py's forced_term_mode='petscii' and
anetbbs/core/petscii_server.py). PETSCII is not just a different code
page -- it's a different control-code system entirely (no ANSI CSI
parsing on a real C64), so this is deliberately separate from the
CP437/ANSI encode path anetbbs/core/session.py's write() already uses
for telnet/SSH/rlogin sessions.

ansi_to_petscii() (near the bottom) translates ANSI SGR color codes
into real C64 color bytes rather than discarding them -- see that
function's own docstring for the reverse-video trick this requires.

Design: control codes are represented here as ordinary Python
single-character strings whose codepoint IS the real PETSCII control
byte (e.g. CLR_HOME = chr(0x93)) -- callers (anetbbs/features/petscii_ui.py)
just embed them directly in an f-string alongside plain text, and
encode() below is the ONE place that turns the whole resulting string
into wire bytes: control-code characters (already the exact byte value)
pass through unchanged; printable text is identity-mapped EXCEPT
letters, which get case-swapped (see _swap_letter_case below) before
being sent as bytes; anything untranslatable (non-ASCII Unicode, CP437
box-drawing, etc.) falls back to '?' rather than corrupting the byte
stream.

Confirmed on real hardware: in the "upper/lowercase" charset (selected
via LOWERCASE_CHARSET), PETSCII's letter-case byte assignment is
INVERTED from ASCII's -- sending the ASCII-uppercase byte value (0x41-
0x5A) displays as a LOWERCASE glyph, and sending the ASCII-lowercase
byte value (0x61-0x7A) displays as an UPPERCASE glyph. A first hardware
test showed every letter of the BBS's own correctly-cased output text
("Welcome" / "Login" / "Invalid username or password") rendered with
its case flipped ("wELCOME" / "lOGIN" / "iNVALID USERNAME OR
PASSWORD"). decode() applies the identical swap to bytes read FROM a
real C64 keyboard for the same reason (the keyboard-decode table and
the display charset are the same ROM-selected mapping) -- without it,
every letter of a typed username/password would arrive with the wrong
case, and login always failed even against a correct account.
"""
import re

# ---- Screen/cursor control ------------------------------------------------
CLR_HOME = chr(0x93)      # clear screen + cursor home
HOME = chr(0x13)          # cursor home only
CURSOR_DOWN = chr(0x11)
CURSOR_UP = chr(0x91)
CURSOR_RIGHT = chr(0x1D)
CURSOR_LEFT = chr(0x9D)
DELETE = chr(0x14)        # backspace/delete
INSERT = chr(0x94)
REVERSE_ON = chr(0x12)
REVERSE_OFF = chr(0x92)

# Selects the "upper/lowercase" charset (vs. the C64's power-on default
# "upper/graphics" charset) -- sent once at session start so plain ASCII
# a-z actually displays as lowercase letters instead of graphics
# symbols. Real C64 BBS software conventionally does this immediately
# on connect, since BBS text content is far more readable with real
# lowercase than the default charset.
LOWERCASE_CHARSET = chr(0x0E)
UPPERCASE_CHARSET = chr(0x8E)

# ---- The 16 C64 color codes ------------------------------------------------
COLOR_BLACK = chr(0x90)
COLOR_WHITE = chr(0x05)
COLOR_RED = chr(0x1C)
COLOR_CYAN = chr(0x9F)
COLOR_PURPLE = chr(0x9C)
COLOR_GREEN = chr(0x1E)
COLOR_BLUE = chr(0x1F)
COLOR_YELLOW = chr(0x9E)
COLOR_ORANGE = chr(0x81)
COLOR_BROWN = chr(0x95)
COLOR_LIGHT_RED = chr(0x96)
COLOR_DARK_GREY = chr(0x97)
COLOR_GREY = chr(0x98)
COLOR_LIGHT_GREEN = chr(0x99)
COLOR_LIGHT_BLUE = chr(0x9A)
COLOR_LIGHT_GREY = chr(0x9B)

# A couple of printable ASCII positions where real PETSCII hardware
# diverges (backslash -> pound sign, underscore -> left-arrow glyph) --
# kept as identity here rather than sending the "correct" C64 code
# point, since font/rendering support for those varies a lot across
# telnet PETSCII clients and identity is the least-surprising default
# for ordinary BBS text (which rarely needs either character anyway).
_PUNCT_OVERRIDES = {}

# Control bytes this module itself emits (see constants above) -- these
# must pass through encode() completely unchanged, never substituted.
_CONTROL_RANGE_LOW = range(0x00, 0x20)
_CONTROL_RANGE_HIGH = range(0x80, 0xA0)


def is_control_byte(code: int) -> bool:
    return code in _CONTROL_RANGE_LOW or code in _CONTROL_RANGE_HIGH


def _swap_letter_case(ch: str) -> str:
    """PETSCII's upper/lowercase charset inverts ASCII's letter-case
    byte assignment (see module docstring). A self-inverse transform,
    so the same helper is used for both encode() and decode()."""
    return ch.swapcase() if ch.isalpha() else ch


def encode(text: str) -> bytes:
    """Turn a string (plain text plus any of this module's control-code
    characters embedded directly) into the PETSCII byte stream to write
    to a raw C64 telnet client. Newlines are normalized to '\\r' (CBM
    convention -- no line-feed concept needed). Anything with no
    reasonable PETSCII representation (non-ASCII Unicode, CP437
    box-drawing, etc.) becomes '?' rather than corrupting the byte
    stream."""
    text = text.replace('\r\n', '\r').replace('\n', '\r')
    out = bytearray()
    for ch in text:
        code = ord(ch)
        if is_control_byte(code):
            out.append(code)
        elif 0x20 <= code <= 0x7E:
            ch2 = _swap_letter_case(ch)
            out.append(ord(_PUNCT_OVERRIDES.get(ch2, ch2)))
        else:
            out.append(0x3F)  # '?'
    return bytes(out)


def decode(data: bytes) -> str:
    """Inverse of encode() for bytes read FROM a real PETSCII client
    (keyboard input) -- the same case-swap applies symmetrically to
    keyboard input as it does to screen output, since both are governed
    by the same charset-ROM selection. Control bytes are dropped (they
    aren't meaningful as line-buffer text; callers handle Enter/
    backspace/etc. before ever reaching here)."""
    out = []
    for b in data:
        if is_control_byte(b):
            continue
        elif 0x20 <= b <= 0x7E:
            out.append(_swap_letter_case(chr(b)))
        else:
            out.append('?')
    return ''.join(out)


def decode_char(byte_value: int) -> str:
    """decode() for a single already-known-printable byte -- used by
    session.py's read_line()/read_password() per-keystroke handling,
    where the control-byte/backspace/Enter cases are already handled
    by the caller before this is ever called."""
    return _swap_letter_case(chr(byte_value))


# ---- ANSI SGR (color) -> real PETSCII color bytes --------------------------
#
# Verified against Synchronet's own open-source implementation
# (src/sbbs3/petscii_term.cpp, PETSCII_Terminal::attrstr()/xlat_atr()) --
# the only other BBS codebase that does this translation for real.
# Every byte value below is cross-checked against that source; none are
# guessed. C64 text mode has no independent per-character background
# color -- only one foreground color per cell, plus a whole-cell REVERSE
# flag that swaps foreground/background pixels. Synchronet's approach
# (replicated here): when ANSI specifies both a non-default foreground
# AND a non-black background, switch to reverse video (REVERSE_ON) and
# show the BACKGROUND color as the visible color -- the original
# foreground is discarded, since real hardware can't keep both. When
# the background returns to black/default, REVERSE_OFF and resume
# normal foreground-color tracking.

_CSI_RE = re.compile(r'\x1b\[([0-9;?]*)([@-~])')

# Combined (bold, base-color 0-7) index -> real C64 color byte. Index =
# base ANSI color (30-37 minus 30) + 8 if bold/bright -- the real ANSI
# SGR order (0=black 1=red 2=green 3=brown/olive 4=blue 5=magenta
# 6=cyan 7=lightgray; bright is +8), matching the _FG dict already used
# by anetbbs/web/render_msg.py, NOT WWIV/Renegade's own differently-
# ordered ctrl-color-code letters (K/B/G/C/R/M/Y/W) -- those are a
# separate, unrelated convention and mapping them here directly would
# scramble every color.
_SGR_INDEX_TO_COLOR = {
    0: COLOR_BLACK, 1: COLOR_RED, 2: COLOR_GREEN, 3: COLOR_BROWN,
    4: COLOR_BLUE, 5: COLOR_ORANGE, 6: COLOR_GREY, 7: COLOR_LIGHT_GREY,
    8: COLOR_DARK_GREY, 9: COLOR_LIGHT_RED, 10: COLOR_LIGHT_GREEN,
    11: COLOR_YELLOW, 12: COLOR_LIGHT_BLUE, 13: COLOR_PURPLE,
    14: COLOR_CYAN, 15: COLOR_WHITE,
}


def _color_index(code, bold):
    """code: an ANSI SGR color number (30-37/90-97 fg, or 40-47/100-107
    bg with 40 subtracted first by the caller) or None for default.
    Returns a 0-15 index into _SGR_INDEX_TO_COLOR."""
    if code is None:
        return 7  # default light-gray-ish, matches ANSI's own default
    if 30 <= code <= 37:
        base = code - 30
        return base + (8 if bold else 0)
    if 90 <= code <= 97:
        return (code - 90) + 8  # already an explicit bright color
    return 7


def ansi_to_petscii(text: str) -> str:
    """Translate ANSI SGR color codes in *text* into real PETSCII color
    control bytes (see module comment above for the reverse-video
    approach used for combined fg+bg). Non-SGR CSI sequences (cursor
    moves, erase, etc.) are dropped, same as before this function
    existed -- PETSCII still can't honor arbitrary cursor addressing
    from ANSI content. Returned string is plain text plus embedded
    control-code characters, ready for encode() exactly like any other
    petscii_codec constant embedded in an f-string.
    """
    out = []
    fg = None    # raw ANSI fg code (30-37/90-97) or None
    bg = None    # raw ANSI bg code (40-47/100-107) or None
    bold = False
    last_emitted = None  # (reversed: bool, color_byte) of the last thing we actually wrote

    def emit_for_state():
        nonlocal last_emitted
        # bg is stored in ANSI bg-code numbering (40-47/100-107); shift
        # by 10 to reuse _color_index's fg-numbering (30-37/90-97) --
        # valid because ANSI defines bg codes as fg_code+10 for the same
        # hue, by spec, not just convention.
        bg_idx = _color_index(bg - 10, False) if bg is not None else 0
        reversed_ = bg_idx != 0
        color = _SGR_INDEX_TO_COLOR[bg_idx if reversed_ else _color_index(fg, bold)]
        state = (reversed_, color)
        if state == last_emitted:
            return
        if reversed_ != (last_emitted[0] if last_emitted else False):
            out.append(REVERSE_ON if reversed_ else REVERSE_OFF)
        out.append(color)
        last_emitted = state

    pos = 0
    for m in _CSI_RE.finditer(text):
        if m.start() > pos:
            out.append(text[pos:m.start()])
        params, final = m.group(1), m.group(2)
        if final == 'm':
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
                    fg, bg = (bg - 10) if bg is not None else 30, (fg + 10) if fg is not None else 40
                elif 30 <= n <= 37 or 90 <= n <= 97:
                    fg = n
                elif 40 <= n <= 47 or 100 <= n <= 107:
                    bg = n
                elif n == 39:
                    fg = None
                elif n == 49:
                    bg = None
            emit_for_state()
        # Drop non-SGR CSI sequences (cursor positioning, erase, etc.)
        pos = m.end()
    if pos < len(text):
        out.append(text[pos:])
    return ''.join(out)
