"""
Inter-BBS Instant Message blueprint.

Routes:
    /imsg/                 — user's inbox (received MSP messages)
    /imsg/send             — compose+send an MSP message to a remote BBS
    /imsg/<id>/read        — mark as read (AJAX)
    /imsg/<id>/delete      — delete (POST)
"""
from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, jsonify, current_app)
from flask_login import login_required, current_user
from wtforms import StringField, IntegerField, TextAreaField, SubmitField
from wtforms.validators import DataRequired, Length, NumberRange, Optional
from flask_wtf import FlaskForm

from ..models import db, InstantMessage
from ..msp.client import send_msp
from ..msp.protocol import MSP_DEFAULT_PORT
from ..features.rate_limit import rate_limit, _user_or_ip


imsg_bp = Blueprint('imsg', __name__, url_prefix='/imsg')


class SendForm(FlaskForm):
    # Either field can be empty if the other contains "user@host".
    host = StringField('Remote BBS Host',
                       validators=[Optional(), Length(max=120)])
    port = IntegerField('MSP Port', default=MSP_DEFAULT_PORT,
                        validators=[Optional(), NumberRange(1, 65535)])
    recipient = StringField('Recipient (username — or paste "user@host")',
                            validators=[DataRequired(), Length(max=255)])
    message = TextAreaField('Message',
                            validators=[DataRequired(), Length(max=8000)])
    submit = SubmitField('Send')


@imsg_bp.route('/')
@login_required
def inbox():
    msgs = (InstantMessage.query
            .filter_by(recipient_id=current_user.id)
            .order_by(InstantMessage.received_at.desc())
            .limit(200).all())
    # Mark anything that's been displayed once as read on the next visit
    return render_template('imsg/inbox.html', messages=msgs)


@imsg_bp.route('/send', methods=['GET', 'POST'])
@login_required
@rate_limit('imsg-send', limit=30, window=3600, key_fn=_user_or_ip)
def send():
    form = SendForm()
    # Pre-fill host (and optionally recipient) from query-string when the
    # user clicked "Send IM" from the directory or who-online page.
    if request.method == 'GET':
        host_q = (request.args.get('host') or '').strip()
        if host_q:
            form.host.data = host_q
        rcpt_q = (request.args.get('recipient') or '').strip()
        if rcpt_q:
            form.recipient.data = rcpt_q
    if form.validate_on_submit():
        # Sender = bare username (no @host) — Synchronet builds the reply
        # address from this and the peer IP, so any embedded @ produces
        # bogus reply destinations like "user@bbs@1.2.3.4".
        sender_label = current_user.username
        bbs_name = current_app.config.get('BBS_NAME', '')

        # Allow the user to paste "name@host" into either the recipient
        # field or the host field — split it out so we always end up with
        # a real recipient + a real host before calling send_msp.
        recipient_raw = (form.recipient.data or '').strip()
        host_raw = (form.host.data or '').strip()
        if '@' in recipient_raw and not host_raw:
            recipient_raw, host_raw = recipient_raw.rsplit('@', 1)
        elif '@' in recipient_raw and host_raw:
            # Recipient has @ but host already filled — trust the host field
            # and split the recipient at @ anyway (Synchronet only wants the
            # username on the wire).
            recipient_raw = recipient_raw.rsplit('@', 1)[0]
        elif '@' in host_raw:
            # User typed user@host into the host box by mistake.
            possible_user, possible_host = host_raw.rsplit('@', 1)
            if not recipient_raw:
                recipient_raw = possible_user
            host_raw = possible_host

        if not recipient_raw or not host_raw:
            flash('Need both a recipient and a host (or paste "user@host").',
                  'danger')
            return render_template('imsg/send.html', form=form)

        # Real name goes in sender_terminal; Synchronet's IMSG renders it
        # as the parenthesized name after the address. Fallback: username.
        real_name = (getattr(current_user, 'display_name', '')
                     or current_user.username)
        ok = send_msp(
            host=host_raw,
            port=form.port.data or MSP_DEFAULT_PORT,
            recipient=recipient_raw,
            message=form.message.data,
            sender=sender_label,
            sender_real_name=real_name,
            sender_system=bbs_name,
        )
        if ok:
            flash(f'Instant message sent to {recipient_raw} @ {host_raw}.',
                  'success')
            return redirect(url_for('imsg.inbox'))
        flash(f'Could not deliver to {recipient_raw}@{host_raw}:'
              f'{form.port.data or MSP_DEFAULT_PORT} — host unreachable, '
              'MSP service not running, or firewalled.', 'danger')
    return render_template('imsg/send.html', form=form)


@imsg_bp.route('/<int:msg_id>/read', methods=['POST'])
@login_required
def mark_read(msg_id):
    im = InstantMessage.query.get_or_404(msg_id)
    if im.recipient_id != current_user.id and not current_user.is_admin:
        return jsonify({'error': 'forbidden'}), 403
    im.is_read = True
    db.session.commit()
    return jsonify({'ok': True})


@imsg_bp.route('/<int:msg_id>/delete', methods=['POST'])
@login_required
def delete(msg_id):
    im = InstantMessage.query.get_or_404(msg_id)
    if im.recipient_id != current_user.id and not current_user.is_admin:
        flash('Not your message.', 'warning')
        return redirect(url_for('imsg.inbox'))
    db.session.delete(im)
    db.session.commit()
    flash('Deleted.', 'success')
    return redirect(url_for('imsg.inbox'))


# ---------------------------------------------------------------------------
# Directory (sbbsimsg.lst) — list of participating BBSes
# ---------------------------------------------------------------------------

@imsg_bp.route('/directory')
@login_required
def directory():
    """Show the inter-BBS directory pulled from sbbsimsg.lst + anetbbs.lst,
    with the local BBS always pinned at the top as a "Your BBS" card.

    The pin is independent of registry state — even if this BBS hasn't
    been approved on the federation hub yet, the sysop and users still
    see their own system listed."""
    from ..models import BbsDirectoryEntry
    q = (request.args.get('q') or '').strip().lower()
    rows = BbsDirectoryEntry.query
    if q:
        rows = rows.filter(db.or_(
            db.func.lower(BbsDirectoryEntry.hostname).like(f'%{q}%'),
            db.func.lower(BbsDirectoryEntry.name).like(f'%{q}%')))
    rows = rows.order_by(BbsDirectoryEntry.name).limit(500).all()

    # Build the "Your BBS" pin. Prefer BBS_DOMAIN config; fall back to the
    # Host header the request came in on so it works out-of-the-box on
    # installs where the sysop hasn't set BBS_DOMAIN yet.
    cfg = current_app.config
    own_host = (cfg.get('BBS_DOMAIN') or '').strip() or \
               (request.host.split(':', 1)[0] if request.host else '')
    self_entry = {
        'name': cfg.get('BBS_NAME') or 'ANetBBS',
        'hostname': own_host,
        'sysop': cfg.get('SYSOP_NAME') or '',
        'location': cfg.get('BBS_LOCATION') or '',
        'msp_port': cfg.get('MSP_PORT') or 18,
        'systat_port': cfg.get('SYSTAT_PORT') or 11,
        'software': 'ANetBBS',
    }

    # If the federation hub has us listed (RegistryEntry), reflect that
    # in the pin so the sysop can see they're already published.
    try:
        from ..models import RegistryEntry
        reg = (RegistryEntry.query
               .filter(db.func.lower(RegistryEntry.host) == own_host.lower())
               .first()) if own_host else None
        if reg is not None:
            self_entry['is_listed'] = bool(reg.is_listed)
            self_entry['is_verified'] = bool(reg.is_verified)
        else:
            self_entry['is_listed'] = None  # no registry row at all
            self_entry['is_verified'] = None
    except Exception:
        self_entry['is_listed'] = None
        self_entry['is_verified'] = None

    return render_template('imsg/directory.html',
                           rows=rows, q=q, self_entry=self_entry)


@imsg_bp.route('/directory/refresh', methods=['POST'])
@login_required
def directory_refresh():
    """Manually re-pull sbbsimsg.lst from the master URL."""
    if not current_user.is_admin:
        flash('Sysop only.', 'warning')
        return redirect(url_for('imsg.directory'))
    from ..msp.directory import refresh as refresh_dir
    n = refresh_dir(current_app._get_current_object())
    flash(f'Directory refreshed — {n} entries active.', 'success')
    return redirect(url_for('imsg.directory'))


@imsg_bp.route('/directory/<int:entry_id>/who')
@login_required
@rate_limit('imsg-directory-who', limit=20, window=3600, key_fn=_user_or_ip)
def directory_who(entry_id):
    """SYSTAT/Finger-over-UDP a remote BBS to see who's online.

    Rate-limited like send() -- this makes a real network round-trip
    (up to systat.py's own timeout) per request, so an unthrottled loop
    over it would tie up a worker per call; combined with the
    destination now being validated (query_systat()'s SSRF guard), the
    rate limit also bounds how fast someone can probe hosts even within
    the allowed (non-private) address space.
    """
    from ..models import BbsDirectoryEntry
    from ..msp.systat import query_systat
    entry = BbsDirectoryEntry.query.get_or_404(entry_id)
    text = query_systat(entry.hostname)
    if not text:
        # Fall back to the IP if the hostname doesn't resolve / replies
        if entry.ip_address:
            text = query_systat(entry.ip_address)
    return render_template('imsg/who.html', entry=entry, text=text)
