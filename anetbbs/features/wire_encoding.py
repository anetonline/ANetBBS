# anetbbs/features/wire_encoding.py
"""
Shared helper for encoding a message body/field for the FTN/QWK wire
(BinkP packets, QWK MESSAGES.DAT records) -- the outbound counterpart
of web/render_msg.py's _decode_charset().

Bodies are stored latin-1-wrapped (each byte becomes the matching
codepoint 0-255), EXCEPT for messages composed directly through the
web/terminal UI, which store genuine already-decoded Unicode text
instead (e.g. a user pasting real box-drawing characters). Real bug
found live: every outbound-encoding call site used a blanket
`text.encode('latin-1', errors='replace')`, which silently turns any
such genuine Unicode character (codepoint above 0xFF, which a real
latin-1-wrapped byte could never produce) into a literal '?' -- so a
locally-composed message with pasted CP437 art relayed out to another
BBS over BinkP/QWK arrived there corrupted, even though it displayed
correctly on this install's own web UI.
"""


def encode_body_cp437(text: str) -> bytes:
    """Encode *text* for the wire: byte-representable characters
    (0-0xFF) go out as that exact byte value (the storage convention);
    genuine Unicode characters above 0xFF get CP437-encoded to recover
    the correct single byte for that glyph (e.g. box-drawing), falling
    back to '?' only for characters CP437 genuinely has no slot for
    (e.g. CJK, emoji) -- a real information loss, but the best any
    single-byte target charset can do, and no worse than the previous
    behavior for that case.
    """
    if not text:
        return b''
    out = bytearray()
    for c in text:
        b = ord(c)
        if b <= 0xFF:
            out.append(b)
            continue
        try:
            out += c.encode('cp437')
        except UnicodeEncodeError:
            out.append(0x3F)  # '?'
    return bytes(out)
