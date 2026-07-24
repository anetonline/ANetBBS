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
import time
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


def _sanitize_inbound_filename(raw: str) -> str:
    """Reduce a peer-supplied CMD_FILE filename to a bare basename before
    it's ever used in a local os.path.join() write. Confirmed real gap:
    nothing anywhere in this file (or binkp_server.py's own equivalent
    fallback-write path) called os.path.basename() or rejected a
    traversal/absolute path -- an authenticated-but-malicious or
    misconfigured peer could supply e.g. '/etc/cron.d/x' (os.path.join
    with an absolute second argument silently DISCARDS the first, per
    Python's own documented join() semantics) or '../../x' to write
    outside the intended inbound directory. Falls back to a fixed safe
    name if sanitizing leaves nothing usable."""
    import os as _os
    name = _os.path.basename((raw or '').strip().replace('\\', '/'))
    name = name.lstrip('.').strip()
    if not name:
        name = 'unnamed.bin'
    return name


def _build_ftn_packet(messages, our_addr: str, hub_addr: str,
                      packet_password: str = '',
                      default_crash: bool = False,
                      default_hold: bool = False,
                      default_direct: bool = False) -> bytes:
    """
    Produce a minimal FTS-0001 Type-2+ .pkt file containing *messages*.
    *messages* is a list of EchomailMessage ORM objects.

    `packet_password` is the FTS-0001 packet-HEADER password (distinct
    from the BinkP session password) -- NUL-padded/truncated to 8 ASCII
    bytes on the wire.

    `default_crash`/`default_hold`/`default_direct` are per-network
    defaults (EchomailNetwork.default_crash/default_hold/default_direct)
    ORed into every outbound netmail message's attribute bits alongside
    any explicit per-message flag already set on it. `default_direct`
    has no FTS-0001 bit of its own -- see EchomailNetwork's own comment
    in models.py -- so it sets the Crash bit too.
    """
    def _parse_ftn(addr):
        try:
            # Real bug found live: a qualified address with NO point
            # number but a domain suffix (e.g. '1200:1/4@anet' -- this
            # network's own convention) was never stripped of '@anet'
            # before splitting node from point, so node_point ended up
            # as the literal string '4@anet' -- int('4@anet') always
            # raised, silently falling back to 1:1/1.0 for EVERY
            # outbound packet destined to this network. Confirmed via
            # the downstream node's own tosser log ("Importing ...
            # from 1200:1/1 to 1:1/1" -- our own hub's real address
            # correctly parsed, but the destination corrupted to the
            # fallback). Strip the domain suffix first, exactly like
            # the qualified-address convention (addr@domain) requires.
            addr = addr.split('@', 1)[0]
            zone, rest = addr.split(':')
            net, node_point = rest.split('/')
            node = node_point.split('.')[0]
            point = node_point.split('.')[1] if '.' in node_point else '0'
            return int(zone), int(net), int(node), int(point)
        except Exception:
            # Silent fallback used to mask malformed addresses reaching
            # the wire as a plausible-looking but WRONG 1:1/1.0 -- a
            # sysop debugging misrouted mail had no signal this ever
            # happened. Logged now per the "logs for send and receive"
            # correctness pass.
            logger.warning(
                "BinkP: could not parse FTN address %r, using 1:1/1.0",
                addr)
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
    # NUL-padded ASCII, exactly 8 bytes -- FTS-0001's header field is a
    # fixed-width slot, not length-prefixed. Longer configured passwords
    # are silently truncated rather than raising, since a working (if
    # truncated) packet is better than a hard failure on every poll.
    pkt_pw_bytes = (packet_password or '').encode('ascii', errors='ignore')[:8]
    pkt_pw_bytes = pkt_pw_bytes.ljust(8, b'\x00')
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
        pkt_pw_bytes,                               # password
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
        _nm_msg = msg._nm if hasattr(msg, '_nm') else msg
        # Crash/Hold/Direct are netmail delivery-flavor concepts (FTN
        # convention has no per-message meaning for them on echomail area
        # posts) -- the network-level defaults below only apply here too.
        msg_crash = is_netmail and getattr(_nm_msg, 'is_crash', False)
        msg_hold = is_netmail and getattr(_nm_msg, 'is_hold', False)
        if is_netmail and (msg_crash or default_crash or default_direct):
            attr |= ATTR_CRASH
        if is_netmail and (msg_hold or default_hold):
            attr |= ATTR_HOLD

        # Re-use any kludges that came in with the message (e.g. when
        # forwarding) and add our own.  We add @CHRS, @MSGID, @TID; for
        # echomail we also add an AREA line and append SEEN-BY/PATH.
        existing = kludges_from_json(msg.kludges) if msg.kludges else []
        wanted = []
        # Drop any existing CHRS/MSGID/TID/PID/REPLY/INTL/FMPT/TOPT --
        # we'll regenerate consistent ones below. Real gap found in a
        # full echomail-subsystem audit: this filter only ever dropped
        # CHRS/MSGID/TID/PID, but web/netmail.py's compose() route
        # pre-builds and stores a FULL kludge set on NetmailMessage.
        # kludges -- including REPLY/INTL/FMPT/TOPT -- which then got
        # appended here via `wanted` UNFILTERED, right alongside this
        # function's own independently-regenerated copies of the exact
        # same kludges (lines below), shipping every inter-zone/point/
        # reply netmail composed through the web UI with each of those
        # kludges duplicated on the wire.
        for k in existing:
            head = k.split(' ', 1)[0].split(':', 1)[0].upper()
            if head in ('CHRS', 'MSGID', 'TID', 'PID', 'REPLY', 'INTL', 'FMPT', 'TOPT'):
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
        # PID identifies the originating software (FTS-0001 convention,
        # same idea as TID below but for the ORIGINATOR rather than the
        # last tosser to touch it) -- was stripped from `existing` above
        # but never regenerated anywhere, so outbound netmail never
        # actually carried one despite compose() building one.
        kludge_head.append('PID: ANETBBS 1.0')

        # Netmail-specific routing kludges (FTS-4001 + FSC-4009).
        # Spec form is space-separated, NO colon: `^AFMPT 1`, `^ATOPT 1`,
        # `^AINTL <to> <from>`. binkterm-php BinkdProcessor.php:1965
        # confirms; Mystic AreaFix matches strictly per spec — sending the
        # colon form ('FMPT:') means the point isn't recognized and the
        # sender appears as the boss, so the per-point AreaFix password
        # lookup fails ("Invalid password" reply). Synchronet accepts
        # either form.
        if is_netmail:
            # Always write @INTL, not just when our zone differs from the
            # recipient's. FTS-0001's per-message binary routing header
            # has NO zone field at all -- @INTL is the only place zone
            # info travels on the wire -- and skipping it whenever zones
            # happen to match assumes the receiving system will default
            # untagged mail to that same zone. Confirmed false in
            # practice: a real downstream node with multiple AKAs (a
            # primary zone-1 FidoNet address plus a secondary zone-1200
            # address for this network) filed a same-zone reply from us
            # under its zone-1 identity instead -- "Craig Hendricks
            # (1:1/4)" instead of "(1200:1/4)" -- because nothing on the
            # wire said otherwise. @INTL is valid and universally
            # supported by every real FTN mailer whether or not zones
            # differ, so there's no downside to always including it.
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
            except (ValueError, TypeError) as exc:
                # Silent swallow used to mean a message with corrupted
                # SEEN-BY/PATH JSON would quietly restart its routing
                # history from empty here -- no loop-detection signal to
                # the next hop, and no sysop-visible sign why. Logged now
                # per the "logs for send and receive" correctness pass.
                logger.warning(
                    "BinkP: msg %s has malformed seenby/path JSON, "
                    "restarting routing history: %s",
                    getattr(msg, 'id', '?'), exc)
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
        #
        # The trailing "(address)" must ALWAYS be appended, not just used
        # as a fallback for an empty origin_line -- real bug found live
        # (a sysop's own outbound message): msg.origin_line is populated
        # for essentially every outbound message (the compose route always
        # sets it from ECHOMAIL_ORIGIN_LINE, a single global tagline), so
        # `msg.origin_line or f'... ({our_addr})'` never actually reached
        # the address-inclusive branch -- every origin line shipped with
        # no AKA at all, on every network, regardless of which network's
        # own address (our_addr, already resolved per-network above for
        # multi-hub-identity support) the message was actually going out
        # under. A real FTN origin line's whole point is identifying which
        # address to route a reply back to.
        origin_text = (msg.origin_line or 'ANETBBS').strip()
        if not origin_text.endswith(')'):
            origin_text = f'{origin_text} ({our_addr})'
        msg_assembled = build_message(
            body=body_text,
            kludges=wanted,
            tear=msg.tear_line or '--- ANETBBS 1.0',
            origin=origin_text,
            seenby=seenby_existing if area_tag else None,
            path=path_existing if area_tag else None,
        )
        # Echomail: prepend AREA: line BEFORE the kludges (FTS-0004).
        if area_tag:
            msg_assembled = f'AREA:{area_tag}\r\n' + msg_assembled

        # Encode using the declared charset.  UTF-8 is the only multi-byte
        # one we honor here — everything else round-trips as 8-bit (CP437).
        # Real bug found live: a message composed through the web UI can
        # contain genuine Unicode characters (e.g. pasted box-drawing art)
        # rather than latin-1-wrapped raw bytes -- a blanket
        # encode('latin-1', errors='replace') silently turned every one
        # of those into '?' in the outbound packet. encode_body_cp437()
        # (features/wire_encoding.py) recovers the correct CP437 byte for
        # genuine Unicode characters instead of destroying them.
        from ..features.wire_encoding import encode_body_cp437
        chrs_upper = chrs.upper()
        if chrs_upper.startswith(('UTF-8', 'UTF8')):
            encoded = msg_assembled.encode('utf-8', errors='replace') + b'\x00'
        else:
            encoded = encode_body_cp437(msg_assembled) + b'\x00'

        from_b = encode_body_cp437(msg.from_name or 'Sysop')[:35] + b'\x00'
        to_b = encode_body_cp437(msg.to_name or 'All')[:35] + b'\x00'
        subj_b = encode_body_cp437(msg.subject or '')[:71] + b'\x00'
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
        fmpt = ''
        topt = ''
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
                # Origin point number (FTS-4008/FSC-0035) -- our OWN send
                # side (_build_ftn_packet above) never puts the point
                # inside @INTL's own address strings, only here and in
                # @TOPT, so this MUST be used to reconstruct a
                # point-qualified from_address or it's silently lost on
                # every inbound message to/from a point system (e.g.
                # "1200:1/2.5" collapsing to "1200:1/2"). Real gap found
                # during a netmail send/receive correctness pass -- this
                # was previously parsed and discarded (`pass`), and @TOPT
                # wasn't even matched at all.
                fmpt = val.strip()
            elif head == 'TOPT':
                topt = val.strip()
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

        # Apply @FMPT/@TOPT point numbers, unless the address already
        # carries one (some senders DO put the point directly in @INTL).
        if fmpt and from_address and '.' not in from_address.split('/', 1)[-1]:
            from_address = f'{from_address}.{fmpt}'
        if topt and to_address and '.' not in to_address.split('/', 1)[-1]:
            to_address = f'{to_address}.{topt}'

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
                 transcript: list = None, packet_password: str = '',
                 default_crash: bool = False, default_hold: bool = False,
                 default_direct: bool = False):
        self.host = host
        self.port = port
        self.our_address = our_address
        self.hub_address = hub_address
        self.password = password
        self.timeout = timeout
        self.use_tls = use_tls
        # FTS-0001 packet-HEADER password + per-network netmail flavor
        # defaults -- distinct from `password` above, which is the BinkP
        # SESSION auth secret. See EchomailNetwork's own model comment.
        self.packet_password = packet_password
        self.default_crash = default_crash
        self.default_hold = default_hold
        self.default_direct = default_direct
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
        # Set for real at the top of poll(); placeholders here so
        # _consume_inbound_file_frame() has somewhere valid to write to
        # even if ever called outside a poll() (e.g. directly in a test).
        self._inbound_dir = None
        # Messages parsed from a file the peer interleaves while we're
        # waiting for our OWN outbound ack (_wait_got/_send_messages),
        # as opposed to during our own dedicated _receive_messages
        # phase -- folded into poll()'s 'received' result at the end.
        # See _consume_inbound_file_frame's docstring for why this can
        # legitimately happen with a spec-compliant peer.
        self._interleaved_received = []
        # Count of CMD_EOB frames the peer sent (and we recognized) during
        # that same early window, before _receive_messages() ever ran its
        # own dedicated EOB-count loop -- see _consume_inbound_file_frame.
        self._interleaved_eob_count = 0

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
        import os as _os
        result = {'received': [], 'sent': 0, 'hatched_ids': []}
        # Persistent inbound path — the prior /tmp default was tmpfs on
        # most distros so anything stashed vanished on restart. Computed
        # once here since _wait_got()/_send_messages() may now also need
        # to stash an interleaved file, not just _receive_messages().
        self._inbound_dir = _os.environ.get('BINKP_INBOUND_DIR') or _os.path.join(
            (data_dir or 'data'), 'binkp', 'inbound')
        self._interleaved_received = []
        self._interleaved_eob_count = 0
        self._connect()
        try:
            self._handshake()
            if outbound_messages:
                result['sent'] = self._send_messages(outbound_messages, data_dir)
            if hatch_items:
                result['hatched_ids'] = self._send_hatch(hatch_items)
            result['received'] = self._receive_messages(data_dir)
            if self._interleaved_received:
                result['received'].extend(self._interleaved_received)
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

    def _wait_got(self, timeout_sec: float = None) -> bool:
        """Read frames until we see M_GOT (success) or M_SKIP/M_ERR (fail).

        Any CMD_FILE the peer interleaves while we wait is received and
        GOT-acknowledged inline via _consume_inbound_file_frame instead
        of being silently discarded -- a spec-compliant peer can and
        does offer a file at any point in the session (confirmed
        against Synchronet's own binkp.js reference implementation,
        which handles M_FILE/M_GOT/M_SKIP/M_EOB together in one
        unified read loop rather than separate send-then-receive
        phases). Discarding such a frame used to permanently lose that
        file with no diagnostic at all.

        Bounded by wall-clock time (default: self.timeout, 60s unless
        the client was constructed with a different value),
        NOT frame count -- mirrors the same fix in _send_messages(),
        which found this exact fragility live against a real SBBSecho
        hub: a peer that talks a lot (many small DATA frames of its
        own) before finally sending our GOT could exhaust a fixed
        frame-count budget with our GOT never reached, even though the
        peer fully received and processed what we sent.
        """
        state = {'pending_file': None, 'pending_size': 0, 'pending_data': b''}
        deadline = time.monotonic() + (timeout_sec or self.timeout)
        while time.monotonic() < deadline:
            is_cmd, data = self._recv_frame_logged()
            if is_cmd and data:
                cmd = data[0]
                if cmd == CMD_GOT:
                    return True
                if cmd in (CMD_SKIP, CMD_ERR):
                    return False
            self._consume_inbound_file_frame(is_cmd, data, state)
        return False

    # ------------------------------------------------------------------
    # Inbound-file dispatch -- shared by _receive_messages' own read
    # loop AND the ack-wait loops above/below (_wait_got, the tail of
    # _send_messages), since a peer may interleave a file offer at any
    # point in the session, not just during our dedicated receive phase.
    # ------------------------------------------------------------------

    def _is_fts_packet(self, buf):
        return len(buf) >= 60 and buf[18:20] in (b'\x02\x00', b'\x02\x01')

    def _is_zip(self, buf):
        return len(buf) >= 4 and buf[:4] == b'PK\x03\x04'

    def _debug_dump_packet(self, name, data):
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
        import os as _os
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

    def _debug_manifest(self, fname, buf):
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
        import os as _os
        debug_dir = _os.environ.get('BINKP_DEBUG_DUMP_DIR')
        if not debug_dir:
            return
        try:
            _os.makedirs(debug_dir, exist_ok=True)
            kind = 'fts' if self._is_fts_packet(buf) else (
                'zip' if self._is_zip(buf) else 'other')
            line = f'{datetime.utcnow().isoformat()} {fname} {len(buf)} {kind}\n'
            with open(_os.path.join(debug_dir, '_manifest.log'), 'a') as fh:
                fh.write(line)
        except OSError:
            pass

    def _import_completed(self, fname, buf, inbound_dir):
        """Dispatch a fully-received file. Returns a list of parsed
        messages (may be empty for non-mail content like TICs)."""
        import io as _io
        import os as _os
        import zipfile as _zipfile

        self._debug_manifest(fname, buf)
        if self._is_fts_packet(buf):
            self._debug_dump_packet(fname, buf)
            try:
                return _parse_ftn_packet(buf)
            except Exception:
                logger.exception('BinkP: failed parsing %s as FTS-0001',
                                 fname)
                return []
        if self._is_zip(buf):
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
                        if self._is_fts_packet(inner):
                            self._debug_dump_packet(
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
            safe_name = _sanitize_inbound_filename(fname)
            _os.makedirs(inbound_dir, exist_ok=True)
            with open(_os.path.join(inbound_dir, safe_name), 'wb') as fh:
                fh.write(buf)
            logger.info(
                'BinkP: stored unrecognised file %s (%d bytes) in %s '
                '— neither ZIP nor FTS-0001 packet, scanning for TIC',
                safe_name, len(buf), inbound_dir)
        except OSError as exc:
            logger.warning('BinkP: could not stash %s in %s: %s',
                           fname, inbound_dir, exc)
        return []

    def _consume_inbound_file_frame(self, is_cmd, data, state):
        """Feed one already-read frame into an in-progress inbound-file
        receive, mutating `state` (a dict with pending_file/pending_size/
        pending_data keys) in place. Uses self._inbound_dir and appends
        any parsed messages to self._interleaved_received (both set at
        the top of poll()). Returns True if this frame was consumed as
        part of a file transfer (a CMD_FILE header, or a DATA frame for
        a file already in progress), False if the caller should handle
        the frame itself (e.g. a command the caller is specifically
        waiting for, like GOT/SKIP/ERR/EOB).
        """
        if is_cmd:
            if not data:
                return False
            cmd = data[0]
            if cmd == CMD_FILE:
                text = data[1:].decode('latin-1', errors='replace')
                parts = text.split()
                state['pending_file'] = parts[0] if parts else 'unknown.pkt'
                try:
                    state['pending_size'] = int(parts[1]) if len(parts) > 1 else 0
                except ValueError:
                    state['pending_size'] = 0
                # binkd's tfile_cmp() (prothlp.c, confirmed live against
                # its actual source) requires an EXACT match on name,
                # size, AND this mtime before it recognizes our M_GOT as
                # acknowledging the file it sent -- remove_from_spool()
                # is only ever reached if that comparison returns 0. This
                # field was parsed here but never stored, forcing the
                # M_GOT send below to hard-code 0 -- which never equals a
                # real mtime (e.g. 1784314217), so binkd's match ALWAYS
                # failed and it never removed the file from its outbound
                # spool, regardless of anything else in the session. The
                # actual root cause of a real hub (binkd/1.1a-113)
                # resending its entire backlog every poll for months.
                state['pending_mtime'] = parts[2] if len(parts) > 2 else '0'
                state['pending_data'] = b''
                logger.debug(
                    "BinkP: receiving file %s (%d bytes expected)",
                    state['pending_file'], state['pending_size'])
                return True
            if cmd == CMD_EOB:
                # A spec-compliant peer sends its own M_EOB the instant its
                # outbound queue empties (FSP-1011 Table 5's Transmit
                # Routine: "No more files -> Send M_EOB", unconditional --
                # not gated on waiting for our GOT, or on us having sent
                # our own EOB yet). Our session is phase-separated (send
                # everything, then _receive_messages() sends our EOB and
                # waits for theirs), so a peer's EOB can arrive while
                # _send_messages()/_wait_got() are still blocking on our
                # own GOT/SKIP/ERR -- this frame used to be silently
                # dropped right here (neither caller checked for CMD_EOB,
                # and this method returned False with nothing recorded),
                # so _receive_messages() would then wait forever for an
                # EOB the peer had already sent once and would never
                # repeat unprompted. From the peer's own side, FSP-1011
                # 6.3 requires it to have RECEIVED *our* EOB before its
                # session counts as successfully completed, so it never
                # did, and requeued + resent its entire backlog on every
                # subsequent poll -- despite every file being correctly
                # GOT-acknowledged. Root-caused against a real SouthEast
                # Star (binkd 1.1a-113) transcript. self._interleaved_eob_count
                # (reset each poll() call) lets _receive_messages() credit
                # this instead of waiting on it again.
                self._interleaved_eob_count += 1
                logger.info(
                    "BinkP: end of batch from hub, seen early during send "
                    "phase (interleaved count now %d)",
                    self._interleaved_eob_count)
                return True
            return False

        # Data frame.
        if state.get('pending_file') is None:
            return False
        state['pending_data'] += data
        if state['pending_size'] > 0 and len(state['pending_data']) >= state['pending_size']:
            fname = state['pending_file']
            buf = state['pending_data'][:state['pending_size']]
            inbound_dir = self._inbound_dir or 'data/binkp/inbound'
            msgs = self._import_completed(fname, buf, inbound_dir)
            if msgs:
                self._interleaved_received.extend(msgs)
                logger.info("BinkP: imported %d msg(s) from %s", len(msgs), fname)
            try:
                # Real bug found live (a peer sysop's poll log): a bare
                # "GOT: filename" is missing the size and timestamp
                # fields FTS-1026 requires (M_GOT filename size time,
                # mirroring M_FILE's own fields) -- binkd tolerated it
                # for the first couple of files in the batch but then
                # replied "ERR: M_GOT: cannot parse args" and hung up.
                # binkp_server.py (the inbound listener) already sends
                # the correct 3-field form; this path (file receipt
                # during a poll THIS side initiated) never got the same
                # fix. The timestamp used to be a literal "0" placeholder
                # here (and in binkp_server.py) -- believed at the time
                # to match a "proven-working" format, but a real mtime
                # (e.g. 1784314217) never equals 0, so binkd's own
                # tfile_cmp() match against its sent-file record always
                # failed silently and it never dequeued the file. Now
                # echoes the real mtime parsed from the M_FILE header.
                self._send_cmd(CMD_GOT,
                              f'{fname} {state["pending_size"]} '
                              f'{state.get("pending_mtime", "0")}')
            except OSError as exc:
                logger.info("BinkP: hub closed before GOT ack (clean): %s", exc)
            state['pending_file'] = None
            state['pending_size'] = 0
            state['pending_data'] = b''
            state['pending_mtime'] = '0'
        return True

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

    def _verify_remote_address(self, remote_adr_text: str):
        """Cross-check the peer's claimed M_ADR against the address we
        actually dialed (self.hub_address).

        Known gap found in a full BinkP-subsystem audit: real binkd
        (protocol.c's ADR() handler) matches an incoming M_ADR's tokens
        against its own configured link table as part of identifying
        which link this session belongs to -- if nothing matches, the
        peer is unknown to it. ANetBBS's outbound client previously
        accepted whatever address the peer claimed with no cross-check
        at all: a wrong host answering on the expected IP/port (stale
        DNS, a misconfigured hub, a MITM) would sail through unnoticed
        as long as it also somehow knew our password.

        This is defense-in-depth, NOT the primary security gate -- the
        password remains the real barrier (a wrong host won't have
        it). Deliberately WARNS rather than aborting the session: a
        legitimate multi-AKA hub may advertise several addresses that
        don't textually match our configured hub_address (e.g. it
        lists its OTHER zones/AKAs first), and hard-aborting on that
        false positive would turn a working link into a broken one --
        a worse outcome than a logged warning for a sysop to review.
        """
        if not self.hub_address:
            return
        expected = self.hub_address.strip().lower()
        expected_bare = expected.split('@', 1)[0]
        remote_tokens = [t.strip().lower() for t in remote_adr_text.split() if t.strip()]
        for tok in remote_tokens:
            bare = tok.split('@', 1)[0]
            if tok in (expected, expected_bare) or bare in (expected, expected_bare):
                return
        if remote_tokens:
            logger.warning(
                "BinkP: hub at %s:%s claims address(es) %r, none of "
                "which match our configured hub_address %r -- possible "
                "misconfiguration, stale DNS, or wrong host. Continuing "
                "since the session password remains the primary auth "
                "check, but a sysop should verify this link.",
                self.host, self.port, remote_adr_text.strip(), self.hub_address)

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
                self._verify_remote_address(text)
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
        """Package and send outbound messages as a .pkt file.

        Returns the number of messages successfully sent -- 0 or
        len(messages), since the whole batch is bundled into a single
        packet and accepted/rejected as a unit (BinkP has no partial
        ack at the individual-message level). Callers MUST NOT mark
        messages as sent/delivered unless this returns nonzero.

        Confirmed real bug (found in a full-subsystem audit, not
        hypothetical): this used to return len(messages) unconditionally
        regardless of whether the loop below ever saw GOT, SKIP, ERR, or
        simply ran out of frames -- and poller.py's caller stamped every
        queued message as sent based on that count with no further
        check, so a busy/unstable hub replying M_SKIP (a normal, spec-
        legal response, e.g. "already have this bundle") or M_ERR
        silently and permanently discarded real outbound mail with no
        retry and no visible error (the poll log still read "success").
        Matches Synchronet's own binkp.js reference implementation,
        which only fires its tx_callback (the hook that marks a file
        truly delivered) on M_GOT for that specific file, and moves it
        to a separate failed-files list on M_SKIP instead of assuming
        success.
        """
        if not messages:
            return 0
        pkt_data = _build_ftn_packet(
            messages, self.our_address, self.hub_address,
            packet_password=self.packet_password,
            default_crash=self.default_crash,
            default_hold=self.default_hold,
            default_direct=self.default_direct)
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

        # Wait for GOT (accepted) or SKIP/ERR (rejected). Any CMD_FILE
        # the peer interleaves while we wait is received and GOT-acked
        # inline rather than silently discarded -- see
        # _consume_inbound_file_frame's docstring.
        #
        # Bounded by wall-clock time, NOT frame count -- real bug found
        # live (a sysop's own AreaFix request to a real SBBSecho hub):
        # the old `for _ in range(20)` limit meant a peer that responds
        # with substantial content of its own (SBBSecho replying with
        # "Area Management Request" + "List of Available Areas", each
        # potentially spanning many small DATA frames) BEFORE finally
        # sending our GOT could exhaust all 20 frames on the peer's OWN
        # reply alone, with our GOT never even reached. _send_messages
        # then correctly (by its own logic) treated the packet as
        # unacknowledged and left the netmail queued for retry -- but
        # since the peer HAD already fully received and processed it,
        # the very next poll sent the exact same request again, and the
        # peer replied again, forever: the sysop saw the same two
        # SBBSecho messages arrive over and over, once per poll,
        # indefinitely. A frame-count limit is inherently fragile
        # against any peer that talks a lot before acking; a time
        # deadline isn't.
        state = {'pending_file': None, 'pending_size': 0, 'pending_data': b''}
        accepted = False
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            try:
                is_cmd, data = self._recv_frame_logged()
            except (ConnectionError, OSError) as exc:
                # Real gap found in the same audit that produced this
                # method: _receive_messages() already treated the peer
                # closing mid-wait as a graceful (if unwelcome) end and
                # returned cleanly, but this ack-wait loop had no
                # equivalent guard -- a peer that processed our packet
                # fine but hung up without an explicit GOT (or a NAT/
                # firewall idle-timeout severing the session at exactly
                # this point) would raise straight out of poll() as an
                # unhandled exception instead of the safe default
                # (treat as unacknowledged, retry next poll).
                logger.warning(
                    "BinkP: connection closed while awaiting ack for %s "
                    "-- %d message(s) NOT marked sent, will retry next "
                    "poll: %s", filename, len(messages), exc)
                break
            if is_cmd and data:
                cmd = data[0]
                text = data[1:].decode('latin-1', errors='replace')
                if cmd == CMD_GOT:
                    logger.info("BinkP: hub acknowledged %s", filename)
                    accepted = True
                    break
                elif cmd == CMD_SKIP:
                    logger.warning(
                        "BinkP: hub skipped %s -- %d message(s) NOT marked "
                        "sent, will retry next poll", filename, len(messages))
                    break
                elif cmd == CMD_ERR:
                    logger.error(
                        "BinkP: error sending %s: %s -- %d message(s) NOT "
                        "marked sent, will retry next poll",
                        filename, text, len(messages))
                    break
            self._consume_inbound_file_frame(is_cmd, data, state)
        else:
            logger.warning(
                "BinkP: no GOT/SKIP/ERR for %s within %ds -- "
                "treating as failed, %d message(s) NOT marked sent, "
                "will retry next poll", filename, self.timeout,
                len(messages))

        return len(messages) if accepted else 0

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
        parsed = []
        sent_eob = 1
        # A peer's own EOB may have already arrived -- and been recorded,
        # not dropped, see _consume_inbound_file_frame -- while we were
        # still in our own send phase, before this method ever ran. Credit
        # it here instead of waiting for a repeat that a spec-compliant
        # peer has no reason to ever send unprompted.
        got_eob = self._interleaved_eob_count
        self._send_cmd(CMD_EOB)
        if got_eob >= 1:
            logger.info(
                "BinkP: crediting %d EOB(s) the hub already sent during "
                "our send phase (%d/2)", got_eob, got_eob)
            if sent_eob < 2:
                try:
                    self._send_cmd(CMD_EOB)
                    sent_eob += 1
                except OSError as exc:
                    logger.info(
                        "BinkP: hub closed before second EOB (clean): %s",
                        exc)
                    # Can't send our second round, but the peer already
                    # sent its one EOB and is gone -- nothing further to
                    # wait for.
                    got_eob = 2

        # File-receive dispatch (CMD_FILE header + DATA frames + the
        # GOT-per-file ack) is handled by _consume_inbound_file_frame,
        # shared with the ack-wait loops in _wait_got/_send_messages --
        # see that method's docstring. This loop only needs to handle
        # the frame types those callers AREN'T waiting for: EOB and ERR.
        state = {'pending_file': None, 'pending_size': 0, 'pending_data': b''}

        # If both EOB rounds were already satisfied above (the peer's
        # round arrived early and we couldn't complete our own second
        # round because it had already hung up), there's nothing left to
        # wait for -- skip straight to the TIC scan below.
        if got_eob < 2:
            # Shrink the socket timeout from self.timeout (60s default)
            # for this receive phase specifically. Real live measurement
            # against a real hub (binkd/1.1a-113) showed the TCP
            # connection itself dying ~15s after its last file,
            # consistently, on every session -- far short of our own
            # 60s configured timeout, meaning something OUTSIDE our code
            # (the network path, not the hub's application logic) was
            # silently killing the idle connection well before we ever
            # tried to speak again. Our own confirmatory M_EOB below was
            # therefore always being sent into an already-dead
            # connection, however successful the local socket write
            # looked. Waiting less than that ~15s window before
            # proactively responding gives our confirmation a chance to
            # actually reach the hub while the link is still alive.
            try:
                self._sock.settimeout(5.0)
            except (OSError, AttributeError):
                pass
            for _ in range(5000):
                try:
                    is_cmd, data = self._recv_frame_logged()
                except (ConnectionError, OSError) as exc:
                    logger.info(
                        "BinkP: hub closed after our M_EOB (clean): %s", exc)
                    got_eob = 2
                    break

                if is_cmd and data:
                    cmd = data[0]
                    text = data[1:].decode('latin-1', errors='replace')

                    if cmd == CMD_EOB:
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
                                got_eob = 2
                                break
                        if got_eob >= 2:
                            break
                        continue

                    if cmd == CMD_ERR:
                        logger.error("BinkP: session error: %s", text)
                        got_eob = 2
                        break

                    if cmd == CMD_NUL:
                        continue  # info frame

                # CMD_FILE header, a DATA frame for an in-progress file, or
                # any other frame we don't specifically act on above.
                before = self._interleaved_received
                self._interleaved_received = parsed
                try:
                    self._consume_inbound_file_frame(is_cmd, data, state)
                finally:
                    self._interleaved_received = before
            # else (5000 frames exhausted without reaching got_eob >= 2):
            # give up rather than waiting forever -- falls through below.

        # Per binkp/1.1 (binkp11.txt): a session only counts as
        # successfully finished once a round passes where NEITHER side
        # sends nor receives any command between two consecutive M_EOB
        # exchanges. Our first M_EOB (sent unconditionally above, before
        # any files were exchanged) gets voided the instant real file
        # activity follows it, so it can never by itself satisfy that --
        # only a SECOND, POST-transfer M_EOB, sent proactively, can. This
        # loop only ever sent that second EOB REACTIVELY, in reply to the
        # hub sending its own -- never proactively. Real live report: a
        # real hub (binkd/1.1a-113) never sends an explicit M_EOB back at
        # all in these sessions, just goes silent for its own ~15s grace
        # period then disconnects, and its own outbound queue never
        # dequeued any of these files on the next poll despite every one
        # being individually M_GOT-acknowledged here -- it was left
        # waiting on a confirmation we never sent. Mirrors the identical
        # fix in binkp_server.py's _finish_session() (the answering
        # side); this is the same gap on the dialing-out side.
        if sent_eob < 2:
            try:
                self._send_cmd(CMD_EOB)
                sent_eob += 1
                logger.info("BinkP: proactive post-transfer M_EOB sent OK")
            except OSError as exc:
                # _send_cmd() logs the transcript line BEFORE attempting
                # the actual socket write -- if the hub already closed
                # the connection by the time we get here, the transcript
                # can show ">> CMD EOB" while the write itself silently
                # failed, making a failed send indistinguishable from a
                # successful one without this log line (real live
                # report: the hub's own outbound queue never shrank even
                # across sessions whose transcript showed this EOB being
                # sent).
                logger.warning(
                    "BinkP: proactive post-transfer M_EOB send FAILED "
                    "(hub likely already closed): %s", exc)

        # Run TIC scan on anything we stashed during this batch.
        import os as _os
        inbound_dir = self._inbound_dir or _os.path.join(
            (data_dir or 'data'), 'binkp', 'inbound')
        try:
            if _os.path.isdir(inbound_dir):
                from .tic import scan_inbound
                n = scan_inbound(inbound_dir)
                if n:
                    logger.info('BinkP: TIC scanner processed %d files', n)
        except Exception:
            logger.exception('BinkP: TIC scan after receive failed')

        return parsed
