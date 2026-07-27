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
from wtforms.validators import DataRequired, Length, Optional, NumberRange, ValidationError
from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileAllowed

from ..models import (db, EchomailNetwork, EchoArea, EchomailMessage,
                       EchomailPollLog, BadAreaLog, UserAka)

echomail_admin_bp = Blueprint('echomail_admin', __name__, url_prefix='/admin/echomail')

from .access_control import require_admin as _admin_required


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
    ftn_domain = StringField(
        'Domain suffix override (addr@domain, max 8 chars, optional)',
        validators=[Optional(), Length(max=8)])
    cram_md5 = BooleanField(
        'Use CRAM-MD5 for BinkP auth (recommended — required by Synchronet)',
        default=True)
    binkp_tls = BooleanField('Use TLS for outbound BinkP', default=False)
    packet_password = PasswordField(
        'Packet Password (FTS-0001 header password — separate from the '
        'BinkP password above; leave blank if this hub doesn\'t require one)',
        validators=[Optional(), Length(max=20)])
    default_crash = BooleanField(
        'Default: Crash (attempt immediate delivery)', default=False)
    default_hold = BooleanField(
        'Default: Hold (hold for pickup, don\'t push)', default=False)
    default_direct = BooleanField(
        'Default: Direct (dial straight to destination, bypass routing)',
        default=False)
    default_recipient = StringField(
        'Default Netmail Recipient (BBS user that catches netmail addressed '
        'to unknown names)',
        validators=[Optional(), Length(max=100)])

    # QWK fields
    qwk_host = StringField('QWK Host', validators=[Optional(), Length(max=255)])
    qwk_port = IntegerField('QWK Port', validators=[Optional(), NumberRange(1, 65535)], default=80)
    qwk_username = StringField(
        'QWK Username — used to LOG IN to the hub\'s FTP/HTTP server. '
        'For a QNET-FTP-style hub (e.g. ANotherNetwork), this must be '
        'set to the SAME value as your Packet ID below, or the login '
        'will fail with no useful error -- the poller sends this field '
        'as the FTP username, not the Packet ID field.',
        validators=[Optional(), Length(max=255)])
    qwk_password = PasswordField('QWK Password', validators=[Optional()])
    qwk_packet_id = StringField(
        'QWK Packet ID — YOUR registered node ID on the hub '
        '(e.g. ANETBBS — this is the ID the hub assigned to YOUR BBS, '
        'NOT the hub\'s own ID). Used to name the outer REP zip and '
        'auto-generate message IDs.',
        validators=[Optional(), Length(max=20)])
    qwk_hub_id = StringField(
        'QWK Hub System ID — the HUB\'s system ID '
        '(e.g. VERT for vert.synchro.net / Dove-Net). '
        'This controls the inner MSG filename inside the REP zip — '
        'Synchronet looks for HUBID.MSG and rejects the packet if it\'s wrong.',
        validators=[Optional(), Length(max=16)])
    qwk_download_url = StringField(
        'QWK Download URL (full URL — overrides the default. '
        'Placeholders: {host} {port} {user} {password} {packet} {hub_id}. '
        'For DOVE-Net leave blank — auto-defaults to '
        'ftp://dove.synchro.net/{hub_id}.qwk)',
        validators=[Optional(), Length(max=500)])
    qwk_upload_url = StringField(
        'QWK Upload URL — for DOVE-Net leave blank (auto: ftp://dove.synchro.net/{packet}.rep). '
        'The outer zip is named {packet}.rep (your node ID); inner file is {hub_id}.MSG.',
        validators=[Optional(), Length(max=500)])

    poll_interval_minutes = IntegerField('Poll Interval (minutes)',
                                         validators=[Optional(), NumberRange(1, 10080)],
                                         default=60)
    is_active = BooleanField('Active', default=True)
    submit = SubmitField('Save Network')


class EchoAreaForm(FlaskForm):
    network_id = SelectField('Network', coerce=int, validators=[DataRequired()])
    tag = StringField('Area Tag (FTN: FIDO_GENERAL — QWK: plain conference number e.g. 2010)', validators=[DataRequired(), Length(max=100)])
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

    def validate_tag(self, field):
        # QWK's wire format (MESSAGES.DAT) only ever carries a numeric
        # conference number -- there is no field anywhere in it for a
        # symbolic tag. A non-numeric tag on a QWK-network area doesn't
        # just look wrong, it silently breaks: outbound posts fall back
        # to conference 0 (private mail) and get dropped entirely on
        # import, since no subscription is ever registered at conf 0.
        # Confirmed live, not theoretical -- see the ANotherNetwork QWK
        # area migration in web_app.py's seeder for the install-wide
        # version of this same bug.
        network = EchomailNetwork.query.get(self.network_id.data)
        if network and network.network_type == 'qwk':
            if not (field.data or '').strip().isdigit():
                raise ValidationError(
                    'QWK areas must use a plain numeric conference number '
                    'as the tag (e.g. "2010"), not a symbolic name -- QWK\'s '
                    'wire format has no field for anything else, and a '
                    'symbolic tag here means posts silently vanish instead '
                    'of being delivered.')


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
    # Scoped to BinkP only -- sent_at is meaningless for QWK, which tracks
    # delivery via a per-node/per-area high-water mark (QWKNodeLastSent)
    # instead and never sets this column at all. Counting QWK messages
    # here made this number climb forever regardless of whether anything
    # was actually stuck, since every QWK message ever sent stays
    # sent_at=None permanently -- confirmed live, not a hypothetical.
    pending_outbound = (EchomailMessage.query
                        .join(EchomailNetwork, EchomailMessage.network_id == EchomailNetwork.id)
                        .filter(EchomailMessage.direction == 'outbound',
                                EchomailMessage.sent_at.is_(None),
                                EchomailNetwork.network_type == 'binkp')
                        .count())
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
            ftn_domain=(form.ftn_domain.data or '').strip().lower() or None,
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
            packet_password=form.packet_password.data or None,
            default_crash=form.default_crash.data,
            default_hold=form.default_hold.data,
            default_direct=form.default_direct.data,
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
        prior_packet_password = network.packet_password
        form.populate_obj(network)
        if not (form.binkp_password.data or '').strip():
            network.binkp_password = prior_binkp
        if not (form.areafix_password.data or '').strip():
            network.areafix_password = prior_areafix
        if not (form.qwk_password.data or '').strip():
            network.qwk_password = prior_qwk
        if not (form.packet_password.data or '').strip():
            network.packet_password = prior_packet_password
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
                import ftplib  # nosec B402 -- FTP hub distribution is an intentional QWK transport
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
                urllib.request.urlopen(url, timeout=10)  # nosec B310 -- admin-configured QWK hub host/port, not user input
                flash(f'QWK HTTP reachable: {network.qwk_host}:{network.qwk_port}.',
                      'success')
    except Exception as exc:
        flash(f'Connection test failed: {exc}', 'danger')
    return redirect(url_for('echomail_admin.networks'))


# ---------------------------------------------------------------------------
# Echo area management
# ---------------------------------------------------------------------------

def _back_to_areas(network_id=None):
    """Redirect helper — return to network areas page or the network chooser."""
    if network_id:
        return redirect(url_for('echomail_admin.network_areas',
                                network_id=network_id))
    return redirect(url_for('echomail_admin.areas'))


@echomail_admin_bp.route('/areas')
@login_required
@_admin_required
def areas():
    """Network chooser — shows each network as a card with area stats."""
    networks = EchomailNetwork.query.order_by(EchomailNetwork.name).all()
    stats = {}
    for net in networks:
        net_areas = EchoArea.query.filter_by(network_id=net.id).all()
        stats[net.id] = {
            'total':      len(net_areas),
            'active':     sum(1 for a in net_areas if a.is_active),
            'subscribed': sum(1 for a in net_areas if a.is_subscribed),
            'sysop_only': sum(1 for a in net_areas if a.is_sysop_only),
        }
    return render_template('echomail/admin/areas.html',
                           networks=networks, stats=stats)


@echomail_admin_bp.route('/areas/network/<int:network_id>')
@login_required
@_admin_required
def network_areas(network_id):
    """Per-network area management with bulk-select and bulk actions."""
    from collections import OrderedDict
    network = EchomailNetwork.query.get_or_404(network_id)
    area_list = (EchoArea.query
                 .filter_by(network_id=network_id)
                 .order_by(EchoArea.category, EchoArea.order, EchoArea.name)
                 .all())
    groups = OrderedDict()
    for area in area_list:
        cat = area.category or 'Uncategorized'
        groups.setdefault(cat, []).append(area)
    all_networks = EchomailNetwork.query.order_by(EchomailNetwork.name).all()
    return render_template('echomail/admin/network_areas.html',
                           network=network, groups=groups,
                           area_list=area_list,
                           all_networks=all_networks)


@echomail_admin_bp.route('/areas/bulk', methods=['POST'])
@login_required
@_admin_required
def bulk_area_action():
    """Apply one action to a set of checked area IDs."""
    area_ids   = request.form.getlist('area_ids', type=int)
    action     = request.form.get('action', '').strip()
    network_id = request.form.get('network_id', type=int)
    lvl        = request.form.get('access_level', type=int)

    if not area_ids:
        flash('No areas selected.', 'warning')
        return _back_to_areas(network_id)

    target = EchoArea.query.filter(EchoArea.id.in_(area_ids)).all()
    n = len(target)

    if action == 'set_active':
        for a in target: a.is_active = True
        db.session.commit()
        flash(f'Set {n} area(s) active.', 'success')
    elif action == 'set_inactive':
        for a in target: a.is_active = False
        db.session.commit()
        flash(f'Set {n} area(s) inactive.', 'success')
    elif action == 'set_subscribed':
        for a in target: a.is_subscribed = True
        db.session.commit()
        flash(f'Subscribed {n} area(s).', 'success')
    elif action == 'set_unsubscribed':
        for a in target: a.is_subscribed = False
        db.session.commit()
        flash(f'Unsubscribed {n} area(s).', 'success')
    elif action == 'set_active_subscribed':
        for a in target:
            a.is_active     = True
            a.is_subscribed = True
        db.session.commit()
        flash(f'Set {n} area(s) active + subscribed.', 'success')
    elif action == 'set_sysop_only':
        for a in target: a.is_sysop_only = True
        db.session.commit()
        flash(f'Set {n} area(s) sysop-only.', 'success')
    elif action == 'clear_sysop_only':
        for a in target: a.is_sysop_only = False
        db.session.commit()
        flash(f'Cleared sysop-only on {n} area(s).', 'success')
    elif action == 'set_access_level' and lvl is not None:
        for a in target: a.min_access_level = lvl
        db.session.commit()
        flash(f'Set access level to {lvl} on {n} area(s).', 'success')
    elif action == 'delete':
        tags = [a.tag for a in target]
        for a in target: db.session.delete(a)
        db.session.commit()
        preview = ', '.join(tags[:5]) + ('…' if n > 5 else '')
        flash(f'Deleted {n} area(s): {preview}.', 'success')
    else:
        flash('Unknown or incomplete action — nothing changed.', 'warning')

    return _back_to_areas(network_id)


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
    net_id = area.network_id
    form = EchoAreaForm(obj=area)
    form.network_id.choices = [(n.id, n.name) for n in EchomailNetwork.query.all()]
    if form.validate_on_submit():
        form.populate_obj(area)
        db.session.commit()
        flash(f'Echo area "{area.name}" updated.', 'success')
        return _back_to_areas(net_id)
    return render_template('echomail/admin/area_form.html', form=form, area=area)


@echomail_admin_bp.route('/areas/<int:area_id>/delete', methods=['POST'])
@login_required
@_admin_required
def delete_area(area_id):
    area = EchoArea.query.get_or_404(area_id)
    name = area.name
    net_id = area.network_id
    db.session.delete(area)
    db.session.commit()
    flash(f'Echo area "{name}" deleted.', 'success')
    return _back_to_areas(net_id)


@echomail_admin_bp.route('/areas/<int:area_id>/toggle_subscribe', methods=['POST'])
@login_required
@_admin_required
def toggle_subscribe(area_id):
    area = EchoArea.query.get_or_404(area_id)
    area.is_subscribed = not area.is_subscribed
    db.session.commit()
    state = 'subscribed' if area.is_subscribed else 'unsubscribed'
    flash(f'Area "{area.name}" {state}.', 'success')
    return _back_to_areas(area.network_id)


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
        return _back_to_areas(area.network_id)
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
    return _back_to_areas(area.network_id)


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
        return _back_to_areas(network_id)
    tags = [a.tag for a in
            EchoArea.query.filter_by(network_id=network.id).all()]
    n_local = EchoArea.query.filter_by(network_id=network.id).update(
        {EchoArea.is_subscribed: True}, synchronize_session=False)
    db.session.commit()
    if not tags:
        flash('No local areas configured for this network. Run '
              '"Bulk Import" first to load a backbone file.', 'warning')
        return _back_to_areas(network_id)
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
    return _back_to_areas(network_id)


@echomail_admin_bp.route('/networks/<int:network_id>/custom_areafix', methods=['POST'])
@login_required
@_admin_required
def custom_areafix(network_id):
    """Queue an AreaFix netmail with a verbatim body the sysop typed.

    Lets the sysop send any AreaFix-spec body — `+TAG`, `-TAG`,
    `%RESCAN <tag>`, `%RESCAN <tag> D=3650`, `%QUERY`, `%LIST`,
    `%COMPRESS GZIP`, etc. Each line becomes one body line as-is.
    Subject is the AreaFix password (FTS-0024). To-name is `AreaFix`
    (override via the form's `robot` field, e.g. `robot=FileFix`,
    if needed for FileFix flow).
    """
    from ..models import NetmailMessage
    import datetime as _dt
    network = EchomailNetwork.query.get_or_404(network_id)
    if network.network_type != 'binkp':
        flash('Custom AreaFix only applies to BinkP networks.', 'warning')
        return _back_to_areas(network_id)
    body = (request.form.get('body') or '').strip()
    if not body:
        flash('Type one or more AreaFix command lines into the body field.',
              'warning')
        return _back_to_areas(network_id)
    if not network.hub_address:
        flash('Network has no hub_address configured.', 'danger')
        return _back_to_areas(network_id)
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
    return _back_to_areas(network_id)


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
        return _back_to_areas(network_id)
    tags = [a.tag for a in
            EchoArea.query.filter_by(
                network_id=network.id, is_subscribed=True).all()]
    if not tags:
        flash('No subscribed areas on this network — nothing to rescan.',
              'warning')
        return _back_to_areas(network_id)
    from ..echomail.areafix import send_areafix_request
    nm_id = send_areafix_request(network, rescan_tags=tags,
                                  subject='AreaFix request')
    if nm_id:
        flash(f'Queued AreaFix %RESCAN for {len(tags)} subscribed areas '
              f'on {network.hub_address} (netmail #{nm_id}).', 'success')
    else:
        flash('Could not queue rescan AreaFix request (no hub address or '
              'no password configured).', 'warning')
    return _back_to_areas(network_id)


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
        return _back_to_areas(network_id)
    tags = [a.tag for a in
            EchoArea.query.filter_by(network_id=network.id).all()]
    n_local = EchoArea.query.filter_by(network_id=network.id).update(
        {EchoArea.is_subscribed: False}, synchronize_session=False)
    db.session.commit()
    if not tags:
        flash('No local areas to unsubscribe from.', 'info')
        return _back_to_areas(network_id)
    from ..echomail.areafix import send_areafix_request
    nm_id = send_areafix_request(network, minus_tags=tags,
                                  subject='AreaFix request')
    flash(f'Marked {n_local} local areas unsubscribed; queued AreaFix '
          f'request with {len(tags)} -TAG entries (netmail #{nm_id or "n/a"}).',
          'success')
    return _back_to_areas(network_id)


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
        return _back_to_areas(form.network_id.data)
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


@echomail_admin_bp.route('/logs/<int:log_id>/transcript')
@login_required
@_admin_required
def poll_log_transcript(log_id):
    """Full frame-by-frame BinkP session transcript for one poll
    attempt -- lets a sysop see exactly what was sent/received without
    needing server log access. QWK polls never have one (BinkP-only
    concept); 404s if this particular poll predates the feature or
    never captured anything -- UNLESS the poll is still running, in
    which case an empty transcript just means no checkpoint has landed
    yet (checkpoints are best-effort, at a couple of natural points in
    the session, not per-frame) -- show that plainly instead of a bare
    404, which would look like the poll never existed at all."""
    log = EchomailPollLog.query.get_or_404(log_id)
    if not log.transcript and log.status != 'running':
        abort(404)
    return render_template('echomail/admin/poll_log_transcript.html', log=log)


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


# ---------------------------------------------------------------------------
# Unclaimed netmail — real gap found in a full echomail-subsystem audit:
# inbound netmail whose to_name/to_address doesn't match any local
# user's AKA/username (routing.py's resolve_netmail_recipient()) has
# always been stored with to_user_id=NULL, but every read path in the
# app (web/netmail.py's inbox/sent/drafts) filters by the logged-in
# user's own id/AKAs -- there was no admin-facing view of these at all,
# unlike the analogous BadAreaLog mechanism for unrecognized echomail
# areas. Mail with a typo'd/stale recipient name, or arriving before a
# sysop ever configured EchomailNetwork.default_recipient, just
# silently vanished from anyone's view with zero visible signal.
# ---------------------------------------------------------------------------

@echomail_admin_bp.route('/unclaimed_netmail')
@login_required
@_admin_required
def unclaimed_netmail():
    """Sysop review queue for inbound netmail that never resolved to a
    local user. Excludes AreaFix/FileFix bot traffic -- those are
    intentionally not tied to a real user and are already tracked via
    AreafixLog, not a gap worth surfacing here."""
    from ..models import NetmailMessage
    _bot_names = ('areafix', 'area fix', 'areamgr',
                 'filefix', 'file fix', 'filemgr')
    rows = (NetmailMessage.query
            .filter(NetmailMessage.direction == 'inbound',
                    NetmailMessage.to_user_id.is_(None),
                    db.func.lower(NetmailMessage.to_name).notin_(_bot_names))
            .order_by(NetmailMessage.received_at.desc())
            .limit(200).all())
    networks = {n.id: n for n in EchomailNetwork.query.all()}
    from ..models import User
    users = User.query.order_by(User.username).all()
    return render_template('echomail/admin/unclaimed_netmail.html',
                           rows=rows, networks=networks, users=users)


@echomail_admin_bp.route('/unclaimed_netmail/<int:nm_id>/assign', methods=['POST'])
@login_required
@_admin_required
def unclaimed_netmail_assign(nm_id):
    """Manually assign an unclaimed netmail to a real local user --
    same notify() hook the automatic resolver already fires, so the
    recipient sees it exactly like a normally-routed netmail."""
    from ..models import NetmailMessage, User
    nm = NetmailMessage.query.get_or_404(nm_id)
    user_id = request.form.get('user_id', type=int)
    user = User.query.get(user_id) if user_id else None
    if user is None:
        flash('Pick a user to assign this netmail to.', 'danger')
        return redirect(url_for('echomail_admin.unclaimed_netmail'))

    nm.to_user_id = user.id
    db.session.commit()

    try:
        from ..features.notify import notify
        notify(user.id, 'netmail',
              title=f'Netmail from {nm.from_name}',
              body=nm.subject or '',
              target_url=f'/netmail/{nm.id}')
    except Exception:
        pass

    flash(f'Assigned to {user.username}.', 'success')
    return redirect(url_for('echomail_admin.unclaimed_netmail'))


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
        return _back_to_areas(network_id)

    if network.network_type != 'binkp':
        flash('AreaFix requests are only supported on BinkP networks.', 'warning')
        return _back_to_areas(network_id)

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
    return _back_to_areas(network_id)


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
        return _back_to_areas(network_id)

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
        # Tag is the plain conference number. Also check legacy 'QWK_N' format
        # and migrate to plain numeric if found.
        tag = str(conf_num)
        existing = EchoArea.query.filter_by(network_id=network.id,
                                             tag=tag).first()
        if not existing:
            old_tag = f'QWK_{conf_num}'
            existing = EchoArea.query.filter_by(network_id=network.id,
                                                 tag=old_tag).first()
            if existing:
                existing.tag = tag
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
    return _back_to_areas(network_id)


@echomail_admin_bp.route('/areafix_log')
@login_required
@_admin_required
def areafix_log():
    """View the AreaFix robot's audit trail (requests in/out, %LIST replies, etc.)."""
    from ..models import AreafixLog
    page = request.args.get('page', 1, type=int)
    network_filter = request.args.get('network_id', type=int)
    bot_filter = (request.args.get('bot') or '').strip()

    q = AreafixLog.query
    if network_filter:
        q = q.filter_by(network_id=network_filter)
    if bot_filter in ('areafix', 'filefix'):
        q = q.filter_by(bot=bot_filter)
    pagination = (q.order_by(AreafixLog.created_at.desc())
                  .paginate(page=page, per_page=50, error_out=False))
    networks = {n.id: n for n in EchomailNetwork.query.all()}
    return render_template('echomail/admin/areafix_log.html',
                           rows=pagination.items,
                           pagination=pagination,
                           networks=networks,
                           all_networks=list(networks.values()),
                           network_filter=network_filter,
                           bot_filter=bot_filter)


@echomail_admin_bp.route('/akas', methods=['GET', 'POST'])
@login_required
@_admin_required
def akas():
    """Manage the FTN AKAs for the currently logged-in admin user."""
    import re
    def parse_address(s):
        return re.match(r'^\d+:\d+/\d+(\.\d+)?$', s)

    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add':
            addr = (request.form.get('address') or '').strip()
            domain = (request.form.get('domain') or 'fidonet').strip().lower()
            if not parse_address(addr):
                flash(f'Bad FTN address: {addr!r} (use zone:net/node[.point])', 'danger')
                return redirect(url_for('echomail_admin.akas'))
            existing = UserAka.query.filter_by(
                user_id=current_user.id, address=addr).first()
            if existing:
                flash('That AKA already exists.', 'warning')
                return redirect(url_for('echomail_admin.akas'))
            aka = UserAka(user_id=current_user.id, address=addr, domain=domain)
            if not UserAka.query.filter_by(user_id=current_user.id).first():
                aka.is_primary = True
            db.session.add(aka)
            db.session.commit()
            flash(f'Added AKA {addr} ({domain}).', 'success')
        elif action == 'delete':
            aka = UserAka.query.get_or_404(int(request.form.get('aka_id')))
            if aka.user_id != current_user.id:
                abort(403)
            db.session.delete(aka)
            db.session.commit()
            flash(f'Deleted {aka.address}.', 'success')
        elif action == 'set_primary':
            aka = UserAka.query.get_or_404(int(request.form.get('aka_id')))
            if aka.user_id != current_user.id:
                abort(403)
            UserAka.query.filter_by(user_id=current_user.id).update(
                {UserAka.is_primary: False})
            aka.is_primary = True
            db.session.commit()
            flash(f'Primary AKA is now {aka.address}.', 'success')
        return redirect(url_for('echomail_admin.akas'))

    user_akas = UserAka.query.filter_by(user_id=current_user.id).order_by(
        UserAka.is_primary.desc(), UserAka.address).all()
    return render_template('echomail/admin/akas.html', akas=user_akas)
