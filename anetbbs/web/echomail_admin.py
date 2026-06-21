# anetbbs/web/echomail_admin.py
"""
Admin blueprint for echomail network and area management.
All routes require login + admin access.
"""
import threading

from flask import Blueprint, render_template, redirect, url_for, flash, request, abort
from flask_login import login_required, current_user
from wtforms import (StringField, TextAreaField, SubmitField, SelectField,
                     IntegerField, BooleanField, PasswordField)
from wtforms.validators import DataRequired, Length, Optional, NumberRange
from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileAllowed

from ..models import (db, EchomailNetwork, EchoArea, EchomailMessage,
                       EchomailPollLog, BadAreaLog)

echomail_admin_bp = Blueprint('echomail_admin', __name__, url_prefix='/admin/echomail')


def _admin_required(f):
    """Decorator that requires admin access."""
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Forms
# ---------------------------------------------------------------------------

class NetworkForm(FlaskForm):
    name = StringField('Network Name', validators=[DataRequired(), Length(max=100)])
    network_type = SelectField('Type', choices=[('binkp', 'BinkP (FidoNet)'), ('qwk', 'QWK (Dove-Net)')],
                               validators=[DataRequired()])
    description = TextAreaField('Description', validators=[Optional()])

    # BinkP fields
    binkp_host = StringField('BinkP Host', validators=[Optional(), Length(max=255)])
    binkp_port = IntegerField('BinkP Port', validators=[Optional(), NumberRange(1, 65535)],
                              default=24554)
    binkp_password = PasswordField('BinkP Password', validators=[Optional()])
    areafix_password = PasswordField(
        'AreaFix Password (leave blank to reuse BinkP password)',
        validators=[Optional()])
    our_address = StringField('Our FTN Address (e.g. 1:234/567)',
                              validators=[Optional(), Length(max=50)])
    hub_address = StringField('Hub FTN Address', validators=[Optional(), Length(max=50)])
    cram_md5 = BooleanField(
        'Use CRAM-MD5 for BinkP auth (recommended — required by Synchronet)',
        default=True)
    binkp_tls = BooleanField('Use TLS for outbound BinkP', default=False)
    default_recipient = StringField(
        'Default Netmail Recipient (BBS user that catches netmail addressed '
        'to unknown names)',
        validators=[Optional(), Length(max=100)])

    # QWK fields
    qwk_host = StringField('QWK Host', validators=[Optional(), Length(max=255)])
    qwk_port = IntegerField('QWK Port', validators=[Optional(), NumberRange(1, 65535)], default=80)
    qwk_username = StringField('QWK Username', validators=[Optional(), Length(max=255)])
    qwk_password = PasswordField('QWK Password', validators=[Optional()])
    qwk_packet_id = StringField('QWK Packet ID — your local BBS sys ID '
                                 '(e.g. ANET)',
                                 validators=[Optional(), Length(max=20)])
    qwk_hub_id = StringField('QWK Hub System ID — the hub\'s sys ID '
                              '(e.g. VERT for DOVE-Net)',
                              validators=[Optional(), Length(max=16)])
    qwk_download_url = StringField(
        'QWK Download URL (full URL — overrides the default. '
        'Placeholders: {host} {port} {user} {password} {packet} {hub_id}. '
        'For DOVE-Net leave blank — auto-defaults to '
        'ftp://dove.synchro.net/{hub_id}.qwk)',
        validators=[Optional(), Length(max=500)])
    qwk_upload_url = StringField(
        'QWK Upload URL (full URL for the REP packet)',
        validators=[Optional(), Length(max=500)])

    poll_interval_minutes = IntegerField('Poll Interval (minutes)',
                                         validators=[Optional(), NumberRange(1, 10080)],
                                         default=60)
    is_active = BooleanField('Active', default=True)
    submit = SubmitField('Save Network')


class EchoAreaForm(FlaskForm):
    network_id = SelectField('Network', coerce=int, validators=[DataRequired()])
    tag = StringField('Area Tag (e.g. FIDO_GENERAL)', validators=[DataRequired(), Length(max=100)])
    name = StringField('Display Name', validators=[DataRequired(), Length(max=200)])
    description = TextAreaField('Description', validators=[Optional()])
    order = IntegerField('Sort Order', validators=[Optional()], default=0)
    is_active = BooleanField('Active', default=True)
    is_subscribed = BooleanField('Subscribed', default=True)
    is_sysop_only = BooleanField('Sysop Only', default=False)
    min_access_level = IntegerField(
        'Min Access Level (0=all users, 10=registered, 50=VIP, 100=sysop)',
        validators=[Optional(), NumberRange(min=0, max=255)], default=10)
    submit = SubmitField('Save Area')


class BulkImportForm(FlaskForm):
    network_id = SelectField('Network', coerce=int, validators=[DataRequired()])
    echolist = TextAreaField(
        'Echo List / Backbone (one per line: TAG  [- ] Display Name)',
        validators=[Optional()])
    backbone_file = FileField(
        'Or upload a backbone file (tqwnet.na, BACKBONE.NA, FILEBONE.NA, …)',
        validators=[Optional(),
                    FileAllowed(['na', 'NA', 'txt', 'lst', 'echo'],
                                'Plain text/.na/.lst only')])
    is_subscribed = BooleanField(
        'Mark imported areas as subscribed (uncheck for backbone catalog '
        'imports — you can subscribe individually later)', default=False)
    submit = SubmitField('Import Areas')


def _parse_backbone(text):
    """Yield (tag, name) tuples from echolist / backbone text.

    Accepts both forms:
        TAG  Display Name
        TAG  - Display Name      (FidoNet backbone style — tqwnet.na, BACKBONE.NA)

    Lines starting with ;, # or blank lines are skipped.
    Tag is uppercased; leading '- ' on the description is stripped.
    """
    out = []
    for line in (text or '').splitlines():
        line = line.strip()
        if not line or line[0] in ';#':
            continue
        parts = line.split(None, 1)
        tag = parts[0].upper()
        if not tag.replace('.', '').replace('_', '').replace('-', '').isalnum():
            continue       # skip junk lines
        name = parts[1].strip() if len(parts) > 1 else tag
        if name.startswith('- '):
            name = name[2:].strip()
        elif name.startswith('-'):
            name = name[1:].strip()
        out.append((tag, name or tag))
    return out


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@echomail_admin_bp.route('/')
@login_required
@_admin_required
def dashboard():
    total_networks = EchomailNetwork.query.count()
    total_areas = EchoArea.query.count()
    total_messages = EchomailMessage.query.count()
    pending_outbound = EchomailMessage.query.filter_by(direction='outbound', sent_at=None).count()
    recent_logs = EchomailPollLog.query.order_by(EchomailPollLog.started_at.desc()).limit(10).all()
    return render_template('echomail/admin/dashboard.html',
                           total_networks=total_networks,
                           total_areas=total_areas,
                           total_messages=total_messages,
                           pending_outbound=pending_outbound,
                           recent_logs=recent_logs)


# ---------------------------------------------------------------------------
# Network management
# ---------------------------------------------------------------------------

@echomail_admin_bp.route('/networks')
@login_required
@_admin_required
def networks():
    all_networks = EchomailNetwork.query.order_by(EchomailNetwork.name).all()
    return render_template('echomail/admin/networks.html', networks=all_networks)


@echomail_admin_bp.route('/networks/new', methods=['GET', 'POST'])
@login_required
@_admin_required
def new_network():
    form = NetworkForm()
    if form.validate_on_submit():
        network = EchomailNetwork(
            name=form.name.data,
            network_type=form.network_type.data,
            description=form.description.data,
            binkp_host=form.binkp_host.data or None,
            binkp_port=form.binkp_port.data or 24554,
            binkp_password=form.binkp_password.data or None,
            areafix_password=form.areafix_password.data or None,
            our_address=form.our_address.data or None,
            hub_address=form.hub_address.data or None,
            qwk_host=form.qwk_host.data or None,
            qwk_port=form.qwk_port.data or None,
            qwk_username=form.qwk_username.data or None,
            qwk_password=form.qwk_password.data or None,
            qwk_packet_id=form.qwk_packet_id.data or None,
            qwk_hub_id=form.qwk_hub_id.data or None,
            qwk_download_url=form.qwk_download_url.data or None,
            qwk_upload_url=form.qwk_upload_url.data or None,
            poll_interval_minutes=form.poll_interval_minutes.data or 60,
            is_active=form.is_active.data,
            cram_md5=form.cram_md5.data,
            binkp_tls=form.binkp_tls.data,
            default_recipient=form.default_recipient.data or None,
        )
        db.session.add(network)
        db.session.commit()
        flash(f'Network "{network.name}" created.', 'success')
        return redirect(url_for('echomail_admin.networks'))
    return render_template('echomail/admin/network_form.html', form=form, network=None)


@echomail_admin_bp.route('/networks/<int:network_id>/edit', methods=['GET', 'POST'])
@login_required
@_admin_required
def edit_network(network_id):
    network = EchomailNetwork.query.get_or_404(network_id)
    form = NetworkForm(obj=network)
    if form.validate_on_submit():
        # PasswordField never round-trips the stored value, so populate_obj
        # would clear any password the user didn't re-type. Preserve the
        # existing values when the field is blank.
        prior_binkp = network.binkp_password
        prior_areafix = network.areafix_password
        prior_qwk = network.qwk_password
        form.populate_obj(network)
        if not (form.binkp_password.data or '').strip():
            network.binkp_password = prior_binkp
        if not (form.areafix_password.data or '').strip():
            network.areafix_password = prior_areafix
        if not (form.qwk_password.data or '').strip():
            network.qwk_password = prior_qwk
        db.session.commit()
        flash(f'Network "{network.name}" updated.', 'success')
        return redirect(url_for('echomail_admin.networks'))
    return render_template('echomail/admin/network_form.html', form=form, network=network)


@echomail_admin_bp.route('/networks/<int:network_id>/delete', methods=['POST'])
@login_required
@_admin_required
def delete_network(network_id):
    network = EchomailNetwork.query.get_or_404(network_id)
    name = network.name
    db.session.delete(network)
    db.session.commit()
    flash(f'Network "{name}" deleted.', 'success')
    return redirect(url_for('echomail_admin.networks'))


@echomail_admin_bp.route('/networks/<int:network_id>/poll', methods=['POST'])
@login_required
@_admin_required
def poll_now(network_id):
    """Trigger an immediate poll for a network (runs in a background thread)."""
    from flask import current_app
    app = current_app._get_current_object()

    def run():
        from ..echomail.poller import poll_network_now
        try:
            poll_network_now(app, network_id)
        except Exception as exc:
            app.logger.error("Manual poll error: %s", exc)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    flash('Poll triggered — check poll logs for results.', 'info')
    return redirect(url_for('echomail_admin.networks'))


@echomail_admin_bp.route('/networks/<int:network_id>/test', methods=['POST'])
@login_required
@_admin_required
def test_connection(network_id):
    """Test connectivity to a network hub. For QWK over FTP, log in and
    LIST the working directory so sysop can see the actual file paths."""
    network = EchomailNetwork.query.get_or_404(network_id)
    try:
        if network.network_type == 'binkp':
            import socket
            s = socket.create_connection((network.binkp_host, network.binkp_port or 24554),
                                         timeout=10)
            s.close()
            flash(f'BinkP connection to {network.binkp_host}:{network.binkp_port} succeeded.', 'success')
        elif network.network_type == 'qwk':
            # Detect FTP from the URL fields. If either is FTP, do a real
            # FTP login + LIST and show the result so the sysop can see
            # the real path layout on the hub.
            urls = [(network.qwk_download_url or ''),
                    (network.qwk_upload_url or '')]
            is_ftp = any(u.startswith(('ftp://', 'ftps://')) for u in urls)
            if not is_ftp and (network.qwk_host or '').lower() in (
                    'dove.synchro.net', 'vert.synchro.net'):
                is_ftp = True   # DOVE-Net is always FTP

            if is_ftp:
                import ftplib
                from urllib.parse import urlparse, unquote
                # Pull host/user/pass from the URL if present, else from
                # the form fields.
                src = next((u for u in urls if u), '')
                p = urlparse(src) if src else None
                host = (p.hostname if p else '') or network.qwk_host
                port = (p.port if p else None) or network.qwk_port or 21
                user = (unquote(p.username) if p and p.username
                        else network.qwk_username) or 'anonymous'
                pw = (unquote(p.password) if p and p.password
                      else network.qwk_password) or ''
                scheme = (p.scheme if p else 'ftp') or 'ftp'
                ftp_cls = ftplib.FTP_TLS if scheme == 'ftps' else ftplib.FTP
                with ftp_cls() as ftp:
                    ftp.connect(host, port, timeout=15)
                    ftp.login(user, pw)
                    if scheme == 'ftps':
                        try:
                            ftp.prot_p()
                        except Exception:
                            pass
                    listing = []
                    try:
                        ftp.cwd('/')
                        listing.append('/  -> ' + ', '.join(ftp.nlst()[:25]))
                    except Exception as exc:
                        listing.append(f'/  -> error: {exc}')
                    # Try the most common QWK paths
                    for path in ('qnet', f'qnet/{network.qwk_packet_id or ""}'):
                        if not path or path.endswith('/'):
                            continue
                        try:
                            files = ftp.nlst(path)
                            listing.append(f'{path}/  -> ' + ', '.join(files[:25]))
                        except Exception:
                            pass
                flash('FTP login OK as ' + user + ' @ ' + host
                      + ':' + str(port) + '. ' + '  ;  '.join(listing),
                      'success')
            else:
                import urllib.request
                url = f'http://{network.qwk_host}:{network.qwk_port or 80}/'
                urllib.request.urlopen(url, timeout=10)
                flash(f'QWK HTTP reachable: {network.qwk_host}:{network.qwk_port}.',
                      'success')
    except Exception as exc:
        flash(f'Connection test failed: {exc}', 'danger')
    return redirect(url_for('echomail_admin.networks'))


# ---------------------------------------------------------------------------
# Echo area management
# ---------------------------------------------------------------------------

@echomail_admin_bp.route('/areas')
@login_required
@_admin_required
def areas():
    all_areas = EchoArea.query.join(EchomailNetwork)\
        .order_by(EchomailNetwork.name, EchoArea.order, EchoArea.name).all()
    all_networks = EchomailNetwork.query.order_by(EchomailNetwork.name).all()
    return render_template('echomail/admin/areas.html',
                           areas=all_areas, all_networks=all_networks)


@echomail_admin_bp.route('/areas/new', methods=['GET', 'POST'])
@login_required
@_admin_required
def new_area():
    form = EchoAreaForm()
    form.network_id.choices = [(n.id, n.name) for n in EchomailNetwork.query.all()]
    if form.validate_on_submit():
        area = EchoArea(
            network_id=form.network_id.data,
            tag=form.tag.data,
            name=form.name.data,
            description=form.description.data,
            order=form.order.data or 0,
            is_active=form.is_active.data,
            is_subscribed=form.is_subscribed.data,
            is_sysop_only=form.is_sysop_only.data,
            min_access_level=form.min_access_level.data if form.min_access_level.data is not None else 10,
        )
        db.session.add(area)
        db.session.commit()
        flash(f'Echo area "{area.name}" created.', 'success')
        return redirect(url_for('echomail_admin.areas'))
    return render_template('echomail/admin/area_form.html', form=form, area=None)


@echomail_admin_bp.route('/areas/<int:area_id>/edit', methods=['GET', 'POST'])
@login_required
@_admin_required
def edit_area(area_id):
    area = EchoArea.query.get_or_404(area_id)
    form = EchoAreaForm(obj=area)
    form.network_id.choices = [(n.id, n.name) for n in EchomailNetwork.query.all()]
    if form.validate_on_submit():
        form.populate_obj(area)
        db.session.commit()
        flash(f'Echo area "{area.name}" updated.', 'success')
        return redirect(url_for('echomail_admin.areas'))
    return render_template('echomail/admin/area_form.html', form=form, area=area)


@echomail_admin_bp.route('/areas/<int:area_id>/delete', methods=['POST'])
@login_required
@_admin_required
def delete_area(area_id):
    area = EchoArea.query.get_or_404(area_id)
    name = area.name
    db.session.delete(area)
    db.session.commit()
    flash(f'Echo area "{name}" deleted.', 'success')
    return redirect(url_for('echomail_admin.areas'))


@echomail_admin_bp.route('/areas/<int:area_id>/toggle_subscribe', methods=['POST'])
@login_required
@_admin_required
def toggle_subscribe(area_id):
    area = EchoArea.query.get_or_404(area_id)
    area.is_subscribed = not area.is_subscribed
    db.session.commit()
    state = 'subscribed' if area.is_subscribed else 'unsubscribed'
    flash(f'Area "{area.name}" {state}.', 'success')
    return redirect(url_for('echomail_admin.areas'))


@echomail_admin_bp.route('/areas/<int:area_id>/rescan', methods=['POST'])
@login_required
@_admin_required
def rescan_area(area_id):
    """Queue an AreaFix `%RESCAN <tag>` netmail to the area's network hub.

    Pulls the full backlog of an already-subscribed area on the next BinkP
    poll. Useful when joining an existing subscription for the first time
    or after losing local messages.
    """
    area = EchoArea.query.get_or_404(area_id)
    network = area.network
    if not network or network.network_type != 'binkp':
        flash('%RESCAN is only supported on BinkP networks.', 'warning')
        return redirect(url_for('echomail_admin.areas'))
    from ..echomail.areafix import send_areafix_request
    nm_id = send_areafix_request(
        network,
        rescan_tags=[area.tag],
        subject=f'Rescan {area.tag}',
    )
    if nm_id:
        flash(
            f'Queued AreaFix %RESCAN {area.tag} netmail to '
            f'{network.hub_address}. Click "Poll now" on the network '
            f'page to ship it.',
            'success')
    else:
        flash(
            'Could not queue %RESCAN — check the network has a hub_address '
            'and binkp_password configured.',
            'danger')
    return redirect(url_for('echomail_admin.areas'))


@echomail_admin_bp.route('/networks/<int:network_id>/subscribe_all', methods=['POST'])
@login_required
@_admin_required
def subscribe_all(network_id):
    """Mark every EchoArea on this network as locally subscribed AND queue
    one or more AreaFix netmails subscribing to each known tag.

    Note: Mystic (and many hubs) do NOT honor `+ALL` as a magic keyword —
    it gets treated as a literal area named "ALL" and rejected. So we
    send `+TAG1 +TAG2 +TAG3...` for every locally-known tag instead.
    Run `Bulk Import` first to populate the list from a backbone file."""
    network = EchomailNetwork.query.get_or_404(network_id)
    if network.network_type != 'binkp':
        flash('Subscribe-all only applies to BinkP networks.', 'warning')
        return redirect(url_for('echomail_admin.areas'))
    tags = [a.tag for a in
            EchoArea.query.filter_by(network_id=network.id).all()]
    n_local = EchoArea.query.filter_by(network_id=network.id).update(
        {EchoArea.is_subscribed: True}, synchronize_session=False)
    db.session.commit()
    if not tags:
        flash('No local areas configured for this network. Run '
              '"Bulk Import" first to load a backbone file.', 'warning')
        return redirect(url_for('echomail_admin.areas'))
    from ..echomail.areafix import send_areafix_request
    nm_id = send_areafix_request(network, plus_tags=tags,
                                  subject='AreaFix request')
    if nm_id:
        flash(f'Marked {n_local} local areas subscribed; queued AreaFix '
              f'request with {len(tags)} +TAG entries to '
              f'{network.hub_address} (netmail #{nm_id}).', 'success')
    else:
        flash(f'Marked {n_local} local areas subscribed, but could not queue '
              f'AreaFix request (no hub address or no password configured).',
              'warning')
    return redirect(url_for('echomail_admin.areas'))


@echomail_admin_bp.route('/networks/<int:network_id>/custom_areafix', methods=['POST'])
@login_required
@_admin_required
def custom_areafix(network_id):
    """Queue an AreaFix netmail with a verbatim body the sysop typed.

    Lets the sysop send any AreaFix-spec body — `+TAG`, `-TAG`,
    `%RESCAN <tag>`, `%RESCAN <tag> D=3650`, `%QUERY`, `%LIST`,
    `%COMPRESS GZIP`, etc. Each line becomes one body line as-is.
    Subject is the AreaFix password (FTS-0024). To-name is `AreaFix`
    (override with `?robot=FileFix` if needed for FileFix flow)."""
    from ..models import NetmailMessage
    import datetime as _dt
    network = EchomailNetwork.query.get_or_404(network_id)
    if network.network_type != 'binkp':
        flash('Custom AreaFix only applies to BinkP networks.', 'warning')
        return redirect(url_for('echomail_admin.areas'))
    body = (request.form.get('body') or '').strip()
    if not body:
        flash('Type one or more AreaFix command lines into the body field.',
              'warning')
        return redirect(url_for('echomail_admin.areas'))
    if not network.hub_address:
        flash('Network has no hub_address configured.', 'danger')
        return redirect(url_for('echomail_admin.areas'))
    af_pw = (getattr(network, 'areafix_password', None) or '').strip()
    if not af_pw:
        af_pw = (getattr(network, 'binkp_password', None) or '').strip()
    robot = (request.form.get('robot') or 'AreaFix').strip() or 'AreaFix'
    nm = NetmailMessage(
        network_id=network.id,
        from_address=network.our_address,
        to_address=network.hub_address,
        from_name='Sysop',
        to_name=robot,
        subject=af_pw or 'AreaFix request',
        body=body,
        direction='outbound',
        status='queued',
        chrs='UTF-8 4',
        is_local=True,
        created_at=_dt.datetime.utcnow(),
    )
    db.session.add(nm)
    db.session.commit()
    flash(f'Queued custom {robot} netmail (#{nm.id}) to {network.hub_address}. '
          f'{len(body.splitlines())} body line(s).', 'success')
    return redirect(url_for('echomail_admin.areas'))


@echomail_admin_bp.route('/networks/<int:network_id>/rescan_all', methods=['POST'])
@login_required
@_admin_required
def rescan_all(network_id):
    """Queue a `%RESCAN <TAG>` AreaFix request for every locally-subscribed
    EchoArea on this network. Useful right after a fresh subscribe to pull
    historical posts the hub may still hold for our point."""
    network = EchomailNetwork.query.get_or_404(network_id)
    if network.network_type != 'binkp':
        flash('Rescan-all only applies to BinkP networks.', 'warning')
        return redirect(url_for('echomail_admin.areas'))
    tags = [a.tag for a in
            EchoArea.query.filter_by(
                network_id=network.id, is_subscribed=True).all()]
    if not tags:
        flash('No subscribed areas on this network — nothing to rescan.',
              'warning')
        return redirect(url_for('echomail_admin.areas'))
    from ..echomail.areafix import send_areafix_request
    nm_id = send_areafix_request(network, rescan_tags=tags,
                                  subject='AreaFix request')
    if nm_id:
        flash(f'Queued AreaFix %RESCAN for {len(tags)} subscribed areas '
              f'on {network.hub_address} (netmail #{nm_id}).', 'success')
    else:
        flash('Could not queue rescan AreaFix request (no hub address or '
              'no password configured).', 'warning')
    return redirect(url_for('echomail_admin.areas'))


@echomail_admin_bp.route('/networks/<int:network_id>/unsubscribe_all', methods=['POST'])
@login_required
@_admin_required
def unsubscribe_all(network_id):
    """Mark every EchoArea on this network as locally unsubscribed AND queue
    AreaFix `-TAG1 -TAG2...` for each currently-known tag.

    Mystic does not honor `-ALL` as a magic keyword either — it becomes a
    literal "unsubscribe area ALL" lookup and silently no-ops."""
    network = EchomailNetwork.query.get_or_404(network_id)
    if network.network_type != 'binkp':
        flash('Unsubscribe-all only applies to BinkP networks.', 'warning')
        return redirect(url_for('echomail_admin.areas'))
    tags = [a.tag for a in
            EchoArea.query.filter_by(network_id=network.id).all()]
    n_local = EchoArea.query.filter_by(network_id=network.id).update(
        {EchoArea.is_subscribed: False}, synchronize_session=False)
    db.session.commit()
    if not tags:
        flash('No local areas to unsubscribe from.', 'info')
        return redirect(url_for('echomail_admin.areas'))
    from ..echomail.areafix import send_areafix_request
    nm_id = send_areafix_request(network, minus_tags=tags,
                                  subject='AreaFix request')
    flash(f'Marked {n_local} local areas unsubscribed; queued AreaFix '
          f'request with {len(tags)} -TAG entries (netmail #{nm_id or "n/a"}).',
          'success')
    return redirect(url_for('echomail_admin.areas'))


@echomail_admin_bp.route('/areas/bulk_import', methods=['GET', 'POST'])
@login_required
@_admin_required
def bulk_import():
    form = BulkImportForm()
    form.network_id.choices = [(n.id, n.name) for n in EchomailNetwork.query.all()]
    if form.validate_on_submit():
        text = form.echolist.data or ''
        upload = form.backbone_file.data
        if upload and upload.filename:
            try:
                raw = upload.read()
                # backbone files are usually CP437 or ASCII; tolerate both
                try:
                    text = (text + '\n' + raw.decode('utf-8')).strip()
                except UnicodeDecodeError:
                    text = (text + '\n' + raw.decode('cp437', errors='replace')).strip()
            except Exception as exc:
                flash(f'Could not read uploaded file: {exc}', 'danger')
                return render_template('echomail/admin/bulk_import.html', form=form)
        if not text.strip():
            flash('Paste an echo list or upload a backbone file.', 'warning')
            return render_template('echomail/admin/bulk_import.html', form=form)

        entries = _parse_backbone(text)
        imported = 0
        skipped = 0
        for tag, name in entries:
            existing = EchoArea.query.filter_by(
                network_id=form.network_id.data, tag=tag).first()
            if existing:
                skipped += 1
                continue
            area = EchoArea(
                network_id=form.network_id.data,
                tag=tag,
                name=name,
                is_subscribed=bool(form.is_subscribed.data),
            )
            db.session.add(area)
            imported += 1
        db.session.commit()
        flash(f'Imported {imported} areas, skipped {skipped} duplicates.',
              'success')
        return redirect(url_for('echomail_admin.areas'))
    return render_template('echomail/admin/bulk_import.html', form=form)


# ---------------------------------------------------------------------------
# Poll logs
# ---------------------------------------------------------------------------

@echomail_admin_bp.route('/logs')
@login_required
@_admin_required
def logs():
    page = request.args.get('page', 1, type=int)
    network_filter = request.args.get('network_id', type=int)

    query = EchomailPollLog.query
    if network_filter:
        query = query.filter_by(network_id=network_filter)

    pagination = query.order_by(EchomailPollLog.started_at.desc())\
        .paginate(page=page, per_page=50, error_out=False)

    all_networks = EchomailNetwork.query.order_by(EchomailNetwork.name).all()
    return render_template('echomail/admin/logs.html',
                           logs=pagination.items,
                           pagination=pagination,
                           all_networks=all_networks,
                           network_filter=network_filter)


# ---------------------------------------------------------------------------
# Bad areas (echomail received for areas we don't carry)
# ---------------------------------------------------------------------------

@echomail_admin_bp.route('/bad_areas')
@login_required
@_admin_required
def bad_areas():
    """Sysop review queue for unknown echo tags arriving from peers."""
    rows = (BadAreaLog.query
            .order_by(BadAreaLog.last_seen_at.desc())
            .limit(500).all())
    networks = {n.id: n for n in EchomailNetwork.query.all()}
    return render_template('echomail/admin/bad_areas.html',
                           rows=rows, networks=networks)


@echomail_admin_bp.route('/bad_areas/<int:bad_id>/subscribe', methods=['POST'])
@login_required
@_admin_required
def bad_area_subscribe(bad_id):
    """Promote a bad-area log entry into a real subscribed echo area, and
    queue an outbound AreaFix +TAG request to the hub so it actually starts
    sending us that area on subsequent polls. The local subscription alone
    isn't enough — the hub has to be told too."""
    row = BadAreaLog.query.get_or_404(bad_id)
    network = EchomailNetwork.query.get(row.network_id)
    tag = row.tag

    existing = EchoArea.query.filter_by(network_id=row.network_id,
                                         tag=tag).first()
    if existing:
        existing.is_subscribed = True
        existing.is_active = True
    else:
        db.session.add(EchoArea(
            network_id=row.network_id,
            tag=tag,
            name=tag,
            is_subscribed=True,
            is_active=True,
        ))
    db.session.delete(row)
    db.session.commit()

    # Queue the AreaFix +TAG request to the hub.
    af_msg = ''
    if network and network.network_type == 'binkp':
        from ..echomail.areafix import send_areafix_request
        nm_id = send_areafix_request(network, plus_tags=[tag])
        if nm_id:
            af_msg = ' AreaFix +' + tag + ' netmail queued for the hub.'
        else:
            af_msg = (' (no AreaFix request sent — set hub_address + '
                      'BinkP password on the network to enable.)')
    flash(f'Subscribed to "{tag}".{af_msg}', 'success')
    return redirect(url_for('echomail_admin.bad_areas'))


@echomail_admin_bp.route('/bad_areas/<int:bad_id>/dismiss', methods=['POST'])
@login_required
@_admin_required
def bad_area_dismiss(bad_id):
    """Drop a bad-area log entry without subscribing."""
    row = BadAreaLog.query.get_or_404(bad_id)
    tag = row.tag
    db.session.delete(row)
    db.session.commit()
    flash(f'Dismissed "{tag}".', 'info')
    return redirect(url_for('echomail_admin.bad_areas'))


@echomail_admin_bp.route('/bad_areas/clear_all', methods=['POST'])
@login_required
@_admin_required
def bad_areas_clear_all():
    BadAreaLog.query.delete()
    db.session.commit()
    flash('Cleared all bad-area entries.', 'info')
    return redirect(url_for('echomail_admin.bad_areas'))


@echomail_admin_bp.route('/networks/<int:network_id>/areafix', methods=['POST'])
@login_required
@_admin_required
def network_areafix(network_id):
    """Send an AreaFix +/- request to the hub for arbitrary tags entered
    by the sysop. Used to subscribe to areas that haven't shown up in the
    bad-area log yet, or to unsubscribe from existing ones at the hub side.
    """
    network = EchomailNetwork.query.get_or_404(network_id)
    plus_raw = (request.form.get('plus_tags') or '').strip()
    minus_raw = (request.form.get('minus_tags') or '').strip()

    plus_tags = [t for t in plus_raw.replace(',', ' ').split() if t]
    minus_tags = [t for t in minus_raw.replace(',', ' ').split() if t]
    if not plus_tags and not minus_tags:
        flash('Enter at least one + or - tag.', 'warning')
        return redirect(url_for('echomail_admin.areas'))

    if network.network_type != 'binkp':
        flash('AreaFix requests are only supported on BinkP networks.', 'warning')
        return redirect(url_for('echomail_admin.areas'))

    from ..echomail.areafix import send_areafix_request
    nm_id = send_areafix_request(network,
                                  plus_tags=plus_tags,
                                  minus_tags=minus_tags)
    if nm_id:
        # Local-side: also create the EchoArea rows for + tags so the
        # importer accepts the messages once they start arriving.
        for tag in plus_tags:
            tag_up = tag.upper()
            existing = EchoArea.query.filter_by(network_id=network.id,
                                                 tag=tag_up).first()
            if existing:
                existing.is_subscribed = True
                existing.is_active = True
            else:
                db.session.add(EchoArea(network_id=network.id,
                                         tag=tag_up, name=tag_up,
                                         is_subscribed=True, is_active=True))
        for tag in minus_tags:
            existing = EchoArea.query.filter_by(network_id=network.id,
                                                 tag=tag.upper()).first()
            if existing:
                existing.is_subscribed = False
        db.session.commit()
        flash(f'AreaFix request queued: +{len(plus_tags)} -{len(minus_tags)} '
              'tags. Next BinkP poll delivers it.', 'success')
    else:
        flash('Could not queue AreaFix request — check hub address and password.',
              'danger')
    return redirect(url_for('echomail_admin.areas'))


@echomail_admin_bp.route('/networks/<int:network_id>/qwk_quick_add',
                          methods=['POST'])
@login_required
@_admin_required
def qwk_quick_add(network_id):
    """Bulk-add QWK areas given a list of `<conf_num> <name>` lines.

    Pre-creates EchoArea rows so when QWK messages arrive with that
    conf_num the importer routes them to a properly-named area without
    waiting for CONTROL.DAT to land.
    """
    network = EchomailNetwork.query.get_or_404(network_id)
    if network.network_type != 'qwk':
        flash('Quick-add is for QWK networks only.', 'warning')
        return redirect(url_for('echomail_admin.areas'))

    text = request.form.get('entries', '') or ''
    added = 0
    updated = 0
    skipped = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        # Accept either "2001 General" or "2001,General" or "2001\tGeneral"
        parts = line.replace(',', ' ').replace('\t', ' ').split(None, 1)
        try:
            conf_num = int(parts[0])
        except (ValueError, IndexError):
            skipped += 1
            continue
        name = parts[1].strip() if len(parts) > 1 else f'Conference {conf_num}'
        tag = f'QWK_{conf_num}'
        existing = EchoArea.query.filter_by(network_id=network.id,
                                             tag=tag).first()
        if existing:
            existing.name = name
            existing.is_subscribed = True
            existing.is_active = True
            updated += 1
        else:
            db.session.add(EchoArea(
                network_id=network.id, tag=tag, name=name,
                is_subscribed=True, is_active=True,
            ))
            added += 1
    db.session.commit()
    flash(f'QWK areas: added {added}, updated {updated}, skipped {skipped}.',
          'success')
    return redirect(url_for('echomail_admin.areas'))


@echomail_admin_bp.route('/areafix_log')
@login_required
@_admin_required
def areafix_log():
    """View the AreaFix robot's audit trail (requests in/out, %LIST replies, etc.)."""
    from ..models import AreafixLog
    page = request.args.get('page', 1, type=int)
    network_filter = request.args.get('network_id', type=int)

    q = AreafixLog.query
    if network_filter:
        q = q.filter_by(network_id=network_filter)
    pagination = (q.order_by(AreafixLog.created_at.desc())
                  .paginate(page=page, per_page=50, error_out=False))
    networks = {n.id: n for n in EchomailNetwork.query.all()}
    return render_template('echomail/admin/areafix_log.html',
                           rows=pagination.items,
                           pagination=pagination,
                           networks=networks,
                           all_networks=list(networks.values()),
                           network_filter=network_filter)
