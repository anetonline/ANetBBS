# anetbbs/echomail/binkp.py
"""
BinkP/1.1 protocol client implementation for FidoNet echomail networking.

Handles:
- BinkP session handshake (address + password exchange)
- File offer / file transfer (send and receive .pkt files)
- FTS-0001 Type-2+ packet parsing and generation
- Creates EchomailMessage records from inbound packets
- Packages outbound EchomailMessage records into .pkt files
"""
import struct
import socket
import logging
import re
from datetime import datetime

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# BinkP frame constants
# ---------------------------------------------------------------------------
CMD_NUL  = 0   # Information / options
CMD_ADR  = 1   # Addresses
CMD_PWD  = 2   # Password
CMD_FILE = 3   # File offer
CMD_OK   = 4   # Password accepted
CMD_EOB  = 5   # End of batch (no more files to send)
CMD_GOT  = 6   # File received OK
CMD_ERR  = 7   # Error / reject
CMD_BSY  = 8   # Busy
CMD_GET  = 9   # Request specific file
CMD_SKIP = 10  # Skip / not needed

CMD_NAMES = {
    CMD_NUL: 'NUL', CMD_ADR: 'ADR', CMD_PWD: 'PWD', CMD_FILE: 'FILE',
    CMD_OK: 'OK', CMD_EOB: 'EOB', CMD_GOT: 'GOT', CMD_ERR: 'ERR',
    CMD_BSY: 'BSY', CMD_GET: 'GET', CMD_SKIP: 'SKIP',
}

# FTS-0001 packed-message types
MSG_TYPE_2 = b'\x02\x00'

# Matches the FTS-0001 packed-message date field, e.g. "10 Jul 26  10:39:20"
# -- used as a second, independent signal (beyond the 2-byte MSG_TYPE_2
# marker) that a candidate message boundary is real and not a coincidental
# byte match inside embedded binary content. See the long comment at the
# body-end scan in _parse_ftn_packet() for why 2 bytes alone isn't enough.
# Requires the FULL date+time+terminator shape, not just "DD Mon YY" --
# that shorter pattern turned out to occur naturally in ordinary English
# prose (a real message body quoting a source dated "30 Nov 99") often
# enough to produce false positives on real production traffic.
_DATE_FIELD_RE = re.compile(rb'^\d{2} [A-Za-z]{3} \d{2}\s{1,2}\d{2}:\d{2}:\d{2}\x00')

# Real FTS-0001 header text fields (to/from/subject) are always plain
# text -- no legitimate one ever contains a raw control character. Used
# to reject a candidate boundary whose marker+date pattern matched by
# coincidence: see _chain_looks_like_real_messages() below.
_HAS_CONTROL_CHAR_RE = re.compile(rb'[\x00-\x1f]')


def _is_real_packet_end(data, candidate):
    """A packet's own end-of-data marker (0x00 0x00) is, by
    construction, always the literal final bytes of the buffer -- the
    last message's own closing null immediately followed by the
    packet-level trailer. Trusting *any* adjacent zero-byte pair as
    "the packet ends here" is unsafe: once an earlier candidate
    boundary has been correctly rejected (see
    _chain_looks_like_real_messages), its own raw bytes get treated as
    unparsed body content -- and a routing header with attr=0 and
    cost=0 (the overwhelmingly common case in real traffic) produces
    exactly this adjacent-zero pattern nowhere near the real end.
    Require the candidate to actually be at the buffer's tail instead.
    """
    return candidate + 3 >= len(data)


def _chain_looks_like_real_messages(data, start, depth=3):
    """Tentatively parse up to `depth` message records starting at
    `start` and verify every to/from/subject field along the chain is
    free of raw control characters -- something no genuine header field
    ever contains.

    Confirmed necessary against a real inbound packet (captured via
    BINKP_DEBUG_DUMP_DIR): a message body happened to contain an
    embedded byte sequence that was not just MSG_TYPE_2 by coincidence,
    but a FULLY well-formed date+time+null field too -- almost
    certainly quoted/reposted old FidoNet message content inside the
    article's own prose, not random binary noise. That candidate's own
    immediate to/from/subject already contained a raw '\\r', which a
    single-level check catches -- but scanning past it lands on a
    *second* coincidental match whose own immediate fields look clean;
    the corruption only becomes visible one level further into the
    chain. Hence checking several levels deep rather than just one.
    Returns True (treat as plausible) whenever there isn't enough
    remaining data to disprove the chain -- this is a rejection filter
    for candidates that are provably bad, not a positive assertion that
    an accepted one is definitely correct.
    """
    pos = start
    for _ in range(depth):
        if pos >= len(data) - 1:
            return True
        if data[pos:pos + 2] == b'\x00\x00':
            return True
        if data[pos:pos + 2] != MSG_TYPE_2:
            return False
        pos += 2 + 12
        for max_len in (20, 36, 36, 72):
            end = data.find(b'\x00', pos, pos + max_len)
            if end == -1:
                end = pos + max_len
            if _HAS_CONTROL_CHAR_RE.search(data[pos:end]):
                return False
            pos = end + 1
        search_from = pos
        body_end = None
        while True:
            candidate = data.find(b'\x00', search_from)
            if candidate == -1:
                return True
            nxt = data[candidate + 1:candidate + 3]
            if _is_real_packet_end(data, candidate) or candidate + 1 >= len(data):
                body_end = candidate
                break
            if nxt == MSG_TYPE_2:
                date_pos = candidate + 1 + 2 + 12
                if _DATE_FIELD_RE.match(data[date_pos:date_pos + 21]):
                    body_end = candidate
                    break
            search_from = candidate + 1
        pos = body_end + 1
    return True


# ---------------------------------------------------------------------------
# Low-level BinkP framing helpers
# ---------------------------------------------------------------------------

def _build_frame(is_command: bool, data: bytes) -> bytes:
    """Build a single BinkP frame (2-byte header + data)."""
    if len(data) > 0x7FFF:
        raise ValueError("BinkP frame payload too large")
    header = len(data) | (0x8000 if is_command else 0)
    return struct.pack('>H', header) + data


def _build_cmd(cmd: int, text: str = '') -> bytes:
    """Build a command frame."""
    payload = bytes([cmd]) + text.encode('latin-1', errors='replace')
    return _build_frame(True, payload)


def _build_data(data: bytes) -> bytes:
    """Build a data frame."""
    return _build_frame(False, data)


def _recv_frame(sock: socket.socket):
    """
    Read one BinkP frame from *sock*.
    Returns (is_command, data_bytes) or raises on error/EOF.
    """
    header_raw = _recv_exactly(sock, 2)
    header = struct.unpack('>H', header_raw)[0]
    is_command = bool(header & 0x8000)
    length = header & 0x7FFF
    data = _recv_exactly(sock, length) if length else b''
    return is_command, data


def _recv_exactly(sock: socket.socket, n: int) -> bytes:
    """Read exactly *n* bytes from socket, raising on EOF."""
    buf = b''
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("BinkP: connection closed unexpectedly")
        buf += chunk
    return buf


# ---------------------------------------------------------------------------
# FTS-0001 Type-2+ packet helpers
# ---------------------------------------------------------------------------

FTN_PKT_HEADER_SIZE = 58  # Type-2+ header

def _build_ftn_packet(messages, our_addr: str, hub_addr: str) -> bytes:
    """
    Produce a minimal FTS-0001 Type-2+ .pkt file containing *messages*.
    *messages* is a list of EchomailMessage ORM objects.
    """
    def _parse_ftn(addr):
        try:
            zone, rest = addr.split(':')
            net, node_point = rest.split('/')
            node = node_point.split('.')[0]
            point = node_point.split('.')[1] if '.' in node_point else '0'
            return int(zone), int(net), int(node), int(point)
        except Exception:
            return 1, 1, 1, 0

    oz, on, oo, op = _parse_ftn(our_addr)
    dz, dn, dd, dp = _parse_ftn(hub_addr)

    now = datetime.utcnow()
    # Build the 58-byte Type-2+ packet header per FTS-0001.  Field order
    # matters — Mystic / SBBSecho / BinkD silently drop packets with the
    # wrong layout.
    #
    #   off  size  field
    #   0    2     orig_node
    #   2    2     dest_node
    #   4    2     year
    #   6    2     month  (0..11 per FTS-0001 §4.1)
    #   8    2     day
    #   10   2     hour
    #   12   2     minute
    #   14   2     second
    #   16   2     baud
    #   18   2     packet_type (always 2)
    #   20   2     orig_net
    #   22   2     dest_net
    #   24   1     prod_code  (low byte; 0xFE = "ANETBBS")
    #   25   1     ftsc_revision (low byte)
    #   26   8     password (NUL-padded ASCII; blank for inbound auth via BinkP)
    #   34   2     orig_zone
    #   36   2     dest_zone
    #   38   2     aux_net          ┐
    #   40   2     cap_validate     │  Type-2+ extension (FSC-0048). cap_word
    #   42   1     prod_code (high) │  is 0x0001 (FSC-0039 "packet type 2+"
    #   43   1     prod_rev_minor   │  flag), written little-endian like every
    #   44   2     cap_word         ┘  other field here. cap_validate holds the
    #   SAME value BYTE-SWAPPED (big-endian) -- not a plain copy -- see the
    #   struct.pack('>H', ...) fixup right after this header is built, and
    #   its comment, for why a naive identical copy silently fails strict
    #   tossers for any non-palindromic capWord value (confirmed against
    #   husky/hpt's real pktread.c/pktwrite.c source).
    #   46   2     orig_zone_copy
    #   48   2     dest_zone_copy
    #   50   2     orig_point
    #   52   2     dest_point
    #   54   4     prod_data
    PROD_CODE_LO = 0xFE
    FTSC_REV     = 0x00
    PROD_CODE_HI = 0x00
    PROD_REV_MIN = 0x01
    CAP_WORD     = 0x0001
    fmt = '<HH HHHHHH HH HH BB 8s HH HH BB H HH HH I'
    header = struct.pack(
        fmt,
        oo, dd,                                    # orig_node, dest_node
        now.year, now.month - 1, now.day,          # FTS-0001: month is 0..11
        now.hour, now.minute, now.second,
        0,                                         # baud
        2,                                         # packet_type
        on, dn,                                    # orig_net, dest_net
        PROD_CODE_LO, FTSC_REV,
        b'\x00' * 8,                               # password
        oz, dz,                                    # zones
        0, CAP_WORD,                               # aux_net, cap_validate
        PROD_CODE_HI, PROD_REV_MIN,
        CAP_WORD,
        oz, dz,                                    # zone copies
        op, dp,                                    # points
        0,                                         # prod_data
    )
    if len(header) != FTN_PKT_HEADER_SIZE:
        # Pad / trim to exactly 58 bytes so downstream parsing offsets match.
        header = header.ljust(FTN_PKT_HEADER_SIZE, b'\x00')[:FTN_PKT_HEADER_SIZE]

    # FSC-0048 Type-2+ capValidate/capWord relationship, confirmed against
    # husky/hpt's actual source (src/pktread.c:openPkt, src/pktwrite.c:
    # createPkt): capValidate (offset 40-41) is NOT a plain copy of
    # capWord (offset 44-45) -- it must hold the SAME integer's bytes
    # BYTE-SWAPPED (big-endian) relative to capWord's own little-endian
    # encoding, which is the convention every other multi-byte field in
    # this header uses. Packing both as plain little-endian (this format
    # string's default, via the two `CAP_WORD` args above) only
    # accidentally produces a matching pair for palindromic values (high
    # byte == low byte); for CAP_WORD=0x0001 specifically it does not,
    # and hpt (and likely other strict tossers) rejects the packet
    # outright: "CapabilityWord error in following pkt! rtfm:
    # IgnoreCapWord." -- root-caused from a real rejected-packet report
    # from an external sysop (SmallTime BBS / hpt), not a hypothetical.
    header = bytearray(header)
    header[40:42] = struct.pack('>H', CAP_WORD)
    header = bytes(header)

    from .kludges import build_message, make_msgid, kludges_from_json
    import json as _json

    # FTS-0001 §5.2 attribute bits
    ATTR_PRIVATE   = 0x0001
    ATTR_CRASH     = 0x0002
    ATTR_LOCAL     = 0x0100
    ATTR_HOLD      = 0x0200
    ATTR_KILLSENT  = 0x0080

    body = b''
    for msg in messages:
        area_tag = msg.area.tag if msg.area else None  # None => netmail
        is_netmail = area_tag is None
        # Per-message destination: netmail uses the recipient's FTN address;
        # echomail uses the hub address (the packet outer envelope).
        msg_dest_addr = (getattr(msg, 'to_address', '') or '') if is_netmail else hub_addr
        if not msg_dest_addr:
            msg_dest_addr = hub_addr
        mz, mn, mdd, mdp = _parse_ftn(msg_dest_addr)
        msg_orig_addr = (getattr(msg, 'from_address', '') or '') if is_netmail else our_addr
        if not msg_orig_addr:
            msg_orig_addr = our_addr
        sz, sn, sd, sp = _parse_ftn(msg_orig_addr)

        # Attribute bitfield.  For netmail mark Private. ATTR_LOCAL is
        # NOT set on outbound — it's the receiver-side "this message
        # was posted on this BBS" flag (FTS-0001 §5.2) and confuses
        # tossers that see it on inbound mail.
        attr = 0
        if is_netmail:
            attr |= ATTR_PRIVATE
        if getattr(msg._nm if hasattr(msg, '_nm') else msg, 'is_crash', False):
            attr |= ATTR_CRASH
        if getattr(msg._nm if hasattr(msg, '_nm') else msg, 'is_hold', False):
            attr |= ATTR_HOLD

        # Re-use any kludges that came in with the message (e.g. when
        # forwarding) and add our own.  We add @CHRS, @MSGID, @TID; for
        # echomail we also add an AREA line and append SEEN-BY/PATH.
        existing = kludges_from_json(msg.kludges) if msg.kludges else []
        wanted = []
        # Drop any existing CHRS/MSGID/TID — we'll regenerate consistent ones
        for k in existing:
            head = k.split(' ', 1)[0].split(':', 1)[0].upper()
            if head in ('CHRS', 'MSGID', 'TID', 'PID'):
                continue
            wanted.append(k)

        chrs = (msg.chrs or 'CP437 2').strip()
        kludge_head = []
        kludge_head.append(f'CHRS: {chrs}')
        if msg.msg_id:
            kludge_head.append(f'MSGID: {msg.msg_id}')
        else:
            new_msgid = make_msgid(msg_orig_addr)
            kludge_head.append(f'MSGID: {new_msgid}')
            msg.msg_id = new_msgid
        if msg.reply_id:
            kludge_head.append(f'REPLY: {msg.reply_id}')

        # Netmail-specific routing kludges (FTS-4001 + FSC-4009).
        # Spec form is space-separated, NO colon: `^AFMPT 1`, `^ATOPT 1`,
        # `^AINTL <to> <from>`. binkterm-php BinkdProcessor.php:1965
        # confirms; Mystic AreaFix matches strictly per spec — sending the
        # colon form ('FMPT:') means the point isn't recognized and the
        # sender appears as the boss, so the per-point AreaFix password
        # lookup fails ("Invalid password" reply). Synchronet accepts
        # either form.
        if is_netmail:
            if sz != mz:
                kludge_head.append(f'INTL {mz}:{mn}/{mdd} {sz}:{sn}/{sd}')
            if sp:
                kludge_head.append(f'FMPT {sp}')
            if mdp:
                kludge_head.append(f'TOPT {mdp}')

        wanted = kludge_head + wanted
        wanted.append('TID: ANETBBS 1.0')

        # SEEN-BY/PATH for echomail relay. Include our_addr in PATH so the
        # next hop can detect loops; preserve any existing seenby/path the
        # message had so we don't fork the routing graph.
        seenby_existing = []
        path_existing = []
        if area_tag:
            try:
                seenby_existing = _json.loads(msg.seenby) if msg.seenby else []
                path_existing = _json.loads(msg.path) if msg.path else []
            except (ValueError, TypeError):
                seenby_existing, path_existing = [], []
            our_short = f'{on}/{oo}'  # net/node form for SEEN-BY/PATH
            if our_short not in (seenby_existing[-1] if seenby_existing else ''):
                seenby_existing.append(our_short)
            if our_short not in (path_existing[-1] if path_existing else ''):
                path_existing.append(our_short)

        body_text = msg.body or ''
        # FTS-0001 message bodies use **bare CR** as the line terminator.
        # LF must not be emitted (binkterm-php BinkdProcessor.php:1851
        # echoes the spec). Our DB stores bodies with LF; if we ship them
        # raw, every line after the first carries a stray \n that breaks
        # strict parsers (Mystic AreaFix won't recognize ^A kludges or the
        # password line, silently drops the netmail).
        body_text = (body_text.replace('\r\n', '\n')
                              .replace('\r', '\n')
                              .replace('\n', '\r'))
        # Strip our own tear/origin if user accidentally embedded them — we
        # add them via build_message() to keep them at the right position.
        msg_assembled = build_message(
            body=body_text,
            kludges=wanted,
            tear=msg.tear_line or '--- ANETBBS 1.0',
            origin=msg.origin_line or f'ANETBBS ({our_addr})',
            seenby=seenby_existing if area_tag else None,
            path=path_existing if area_tag else None,
        )
        # Echomail: prepend AREA: line BEFORE the kludges (FTS-0004).
        if area_tag:
            msg_assembled = f'AREA:{area_tag}\r\n' + msg_assembled

        # Encode using the declared charset.  UTF-8 is the only multi-byte
        # one we honor here — everything else round-trips as 8-bit (CP437).
        chrs_upper = chrs.upper()
        if chrs_upper.startswith(('UTF-8', 'UTF8')):
            encoded = msg_assembled.encode('utf-8', errors='replace') + b'\x00'
        else:
            encoded = msg_assembled.encode('latin-1', errors='replace') + b'\x00'

        from_b = (msg.from_name or 'Sysop').encode('latin-1', errors='replace')[:35] + b'\x00'
        to_b = (msg.to_name or 'All').encode('latin-1', errors='replace')[:35] + b'\x00'
        subj_b = (msg.subject or '').encode('latin-1', errors='replace')[:71] + b'\x00'
        date_b = now.strftime('%d %b %y  %H:%M:%S').encode('ascii') + b'\x00'

        # FTS-0001 packed message header: msg_type + 12 routing bytes,
        # then ASCIIZ date / to / from / subject / body.
        # Field order: orig_node, dest_node, orig_net, dest_net, attr, cost.
        msg_hdr = MSG_TYPE_2 + struct.pack(
            '<HHHHHH',
            sd & 0xFFFF, mdd & 0xFFFF,
            sn & 0xFFFF, mn & 0xFFFF,
            attr & 0xFFFF, 0)
        body += msg_hdr + date_b + to_b + from_b + subj_b + encoded

    return header + body + b'\x00\x00'


def _parse_ftn_packet(data: bytes):
    """
    Parse an FTS-0001 Type-2+ .pkt file.
    Returns a list of dicts with message fields.
    """
    messages = []
    if len(data) < FTN_PKT_HEADER_SIZE:
        return messages

    pos = FTN_PKT_HEADER_SIZE
    while pos < len(data) - 1:
        if data[pos:pos+2] == b'\x00\x00':
            break  # end of packet
        if data[pos:pos+2] != MSG_TYPE_2:
            break

        pos += 2

        # 12-byte routing header (FTS-0001 §4.2):
        # orig_node, dest_node, orig_net, dest_net, attribute, cost.
        try:
            orig_node, dest_node, orig_net, dest_net, msg_attr, _cost = \
                struct.unpack_from('<HHHHHH', data, pos)
        except struct.error:
            break
        pos += 12

        def read_cstr(buf, offset, max_len=256):
            end = buf.find(b'\x00', offset, offset + max_len)
            if end == -1:
                end = offset + max_len
            return buf[offset:end].decode('latin-1', errors='replace'), end + 1

        # ASCIIZ order per spec: date_time, to_user, from_user, subject.
        date_str, pos = read_cstr(data, pos, 20)
        to_name, pos = read_cstr(data, pos, 36)
        from_name, pos = read_cstr(data, pos, 36)
        subject, pos = read_cstr(data, pos, 72)

        # The message text is null-terminated (FTS-0001 has no explicit
        # length field) -- but real-world posts, especially raw ANSI-art
        # content, can legitimately contain an embedded 0x00 byte before
        # the true end-of-message null. Naively stopping at the FIRST
        # null truncates the body there and permanently desyncs this
        # cursor for every message record after it in the packet: each
        # subsequent read starts mid-body instead of at a real message
        # boundary, producing one bogus "message" (garbled header
        # fields pulled from what should still be body text) before the
        # cursor happens to re-align on a genuine MSG_TYPE_2 boundary.
        # Confirmed against a real inbound rescan packet: an "[ANSI]"
        # post's body was truncated to 3 bytes ("Esc"), and the very
        # next parsed "message" was pure garbage lifted from that same
        # post's remaining body text.
        #
        # Defend against this by only accepting a candidate null as the
        # real terminator if what immediately follows it looks like a
        # valid boundary: the packet's own end-of-data marker (0x00
        # 0x00), the end of the buffer, or another message's MSG_TYPE_2
        # marker *and* a plausible date field right where one should be
        # (14 bytes further in, past the 2-byte marker + 12-byte
        # routing header). The 2-byte marker alone isn't a reliable
        # enough signal: a real inbound packet (confirmed via a
        # BINKP_DEBUG_DUMP_DIR capture) contained a message whose body
        # had embedded binary content that happened to contain the
        # exact byte sequence 0x00 0x02 0x00 by pure chance, which the
        # marker-only check falsely accepted as a message boundary.
        # Requiring a date-shaped string immediately after makes a
        # coincidental match astronomically less likely without
        # weakening detection of genuine boundaries (every real
        # FTS-0001 message record has this field by definition).
        search_from = pos
        body_end = None
        while True:
            candidate = data.find(b'\x00', search_from)
            if candidate == -1:
                break
            nxt = data[candidate + 1:candidate + 3]
            if _is_real_packet_end(data, candidate) or candidate + 1 >= len(data):
                body_end = candidate
                break
            if nxt == MSG_TYPE_2:
                date_pos = candidate + 1 + 2 + 12
                if _DATE_FIELD_RE.match(data[date_pos:date_pos + 21]) and \
                        _chain_looks_like_real_messages(data, candidate + 1):
                    body_end = candidate
                    break
            search_from = candidate + 1
        if body_end is None:
            body_bytes = data[pos:]
            pos = len(data)
        else:
            body_bytes = data[pos:body_end]
            pos = body_end + 1

        # Strip soft-CR (0x8D) — SBBSecho's StripSoftCRs default. Some
        # editors insert these as "soft" line wraps; they break ours later.
        body_bytes = body_bytes.replace(b'\x8d', b'')

        # Detect @CHRS kludge in the raw bytes to pick the right codec.
        # Synchronet's AutoUTF8 marks UTF-8 messages with @CHRS UTF-8 4.
        chrs_decl = b''
        for line in body_bytes.split(b'\r'):
            line = line.lstrip(b'\n')
            if line.startswith(b'\x01CHRS:') or line.startswith(b'\x01CHARSET:'):
                chrs_decl = line.split(b':', 1)[1].strip().upper()
                break
        if chrs_decl.startswith(b'UTF-8') or chrs_decl.startswith(b'UTF8'):
            body_text = body_bytes.decode('utf-8', errors='replace')
        else:
            body_text = body_bytes.decode('latin-1', errors='replace')

        # Extract AREA: tag (echomail discriminator), kludges (^A-prefixed),
        # SEEN-BY/PATH (echomail routing), tear/origin lines. Netmail has no
        # AREA: line — we detect it here and label area_tag=None.
        area_tag = None
        lines = body_text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
        clean_lines = []
        kludges = []
        seenby = []
        path = []
        origin = None
        tear = None
        for line in lines:
            if line.startswith('AREA:'):
                area_tag = line[5:].strip()
            elif line.startswith('\x01PATH:'):
                path.append(line[6:].strip())
            elif line.startswith('\x01'):
                kludges.append(line[1:])     # store WITHOUT the SOH
            elif line.startswith('SEEN-BY:'):
                seenby.append(line[8:].strip())
            elif line.startswith(' * Origin:'):
                origin = line[10:].strip()
            elif line.startswith('---'):
                tear = line
            else:
                clean_lines.append(line)

        # Extract specific kludge values for the DB.
        msg_id = ''
        reply_id = ''
        chrs_value = chrs_decl.decode('latin-1', errors='replace') if chrs_decl else ''
        from_address = ''
        to_address = ''
        for k in kludges:
            # Kludges can be `KEY: value` (colon form) or `KEY value`
            # (space form, FTS-0001 spec form for INTL/FMPT/TOPT). Split
            # on the first whitespace OR colon — whichever comes first.
            import re as _kr
            mt = _kr.match(r'\s*([A-Z][A-Z0-9_-]*)\s*[:\s]\s*(.*)', k, _kr.IGNORECASE)
            if mt:
                head = mt.group(1).upper()
                val = mt.group(2).strip()
            else:
                head, _, val = k.partition(':')
                head = head.strip().upper()
                val = val.strip()
            if head == 'MSGID':
                msg_id = val.split(' ', 1)[0] + (' ' + val.split(' ', 1)[1] if ' ' in val else '')
                msg_id = val
            elif head in ('REPLY', 'REPLYID'):
                reply_id = val
            elif head == 'CHRS' and not chrs_value:
                chrs_value = val
            elif head == 'FMPT':
                pass  # point info — already encoded in from_address if any
            elif head == 'INTL':
                # INTL <dest_addr> <orig_addr>
                parts_intl = val.split()
                if len(parts_intl) >= 2:
                    to_address, from_address = parts_intl[0], parts_intl[1]

        # If the kludge-based @INTL didn't fill in the per-message FTN
        # addresses, derive them from the routing header. orig/dest_node and
        # net are in the 12-byte header; zone comes from the packet header
        # (we don't have direct access here so this only synthesizes the
        # net/node — sufficient for routing within a zone).
        if not from_address and orig_net:
            from_address = f'{orig_net}/{orig_node}'
        if not to_address and dest_net:
            to_address = f'{dest_net}/{dest_node}'

        messages.append({
            'from_name': from_name,
            'to_name': to_name,
            'subject': subject,
            'date_str': date_str,
            'body': '\n'.join(clean_lines).strip(),
            'area_tag': area_tag,            # None means netmail
            'origin_line': origin,
            'tear_line': tear,
            'kludges': kludges,
            'seenby': seenby,
            'path': path,
            'msg_id': msg_id,
            'reply_id': reply_id,
            'chrs': chrs_value,
            'from_address': from_address,
            'to_address': to_address,
            'attribute': msg_attr,
            'is_private': bool(msg_attr & 0x0001),
            'is_crash': bool(msg_attr & 0x0002),
        })

    return messages


# ---------------------------------------------------------------------------
# BinkP session client
# ---------------------------------------------------------------------------

class BinkPClient:
    """
    Minimal BinkP/1.1 client that can connect to an upstream hub,
    exchange .pkt files, and return parsed message data.
    """

    def __init__(self, host: str, port: int, our_address: str,
                 hub_address: str, password: str = '', timeout: int = 60,
                 use_tls: bool = False, domain: str = None,
                 transcript: list = None):
        self.host = host
        self.port = port
        self.our_address = our_address
        self.hub_address = hub_address
        self.password = password
        self.timeout = timeout
        self.use_tls = use_tls
        # Per FSP-1028 the FTN domain must match [a-z0-9_~-]+, ≤8 chars.
        # Strip illegal chars from a human-readable network name.
        import re as _re
        clean = _re.sub(r'[^a-z0-9_~-]+', '',
                        (domain or '').strip().lower())[:8]
        self.domain = clean or None
        self._sock = None
        # Caller-owned list (not an instance-only attribute) -- if
        # poll() raises partway through, the caller (poller.py's
        # _do_poll()) still holds this same list and can read whatever
        # got captured up to the failure point, since the BinkPClient
        # instance itself goes out of scope with the exception.
        self._transcript = transcript if transcript is not None else []

    def _log_transcript(self, line: str):
        ts = datetime.utcnow().strftime('%H:%M:%S.%f')[:-3]
        self._transcript.append(f'{ts} {line}')

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def poll(self, outbound_messages=None, data_dir: str = '/tmp',
             hatch_items=None):
        """
        Connect to the hub, send outbound messages + queued hatch files,
        receive inbound files.

        `hatch_items` is an optional list of HatchQueue rows targeted at this
        peer. For each, we transmit the binary AND a corresponding .tic
        manifest. On success we collect the row IDs in result['hatched_ids']
        so the caller can flip status to 'sent'.

        Returns:
            dict with 'received' (list of parsed message dicts),
                      'sent' (count of outbound messages sent),
                      'hatched_ids' (HatchQueue ids successfully shipped).
        """
        result = {'received': [], 'sent': 0, 'hatched_ids': []}
        self._connect()
        try:
            self._handshake()
            if outbound_messages:
                result['sent'] = self._send_messages(outbound_messages, data_dir)
            if hatch_items:
                result['hatched_ids'] = self._send_hatch(hatch_items)
            result['received'] = self._receive_messages(data_dir)
        finally:
            self._disconnect()
        return result

    def _send_hatch(self, hatch_items):
        """Ship pending HatchQueue rows: binary file + .tic manifest pair.

        Returns a list of HatchQueue.id values successfully sent. The caller
        is responsible for flipping their status to 'sent' in the DB."""
        from .tic import build_tic_text
        sent_ids = []
        for item in hatch_items:
            # 1. Binary file
            try:
                with open(item.binary_path, 'rb') as f:
                    binary = f.read()
            except OSError as exc:
                logger.error("Hatch: cannot read binary %s: %s",
                             item.binary_path, exc)
                continue
            mtime = int(datetime.utcnow().timestamp())
            self._send_cmd(CMD_FILE,
                           f'{item.filename} {len(binary)} {mtime} 0')
            self._send_data(binary)
            if not self._wait_got():
                logger.warning("Hatch: peer didn't ack %s", item.filename)
                continue

            # 2. TIC manifest — same basename with .tic suffix
            tic_text = build_tic_text(item, self.our_address)
            tic_bytes = tic_text.encode('cp437', errors='replace')
            tic_name = item.filename.rsplit('.', 1)[0] + '.tic'
            self._send_cmd(CMD_FILE,
                           f'{tic_name} {len(tic_bytes)} {mtime} 0')
            self._send_data(tic_bytes)
            if not self._wait_got():
                logger.warning("Hatch: peer didn't ack %s", tic_name)
                continue

            sent_ids.append(item.id)
            logger.info("Hatch: shipped %s + %s to %s",
                        item.filename, tic_name, item.peer_address)
        return sent_ids

    def _wait_got(self, max_frames: int = 20) -> bool:
        """Read frames until we see M_GOT (success) or M_SKIP/M_ERR (fail)."""
        for _ in range(max_frames):
            is_cmd, data = self._recv_frame_logged()
            if is_cmd:
                cmd = data[0]
                if cmd == CMD_GOT:
                    return True
                if cmd in (CMD_SKIP, CMD_ERR):
                    return False
        return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _connect(self):
        logger.info("BinkP: connecting to %s:%s%s",
                    self.host, self.port, ' (TLS)' if self.use_tls else '')
        self._log_transcript(
            f'CONNECT {self.host}:{self.port}{" (TLS)" if self.use_tls else ""}')
        sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        if self.use_tls:
            import ssl
            ctx = ssl.create_default_context()
            sock = ctx.wrap_socket(sock, server_hostname=self.host)
        self._sock = sock
        self._log_transcript('CONNECTED')

    def _disconnect(self):
        if self._sock:
            # Brief drain before closing: an immediate close() while the
            # peer still has trailing bytes in flight (e.g. its own
            # final M_EOB) can register on their end as an abrupt,
            # unexpected disconnect rather than a clean session end.
            try:
                self._sock.settimeout(2.0)
                while self._sock.recv(4096):
                    pass
            except Exception:
                pass
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
        self._log_transcript('DISCONNECT')

    def _send_cmd(self, cmd: int, text: str = ''):
        name = CMD_NAMES.get(cmd, str(cmd))
        self._log_transcript(f'>> CMD {name}' + (f': {text}' if text else ''))
        self._sock.sendall(_build_cmd(cmd, text))

    def _send_data(self, data: bytes):
        # Send in chunks ≤ 32 KiB
        self._log_transcript(f'>> DATA: {len(data)} bytes')
        chunk_size = 0x7FFF
        for i in range(0, len(data), chunk_size):
            self._sock.sendall(_build_data(data[i:i + chunk_size]))

    def _recv_frame_logged(self):
        """Wrapper around the module-level _recv_frame() that also
        appends a transcript line -- one centralized place instead of
        repeating logging at every call site (_handshake,
        _send_messages, _receive_messages, _wait_got)."""
        is_cmd, data = _recv_frame(self._sock)
        if is_cmd:
            cmd = data[0]
            text = data[1:].decode('latin-1', errors='replace')
            name = CMD_NAMES.get(cmd, str(cmd))
            self._log_transcript(f'<< CMD {name}' + (f': {text}' if text else ''))
        else:
            self._log_transcript(f'<< DATA: {len(data)} bytes')
        return is_cmd, data

    def _handshake(self):
        """Perform BinkP session setup with optional CRAM-MD5 (FTS-1027).

        The hub may advertise an `OPT CRAM-MD5-<challenge>` NUL frame before
        ADR. If it does, we hash our password with that challenge and send
        it back as `CRAM-MD5-<digest>` instead of plain text. Synchronet's
        BinkIT defaults to requiring this; older Argus/Husky hubs accept
        plain. We always send our own auth method based on what we've seen.
        """
        import hashlib
        import hmac

        # Announce ourselves — full Synchronet-style preamble.
        import os as _os
        from datetime import datetime as _dt
        sysop = _os.environ.get('SYSOP_NAME', 'sysop')
        loc = _os.environ.get('BBS_LOCATION', 'Earth')
        sys_name = _os.environ.get('BBS_NAME', 'ANetBBS')
        self._send_cmd(CMD_NUL, f'SYS {sys_name}')
        self._send_cmd(CMD_NUL, f'ZYZ {sysop}')
        self._send_cmd(CMD_NUL, f'LOC {loc}')
        self._send_cmd(CMD_NUL, 'NDL 115200,TCP,BINKP')
        self._send_cmd(CMD_NUL,
                       f'TIME {_dt.utcnow().strftime("%a, %d %b %Y %H:%M:%S +0000")}')
        from .. import __version__ as _anetbbs_version
        self._send_cmd(CMD_NUL, f'VER ANetBBS/{_anetbbs_version} binkp/1.1')
        self._send_cmd(CMD_NUL, 'OPT CRAM-MD5')
        # Send ONE address per M_ADR -- qualified (`addr@domain`) when we
        # have a domain, bare otherwise. Previously sent BOTH forms as a
        # "cover whichever form the peer keys on" compat measure, but
        # real binkd's ADR() handler (protocol.c) calls bsy_add() once
        # per space-separated token in the line, with no de-duplication.
        # For a "secure" (password-protected) link, the first token
        # acquires binkd's busy-lock for that AKA; the second token --
        # same numeric address, just unqualified -- immediately fails to
        # acquire the SAME lock a split-second later in the same loop,
        # and binkd sends "Secure AKA <addr> busy" and drops the session
        # BEFORE password verification ever runs. Live-caught: a real
        # binkd peer (binkp.pharcyde.org) rejected every single connect
        # attempt this way regardless of password correctness, since the
        # failure happens purely from binkd colliding with itself on our
        # own duplicate token. A spec-compliant peer parses the
        # domain-qualified form fine on its own (binkd's own
        # parse_ftnaddress() supports the @domain suffix), so sending
        # only one form is sufficient and doesn't trigger this collision.
        if self.domain:
            self._send_cmd(CMD_ADR, f'{self.our_address}@{self.domain}')
        else:
            self._send_cmd(CMD_ADR, self.our_address)

        cram_challenge = None
        password_sent = False
        authenticated = False

        for _ in range(50):
            is_cmd, data = self._recv_frame_logged()
            if not is_cmd:
                continue
            cmd = data[0]
            text = data[1:].decode('latin-1', errors='replace')

            if cmd == CMD_NUL:
                logger.debug("BinkP NUL: %s", text)
                # OPT line may carry a CRAM-MD5 challenge: "OPT CRAM-MD5-<hex>"
                if text.upper().startswith('OPT '):
                    for opt in text[4:].split():
                        if opt.upper().startswith('CRAM-MD5-'):
                            cram_challenge = opt[len('CRAM-MD5-'):]
                            logger.info("BinkP: hub offered CRAM-MD5 challenge")
            elif cmd == CMD_ADR:
                logger.debug("BinkP remote ADR: %s", text)
                if password_sent:
                    continue
                pw = self.password or '-'
                if cram_challenge:
                    try:
                        challenge = bytes.fromhex(cram_challenge)
                    except ValueError:
                        # Some hubs send the raw challenge text; hash that
                        challenge = cram_challenge.encode('latin-1')
                    digest = hmac.new(pw.encode('latin-1'),
                                      challenge,
                                      hashlib.md5).hexdigest()
                    self._send_cmd(CMD_PWD, f'CRAM-MD5-{digest}')
                    logger.info("BinkP: replied with CRAM-MD5 digest")
                else:
                    self._send_cmd(CMD_PWD, pw)
                    logger.info("BinkP: sent plain password")
                password_sent = True
            elif cmd == CMD_OK:
                logger.info("BinkP: password accepted")
                authenticated = True
                break
            elif cmd == CMD_ERR:
                raise ConnectionError(f"BinkP: authentication error: {text}")
            elif cmd == CMD_BSY:
                raise ConnectionError(f"BinkP: hub busy: {text}")

        if not authenticated:
            raise ConnectionError("BinkP: handshake did not complete")

    def _send_messages(self, messages, data_dir: str) -> int:
        """Package and send outbound messages as a .pkt file."""
        if not messages:
            return 0
        pkt_data = _build_ftn_packet(messages, self.our_address, self.hub_address)
        # Conventional FTN packet-naming style (8-hex-digit timestamp,
        # e.g. binkd/SBBSecho/Mystic all use this) rather than a
        # decimal, prefixed name -- purely cosmetic/compliance, flagged
        # by a real FTN sysop reviewing a session log, unrelated to any
        # functional bug.
        filename = f'{int(datetime.utcnow().timestamp()):08x}.pkt'
        size = len(pkt_data)
        mtime = int(datetime.utcnow().timestamp())

        self._send_cmd(CMD_FILE, f'{filename} {size} {mtime} 0')
        self._send_data(pkt_data)

        # Wait for GOT or SKIP
        for _ in range(20):
            is_cmd, data = self._recv_frame_logged()
            if is_cmd:
                cmd = data[0]
                text = data[1:].decode('latin-1', errors='replace')
                if cmd == CMD_GOT:
                    logger.info("BinkP: hub acknowledged %s", filename)
                    break
                elif cmd == CMD_SKIP:
                    logger.warning("BinkP: hub skipped %s", filename)
                    break
                elif cmd == CMD_ERR:
                    logger.error("BinkP: error during send: %s", text)
                    break

        return len(messages)

    def _receive_messages(self, data_dir: str):
        """Receive inbound files from the hub, dispatch by content.

        Three content paths:
          - raw FTS-0001 packet (magic bytes 02 00 / 02 01 at offset 18)
              → parse + collect
          - ZIP bundle (magic bytes 50 4B 03 04)
              → unzip, parse each member that looks like a packet
          - anything else (TIC manifests, hatched binaries, unknown)
              → write to `data/binkp/inbound` for the TIC scanner

        The OLD implementation treated every file as a raw .pkt and
        used `last 2 bytes == \\x00\\x00` as the completion marker —
        which Mystic's ZIP-wrapped bundles never satisfy. Result was
        5 files arrive but nothing imports (the `received=0` we kept
        seeing in the TQWnet logs). Fixed by using the byte-count
        from CMD_FILE (`name size mtime offset`) for completion and
        dispatching through proper content sniffers.

        After our M_EOB the hub may reply M_EOB or close immediately;
        both are clean (no poll-failure signal).

        binkp/1.1 (which our own VER line advertises) expects a
        two-round M_EOB handshake, not a single exchange: once each
        side has both sent and received at least one M_EOB, it
        acknowledges with a second M_EOB before the session is
        considered done. A strict, spec-compliant peer (real binkd
        confirmed live) waits for that second round and treats an
        early close as an unexpected mid-session disconnect -- even
        though the file transfer itself completed -- rather than a
        clean end. sent_eob/got_eob track this; a lenient peer that
        only ever sends one M_EOB and then closes is handled fine too,
        since the resulting connection-close is still caught below as
        clean.
        """
        import io as _io
        import os as _os
        import zipfile as _zipfile

        parsed = []
        sent_eob = 1
        got_eob = 0
        self._send_cmd(CMD_EOB)

        pending_file = None
        pending_size = 0
        pending_data = b''
        # Persistent inbound path — the prior /tmp default was tmpfs
        # on most distros so anything stashed vanished on restart.
        inbound_dir = _os.environ.get('BINKP_INBOUND_DIR') or _os.path.join(
            (data_dir or 'data'), 'binkp', 'inbound')

        def _is_fts_packet(buf):
            return len(buf) >= 60 and buf[18:20] in (b'\x02\x00', b'\x02\x01')

        def _is_zip(buf):
            return len(buf) >= 4 and buf[:4] == b'PK\x03\x04'

        def _debug_dump_packet(name, data):
            """Diagnostic-only, opt-in via env var, no effect unless set:
            dump the raw bytes of an inbound FTS-0001 packet -- whether
            received directly or extracted from an arcmail ZIP bundle --
            before parsing. Added to forensically diagnose a
            packet-record parser desync (embedded-null bodies truncating
            a message and misaligning everything after it) that a first
            fix attempt (v1.0b2.70) reduced but did not fully eliminate.
            By the time a corrupted message reaches the DB, the true
            byte layout that caused the misparse is already gone, so
            root-causing the remainder needs the actual wire bytes, not
            another guess. Safe to leave in permanently: zero cost
            unless BINKP_DEBUG_DUMP_DIR is explicitly set."""
            debug_dir = _os.environ.get('BINKP_DEBUG_DUMP_DIR')
            if not debug_dir:
                return
            try:
                _os.makedirs(debug_dir, exist_ok=True)
                dump_name = f'{datetime.utcnow().strftime("%Y%m%dT%H%M%S%f")}_{name}'
                with open(_os.path.join(debug_dir, dump_name), 'wb') as fh:
                    fh.write(data)
                logger.info('BinkP: dumped raw packet %s (%d bytes) to %s',
                           dump_name, len(data), debug_dir)
            except OSError as exc:
                logger.warning('BinkP: failed to dump debug packet %s: %s',
                               name, exc)

        def _debug_manifest(fname, buf):
            """Unconditional companion to _debug_dump_packet: appends one
            line per file handed to _import_completed, regardless of
            whether it's recognized as an FTS-0001 packet, a ZIP bundle,
            or neither. Uses plain file I/O, not the `logging` module --
            confirmed on the live server that production's LOG_LEVEL
            silently swallows this module's INFO-level messages entirely
            (zero "BinkP" lines found in gunicorn-error.log despite real
            traffic flowing through it), so a log-based diagnostic can't
            be trusted to be visible. Same opt-in, zero-cost-unless-set
            gating as _debug_dump_packet."""
            debug_dir = _os.environ.get('BINKP_DEBUG_DUMP_DIR')
            if not debug_dir:
                return
            try:
                _os.makedirs(debug_dir, exist_ok=True)
                kind = 'fts' if _is_fts_packet(buf) else (
                    'zip' if _is_zip(buf) else 'other')
                line = f'{datetime.utcnow().isoformat()} {fname} {len(buf)} {kind}\n'
                with open(_os.path.join(debug_dir, '_manifest.log'), 'a') as fh:
                    fh.write(line)
            except OSError:
                pass

        def _import_completed(fname, buf):
            """Dispatch a fully-received file. Returns a list of parsed
            messages (may be empty for non-mail content like TICs)."""
            _debug_manifest(fname, buf)
            if _is_fts_packet(buf):
                _debug_dump_packet(fname, buf)
                try:
                    return _parse_ftn_packet(buf)
                except Exception:
                    logger.exception('BinkP: failed parsing %s as FTS-0001',
                                     fname)
                    return []
            if _is_zip(buf):
                out = []
                try:
                    with _zipfile.ZipFile(_io.BytesIO(buf)) as zf:
                        for info in zf.infolist():
                            if info.is_dir():
                                continue
                            try:
                                inner = zf.read(info.filename)
                            except Exception as exc:
                                logger.warning(
                                    'BinkP: zip member %s in %s unreadable: %s',
                                    info.filename, fname, exc)
                                continue
                            if _is_fts_packet(inner):
                                _debug_dump_packet(
                                    f'{fname}__{info.filename}', inner)
                                try:
                                    out.extend(_parse_ftn_packet(inner))
                                except Exception:
                                    logger.exception(
                                        'BinkP: failed parsing %s inside %s',
                                        info.filename, fname)
                            else:
                                logger.info(
                                    'BinkP: zip member %s in %s is not a '
                                    'FTS-0001 packet — skipped',
                                    info.filename, fname)
                except _zipfile.BadZipFile as exc:
                    logger.warning('BinkP: bad ZIP %s: %s', fname, exc)
                if out:
                    return out
                # ZIP contained no mail packets — it's a hatched file binary
                # (e.g. a nodelist .zip delivered via TIC). Fall through to
                # write it to inbound so the TIC scanner can find it.
            # Anything else → file for TIC scanner
            try:
                _os.makedirs(inbound_dir, exist_ok=True)
                with open(_os.path.join(inbound_dir, fname), 'wb') as fh:
                    fh.write(buf)
                logger.info(
                    'BinkP: stored unrecognised file %s (%d bytes) in %s '
                    '— neither ZIP nor FTS-0001 packet, scanning for TIC',
                    fname, len(buf), inbound_dir)
            except OSError as exc:
                logger.warning('BinkP: could not stash %s in %s: %s',
                               fname, inbound_dir, exc)
            return []

        for _ in range(5000):
            try:
                is_cmd, data = self._recv_frame_logged()
            except (ConnectionError, OSError) as exc:
                logger.info(
                    "BinkP: hub closed after our M_EOB (clean): %s", exc)
                break

            if is_cmd:
                cmd = data[0]
                text = data[1:].decode('latin-1', errors='replace')

                if cmd == CMD_FILE:
                    parts = text.split()
                    pending_file = parts[0] if parts else 'unknown.pkt'
                    # CMD_FILE is `name size mtime offset`
                    try:
                        pending_size = int(parts[1]) if len(parts) > 1 else 0
                    except ValueError:
                        pending_size = 0
                    pending_data = b''
                    logger.debug(
                        "BinkP: receiving file %s (%d bytes expected)",
                        pending_file, pending_size)

                elif cmd == CMD_EOB:
                    got_eob += 1
                    logger.info("BinkP: end of batch from hub (%d/2)", got_eob)
                    if sent_eob < 2:
                        try:
                            self._send_cmd(CMD_EOB)
                            sent_eob += 1
                        except OSError as exc:
                            logger.info(
                                "BinkP: hub closed before second EOB (clean): %s",
                                exc)
                            break
                    if got_eob >= 2:
                        break

                elif cmd == CMD_ERR:
                    logger.error("BinkP: session error: %s", text)
                    break

                elif cmd == CMD_NUL:
                    pass  # info frame

            else:
                if pending_file is None:
                    continue
                pending_data += data
                if pending_size > 0 and len(pending_data) >= pending_size:
                    msgs = _import_completed(pending_file,
                                             pending_data[:pending_size])
                    if msgs:
                        parsed.extend(msgs)
                        logger.info(
                            "BinkP: imported %d msg(s) from %s",
                            len(msgs), pending_file)
                    try:
                        self._send_cmd(CMD_GOT, pending_file)
                    except OSError as exc:
                        logger.info(
                            "BinkP: hub closed before GOT ack (clean): %s", exc)
                        break
                    pending_file = None
                    pending_size = 0
                    pending_data = b''

        # Run TIC scan on anything we stashed during this batch.
        try:
            if _os.path.isdir(inbound_dir):
                from .tic import scan_inbound
                n = scan_inbound(inbound_dir)
                if n:
                    logger.info('BinkP: TIC scanner processed %d files', n)
        except Exception:
            logger.exception('BinkP: TIC scan after receive failed')

        return parsed
