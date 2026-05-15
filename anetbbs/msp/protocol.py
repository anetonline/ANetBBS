"""
RFC 1312 — Message Send Protocol 2

Wire format:

    <type><RECIPIENT>\\0<RECIP_TERM>\\0<MESSAGE>\\0<SENDER>\\0<SEND_TERM>\\0<COOKIE>\\0<SIG>\\0

The first byte is the message type:
    'B' — Brief / best-effort (no application ack)
    'A' — Acknowledged (server replies '+\\n' on success or '-<reason>\\n' on error)

Lengths (per RFC §3):
    RECIPIENT    1 ..   64
    RECIP_TERM   0 ..   64
    MESSAGE      0 .. 8192
    SENDER       0 ..   64
    SEND_TERM    0 ..   64
    COOKIE       0 .. 8192
    SIGNATURE    0 .. 8192
"""
from __future__ import annotations

MSP_DEFAULT_PORT = 18

MAX_RECIPIENT = 64
MAX_TERMINAL  = 64
MAX_MESSAGE   = 8192
MAX_SENDER    = 64
MAX_COOKIE    = 8192
MAX_SIGNATURE = 8192

# Number of NUL terminators in a complete packet (one per field).
NUM_FIELDS = 7


def encode(recipient: str, message: str = '', sender: str = '',
           recipient_terminal: str = '', sender_terminal: str = '',
           cookie: str = '', signature: str = '',
           msg_type: str = 'B') -> bytes:
    """Encode a Message-Send packet ready to write to a TCP socket."""
    if msg_type not in ('A', 'B'):
        raise ValueError(f'invalid msg_type {msg_type!r} (must be A or B)')
    if not recipient:
        raise ValueError('recipient is required')

    def _enc(s, max_len, name):
        b = s.encode('utf-8', errors='replace') if s else b''
        if len(b) > max_len:
            raise ValueError(f'{name} too long ({len(b)} > {max_len})')
        return b

    parts = [
        _enc(recipient,          MAX_RECIPIENT, 'recipient'),
        _enc(recipient_terminal, MAX_TERMINAL,  'recipient_terminal'),
        _enc(message,            MAX_MESSAGE,   'message'),
        _enc(sender,             MAX_SENDER,    'sender'),
        _enc(sender_terminal,    MAX_TERMINAL,  'sender_terminal'),
        _enc(cookie,             MAX_COOKIE,    'cookie'),
        _enc(signature,          MAX_SIGNATURE, 'signature'),
    ]
    return msg_type.encode('ascii') + b'\x00'.join(parts) + b'\x00'


def decode(raw: bytes) -> dict:
    """Decode an MSP-2 packet. Returns dict with msg_type + 7 fields.

    Tolerates trailing-NUL omission — pads missing fields with empty strings.
    """
    if len(raw) < 2:
        raise ValueError(f'packet too short ({len(raw)} bytes)')

    type_byte = chr(raw[0])
    if type_byte not in ('A', 'B'):
        raise ValueError(f'unknown msg_type {type_byte!r}')

    fields = raw[1:].split(b'\x00')
    # Trailing NUL produces an extra empty field — drop it.
    if fields and fields[-1] == b'':
        fields.pop()
    while len(fields) < NUM_FIELDS:
        fields.append(b'')

    def _dec(b):
        if not b:
            return ''
        try:
            return b.decode('utf-8')
        except UnicodeDecodeError:
            return b.decode('latin-1', errors='replace')

    return {
        'msg_type':           type_byte,
        'recipient':          _dec(fields[0])[:MAX_RECIPIENT],
        'recipient_terminal': _dec(fields[1])[:MAX_TERMINAL],
        'message':            _dec(fields[2])[:MAX_MESSAGE],
        'sender':             _dec(fields[3])[:MAX_SENDER],
        'sender_terminal':    _dec(fields[4])[:MAX_TERMINAL],
        'cookie':             _dec(fields[5])[:MAX_COOKIE],
        'signature':          _dec(fields[6])[:MAX_SIGNATURE],
    }
