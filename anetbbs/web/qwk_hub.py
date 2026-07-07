# anetbbs/web/qwk_hub.py
"""
QWK hub endpoints — allow downstream nodes to poll this BBS as a QWK hub.

GET  /qwkhub/<packet_id>.qwk   — download a per-node QWK packet
POST /qwkhub/<packet_id>.rep   — upload a REP packet (outbound messages)

Authentication: HTTP Basic Auth  username=packet_id  password=node_password.

The hub system ID (the filename of the QWK the node downloads) comes from
the QWK_HUB_ID setting in Admin → Settings.  Set it to something short like
'ANET'.  The node downloads ANET.QWK and uploads a REP with ANET.MSG inside.

FTP support: the same logic runs after FTP login (FTP handler calls
build_qwk_for_node / import_rep_for_node directly).
"""
import io
import struct
import zipfile
import logging
from datetime import datetime
from functools import wraps

from flask import (Blueprint, request, abort, send_file,
                   current_app, g, jsonify)

from ..models import (db, QWKNode, QWKNodeLastSent, EchoArea,
                       EchomailNetwork, EchomailMessage)
from ..echomail.qwk import _parse_messages_dat, QWK_HEADER_SIZE, QWK_BLOCK_SIZE

logger = logging.getLogger(__name__)

qwk_hub_bp = Blueprint('qwk_hub', __name__, url_prefix='/qwkhub')


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------

def _auth_node():
    """Validate HTTP Basic Auth against QWKNode.  Returns the node or aborts 401."""
    auth = request.authorization
    if not auth:
        abort(401, description='QWK hub: auth required')
    node = QWKNode.query.filter_by(
        packet_id=auth.username.strip().upper(),
        is_active=True,
    ).first()
    if node is None or node.password != auth.password:
        logger.warning('QWK hub: bad auth for %r', auth.username)
        abort(401, description='QWK hub: invalid credentials')
    return node


# ---------------------------------------------------------------------------
# QWK packet builder (hub → node)
# ---------------------------------------------------------------------------

def _hub_id():
    """Return the configured hub system ID (e.g. 'ANET')."""
    import os
    val = os.environ.get('QWK_HUB_ID', '').strip().upper()
    if not val:
        # Fall back to first 8 chars of BBS_NAME.
        bbs = os.environ.get('BBS_NAME', 'ANET')
        import re as _re
        val = _re.sub(r'[^A-Z0-9]', '', bbs.upper())[:8] or 'ANET'
    return val


def _build_qwk_hub_packet(node: QWKNode) -> bytes:
    """Build a QWK packet containing all new messages for this node.

    Queries each subscribed area for messages with id > last_message_id,
    builds CONTROL.DAT + MESSAGES.DAT, and returns a .QWK zip in bytes.
    Updates last_message_id for each area (committed after caller verifies
    the send succeeded — caller should call mark_qwk_sent()).
    """
    hub_id = _hub_id()

    # Gather subscriptions.
    subs = (QWKNodeLastSent.query
            .filter_by(node_id=node.id)
            .join(EchoArea, QWKNodeLastSent.echo_area_id == EchoArea.id)
            .filter(EchoArea.is_active == True)
            .order_by(QWKNodeLastSent.conf_number)
            .all())

    # Build conference list for CONTROL.DAT.
    conferences = {}        # conf_num → name
    messages_by_conf = {}   # conf_num → [EchomailMessage]
    sub_by_area = {}        # area_id → sub row

    for sub in subs:
        conf_num = sub.conf_number or 1
        area = sub.echo_area
        conferences[conf_num] = area.tag
        sub_by_area[area.id] = sub

        # Messages newer than last HWM.
        q = EchomailMessage.query.filter_by(area_id=area.id)
        if sub.last_message_id:
            q = q.filter(EchomailMessage.id > sub.last_message_id)
        msgs = q.order_by(EchomailMessage.id).all()
        messages_by_conf[conf_num] = msgs

    bbs_name = current_app.config.get('BBS_NAME', 'ANetBBS')
    bbs_location = current_app.config.get('BBS_LOCATION', 'Online')

    # Build CONTROL.DAT.
    control_lines = [
        bbs_name,
        bbs_location,
        '---',
        current_app.config.get('SYSOP_NAME', 'Sysop'),
        f'{hub_id}, 0',          # sys_id, version (unused)
        datetime.utcnow().strftime('%m-%d-%Y, %H:%M:%S'),
        node.packet_id,           # logged-in user
        '0',                      # starting msg number
        '0',                      # total messages
        str(len(conferences)),    # number of conferences
        '0',                      # first conference number
    ]
    for conf_num in sorted(conferences):
        control_lines.append(str(conf_num))
        control_lines.append(conferences[conf_num])
    control_dat = '\r\n'.join(control_lines) + '\r\n'

    # Build MESSAGES.DAT.
    msg_dat = io.BytesIO()

    # Welcoming block — hub's ID in first 128 bytes.
    welcome = bytearray(QWK_HEADER_SIZE)
    _id_bytes = hub_id.encode('ascii', errors='replace')[:QWK_HEADER_SIZE]
    welcome[:len(_id_bytes)] = _id_bytes
    msg_dat.write(bytes(welcome))

    total_msgs = 0
    new_hwm = {}   # area_id → last message id

    for conf_num in sorted(conferences):
        msgs = messages_by_conf.get(conf_num, [])
        for msg in msgs:
            body_text = (msg.body or '').replace('\n', '\xe3').replace('\r', '')
            body_encoded = body_text.encode('latin-1', errors='replace')
            remainder = len(body_encoded) % QWK_BLOCK_SIZE
            if remainder:
                body_encoded += b' ' * (QWK_BLOCK_SIZE - remainder)
            num_chunks = 1 + len(body_encoded) // QWK_BLOCK_SIZE

            header = bytearray(b' ' * QWK_HEADER_SIZE)
            header[0] = ord(' ')   # public message
            # Bytes 1-7: sequential msg number within packet.
            total_msgs += 1
            header[1:8] = str(total_msgs).encode('ascii')[:7].ljust(7)
            date_str = (msg.created_at or datetime.utcnow()).strftime('%m-%d-%y%H:%M').encode('ascii')[:13].ljust(13)
            header[8:21] = date_str
            to_b = (msg.to_name or 'All').encode('latin-1', errors='replace')[:25].ljust(25)
            header[21:46] = to_b
            from_b = (msg.from_name or '').encode('latin-1', errors='replace')[:25].ljust(25)
            header[46:71] = from_b
            subj_b = (msg.subject or '').encode('latin-1', errors='replace')[:25].ljust(25)
            header[71:96] = subj_b
            header[108:116] = b'0'.rjust(8)
            header[116:122] = str(num_chunks).encode('ascii')[:6].rjust(6)
            header[122] = 0xE1   # active
            struct.pack_into('<H', header, 123, conf_num)
            header[127] = ord(' ')

            msg_dat.write(bytes(header))
            msg_dat.write(body_encoded)

            sub = sub_by_area.get(msg.area_id)
            if sub:
                new_hwm[msg.area_id] = msg.id

    msg_dat.seek(0)
    msg_bytes = msg_dat.read()

    # Package.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('CONTROL.DAT', control_dat)
        zf.writestr('MESSAGES.DAT', msg_bytes)

    return buf.getvalue(), new_hwm, total_msgs


def mark_qwk_sent(node_id: int, new_hwm: dict):
    """Commit high-water mark updates after a successful packet delivery."""
    for area_id, msg_id in new_hwm.items():
        row = QWKNodeLastSent.query.filter_by(
            node_id=node_id, echo_area_id=area_id).first()
        if row:
            row.last_message_id = msg_id
    node = QWKNode.query.get(node_id)
    if node:
        node.last_poll_at = datetime.utcnow()
    db.session.commit()


# ---------------------------------------------------------------------------
# REP packet importer (node → hub)
# ---------------------------------------------------------------------------

def import_rep_packet(node: QWKNode, rep_bytes: bytes) -> int:
    """Parse a REP zip and import messages into echomail areas.

    Expects a .REP zip containing <HUB_ID>.MSG (e.g. ANET.MSG).
    Imports each message into the EchoArea mapped by conf_number.
    Returns the number of messages imported.
    """
    hub_id = _hub_id()
    msg_filename = f'{hub_id}.MSG'

    try:
        with zipfile.ZipFile(io.BytesIO(rep_bytes)) as zf:
            names_upper = {n.upper(): n for n in zf.namelist()}
            inner_name = names_upper.get(msg_filename)
            if inner_name is None:
                logger.warning('QWK hub REP from %s: no %s in zip (found: %s)',
                               node.packet_id, msg_filename, list(zf.namelist()))
                return 0
            msg_bytes = zf.read(inner_name)
    except zipfile.BadZipFile:
        logger.warning('QWK hub REP from %s: bad zip', node.packet_id)
        return 0

    # Build conf_num → EchoArea map for this node.
    subs = QWKNodeLastSent.query.filter_by(node_id=node.id).all()
    conf_to_area = {s.conf_number: EchoArea.query.get(s.echo_area_id)
                    for s in subs if s.conf_number}
    conferences = {n: (a.tag if a else str(n)) for n, a in conf_to_area.items()}

    messages = _parse_messages_dat(msg_bytes, conferences)
    imported = 0

    # Pick a default network (the first active BinkP/QWK network we know).
    default_net = EchomailNetwork.query.filter_by(is_active=True).first()
    net_id = default_net.id if default_net else None

    for m in messages:
        conf_num = m.get('conf_num', 0)
        area = conf_to_area.get(conf_num)
        if area is None:
            logger.debug('QWK hub REP from %s: skipping msg in unknown conf %d',
                         node.packet_id, conf_num)
            continue

        em = EchomailMessage(
            area_id=area.id,
            network_id=net_id or area.network_id,
            from_name=(m['from_name'] or '')[:100],
            to_name=(m['to_name'] or 'All')[:100],
            subject=(m['subject'] or '')[:200],
            body=m['body'],
            msg_id=m.get('msg_id'),
            reply_id=m.get('reply_id'),
            direction='inbound',
            created_at=datetime.utcnow(),
            imported_at=datetime.utcnow(),
        )
        db.session.add(em)
        area.total_messages = (area.total_messages or 0) + 1
        area.last_message_at = datetime.utcnow()
        imported += 1

    if imported:
        # Collect ORM objects before commit for post-commit tossing.
        new_echomail = [em for em in db.session.new
                        if isinstance(em, EchomailMessage)]
        db.session.commit()
        # Fan out to other BinkP nodes and QWK subscribers.
        from ..echomail.tosser import toss_message
        for em in new_echomail:
            if em.id:
                try:
                    toss_message(em.id)
                except Exception:
                    logger.exception('tosser failed for QWK hub inbound')

    logger.info('QWK hub: imported %d messages from REP by %s', imported, node.packet_id)
    return imported


# ---------------------------------------------------------------------------
# HTTP routes
# ---------------------------------------------------------------------------

@qwk_hub_bp.route('/<packet_id_qwk>', methods=['GET'])
def download_qwk(packet_id_qwk: str):
    """GET /qwkhub/<nodeid>.qwk — serve a fresh QWK packet for the node."""
    hub_id = _hub_id()
    # Accept both <nodeid>.qwk and <hubid>.qwk (some clients download using hub ID).
    node = _auth_node()

    packet_data, new_hwm, total_msgs = _build_qwk_hub_packet(node)

    if total_msgs == 0:
        # Nothing new — still send an empty (but valid) packet.
        logger.info('QWK hub: no new messages for %s', node.packet_id)

    # Commit HWM immediately — the node has the data now.
    mark_qwk_sent(node.id, new_hwm)

    filename = f'{hub_id}.QWK'
    return send_file(
        io.BytesIO(packet_data),
        mimetype='application/zip',
        as_attachment=True,
        download_name=filename,
    )


@qwk_hub_bp.route('/<packet_id_rep>', methods=['POST'])
def upload_rep(packet_id_rep: str):
    """POST /qwkhub/<nodeid>.rep — accept a REP packet from a node."""
    node = _auth_node()

    # Accept multipart file upload or raw body.
    if request.files:
        f = next(iter(request.files.values()))
        rep_bytes = f.read()
    else:
        rep_bytes = request.get_data()

    if not rep_bytes:
        abort(400, description='Empty REP packet')

    n = import_rep_packet(node, rep_bytes)
    return f'OK {n} messages imported\r\n', 200


# ---------------------------------------------------------------------------
# Node-request API — lets a REMOTE BBS's terminal wizard (bbs_ui.py's
# _apply_qwk_node) submit an application to the real hub over HTTP,
# instead of writing straight into whichever install's own database
# happened to run the wizard. Gated to the designated hub only (see
# hub_admin.py's blueprint-wide gate for the human-facing review UI --
# this is the machine-facing counterpart for the same underlying data).
# ---------------------------------------------------------------------------

def _require_hub_mode():
    if not current_app.config.get('REGISTRY_MODE_ENABLED'):
        abort(404)


@qwk_hub_bp.route('/apply', methods=['POST'])
def apply_for_node():
    """POST /qwkhub/apply — a remote BBS applies for a QWK node.

    JSON body: bbs_name, packet_id, sysop_name, email, bbs_address, notes.
    Returns {"ok": true, "request_token": "..."} on success, or
    {"ok": false, "error": "..."} with a 4xx status otherwise.
    """
    _require_hub_mode()
    import re
    import secrets
    from ..models import QWKNodeRequest

    data = request.get_json(silent=True) or {}
    bbs_name = (data.get('bbs_name') or '').strip()[:100]
    packet_id = (data.get('packet_id') or '').strip().upper()[:8]
    if not bbs_name or not packet_id:
        return jsonify(ok=False, error='bbs_name and packet_id are required'), 400
    if not re.match(r'^[A-Z0-9]{2,8}$', packet_id):
        return jsonify(ok=False, error='packet_id must be 2-8 chars, A-Z/0-9 only'), 400

    taken = (QWKNode.query.filter_by(packet_id=packet_id).first() or
             QWKNodeRequest.query.filter_by(packet_id=packet_id, status='pending').first())
    if taken:
        return jsonify(ok=False, error=f'packet_id {packet_id} is already taken'), 409

    token = secrets.token_urlsafe(32)
    req = QWKNodeRequest(
        bbs_name=bbs_name,
        packet_id=packet_id,
        sysop_name=(data.get('sysop_name') or '').strip()[:100] or None,
        email=(data.get('email') or '').strip()[:200] or None,
        bbs_address=(data.get('bbs_address') or '').strip()[:200] or None,
        notes=(data.get('notes') or '').strip()[:500] or None,
        applied_via='api',
        applied_by_username=(data.get('applied_by_username') or '').strip()[:100] or None,
        request_token=token,
    )
    db.session.add(req)
    db.session.commit()
    from ..features.notify import notify_admins
    notify_admins(
        'qwk_node_app',
        title=f'QWK node application: {bbs_name} ({packet_id})',
        body=f'{bbs_name} applied for packet ID {packet_id}. '
             f'Review under Admin -> Echomail -> Hub -> Node Requests.',
        target_url='/admin/echomail/hub/qwk/requests')
    return jsonify(ok=True, request_token=token), 200


@qwk_hub_bp.route('/status/<token>', methods=['GET'])
def request_status(token: str):
    """GET /qwkhub/status/<token> — check a previously submitted
    application's status. No login needed -- the token itself (32
    bytes of secrets.token_urlsafe) is the credential, same principle
    as an email verify link."""
    _require_hub_mode()
    from ..models import QWKNodeRequest

    req = QWKNodeRequest.query.filter_by(request_token=token).first()
    if req is None:
        return jsonify(ok=False, error='unknown request token'), 404

    body = {
        'ok': True,
        'status': req.status,
        'packet_id': req.packet_id,
        'bbs_name': req.bbs_name,
    }
    if req.status == 'approved':
        body['generated_password'] = req.generated_password
        body['hub_ftp'] = 'bbs.a-net.fyi:21'
    elif req.status == 'denied':
        body['deny_reason'] = req.deny_reason
    return jsonify(body), 200
