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


qwk_user_bp = Blueprint('qwk_user', __name__, url_prefix='/qwk')


def _last_read_map(user_id):
    """{(area_id) -> last EchomailMessage.id read}."""
    out = {}
    try:
        for r in EchomailLastRead.query.filter_by(user_id=user_id).all():
            out[r.area_id] = r.last_read_id or 0
    except Exception:
        db.session.rollback()
    return out


def _build_qwk_blob(user):
    """Construct a minimal QWK packet (CONTROL.DAT + MESSAGES.DAT zipped).
    Format follows the original PCBoard / qwk-classic layout.
    """
    bbs_name = os.environ.get('BBS_NAME', 'ANetBBS')
    sysop = os.environ.get('SYSOP_NAME', 'Sysop')

    # Active areas user is subscribed to (or all active if no filter).
    areas = EchoArea.query.filter_by(is_active=True).all()
    last_read = _last_read_map(user.id)

    # Conference list — one numeric id per area.
    conf_lines = []
    conf_lookup = {}
    for i, area in enumerate(areas, start=1):
        conf_lines.append(str(i))
        conf_lines.append((area.tag or area.name or f'AREA{i}')[:13])
        conf_lookup[area.id] = i

    control_dat_lines = [
        bbs_name,
        '',                        # city
        '',                        # phone
        sysop,
        '00000,' + bbs_name[:13],
        datetime.utcnow().strftime('%m-%d-%Y,%H:%M:%S'),
        user.username,
        '',                        # menu name
        '0',                       # not used
        str(len(areas)),           # number of confs - 1
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
            body = (m.body or '').replace('\n', '\r\n')
            body_bytes = body.encode('cp437', errors='replace')
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
            msg_left = struct.pack('<H', 1)
            net_tag = b' '
            header = (status + number + date + time + to + sender + subject +
                      password + ref + num_blocks + active + conf + msg_left + net_tag)
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
            names = [n.upper() for n in z.namelist()]
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
        # Look up the area by conference number — the user's same area
        # listing as in download(); index 1 = first active area, etc.
        areas = EchoArea.query.filter_by(is_active=True).all()
        area = areas[conf_num - 1] if 1 <= conf_num <= len(areas) else None
        if area is None:
            continue
        try:
            to_name = header[21:46].decode('cp437', errors='replace').rstrip()
            subject = header[71:96].decode('cp437', errors='replace').rstrip()
            body_text = body.decode('cp437', errors='replace').rstrip(' \x00').replace('\r\n', '\n')
            msg = EchomailMessage(
                area_id=area.id,
                from_name=current_user.username,
                to_name=to_name or 'All',
                subject=subject or '(no subject)',
                body=body_text,
                created_at=datetime.utcnow(),
                direction='outbound',
            )
            db.session.add(msg)
            imported += 1
        except Exception:
            db.session.rollback()
            continue
    db.session.commit()
    flash(f'Imported {imported} reply message(s).', 'success')
    return redirect(url_for('qwk_user.index'))
