"""
BinkP/1.1 inbound listener for ANetBBS — accepts incoming sessions from
remote echomail nodes that want to deliver mail to us (or pull mail FROM us).

Pairs with the existing outbound binkp.py client. Together they make this BBS
a full peer node, not just a leaf-client.

Listens on 0.0.0.0:24554 by default (configurable via BINKP_LISTEN_PORT env).
Per connection:
  1. Send M_NUL info + our M_ADR
  2. Receive client M_NUL/M_ADR/M_PWD
  3. Look up the remote address in EchomailNetwork (matching hub_address)
  4. Validate password against EchomailNetwork.binkp_password
  5. Send M_OK on success, M_ERR on failure
  6. Receive their .pkt file frames → parse → import as inbound EchomailMessages
  7. Send our queued outbound EchomailMessages for that node, mark sent_at
  8. Exchange M_EOB and close

Usage:
    python -m anetbbs.echomail.binkp_server
"""
import os
import logging
import asyncio
import struct
from datetime import datetime

from .binkp import (
    CMD_NUL, CMD_ADR, CMD_PWD, CMD_FILE, CMD_OK, CMD_EOB,
    CMD_GOT, CMD_ERR, CMD_SKIP,
    _build_cmd, _build_data,
    _build_ftn_packet, _parse_ftn_packet,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Async frame I/O (mirror of the sync helpers in binkp.py)
# ---------------------------------------------------------------------------

async def _recv_frame(reader: asyncio.StreamReader):
    """Read one BinkP frame. Returns (is_command, data_bytes)."""
    hdr = await reader.readexactly(2)
    word = struct.unpack('>H', hdr)[0]
    is_command = bool(word & 0x8000)
    length = word & 0x7FFF
    payload = await reader.readexactly(length) if length else b''
    return is_command, payload


async def _send_cmd(writer, cmd, text=''):
    writer.write(_build_cmd(cmd, text))
    await writer.drain()


async def _send_data(writer, data):
    writer.write(_build_data(data))
    await writer.drain()


# ---------------------------------------------------------------------------
# Per-connection session
# ---------------------------------------------------------------------------

async def _handle_connection(reader, writer, our_address: str, system_name: str):
    peer = writer.get_extra_info('peername')
    logger.info('BinkP inbound from %s', peer)

    # 1. Send our M_NUL info + M_ADR.  Match Synchronet binkit's
    # preamble (SYS / ZYZ / LOC / NDL / TIME / VER) so peers that key
    # routing decisions off these fields recognise us as a real BBS.
    sysop_name = os.environ.get('SYSOP_NAME', 'sysop')
    location = os.environ.get('BBS_LOCATION', 'Earth')
    from .. import __version__ as _anetbbs_version
    full_ver = f'ANetBBS/{_anetbbs_version} binkp/1.1'
    await _send_cmd(writer, CMD_NUL, f'SYS {system_name}')
    await _send_cmd(writer, CMD_NUL, f'ZYZ {sysop_name}')
    await _send_cmd(writer, CMD_NUL, f'LOC {location}')
    await _send_cmd(writer, CMD_NUL, 'NDL 115200,TCP,BINKP')
    await _send_cmd(writer, CMD_NUL, f'TIME {datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S +0000")}')
    await _send_cmd(writer, CMD_NUL, f'VER {full_ver}')

    akas = []
    try:
        from flask import Flask
        from anetbbs.config import get_config
        from anetbbs.models import db, EchomailNetwork
        _app = Flask(__name__)
        _app.config.from_object(
            get_config(os.environ.get('FLASK_ENV', 'production')))
        db.init_app(_app)
        with _app.app_context():
            rows = (EchomailNetwork.query
                    .filter_by(network_type='binkp', is_active=True)
                    .all())
            import re as _re
            for r in rows:
                a = (r.our_address or '').strip()
                if not a:
                    continue
                # Advertise ONE form per address -- qualified
                # (`addr@domain`) when a domain is derivable, bare
                # otherwise. Previously sent BOTH forms for every
                # address as a "cover whichever form the peer keys on"
                # compat measure, but real binkd's ADR() handler calls
                # bsy_add() (its busy-lock acquire) once per
                # space-separated token with no de-duplication -- for a
                # secure (password-protected) link the first token
                # acquires the lock and the second, same address in the
                # other form, immediately fails to acquire it and binkd
                # drops the session with "Secure AKA busy", before
                # password verification ever runs. Confirmed live in
                # both directions: ANetBBS calling out to a real binkd
                # hub, AND a real binkd hub calling in to an ANetBBS
                # install (this code path). A spec-compliant peer
                # parses the domain-qualified form fine on its own, so
                # one form is sufficient. Domain is sanitised per
                # FSP-1028 (a-z 0-9 _ - ~, ≤8 chars).
                raw = (r.name or '').strip().lower()
                domain = _re.sub(r'[^a-z0-9_~-]+', '', raw)[:8]
                one_form = f'{a}@{domain}' if domain else a
                if one_form not in akas:
                    akas.append(one_form)
    except Exception as exc:
        logger.warning('BinkP listener: AKA lookup failed (%s) — using default', exc)

    # Fallback default address, only if its bare form isn't ALREADY
    # covered by an entry from the DB loop above (qualified or bare) --
    # a plain `not in akas` exact-string check missed this, since the
    # DB loop may have added the qualified form ("1:114/30@fidonet")
    # while this bare "1:114/30" is a different string, silently
    # re-introducing the exact same duplicate-token bug this whole fix
    # is for.
    if our_address and not any(
            existing == our_address or existing.split('@', 1)[0] == our_address
            for existing in akas):
        akas.append(our_address)

    # Sort AKAs so the most-FTN-looking ones come first. BinkP/1.1 has
    # ONE M_ADR per side and the argument is a space-separated list of
    # AKAs — but some peers (Mystic, older binkd builds) match only
    # against the FIRST AKA when deciding which outbound queue to
    # flush.  4D/point addresses (`zone:net/node.point`) are the most
    # specific FTN form, so put those first; then bare 3D; then any
    # non-FTN custom addresses last.
    def _aka_rank(a):
        # Lower rank sorts earlier.  Heuristics:
        #   0 = qualified 4D/point address (e.g. "1337:3/207.5@tqwnet")
        #   1 = bare 4D/point
        #   2 = qualified 3D
        #   3 = bare 3D
        #   4 = unparseable / unknown
        if not a or ':' not in a or '/' not in a:
            return (4, a)
        bare, _, _ = a.partition('@')
        has_domain = '@' in a
        zone_str = bare.split(':', 1)[0]
        try:
            int(zone_str)
        except ValueError:
            return (4, a)
        has_point = '.' in bare.split('/', 1)[1] if '/' in bare else False
        if has_point and has_domain: return (0, a)
        if has_point:               return (1, a)
        if has_domain:              return (2, a)
        return (3, a)
    akas.sort(key=_aka_rank)
    aka_line = ' '.join(akas) if akas else our_address
    logger.info('BinkP %s: announcing AKAs %s', peer, aka_line)
    await _send_cmd(writer, CMD_ADR, aka_line)

    # 2. Read client commands until we get their address + password
    remote_addr = None
    remote_akas = []
    remote_pwd = None
    while remote_addr is None or remote_pwd is None:
        try:
            is_cmd, payload = await asyncio.wait_for(_recv_frame(reader), timeout=30)
        except asyncio.TimeoutError:
            logger.warning('BinkP %s: handshake timed out', peer)
            return
        except (asyncio.IncompleteReadError, ConnectionResetError):
            return
        if not is_cmd or not payload:
            continue
        cmd, body = payload[0], payload[1:].decode('latin-1', errors='replace')
        if cmd == CMD_ADR:
            # M_ADR carries one or more whitespace-separated AKAs and may
            # be sent more than once. Many implementations (Mystic, BinkD,
            # Argus) suffix the address with @domain (e.g. "1337:3/207@tqwnet")
            # which has to be stripped for a 5D-style match.
            remote_akas = list(body.strip().split())
            if remote_akas and not remote_addr:
                remote_addr = remote_akas[0]
        elif cmd == CMD_PWD:
            remote_pwd = body.strip()
        elif cmd == CMD_NUL:
            logger.debug('BinkP %s NUL: %s', peer, body)

    # 3. Look up the caller — may be an upstream hub (EchomailNetwork) or a
    #    downstream node that registered with us as hub (BinkPNode).
    from flask import Flask
    from anetbbs.config import get_config
    from anetbbs.models import db, EchomailNetwork, BinkPNode

    app = Flask(__name__)
    app.config.from_object(get_config(os.environ.get('FLASK_ENV', 'production')))
    db.init_app(app)

    # Build the set of address candidates to check. Each AKA the peer
    # sent — and its bare 5D form without the @domain suffix — is a valid
    # match against hub_address (upstream) or ftn_address (downstream).
    candidates = []
    for a in (remote_akas or [remote_addr]) if remote_addr else []:
        if not a:
            continue
        if a not in candidates:
            candidates.append(a)
        bare = a.split('@', 1)[0]
        if bare and bare not in candidates:
            candidates.append(bare)

    net_id = None
    net_name = None
    downstream_node_id = None   # set when caller is a downstream BinkPNode

    with app.app_context():
        # 3a. Check upstream networks first.
        network = (EchomailNetwork.query
                   .filter(EchomailNetwork.hub_address.in_(candidates))
                   .first()) if candidates else None
        if network and network.network_type == 'binkp':
            if (network.binkp_password or '') != (remote_pwd or ''):
                logger.warning('BinkP %s: bad password for upstream %s', peer, remote_addr)
                await _send_cmd(writer, CMD_ERR, 'bad password')
                writer.close()
                return
            net_id = network.id
            net_name = network.name
        else:
            # 3b. Check downstream nodes registered with us as hub.
            node = (BinkPNode.query
                    .filter(BinkPNode.ftn_address.in_(candidates))
                    .filter_by(is_active=True)
                    .first()) if candidates else None
            if node:
                if (node.password or '') != (remote_pwd or ''):
                    logger.warning('BinkP %s: bad password for downstream node %s',
                                   peer, remote_addr)
                    await _send_cmd(writer, CMD_ERR, 'bad password')
                    writer.close()
                    return
                downstream_node_id = node.id
                net_name = node.name
                # Update last-seen timestamp.
                node.last_seen_at = datetime.utcnow()
                db.session.commit()
            else:
                logger.warning('BinkP %s: unknown remote address %s (tried: %s)',
                               peer, remote_addr, ', '.join(candidates))
                await _send_cmd(writer, CMD_ERR, f'unknown address {remote_addr}')
                writer.close()
                return

    logger.info('BinkP %s: authenticated as %s (%s)', peer, remote_addr, net_name)

    # 4. M_OK
    await _send_cmd(writer, CMD_OK, 'secure')

    # 5. Receive inbound files until M_EOB. Then send outbound. Then M_EOB.
    inbound_files = await _receive_files(reader, writer, peer)

    # 6. Import inbound packets + scan for .tic file echoes.
    #
    # FidoNet hubs deliver mail in several wrappers:
    #
    #   * Raw FTS-0001 type-2 packet — bytes 18-19 == 02 00 / 02 01.
    #     Extension typically .pkt, but Mystic ships point-targeted variants:
    #       .pkt              standard
    #       .cut .crt         crash mail
    #       .dut .drt         direct mail
    #       .iut .irt         immediate mail
    #       .hut .hrt         hold mail
    #       .t<flavor><hex>   point-targeted (.th5 = hold for point .5,
    #                          .we3 = weekly echomail for point .3, etc.)
    #
    #   * ZIP-compressed mail bundle (FTS-5003 / standard FidoNet). Magic
    #     bytes 50 4B 03 04 = "PK..".  Inside is one or more .pkt files.
    #     Mystic and binkd default to ZIP for echomail to save bandwidth;
    #     if we don't extract these the entire feed is silently dropped.
    #
    # Strategy: detect by content (magic bytes), extract any .pkt files
    # inside ZIPs, then import each. Fall back to the extension regex.
    import re as _re
    import io as _io
    import zipfile as _zipfile
    # Mail-file extension acceptance. Several FTN conventions coexist:
    #   .pkt                — raw FTS-0001 packet (any tosser)
    #   .[cdih]ut/.[cdih]rt — Mystic point flavors (crash/direct/
    #                         immediate/hold, 'ut' = unzipped, 'rt' = ARC)
    #   .t[a-z][0-9a-z]     — point-targeted bundles per Mystic naming
    #   .(mo|tu|we|th|fr|sa|su)[0-9a-z]
    #                       — FTS-5003 day-of-week bundled mail for nodes,
    #                         where the prefix is the local day at the
    #                         sending hub and the trailing char is a
    #                         per-file sequence (0..9, then a..z = 36 per day).
    # The OLD regex only matched `we<hex>` (Wednesday only) which made
    # Friday-bundled mail (`.frk`, `.frl`, …) get silently filed as
    # non-mail and dropped. Mystic hubs delivering to a node use these
    # by default. Pattern below now covers all 7 days and 36-char
    # sequence space.
    _PKT_EXT_RE = _re.compile(
        r'^\.(?:'
        r'pkt'
        r'|[cdih]ut|[cdih]rt'
        r'|t[cdih][0-9a-f]'
        r'|(?:mo|tu|we|th|fr|sa|su)[0-9a-z]'
        r')$',
        _re.IGNORECASE)

    def _is_fts_packet(payload):
        return len(payload) >= 60 and payload[18:20] in (b'\x02\x00', b'\x02\x01')

    def _is_zip(payload):
        return len(payload) >= 4 and payload[:4] == b'PK\x03\x04'

    def _extract_packets(name, payload):
        """Yield (inner_name, inner_bytes) for every FTS-0001 packet found
        in this file — directly, or after unzipping a mail bundle."""
        if _is_zip(payload):
            try:
                with _zipfile.ZipFile(_io.BytesIO(payload)) as zf:
                    for info in zf.infolist():
                        if info.is_dir():
                            continue
                        try:
                            inner = zf.read(info.filename)
                        except Exception as exc:
                            logger.warning('Failed to read %s from %s: %s',
                                           info.filename, name, exc)
                            continue
                        if _is_fts_packet(inner) or _PKT_EXT_RE.match(
                                os.path.splitext(info.filename)[1]):
                            yield (info.filename, inner)
                        else:
                            logger.info('Skipping non-packet %s inside %s',
                                        info.filename, name)
            except _zipfile.BadZipFile as exc:
                logger.warning('Bad ZIP %s: %s', name, exc)
        elif _is_fts_packet(payload) or _PKT_EXT_RE.match(
                os.path.splitext(name)[1]):
            yield (name, payload)

    if inbound_files:
        with app.app_context():
            inbound_dir = None
            for fname, payload in inbound_files:
                extracted = list(_extract_packets(fname, payload))
                if extracted:
                    for inner_name, inner_payload in extracted:
                        _import_pkt_payload(inner_payload, net_id, inner_name)
                else:
                    # Non-packet files (TIC manifests, hatched binaries, etc.)
                    # get written to inbound for later processing.
                    # Default landed in /tmp/ which is tmpfs on most distros
                    # — files vanish on service restart. Default to data/
                    # so the sysop can recover from a tossing miss.
                    inbound_dir = inbound_dir or (
                        os.environ.get('BINKP_INBOUND_DIR')
                        or os.path.join(
                            app.config.get('DATA_DIR') or 'data',
                            'binkp', 'inbound'))
                    try:
                        os.makedirs(inbound_dir, exist_ok=True)
                        with open(os.path.join(inbound_dir, fname), 'wb') as f:
                            f.write(payload)
                        # Visible-by-default log so the sysop can spot
                        # mystery files that don't match the regex /
                        # magic bytes — easier diagnosis than digging
                        # at DEBUG level.
                        logger.info(
                            'BinkP: stored unrecognised file %s '
                            '(%d bytes) in %s — neither ZIP nor FTS-0001 '
                            'packet, scanning for TIC manifest',
                            fname, len(payload), inbound_dir)
                    except OSError as exc:
                        logger.warning('Failed to write inbound file %s: %s',
                                       fname, exc)
            # After dropping any non-.pkt files into inbound, run the TIC scan
            # so file-echo distributions get filed automatically.
            if inbound_dir and os.path.isdir(inbound_dir):
                try:
                    from .tic import scan_inbound
                    n = scan_inbound(inbound_dir)
                    if n:
                        logger.info('Processed %d TIC files from %s',
                                    n, inbound_dir)
                except Exception:
                    logger.exception('TIC scan failed')

    # 7. Send queued outbound messages.
    #    For upstream hub sessions: flush outbound EchomailMessages for that network.
    #    For downstream node sessions: flush the BinkPHoldQueue for that node.
    sent_count = 0
    with app.app_context():
        if downstream_node_id is not None:
            from .tosser import get_pending_for_node, mark_sent_for_node
            outbound = get_pending_for_node(downstream_node_id)
            if outbound:
                pkt_bytes = _build_ftn_packet(outbound, our_address, remote_addr)
                fname = f'{int(datetime.utcnow().timestamp()):08x}.pkt'
                accepted = await _send_pkt_file(reader, writer, fname, pkt_bytes)
                if accepted:
                    mark_sent_for_node(downstream_node_id,
                                       [m.id for m in outbound])
                    sent_count = len(outbound)
                else:
                    logger.warning('Hold-queue .pkt not accepted by %s — '
                                   'leaving pending for retry', remote_addr)
        elif net_id is not None:
            from ..models import EchomailMessage
            outbound = EchomailMessage.query.filter_by(
                network_id=net_id,
                direction='outbound',
                sent_at=None,
            ).all()
            if outbound:
                pkt_bytes = _build_ftn_packet(outbound, our_address, remote_addr)
                fname = f'{int(datetime.utcnow().timestamp()):08x}.pkt'
                accepted = await _send_pkt_file(reader, writer, fname, pkt_bytes)
                if accepted:
                    now = datetime.utcnow()
                    for m in outbound:
                        m.sent_at = now
                    db.session.commit()
                    sent_count = len(outbound)
                else:
                    logger.warning('Outbound .pkt was not accepted by %s — '
                                   'leaving in queue for retry', remote_addr)

    # 8. End of batch.
    await _finish_session(reader, writer, peer, inbound_files, sent_count)


async def _finish_session(reader, writer, peer, inbound_files, sent_count):
    """Send our final M_EOB, complete binkp/1.1's two-round handshake,
    briefly drain any trailing bytes, then close.

    binkp/1.1 (which our own VER line advertises) expects a two-round
    M_EOB handshake: since we already received the client's first
    M_EOB in _receive_files(), a spec-compliant peer (real binkd,
    confirmed live) now expects one more EOB round-trip before
    treating the session as cleanly finished -- closing right after
    our own single M_EOB reads as an unexpected mid-session disconnect
    on their end, even though every file transferred successfully.
    Wait briefly for their second M_EOB and answer it; a lenient peer
    that just closes instead is still handled cleanly below.

    Also does a brief drain before closing: an immediate close() while
    the peer still has trailing bytes in flight can register on their
    end as an abrupt disconnect rather than a clean session end.
    """
    await _send_cmd(writer, CMD_EOB)
    try:
        is_cmd, payload = await asyncio.wait_for(_recv_frame(reader), timeout=10)
        if is_cmd and payload and payload[0] == CMD_EOB:
            await _send_cmd(writer, CMD_EOB)
    except Exception:
        pass

    logger.info('BinkP %s: done — received=%d sent=%d', peer, len(inbound_files), sent_count)

    try:
        drain_deadline = asyncio.get_event_loop().time() + 2.0
        while True:
            remaining = drain_deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            chunk = await asyncio.wait_for(reader.read(4096), timeout=remaining)
            if not chunk:
                break
    except Exception:
        pass

    try:
        writer.close()
        await writer.wait_closed()
    except Exception:
        pass


async def _receive_files(reader, writer, peer):
    """Receive M_FILE offers + data frames until M_EOB. Returns [(filename, bytes)]."""
    files = []
    current_name = None
    current_size = 0
    current_buf = bytearray()

    while True:
        try:
            is_cmd, payload = await asyncio.wait_for(_recv_frame(reader), timeout=120)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionResetError):
            break
        if is_cmd:
            if not payload:
                continue
            cmd, body = payload[0], payload[1:].decode('latin-1', errors='replace')
            if cmd == CMD_FILE:
                # "filename size unix_time offset"
                parts = body.split()
                if len(parts) >= 2:
                    current_name = parts[0]
                    current_size = int(parts[1])
                    current_buf = bytearray()
                    logger.info('BinkP %s: receiving %s (%d bytes)', peer, current_name, current_size)
            elif cmd == CMD_EOB:
                break
            elif cmd == CMD_ERR:
                logger.warning('BinkP %s: client sent ERR: %s', peer, body)
                break
        else:
            # Data frame for the current file
            current_buf.extend(payload)
            if current_name and len(current_buf) >= current_size:
                files.append((current_name, bytes(current_buf[:current_size])))
                await _send_cmd(writer, CMD_GOT, f'{current_name} {current_size} 0')
                current_name = None
                current_buf = bytearray()
    return files


async def _send_pkt_file(reader, writer, filename, payload):
    """Send a M_FILE offer + data frames, then wait for M_GOT (or M_SKIP/M_ERR).

    Returns True on accepted (M_GOT), False on rejected/skipped/error.
    """
    unix_time = int(datetime.utcnow().timestamp())
    await _send_cmd(writer, CMD_FILE, f'{filename} {len(payload)} {unix_time} 0')
    # Send in 4 KB chunks
    chunk = 4096
    for i in range(0, len(payload), chunk):
        await _send_data(writer, payload[i:i+chunk])

    # Wait for M_GOT — many BinkP impls won't actually process the file until
    # they confirm. Without this, we'd send M_EOB + close immediately and the
    # remote might not have flushed/written the .pkt to its inbound dir.
    deadline = asyncio.get_event_loop().time() + 60
    while True:
        timeout = max(0.1, deadline - asyncio.get_event_loop().time())
        try:
            is_cmd, frame = await asyncio.wait_for(_recv_frame(reader), timeout=timeout)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionResetError):
            logger.warning('No M_GOT received for %s within 60s', filename)
            return False
        if not is_cmd or not frame:
            continue
        cmd, body = frame[0], frame[1:].decode('latin-1', errors='replace')
        if cmd == CMD_GOT:
            logger.info('M_GOT received for %s', filename)
            return True
        if cmd in (CMD_SKIP, CMD_ERR):
            logger.warning('Remote rejected %s: cmd=%d body=%s', filename, cmd, body)
            return False
        # Some other frame — log and keep waiting
        logger.debug('Waiting for M_GOT, got cmd=%d body=%s', cmd, body)


def _import_pkt_payload(pkt_bytes: bytes, network_id: int, filename: str) -> int:
    """Parse an FTS-0001 .pkt and import each message as inbound. Returns count.

    Routes by AREA: kludge:
      - present → echomail → EchomailMessage (with kludges, seenby, path)
      - absent  → netmail  → NetmailMessage (with kludges, all attribute flags)
    Triggers areafix bot if a netmail's to_name matches 'areafix' (any case).
    """
    import json
    from ..models import db, EchomailMessage, EchoArea, NetmailMessage, EchomailNetwork
    from .kludges import find_kludge
    from .routing import resolve_netmail_recipient

    network = EchomailNetwork.query.get(network_id)

    messages = _parse_ftn_packet(pkt_bytes)
    imported = 0
    echomail_objects = []       # EchomailMessage ORM objects for post-commit toss
    netmails_to_process = []   # ids of newly-inserted netmails for post-processing

    for m in messages:
        kludges = m.get('kludges') or []
        kludges_json = json.dumps(kludges) if kludges else None
        seenby_json = json.dumps(m.get('seenby') or []) if m.get('seenby') else None
        path_json = json.dumps(m.get('path') or []) if m.get('path') else None
        chrs = find_kludge(kludges, 'CHRS') or 'CP437 2'

        if m['area_tag']:
            # ECHOMAIL — route to area
            area = EchoArea.query.filter_by(
                network_id=network_id, tag=m['area_tag']).first()
            if area is None:
                area = EchoArea(network_id=network_id,
                                tag=m['area_tag'], name=m['area_tag'])
                db.session.add(area)
                db.session.flush()
            em = EchomailMessage(
                area_id=area.id,
                network_id=network_id,
                from_name=m['from_name'][:100],
                to_name=m['to_name'][:100],
                subject=m['subject'][:200],
                body=m['body'],
                tear_line=(m['tear_line'] or '')[:200] or None,
                origin_line=(m['origin_line'] or '')[:200] or None,
                kludges=kludges_json,
                chrs=chrs,
                seenby=seenby_json,
                path=path_json,
                direction='inbound',
                created_at=datetime.utcnow(),
                imported_at=datetime.utcnow(),
            )
            db.session.add(em)
            echomail_objects.append(em)
            # Bump the area counters so the admin index and area list show
            # accurate "Messages" and "Last Activity" columns. The QWK
            # poller does this in poller.py — the listener path was missing
            # it, leading to "0 messages" on areas that had real content.
            area.total_messages = (area.total_messages or 0) + 1
            area.last_message_at = datetime.utcnow()
        else:
            # NETMAIL — point-to-point
            msgid = find_kludge(kludges, 'MSGID')
            reply = find_kludge(kludges, 'REPLY')
            # INTL kludge holds dest+orig zone:net/node — extract fallback
            # FROM/TO addresses if the .pkt header didn't include them.
            intl = find_kludge(kludges, 'INTL')
            to_addr = ''
            from_addr = ''
            if intl:
                parts = intl.split(None, 1)
                if parts:
                    to_addr = parts[0]
                    if len(parts) > 1:
                        from_addr = parts[1]

            # Match a local user by AKA address or by name -- this
            # listener path never did this at all before, so BinkP-
            # received netmail was never linked to a User row and could
            # only be found later by string-matching to_name in the web
            # inbox (and its recipient never got notified of new mail).
            to_user = resolve_netmail_recipient(m['to_name'], to_addr, network)

            nm = NetmailMessage(
                network_id=network_id,
                from_address=from_addr or None,
                to_address=to_addr or None,
                from_name=m['from_name'][:120],
                to_name=m['to_name'][:120],
                to_user_id=to_user.id if to_user else None,
                subject=m['subject'][:200],
                body=m['body'],
                kludges=kludges_json,
                msgid=msgid or None,
                reply_msgid=reply or None,
                chrs=chrs,
                direction='inbound',
                status='received',
                created_at=datetime.utcnow(),
                received_at=datetime.utcnow(),
            )
            db.session.add(nm)
            db.session.flush()
            netmails_to_process.append((
                nm.id, (m.get('to_name') or '').lower(),
                to_user.id if to_user else None,
                m['from_name'], m['subject']))
        imported += 1

    db.session.commit()
    logger.info('Imported %d messages from %s into network %d',
                imported, filename, network_id)

    # Hub tosser — fan out each newly imported echomail to downstream nodes.
    # After commit the ORM objects have their .id populated.
    if echomail_objects:
        try:
            from .tosser import toss_message
            for em_obj in echomail_objects:
                if em_obj.id:
                    toss_message(em_obj.id)
        except Exception:
            logger.exception('Hub tosser failed for %s', filename)

    # Areafix bot — trigger reply for any netmail addressed to 'areafix'
    # (or any Synchronet-style robot name we recognize). Done AFTER the
    # commit so the netmail is durable before the bot runs. Everything
    # else that resolved to a real local user gets a Notification row
    # (in-app bell + terminal "You have new" banner) -- this listener
    # path never notified the recipient of new netmail at all before.
    from .areafix import handle_areafix_netmail
    from ..features.notify import notify
    for nm_id, to_lower, to_uid, from_name, subject in netmails_to_process:
        if to_lower in ('areafix', 'area fix', 'areamgr'):
            try:
                handle_areafix_netmail(nm_id)
            except Exception:
                logger.exception('Areafix bot failed for netmail %d', nm_id)
        elif to_uid:
            try:
                notify(to_uid, 'netmail', title=f'Netmail from {from_name}',
                      body=subject or '', target_url=f'/netmail/{nm_id}')
            except Exception:
                logger.exception('Netmail notify failed for netmail %d', nm_id)

    return imported


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def _serve():
    host = os.environ.get('BINKP_LISTEN_HOST', '0.0.0.0')
    port = int(os.environ.get('BINKP_LISTEN_PORT', '24554'))
    our_address = os.environ.get('BINKP_OUR_ADDRESS', '1:1/1')
    system_name = os.environ.get('BINKP_SYSTEM_NAME', 'ANetBBS')

    async def _wrapper(reader, writer):
        try:
            await _handle_connection(reader, writer, our_address, system_name)
        except (ConnectionResetError, BrokenPipeError):
            pass  # health probe or client disconnect — not a crash
        except Exception:
            logger.exception('BinkP session crashed')
        finally:
            try:
                writer.close()
            except Exception:
                pass

    server = await asyncio.start_server(_wrapper, host=host, port=port)
    logger.info('BinkP listener on %s:%d as %s', host, port, our_address)
    async with server:
        await server.serve_forever()


def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    )
    try:
        asyncio.run(_serve())
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
