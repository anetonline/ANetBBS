# anetbbs/features/petscii_codec.py
"""
PETSCII encoding + C64 control-code constants for the PETSCII terminal
mode (see anetbbs/core/session.py's forced_term_mode='petscii' and
anetbbs/core/petscii_server.py). PETSCII is not just a different code
page -- it's a different control-code system entirely (no ANSI CSI
parsing on a real C64), so this is deliberately separate from the
CP437/ANSI encode path anetbbs/core/session.py's write() already uses
for telnet/SSH/rlogin sessions.

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
