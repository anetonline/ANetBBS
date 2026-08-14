# anetbbs/echomail/qwk_hub_ftp.py
"""FTP-side QWK hub helpers.

When a QWK node logs into ANetBBS's FTP server their home directory is
`<DATA_DIR>/qwk-hub/<PACKET_ID>/`.  This module handles two operations:

  generate_node_packet(node, data_dir, hub_id)
      Build ANET.qwk (or <hub_id>.qwk) for the node and write it to their
      home dir.  Called from AnetbbsFTPHandler.on_login so the packet is
      fresh each session.

  process_rep_upload(node_id, rep_path, app)
      Parse an uploaded <PACKET_ID>.rep file, import the messages, and fan
      them out to BinkP downstream nodes via the tosser.  Called from
      AnetbbsFTPHandler.on_file_received.

  ensure_node_dir(packet_id, data_dir) -> str
      Return (and create) the per-node directory.
"""
import logging
import os
import zipfile
import io

logger = logging.getLogger(__name__)

_HUB_ID = 'ANET'


def resolve_hub_id(node=None):
    """Resolve the QWK system ID (e.g. 'ANET') a node's packets should
    use -- the filename of the <HUBID>.QWK the node downloads and the
    <HUBID>.MSG a valid REP upload must contain.

    Collapses what used to be three independently-duplicated resolvers
    (anetbbs/web/qwk_hub.py's own _hub_id(), this module's _HUB_ID
    default arg, and anetbbs/ftp/server.py's on_login) into one shared
    function, now identity-aware: a node belonging to a non-default
    HubIdentity uses THAT identity's own qwk_hub_id, so two hub
    networks on the same install never collide on the same <HUBID>.QWK
    filename. A node on the default identity -- or no node at all
    (e.g. building a generic/preview packet) -- falls back to the
    legacy QWK_HUB_ID env var / BBS_NAME derivation exactly as before,
    so every existing single-hub install needs zero config changes.
    """
    identity = getattr(node, 'hub_identity', None) if node is not None else None
    if identity is not None and not identity.is_default and identity.qwk_hub_id:
        return identity.qwk_hub_id.strip().upper()

    val = os.environ.get('QWK_HUB_ID', '').strip().upper()
    if val:
        return val
    if identity is not None and identity.qwk_hub_id:
        return identity.qwk_hub_id.strip().upper()
    bbs = os.environ.get('BBS_NAME', 'ANET')
    import re as _re
    return _re.sub(r'[^A-Z0-9]', '', bbs.upper())[:8] or 'ANET'


def ensure_node_dir(packet_id: str, data_dir: str) -> str:
    path = os.path.join(data_dir, 'qwk-hub', packet_id.upper())
    os.makedirs(path, exist_ok=True)
    return path


def generate_node_packet(node, data_dir: str, hub_id: str = _HUB_ID) -> str:
    """Build <hub_id>.qwk for *node* and write it to their home dir.

    Returns the path to the written file, or '' on failure.
    """
    from ..models import db, QWKNodeLastSent, EchoArea, EchomailMessage

    node_dir = ensure_node_dir(node.packet_id, data_dir)
    out_path = os.path.join(node_dir, f'{hub_id.upper()}.qwk')

    try:
        # Gather subscribed areas + their HWMs.
        subs = (QWKNodeLastSent.query
                .filter_by(node_id=node.id)
                .all())
        if not subs:
            logger.info('QWK FTP: node %s has no subscriptions — empty packet',
                        node.packet_id)
            _write_empty_packet(out_path, hub_id, node.packet_id)
            return out_path

        # Build conference list and collect messages.
        conferences = {}     # conf_num → area
        messages_by_conf = {}
        # area_id → highest message id sent this packet -- committed
        # below AFTER the packet is written successfully. Real gap
        # found in audit: this function is called on every FTP login
        # (see module docstring) but nothing ever advanced
        # QWKNodeLastSent.last_message_id on this path, unlike the
        # already-correct HTTP hub path (web/qwk_hub.py's
        # mark_qwk_sent()) -- every login re-sent the identical up-to-
        # 500-message batch per conference forever.
        new_hwm = {}
        for sub in subs:
            area = EchoArea.query.get(sub.echo_area_id)
            if area is None or not area.is_active:
                continue
            conf_num = sub.conf_number
            conferences[conf_num] = area
            since_id = sub.last_message_id or 0
            msgs = (EchomailMessage.query
                    .filter_by(area_id=area.id)
                    .filter(EchomailMessage.id > since_id)
                    .order_by(EchomailMessage.id)
                    .limit(500)
                    .all())
            if msgs:
                messages_by_conf[conf_num] = msgs
                new_hwm[area.id] = msgs[-1].id

        _write_qwk_packet(out_path, hub_id, node.packet_id,
                          conferences, messages_by_conf)

        for sub in subs:
            new_id = new_hwm.get(sub.echo_area_id)
            if new_id is not None:
                sub.last_message_id = new_id
        db.session.commit()

        logger.info('QWK FTP: wrote %s for node %s (%d confs, hwm advanced for %d)',
                    out_path, node.packet_id, len(conferences), len(new_hwm))
        return out_path

    except Exception:
        logger.exception('QWK FTP: failed to generate packet for %s',
                         node.packet_id)
        return ''


def _write_empty_packet(path: str, hub_id: str, packet_id: str = ''):
    """Write a minimal valid QWK packet with no messages."""
    control = _build_control_dat(hub_id, {}, packet_id)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('CONTROL.DAT', control)
        zf.writestr('MESSAGES.DAT', b'\x1a')
    with open(path, 'wb') as f:
        f.write(buf.getvalue())


def _write_qwk_packet(path: str, hub_id: str, packet_id: str,
                      conferences: dict, messages_by_conf: dict):
    """Write a complete QWK packet zip to *path*."""
    control = _build_control_dat(hub_id, conferences, packet_id)
    messages_dat = _build_messages_dat(messages_by_conf, packet_id)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('CONTROL.DAT', control)
        zf.writestr('MESSAGES.DAT', messages_dat)
    with open(path, 'wb') as f:
        f.write(buf.getvalue())


def _build_control_dat(hub_id: str, conferences: dict, packet_id: str = '') -> str:
    """Build CONTROL.DAT content for the packet.

    Line layout verified against Synchronet's own writer (pack_qwk.cpp)
    and matches anetbbs/web/qwk_hub.py's already-fixed writer exactly:
    a strict positional reader (this codebase's own
    qwk.py:_parse_control_dat, plus real readers like Blue Wave/
    EZReader/Mystic) expects the conference count at line index 10 and
    a mandatory "0"/"E-mail" pair for the reserved conference-0
    (netmail) slot immediately before any real conferences. The
    previous version here only emitted 10 header lines total (count at
    index 9) and no conf-0 pair at all -- every conference in the
    packet parsed back as an empty dict, silently dropping all mail.
    This was already fixed once in qwk_hub.py and once in qwk_user.py;
    this was the third, independent writer that never got the fix.
    """
    lines = [
        hub_id,                    # BBS name
        'Internet',                # City
        '000-000-0000',            # Phone
        'SysOp',                   # SysOp
        f'{hub_id},0',             # Serial,max_msgs
        '00-00-00,00:00:00',       # Download date/time
        packet_id,                 # logged-in user (alias)
        '',                        # blank (placeholder)
        '0',                       # placeholder
        '0',                       # placeholder
        str(len(conferences)),     # number of conferences
        '0',                       # reserved conference 0 (netmail) number
        'E-mail',                  # reserved conference 0 name
    ]
    # Conference block: number then name, alternating
    for conf_num, area in sorted(conferences.items()):
        lines.append(str(conf_num))
        lines.append(area.name)
    lines.append('')
    return '\r\n'.join(lines)


def _build_messages_dat(messages_by_conf: dict, packet_id: str) -> bytes:
    """Build MESSAGES.DAT block content."""
    import struct
    from datetime import datetime

    BLOCK = 128
    out = bytearray()

    # Header block.
    hdr = bytearray(BLOCK)
    hdr[0:6] = b'Welcom'
    out += bytes(hdr)

    msg_counter = 0
    for conf_num, msgs in sorted(messages_by_conf.items()):
        for msg in msgs:
            msg_counter += 1
            from_name = (msg.from_name or 'SysOp')[:25]
            to_name   = (msg.to_name or 'All')[:25]
            subject   = (msg.subject or '')[:25]
            when      = msg.created_at or datetime.utcnow()
            date_str  = when.strftime('%m-%d-%y')
            time_str  = when.strftime('%H:%M')

            # Real gap found in a full echomail-subsystem audit: reply-
            # threading was never propagated into outbound QWK bodies on
            # this path -- see web/qwk_hub.py's _build_qwk_hub_packet for
            # the full rationale (mirrors qwk.py's already-working
            # @MSGID: convention rather than the packet-local-only
            # binary reference-number header field).
            body_raw = msg.body or ''
            if getattr(msg, 'reply_id', None) and '@REPLY:' not in body_raw:
                body_raw = f'@REPLY: {msg.reply_id}\n' + body_raw

            # Encode to CP437 BYTES first, then do the newline -> 0xE3
            # paragraph-separator substitution at the byte level, not on
            # the Python str. The Python string literal '\xe3' is the
            # Unicode codepoint U+00E3 ('ã'), which CP437 cannot represent
            # (no such character in that code page) -- doing the replace
            # on the str before encoding meant every embedded newline
            # silently became a literal '?' (errors='replace''s fallback)
            # instead of the real QWK paragraph-separator byte, corrupting
            # every multi-line outbound QWK message. \r and \n are plain
            # ASCII and survive CP437 encoding unchanged, so replacing
            # them with the raw byte b'\xe3' after encoding is safe and
            # unambiguous.
            body_bytes = body_raw.encode('cp437', errors='replace')
            body_bytes = body_bytes.replace(b'\r\n', b'\xe3').replace(b'\n', b'\xe3') + b'\xe3'

            # Number of 128-byte blocks needed for header + body.
            total_blocks = 1 + (len(body_bytes) + BLOCK - 1) // BLOCK

            # QWK message header layout (128 bytes) -- this must match
            # what QWKClient._parse_messages_dat() (anetbbs/echomail/qwk.py)
            # expects on the reading side, which is the real, standard QWK
            # format (verified against real Dove-Net packets). A previous
            # version of this writer had every field from byte 113 onward
            # at the wrong offset (off by several bytes) and used binary
            # encoding for num_chunks where the format requires 6-char
            # ASCII text -- every message silently failed to parse as a
            # result (conf_num read as garbage, dropped as "not advertised
            # in CONTROL.DAT"), live-caught testing the real ANotherNetwork
            # QWK flow end-to-end (0 messages ever received, no error).
            #   byte    0      : status flag -- printable ASCII, not the
            #                    0xE1 marker (that belongs at byte 122
            #                    only): ' '=public/unread, '-'=public/read,
            #                    '+'=private/unread, '*'=private/read.
            #                    Verified against Synchronet's own reader
            #                    (qwktomsg.cpp), which checks byte 0
            #                    against exactly those characters -- 0xE1
            #                    here isn't a valid status value at all.
            #                    ANetBBS's own reader is lenient (treats
            #                    anything but '*'/'+' as public), which is
            #                    why this went unnoticed talking to itself.
            #   bytes   1:8    : message number (7-char ASCII)
            #   bytes   8:21   : date+time (13-char ASCII, "MM-DD-YYHH:MM")
            #   bytes  21:46   : to (25 chars)
            #   bytes  46:71   : from (25 chars)
            #   bytes  71:96   : subject (25 chars)
            #   bytes  96:108  : password (12 chars, unused)
            #   bytes 108:116  : reference message number (8-char ASCII)
            #   bytes 116:122  : number of 128-byte blocks (6-char ASCII!)
            #   byte   122     : active flag (0xE1=active, 0xE2=killed)
            #   bytes 123:125  : conference number (binary, little-endian uint16)
            header = bytearray(BLOCK)
            header[0]      = ord(' ')    # public, unread
            header[1:8]    = f'{msg_counter:07d}'.encode()[:7]
            header[8:21]   = (date_str + time_str).encode()[:13].ljust(13)
            header[21:46]  = to_name.encode('cp437', errors='replace')[:25].ljust(25)
            header[46:71]  = from_name.encode('cp437', errors='replace')[:25].ljust(25)
            header[71:96]  = subject.encode('cp437', errors='replace')[:25].ljust(25)
            header[96:108] = b' ' * 12   # password, unused
            header[108:116] = b' ' * 8   # reference message number -- no reply-threading yet
            header[116:122] = f'{total_blocks:<6d}'.encode()[:6]
            header[122]    = 0xe1        # active
            struct.pack_into('<H', header, 123, conf_num)
            out += bytes(header)

            # Body blocks (padded with spaces).
            padded = body_bytes.ljust(
                ((len(body_bytes) + BLOCK - 1) // BLOCK) * BLOCK, b' ')
            out += padded

    # EOF marker.
    out += bytes([0x1a])
    return bytes(out)


def process_rep_upload(node_id: int, rep_path: str, app) -> int:
    """Parse a .rep file and import messages into the DB, fan out via tosser.

    Returns count of messages imported.
    """
    from ..echomail.qwk import _parse_messages_dat, _parse_control_dat
    from ..echomail.tosser import toss_message

    count = 0
    try:
        with open(rep_path, 'rb') as f:
            rep_bytes = f.read()
    except OSError as exc:
        logger.warning('QWK FTP REP read error: %s', exc)
        return 0

    with app.app_context():
        from ..models import (db, QWKNode, QWKNodeLastSent, EchoArea,
                              EchomailMessage, maybe_tag_ansi_subject)

        node = QWKNode.query.get(node_id)
        if node is None:
            return 0

        try:
            buf = io.BytesIO(rep_bytes)
            with zipfile.ZipFile(buf, 'r') as zf:
                names = zf.namelist()
                # Find <hub_id>.MSG or <hub_id>.msg
                msg_file = next(
                    (n for n in names
                     if n.upper().endswith('.MSG')),
                    None)
                ctrl_file = next(
                    (n for n in names
                     if n.upper() == 'CONTROL.DAT'),
                    None)

                if msg_file is None:
                    logger.warning('QWK FTP REP: no .MSG file in %s', rep_path)
                    return 0

                # Real gap found in a security/performance audit: reading
                # a ZIP member's decompressed bytes with no check on its
                # declared uncompressed size lets a small, highly-
                # compressed archive ("zip bomb") expand to gigabytes in
                # memory. Checked BEFORE zf.read() -- ZipInfo.file_size
                # is free to read (from the local file header).
                from .zip_safety import MAX_MEMBER_UNCOMPRESSED
                for _name in filter(None, (msg_file, ctrl_file)):
                    if zf.getinfo(_name).file_size > MAX_MEMBER_UNCOMPRESSED:
                        logger.warning(
                            'QWK FTP REP: %s in %s declares an oversized '
                            'uncompressed size -- refusing to extract',
                            _name, rep_path)
                        return 0

                msg_data = zf.read(msg_file)
                ctrl_data = zf.read(ctrl_file).decode('cp437', errors='replace') \
                    if ctrl_file else ''

            ctrl = _parse_control_dat(ctrl_data) if ctrl_data else {}
            parsed = _parse_messages_dat(msg_data, ctrl.get('conferences', {}))

        except Exception:
            logger.exception('QWK FTP REP: parse error for node %s', node.packet_id)
            return 0

        new_ids = []
        notify_pairs = []  # (EchomailMessage, EchoArea) for post-commit reply-notify
        for msg_dict in parsed:
            try:
                # _parse_messages_dat() (qwk.py) returns dicts keyed
                # 'conf_num'/'from_name'/'to_name' -- these three read the
                # wrong keys ('conference'/'from'/'to', which never exist
                # in that dict) and silently fell back to their .get()
                # defaults. conf_num defaulting to 0 meant every REP
                # upload, from every QWK node, always resolved to
                # conference 0 -- which no node ever has a legitimate
                # subscription to -- so every uploaded reply was silently
                # dropped by the `if sub is None: continue` below. This
                # was never Pi3-specific; it broke inbound REP processing
                # entirely, for as long as this function has existed.
                conf_num = msg_dict.get('conf_num', 0)
                sub = (QWKNodeLastSent.query
                       .filter_by(node_id=node.id, conf_number=conf_num)
                       .first())
                if sub is None:
                    continue
                area = EchoArea.query.get(sub.echo_area_id)
                if area is None:
                    continue

                # Real gap found in a full echomail-subsystem audit: this
                # REP importer never deduplicated by msg_id before
                # inserting, unlike the BinkP import paths (poller.py's
                # _import_message(), binkp_server.py's
                # _import_pkt_payload()), which both check for an
                # existing (msg_id, area_id) row first. A node re-
                # uploading the same REP (a retried STOR after a dropped
                # FTP connection, a resubmission after a timed-out ack)
                # duplicated every message in it.
                msg_id = msg_dict.get('msg_id')
                if msg_id and EchomailMessage.query.filter_by(
                        msg_id=msg_id, area_id=area.id).first():
                    continue

                # Real bug found in a full echomail-subsystem audit: a
                # bare db.session.rollback() here rolls back the ENTIRE
                # open transaction, not just this one message -- so any
                # messages already add()+flush()ed earlier in this SAME
                # loop (their ids already captured into new_ids, count
                # already incremented) got silently discarded the moment
                # any LATER message in the same upload hit an exception.
                # The final "imported %d messages" log line then reports
                # a number higher than what's actually in the database,
                # with zero visible error. Matches the exact SAVEPOINT
                # pattern poller.py's _import_message() already uses for
                # the identical problem (a single bad row poisoning a
                # whole batch) -- begin_nested() isolates each message's
                # own insert so a rollback only undoes that one row.
                with db.session.begin_nested():
                    em = EchomailMessage(
                        area_id=area.id,
                        network_id=area.network_id,
                        from_name=msg_dict.get('from_name', '')[:100],
                        to_name=msg_dict.get('to_name', 'All')[:100],
                        subject=maybe_tag_ansi_subject(
                            msg_dict.get('subject', ''),
                            msg_dict.get('body', ''))[:200],
                        body=msg_dict.get('body', ''),
                        msg_id=msg_dict.get('msg_id', ''),
                        reply_id=msg_dict.get('reply_id', ''),
                        origin_line=msg_dict.get('origin_line', ''),
                        tear_line=msg_dict.get('tear_line', ''),
                        from_address=f'QWK:{node.packet_id}',
                    )
                    db.session.add(em)
                    db.session.flush()
                new_ids.append(em.id)
                notify_pairs.append((em, area))
                count += 1
            except Exception:
                logger.exception('QWK FTP REP: error importing message '
                                 '(this message skipped, earlier ones in '
                                 'this batch are unaffected)')

        if new_ids:
            try:
                db.session.commit()
                for mid in new_ids:
                    toss_message(mid)
            except Exception:
                logger.exception('QWK FTP REP: commit/toss error')
                db.session.rollback()
            else:
                # Reply-to-a-real-user notifications -- a hub often has
                # its own local users reading the very same shared areas
                # a downstream node just uploaded replies into, so this
                # path needs the same "so-and-so replied to you" notice
                # poller.py's and binkp_server.py's inbound paths already
                # get (see notify_reply.py). Only runs after a successful
                # commit, same as the toss loop above.
                from ..models import EchomailNetwork
                from .notify_reply import maybe_notify_recipient
                for em_obj, area_obj in notify_pairs:
                    network_obj = EchomailNetwork.query.get(area_obj.network_id)
                    if network_obj is not None:
                        maybe_notify_recipient(em_obj, area_obj, network_obj)

        node.last_poll_at = __import__('datetime').datetime.utcnow()
        try:
            db.session.commit()
        except Exception:
            # Unlike every other exception handler in this function,
            # this one had no log call at all -- a failure here (e.g. a
            # lock timeout under real concurrent QWK/BinkP traffic on
            # SQLite) silently left last_poll_at stale with zero trail.
            logger.exception('QWK FTP REP: failed to update last_poll_at '
                             'for node %s', node.packet_id)
            db.session.rollback()

        # Capture while still inside the app context -- node.packet_id is
        # a lazy-loaded ORM attribute, and the commits above leave it
        # expired (SQLAlchemy's default expire_on_commit). Reading it
        # after the context (and its session) tears down raises
        # DetachedInstanceError. Live-caught: the import and both commits
        # above completed successfully every time, but this trailing log
        # line crashed anyway, which made on_file_received()'s except
        # block log "REP processing failed" for an upload that had
        # actually already succeeded.
        node_label = node.packet_id

    logger.info('QWK FTP REP: imported %d messages from node %s',
                count, node_label)
    return count
