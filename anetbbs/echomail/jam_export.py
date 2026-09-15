# anetbbs/echomail/jam_export.py
"""Read-only JAM message-base export.

Exports one EchoArea's EchomailMessage rows into a real JAM-format
message base (a `<tag>.jhr`/`.jdt`/`.jdx` file triple) so classic
JAM-API door games can read ANetBBS's echomail directly, without
touching ANetBBS's own SQL storage. SQL stays the sole source of
truth: this is regenerated from scratch on every run (see the
`export_jam_message_bases` scheduled-event handler in
anetbbs/events/handlers.py), never live-incrementally updated, and a
JAM-reading door only ever sees this exported snapshot, never a path
back into the real database.

Byte layout verified against the real JAM.doc specification (Joaquim
Homrighausen / Andrew Milner / Mats Wallin, 1993) rather than from
memory, given how easy it is to get a binary format like this silently
wrong in a way that "looks fine" (a file gets written, sizes look
plausible) but breaks for any real reader -- the same rigor this
project already applied to the BinkP FTS-0001 packet-header byte-swap
bug (see docs/CHANGELOG-beta.md v1.0b2.69) after it silently broke on
a real strict receiving tosser. The CRC-32 variant JAM uses for its
.JDX index (commonly known as CRC-32/JAMCRC: same polynomial/init as
the standard zlib/zip CRC-32, but with NO final XOR) was additionally
confirmed against CRC-32/JAMCRC's published check value
(jam_crc32("123456789") == 0x340bc6d9, matching
reveng.sourceforge.io's CRC catalogue) -- see jam_crc32()'s own
docstring and tests/test_jam_export.py.

No `.jlr` (last-read) file is written -- that's per-user reader
bookkeeping, not exportable message data; every real JAM reader
creates its own on first open when one is missing.
"""
import os
import struct
import zlib
from datetime import datetime

_SIGNATURE = b'JAM\x00'


def _struct_format(fields):
    """Build a little-endian struct format string from an ordered
    list of (name, format_char, size) tuples, and assert the computed
    size matches the sum of declared sizes -- catches a mismatched
    format character (e.g. 'H' where 'I' was meant) at import time
    instead of silently producing a wrongly-sized record. Written
    this way (ordered list, not a hand-typed literal format string)
    specifically to avoid a manual-character-counting mistake in a
    format this long -- the same reasoning the door-game skill's own
    "generate long runs programmatically" guidance applies to CP437
    box-drawing escapes."""
    fmt = '<' + ''.join(f[1] for f in fields)
    computed = struct.calcsize(fmt)
    declared = sum(f[2] for f in fields)
    assert computed == declared, (
        f'struct format size mismatch: calcsize={computed}, '
        f'declared sum={declared} -- a field\'s format char/size is wrong')
    return fmt


# JamBaseHeader: the first 1024 bytes of every .jhr file.
_BASE_HEADER_FIELDS = [
    ('signature', '4s', 4),
    ('date_created', 'I', 4),
    ('mod_counter', 'I', 4),
    ('active_msgs', 'I', 4),
    ('password_crc', 'I', 4),
    ('base_msg_num', 'I', 4),
    ('reserved', '1000s', 1000),
]
_BASE_HEADER_FMT = _struct_format(_BASE_HEADER_FIELDS)
assert struct.calcsize(_BASE_HEADER_FMT) == 1024

# MessageFixedHeader: one fixed-size record per message in .jhr,
# immediately followed by subfield_len bytes of variable subfields.
_MSG_HEADER_FIELDS = [
    ('signature', '4s', 4),
    ('revision', 'H', 2),
    ('reserved_word', 'H', 2),
    ('subfield_len', 'I', 4),
    ('times_read', 'I', 4),
    ('msgid_crc', 'I', 4),
    ('reply_crc', 'I', 4),
    ('reply_to', 'I', 4),
    ('reply_1st', 'I', 4),
    ('reply_next', 'I', 4),
    ('date_written', 'I', 4),
    ('date_received', 'I', 4),
    ('date_processed', 'I', 4),
    ('message_number', 'I', 4),
    ('attribute', 'I', 4),
    ('attribute2', 'I', 4),
    ('offset', 'I', 4),
    ('txt_len', 'I', 4),
    ('password_crc', 'I', 4),
    ('cost', 'I', 4),
]
_MSG_HEADER_FMT = _struct_format(_MSG_HEADER_FIELDS)
assert struct.calcsize(_MSG_HEADER_FMT) == 76

# SubField: fixed 8-byte header (LoID, HiID, Datlen) + Datlen bytes.
_SUBFIELD_HDR_FIELDS = [
    ('loid', 'H', 2),
    ('hiid', 'H', 2),
    ('datlen', 'I', 4),
]
_SUBFIELD_HDR_FMT = _struct_format(_SUBFIELD_HDR_FIELDS)
assert struct.calcsize(_SUBFIELD_HDR_FMT) == 8

# .jdx index record: RecipientNameCRC(4) + HeaderOffset(4).
_INDEX_FIELDS = [
    ('recipient_crc', 'I', 4),
    ('header_offset', 'I', 4),
]
_INDEX_FMT = _struct_format(_INDEX_FIELDS)
assert struct.calcsize(_INDEX_FMT) == 8

# Msg Attributes bit flags actually set here (JAM.doc defines many
# more; these are the only two this exporter has a real use for).
MSG_LOCAL = 0x00000001       # message created locally (outbound)
MSG_TYPEECHO = 0x01000000    # message is for conference distribution

# Subfield LoID constants actually written here.
LOID_SENDERNAME = 2
LOID_RECEIVERNAME = 3
LOID_MSGID = 4
LOID_REPLYID = 5
LOID_SUBJECT = 6


def jam_crc32(s):
    """JAM's own CRC-32 variant (commonly catalogued as CRC-32/
    JAMCRC): identical polynomial/init to the standard zlib/zip
    CRC-32, but with NO final XOR applied -- zlib.crc32() already
    applies that final XOR as the last step of producing a standard
    CRC-32, so XORing its result with 0xFFFFFFFF again undoes exactly
    that step and recovers the raw pre-final-XOR value JAM wants.
    Verified against CRC-32/JAMCRC's own published check value:
    jam_crc32('123456789') == 0x340bc6d9 (reveng.sourceforge.io's CRC
    catalogue) -- see tests/test_jam_export.py for that exact
    assertion. Strings are lowercased first (matching the spec's own
    wording: "converted to lowercase, A-Z to a-z only") before
    hashing."""
    lowered = (s or '').encode('cp437', errors='replace').lower()
    return zlib.crc32(lowered) ^ 0xFFFFFFFF


def _pack_subfield(loid, text):
    data = (text or '').encode('cp437', errors='replace')
    return struct.pack(_SUBFIELD_HDR_FMT, loid, 0, len(data)) + data


def export_echo_area(area, output_dir, base_name=None):
    """Write `area`'s EchomailMessage rows as a JAM message base
    (`<base_name>.jhr`/`.jdt`/`.jdx`) into output_dir. `base_name`
    defaults to `area.tag` lowercased (matching the FTN convention a
    JAM-reading door expects a message-base filename to follow).
    Regenerates the whole base from scratch on every call. Returns a
    summary dict (message count, byte sizes) for logging/testing.
    Must be called inside an app context (queries EchomailMessage).
    """
    from ..models import EchomailMessage

    base_name = (base_name or area.tag).lower()
    os.makedirs(output_dir, exist_ok=True)
    jhr_path = os.path.join(output_dir, f'{base_name}.jhr')
    jdt_path = os.path.join(output_dir, f'{base_name}.jdt')
    jdx_path = os.path.join(output_dir, f'{base_name}.jdx')

    messages = (EchomailMessage.query
               .filter_by(area_id=area.id)
               .order_by(EchomailMessage.id)
               .all())

    with open(jhr_path, 'wb') as jhr, \
         open(jdt_path, 'wb') as jdt, \
         open(jdx_path, 'wb') as jdx:

        jhr.write(struct.pack(
            _BASE_HEADER_FMT,
            _SIGNATURE,
            int(datetime.utcnow().timestamp()),   # date_created
            0,                                    # mod_counter
            len(messages),                        # active_msgs
            0xFFFFFFFF,                           # password_crc (none)
            1,                                    # base_msg_num
            b'\x00' * 1000))                      # reserved

        for i, msg in enumerate(messages, start=1):
            header_offset = jhr.tell()

            body_bytes = (msg.body or '').encode('cp437', errors='replace')
            text_offset = jdt.tell()
            jdt.write(body_bytes)

            subfields = (
                _pack_subfield(LOID_SENDERNAME, msg.from_name)
                + _pack_subfield(LOID_RECEIVERNAME, msg.to_name)
                + _pack_subfield(LOID_SUBJECT, msg.subject)
            )
            if msg.msg_id:
                subfields += _pack_subfield(LOID_MSGID, msg.msg_id)
            if msg.reply_id:
                subfields += _pack_subfield(LOID_REPLYID, msg.reply_id)

            attribute = MSG_TYPEECHO
            if msg.direction == 'outbound':
                attribute |= MSG_LOCAL

            written_ts = int((msg.created_at or datetime.utcnow()).timestamp())

            jhr.write(struct.pack(
                _MSG_HEADER_FMT,
                _SIGNATURE,
                1,                                 # revision
                0,                                 # reserved_word
                len(subfields),                    # subfield_len
                0,                                 # times_read
                jam_crc32(msg.msg_id) if msg.msg_id else 0,
                jam_crc32(msg.reply_id) if msg.reply_id else 0,
                0,                                 # reply_to
                0,                                 # reply_1st
                0,                                 # reply_next
                written_ts,                        # date_written
                written_ts,                        # date_received
                written_ts,                        # date_processed
                i,                                 # message_number
                attribute,
                0,                                 # attribute2
                text_offset,                       # offset
                len(body_bytes),                   # txt_len
                0xFFFFFFFF,                        # password_crc (none)
                0))                                # cost
            jhr.write(subfields)

            jdx.write(struct.pack(
                _INDEX_FMT,
                jam_crc32(msg.to_name),
                header_offset))

    return {
        'message_count': len(messages),
        'jhr_bytes': os.path.getsize(jhr_path),
        'jdt_bytes': os.path.getsize(jdt_path),
        'jdx_bytes': os.path.getsize(jdx_path),
    }
