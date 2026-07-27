# anetbbs/web/qwk_user.py
"""
Per-user QWK offline-reader workflow.

A user grabs a .QWK packet of new echomail in their subscribed areas,
reads/replies offline in something like MultiMail, then uploads a .REP
packet with their replies. The replies get inserted as outbound
echomail and propagated like any other post.
"""
import io
import os
import zipfile
import struct
from datetime import datetime
from flask import Blueprint, send_file, request, redirect, url_for, flash, render_template
from flask_login import login_required, current_user

from ..models import (db, EchomailMessage, EchoArea, EchomailLastRead)
from ..features.access_control import evaluate_access


qwk_user_bp = Blueprint('qwk_user', __name__, url_prefix='/qwk')


def _qwk_accessible_areas(user):
    """Active areas this user may see via QWK, in a stable order.

    Real gap found in a full echomail-subsystem audit: every other
    echomail entry point in this codebase (web/echomail.py) gates areas
    through evaluate_access()/min_access_level/is_sysop_only -- this QWK
    path queried EchoArea.query.filter_by(is_active=True).all() with NO
    access filtering at all, so any logged-in user's QWK download
    included messages from sysop-only/restricted areas, and upload let
    them post INTO those same areas via REP import (full read+write
    bypass). Also explicit .order_by(EchoArea.id) -- conf_num is a
    1-based index into this exact list, independently re-queried by
    download() and upload(); an unordered query relies on incidental SQL
    row order, which isn't guaranteed stable between two separate calls.
    (A truly stable per-user conf_num mapping across area-set CHANGES
    between a user's download and later upload -- the same problem
    QWKNodeLastSent.conf_number solves for federated hub nodes -- would
    need a persistent per-user table; not addressed here, only the
    access-control gap and same-request-window ordering determinism.)
    """
    q = EchoArea.query.filter_by(is_active=True).order_by(EchoArea.id)
    if getattr(user, 'is_admin', False):
        return q.all()
    return [a for a in q.all()
            if evaluate_access(user, a.min_access_level,
                               is_sysop_only=a.is_sysop_only, bypass_admin=True)]


def _last_read_map(user_id):
    """{(area_id) -> last EchomailMessage.id read}."""
    out = {}
    try:
        for r in EchomailLastRead.query.filter_by(user_id=user_id).all():
            out[r.area_id] = r.last_message_id or 0
    except Exception:
        db.session.rollback()
    return out


def _build_qwk_blob(user):
    """Construct a minimal QWK packet (CONTROL.DAT + MESSAGES.DAT zipped).
    Format follows the original PCBoard / qwk-classic layout.
    """
    bbs_name = os.environ.get('BBS_NAME', 'ANetBBS')
    sysop = os.environ.get('SYSOP_NAME', 'Sysop')

    # Active areas this user has access to.
    areas = _qwk_accessible_areas(user)
    last_read = _last_read_map(user.id)

    # Conference list — one numeric id per area.
    conf_lines = []
    conf_lookup = {}
    for i, area in enumerate(areas, start=1):
        conf_lines.append(str(i))
        conf_lines.append((area.tag or area.name or f'AREA{i}')[:13])
        conf_lookup[area.id] = i

    # Line layout verified against Synchronet's own CONTROL.DAT writer
    # (pack_qwk.cpp): a strict positional reader expects exactly this
    # many lines before the conference count, plus a mandatory
    # "0" / "E-mail" pair for the reserved conference-0 (netmail)
    # slot before any real conferences are listed. The previous
    # version was missing one blank placeholder line and the conf-0
    # pair entirely, which shifts every line after it out of position.
    control_dat_lines = [
        bbs_name,                  # sys_name
        '',                        # city / location
        '',                        # phone
        f'{sysop}, Sysop',
        '0000,' + bbs_name[:13],   # serial, sys_id
        datetime.utcnow().strftime('%m-%d-%Y,%H:%M:%S'),
        user.username,             # user alias
        '',                        # blank (placeholder)
        '0',                       # placeholder
        '0',                       # placeholder
        str(len(areas)),           # number of conferences
        '0',                       # reserved conference 0 (netmail) number
        'E-mail',                  # reserved conference 0 name
    ]
    control_dat_lines.extend(conf_lines)
    control_dat_lines.append('HELLO')
    control_dat_lines.append('NEWS')
    control_dat_lines.append('GOODBYE')
    control_dat = '\r\n'.join(control_dat_lines).encode('cp437', errors='replace') + b'\r\n'

    # Build MESSAGES.DAT
    msgs = bytearray()
    msgs.extend(b' ' * 128)        # First 128 byte header is reserved
    msg_index = 1
    for area in areas:
        conf_num = conf_lookup[area.id]
        cutoff = last_read.get(area.id, 0)
        rows = (EchomailMessage.query.filter_by(area_id=area.id)
                .filter(EchomailMessage.id > cutoff)
                .order_by(EchomailMessage.created_at).all())
        for m in rows:
            body = (m.body or '').replace('\n', '\xe3')
            # Real bug found live: a web-composed message can contain
            # genuine Unicode characters (pasted box-drawing art) rather
            # than latin-1-wrapped raw bytes -- encode_body_cp437()
            # recovers the correct CP437 byte instead of a blanket
            # encode('latin-1', errors='replace') silently turning them
            # into '?'.
            from ..features.wire_encoding import encode_body_cp437
            body_bytes = encode_body_cp437(body)
            # Pad body to 128-byte blocks; minimum 1 block.
            block_count = max(1, (len(body_bytes) + 127) // 128 + 1)
            # Header is 128 bytes.
            status = b' '
            number = f'{m.id:>7}'.encode('ascii')
            date = m.created_at.strftime('%m-%d-%y')[:8].encode('ascii')
            time = m.created_at.strftime('%H:%M')[:5].encode('ascii')
            to = (m.to_name or '').upper()[:25].ljust(25).encode('cp437', errors='replace')
            sender = (m.from_name or '?')[:25].ljust(25).encode('cp437', errors='replace')
            subject = (m.subject or '')[:25].ljust(25).encode('cp437', errors='replace')
            password = b' ' * 12
            ref = b' ' * 8
            num_blocks = f'{block_count:>6}'.encode('ascii')
            active = b'\xe1'        # active flag
            conf = struct.pack('<H', conf_num)
            # Bytes 125-126 are unused in the real QWK header format --
            # two literal space bytes, not a binary field. Verified
            # against Synchronet's own writer (msgtoqwk.cpp), whose
            # format string spells these out as two explicit ' ' chars
            # right after the conference number. There's no "messages
            # left" field anywhere in the spec at this position.
            unused = b'  '
            net_tag = b' '
            header = (status + number + date + time + to + sender + subject +
                      password + ref + num_blocks + active + conf + unused + net_tag)
            header = header.ljust(128, b' ')
            msgs.extend(header)
            # Body in 128-byte blocks. First byte of each text block is 0xE3.
            payload_len = (block_count - 1) * 128
            padded = body_bytes.ljust(payload_len, b' ')
            msgs.extend(padded)
            msg_index += 1

    # Zip CONTROL.DAT + MESSAGES.DAT into a QWK packet.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('CONTROL.DAT', control_dat)
        z.writestr('MESSAGES.DAT', bytes(msgs))
    buf.seek(0)
    return buf


@qwk_user_bp.route('/')
@login_required
def index():
    """Landing page — explains QWK + offers download/upload."""
    return render_template('qwk_user/index.html')


@qwk_user_bp.route('/download')
@login_required
def download():
    """Stream a fresh .QWK packet for the user."""
    from ..models import UserAccessFlags
    flags = UserAccessFlags.query.filter_by(user_id=current_user.id).first()
    if flags and flags.no_qwk:
        flash('Your QWK download access has been suspended.', 'danger')
        return redirect(url_for('qwk_user.index'))
    blob = _build_qwk_blob(current_user)
    fname = f'{(os.environ.get("BBS_NAME","ANETBBS")[:8] or "ANETBBS").upper()}.QWK'
    return send_file(blob, as_attachment=True, download_name=fname,
                     mimetype='application/zip')


@qwk_user_bp.route('/upload', methods=['POST'])
@login_required
def upload():
    """Accept a .REP packet from the user. We extract MESSAGES.DAT
    and import each message as outbound echomail in the matching area
    (matched by conference number)."""
    from ..models import UserAccessFlags
    # Real gap found in a full echomail-subsystem audit: download()
    # checks UserAccessFlags.no_qwk and blocks the download, but upload()
    # never checked it at all -- a user whose QWK access was suspended
    # could still upload REP replies and post messages.
    flags = UserAccessFlags.query.filter_by(user_id=current_user.id).first()
    if flags and flags.no_qwk:
        flash('Your QWK access has been suspended.', 'danger')
        return redirect(url_for('qwk_user.index'))

    upload = request.files.get('rep')
    if not upload or not upload.filename:
        flash('No .REP file selected.', 'danger')
        return redirect(url_for('qwk_user.index'))
    raw = upload.read()
    if not raw or len(raw) < 200:
        flash('REP packet looks empty/invalid.', 'danger')
        return redirect(url_for('qwk_user.index'))
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            target = next((n for n in z.namelist()
                           if n.upper().endswith('.MSG')
                           or n.upper() == 'MESSAGES.DAT'), None)
            if not target:
                flash('No MESSAGES.DAT in the REP.', 'danger')
                return redirect(url_for('qwk_user.index'))
            data = z.read(target)
    except Exception as exc:
        flash(f'Could not unzip: {exc}', 'danger')
        return redirect(url_for('qwk_user.index'))

    # Skip the first 128-byte filler header.
    if len(data) < 128:
        flash('Truncated REP packet.', 'danger')
        return redirect(url_for('qwk_user.index'))

    pos = 128
    imported = 0
    # Load area list once outside the loop — conf_num is a 1-based index
    # into the same ordered, access-filtered list _build_qwk_blob used
    # when producing the packet (see _qwk_accessible_areas()'s own
    # docstring for why this must match, not just filter is_active).
    areas = _qwk_accessible_areas(current_user)
    while pos + 128 <= len(data):
        header = data[pos:pos + 128]
        pos += 128
        try:
            num_blocks = int(header[116:122].decode('ascii', errors='replace').strip() or '1')
        except ValueError:
            num_blocks = 1
        body_blocks = max(0, num_blocks - 1)
        body = data[pos:pos + body_blocks * 128]
        pos += body_blocks * 128
        try:
            conf_num = struct.unpack('<H', header[123:125])[0]
        except struct.error:
            conf_num = 0
        area = areas[conf_num - 1] if 1 <= conf_num <= len(areas) else None
        if area is None:
            continue
        try:
            to_name = header[21:46].decode('cp437', errors='replace').rstrip()
            subject = header[71:96].decode('cp437', errors='replace').rstrip()
            # QWK body uses 0xE3 as the line separator (latin-1 decode preserves
            # the byte value; cp437 decode would turn it into π instead).
            body_text = (body.decode('latin-1', errors='replace')
                         .replace('\xe3', '\n')
                         .replace('\r\n', '\n')
                         .rstrip(' \x00\n'))
            # Real gap found in a full echomail-subsystem audit: this is
            # the one REP importer that does its own raw positional
            # parse instead of reusing qwk.py's _parse_messages_dat(),
            # so it never got the reply-threading extraction that
            # function's own _clean_body() step already does for the
            # other two importers (qwk_hub.py/qwk_hub_ftp.py) --
            # @REPLY:/@REPLYID:/@REPLYTO: kludge lines just stayed as
            # visible junk text at the top of the message instead of
            # setting reply_id. Reuses the same _clean_body() helper
            # rather than re-implementing the same parsing a third time.
            from ..echomail.qwk import _clean_body
            _cleaned = _clean_body(body_text)
            body_text = _cleaned['body']
            reply_id = _cleaned['reply_id']
            # Real bug found in a full echomail-subsystem audit: a bare
            # db.session.rollback() here rolls back the ENTIRE open
            # transaction, not just this one message -- so every message
            # already add()ed earlier in this SAME loop got silently
            # discarded the moment any LATER message in the same upload
            # hit an exception, while `imported` still reported success
            # for all of them. This is the exact bug already fixed in
            # qwk_hub_ftp.py's process_rep_upload() (see its own comment)
            # -- a third, independent REP importer that never got the
            # same fix. begin_nested() isolates each message's own
            # insert so a rollback only undoes that one row.
            with db.session.begin_nested():
                msg = EchomailMessage(
                    area_id=area.id,
                    network_id=area.network_id,
                    from_name=current_user.username,
                    to_name=to_name or 'All',
                    subject=subject or '(no subject)',
                    body=body_text,
                    reply_id=reply_id,
                    created_at=datetime.utcnow(),
                    direction='outbound',
                )
                db.session.add(msg)
            imported += 1
        except Exception:
            continue
    db.session.commit()
    flash(f'Imported {imported} reply message(s).', 'success')
    return redirect(url_for('qwk_user.index'))
