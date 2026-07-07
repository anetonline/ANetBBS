# anetbbs/web/hub_admin.py
"""
Hub administration — manage downstream BinkP nodes and QWK nodes.

Sits under /admin/echomail/hub/ alongside the existing echomail_admin blueprint.
All routes require admin login.

This entire blueprint only makes sense on the ONE install designated as
the network hub (REGISTRY_MODE_ENABLED=true) -- a peer install has no
downstream BinkP/QWK nodes of its own to manage. Previously every
install exposed this same admin UI (including the QWK "Node Requests"
review queue), so a sysop applying for a node via the terminal wizard
on ANY install would have their request land in THAT install's own
local queue instead of the real hub's -- "all the sysops try to put in
for a node and it goes to their system." Gated with a blueprint-wide
before_request check below.
"""
from flask import (Blueprint, render_template, redirect, url_for,
                   flash, request, abort, jsonify, current_app)
from flask_login import login_required, current_user
from wtforms import (StringField, TextAreaField, SubmitField, IntegerField,
                     BooleanField, PasswordField, SelectMultipleField)
from wtforms.validators import DataRequired, Length, Optional, NumberRange
from flask_wtf import FlaskForm

from ..models import (db, BinkPNode, EchoAreaNode, BinkPHoldQueue,
                       QWKNode, QWKNodeLastSent, EchoArea, EchomailNetwork,
                       QWKNodeRequest)

hub_admin_bp = Blueprint('hub_admin', __name__, url_prefix='/admin/echomail/hub')


@hub_admin_bp.before_request
def _require_hub_mode():
    """404 the entire blueprint on any install that isn't the designated
    hub -- see the module docstring for why this matters."""
    if not current_app.config.get('REGISTRY_MODE_ENABLED'):
        abort(404)


def _admin_required(f):
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

class BinkPNodeForm(FlaskForm):
    name = StringField('Friendly Name', validators=[DataRequired(), Length(max=100)])
    ftn_address = StringField('FTN Address (e.g. 1:337/100)',
                              validators=[DataRequired(), Length(max=60)])
    password = PasswordField('Session Password', validators=[DataRequired(), Length(max=255)])
    sysop = StringField('Sysop Name', validators=[Optional(), Length(max=100)])
    system_name = StringField('System Name', validators=[Optional(), Length(max=100)])
    location = StringField('Location', validators=[Optional(), Length(max=100)])
    email = StringField('Sysop Email', validators=[Optional(), Length(max=200)])
    phone = StringField('Phone (for nodelist)', validators=[Optional(), Length(max=50)])
    baud = IntegerField('Baud Rate (for nodelist)', default=115200,
                        validators=[Optional(), NumberRange(min=300)])
    is_active = BooleanField('Active', default=True)
    notes = TextAreaField('Notes', validators=[Optional()])
    submit = SubmitField('Save Node')


class QWKNodeForm(FlaskForm):
    packet_id = StringField('Packet ID (up to 8 chars, e.g. MYNODE)',
                            validators=[DataRequired(), Length(max=8)])
    name = StringField('BBS Name', validators=[DataRequired(), Length(max=100)])
    sysop = StringField('Sysop Name', validators=[Optional(), Length(max=100)])
    email = StringField('Sysop Email', validators=[Optional(), Length(max=200)])
    password = PasswordField('Download Password', validators=[DataRequired(), Length(max=255)])
    is_active = BooleanField('Active', default=True)
    notes = TextAreaField('Notes', validators=[Optional()])
    submit = SubmitField('Save Node')


# ---------------------------------------------------------------------------
# Hub overview
# ---------------------------------------------------------------------------

@hub_admin_bp.route('/')
@login_required
@_admin_required
def index():
    from ..models import ScheduledEvent, HatchQueue
    from .events_admin import _row_view
    binkp_nodes     = BinkPNode.query.order_by(BinkPNode.ftn_address).all()
    qwk_nodes       = QWKNode.query.order_by(QWKNode.packet_id).all()
    pending_hold    = BinkPHoldQueue.query.filter_by(status='pending').count()
    pending_requests = QWKNodeRequest.query.filter_by(status='pending').count()
    nodelist_ev_row = ScheduledEvent.query.filter_by(
        handler_key='hub_generate_nodelist').first()
    nodelist_event  = _row_view(nodelist_ev_row) if nodelist_ev_row else None
    hatch_pending   = HatchQueue.query.filter_by(status='pending').count()
    hatch_failed    = HatchQueue.query.filter_by(status='failed').count()
    gen_tab = request.args.get('gen_tab', 'nodelist')
    if gen_tab not in ('nodelist', 'qwk', 'tic'):
        gen_tab = 'nodelist'
    return render_template(
        'echomail/admin/hub/index.html',
        binkp_nodes=binkp_nodes,
        qwk_nodes=qwk_nodes,
        pending_hold=pending_hold,
        pending_requests=pending_requests,
        nodelist_event=nodelist_event,
        hatch_pending=hatch_pending,
        hatch_failed=hatch_failed,
        gen_tab=gen_tab,
    )


@hub_admin_bp.route('/nodelist/generate-now', methods=['POST'])
@login_required
@_admin_required
def nodelist_generate_now():
    """Manually trigger nodelist generation right now (same handler the
    scheduler calls) -- runs synchronously, same as events_admin's
    "Run now" button."""
    from ..events.runner import fire
    from ..models import ScheduledEvent
    event = ScheduledEvent.query.filter_by(
        handler_key='hub_generate_nodelist').first()
    if event is None:
        flash('No nodelist-generation event is configured.', 'danger')
        return redirect(url_for('hub_admin.index'))
    ok, out = fire(current_app._get_current_object(), event.id)
    flash(('Nodelist generated: ' if ok else 'Nodelist generation failed: ') + out,
          'success' if ok else 'danger')
    return redirect(url_for('hub_admin.index'))


# ---------------------------------------------------------------------------
# BinkP node management
# ---------------------------------------------------------------------------

@hub_admin_bp.route('/binkp/')
@login_required
@_admin_required
def binkp_nodes():
    nodes = BinkPNode.query.order_by(BinkPNode.ftn_address).all()
    return render_template('echomail/admin/hub/binkp_nodes.html', nodes=nodes)


@hub_admin_bp.route('/binkp/new', methods=['GET', 'POST'])
@login_required
@_admin_required
def new_binkp_node():
    form = BinkPNodeForm()
    if form.validate_on_submit():
        existing = BinkPNode.query.filter_by(
            ftn_address=form.ftn_address.data.strip()).first()
        if existing:
            flash('A node with that FTN address already exists.', 'danger')
            return render_template('echomail/admin/hub/binkp_node_form.html',
                                   form=form, node=None)
        node = BinkPNode(
            name=form.name.data.strip(),
            ftn_address=form.ftn_address.data.strip(),
            password=form.password.data,
            sysop=form.sysop.data.strip() or None,
            system_name=form.system_name.data.strip() or None,
            location=form.location.data.strip() or None,
            email=form.email.data.strip() or None,
            phone=form.phone.data.strip() or None,
            baud=form.baud.data or 115200,
            is_active=form.is_active.data,
            notes=form.notes.data.strip() or None,
        )
        db.session.add(node)
        db.session.commit()
        flash(f'Node {node.ftn_address} created.', 'success')
        return redirect(url_for('hub_admin.binkp_node_detail', node_id=node.id))
    return render_template('echomail/admin/hub/binkp_node_form.html',
                           form=form, node=None)


@hub_admin_bp.route('/binkp/<int:node_id>')
@login_required
@_admin_required
def binkp_node_detail(node_id):
    node = BinkPNode.query.get_or_404(node_id)
    # Areas this node is subscribed to.
    subscribed_ids = {s.echo_area_id for s in node.subscriptions.all()}
    # All available areas for subscription picker.
    all_areas = (EchoArea.query
                 .join(EchomailNetwork, EchoArea.network_id == EchomailNetwork.id)
                 .filter(EchoArea.is_active == True)
                 .order_by(EchomailNetwork.name, EchoArea.tag)
                 .all())
    # Recent hold queue entries.
    hold_entries = (BinkPHoldQueue.query
                    .filter_by(node_id=node_id)
                    .order_by(BinkPHoldQueue.queued_at.desc())
                    .limit(50)
                    .all())
    pending_count = BinkPHoldQueue.query.filter_by(
        node_id=node_id, status='pending').count()
    return render_template(
        'echomail/admin/hub/binkp_node_detail.html',
        node=node,
        subscribed_ids=subscribed_ids,
        all_areas=all_areas,
        hold_entries=hold_entries,
        pending_count=pending_count,
    )


@hub_admin_bp.route('/binkp/<int:node_id>/edit', methods=['GET', 'POST'])
@login_required
@_admin_required
def edit_binkp_node(node_id):
    node = BinkPNode.query.get_or_404(node_id)
    form = BinkPNodeForm(obj=node)
    if form.validate_on_submit():
        node.name = form.name.data.strip()
        node.ftn_address = form.ftn_address.data.strip()
        if form.password.data:
            node.password = form.password.data
        node.sysop = form.sysop.data.strip() or None
        node.system_name = form.system_name.data.strip() or None
        node.location = form.location.data.strip() or None
        node.email = form.email.data.strip() or None
        node.phone = form.phone.data.strip() or None
        node.baud = form.baud.data or 115200
        node.is_active = form.is_active.data
        node.notes = form.notes.data.strip() or None
        db.session.commit()
        flash('Node updated.', 'success')
        return redirect(url_for('hub_admin.binkp_node_detail', node_id=node.id))
    return render_template('echomail/admin/hub/binkp_node_form.html',
                           form=form, node=node)


@hub_admin_bp.route('/binkp/<int:node_id>/delete', methods=['POST'])
@login_required
@_admin_required
def delete_binkp_node(node_id):
    node = BinkPNode.query.get_or_404(node_id)
    db.session.delete(node)
    db.session.commit()
    flash(f'Node {node.ftn_address} deleted.', 'success')
    return redirect(url_for('hub_admin.binkp_nodes'))


@hub_admin_bp.route('/binkp/<int:node_id>/subscribe', methods=['POST'])
@login_required
@_admin_required
def binkp_subscribe(node_id):
    """Add or remove area subscriptions for a BinkP node."""
    node = BinkPNode.query.get_or_404(node_id)
    area_id = request.form.get('area_id', type=int)
    action = request.form.get('action', 'subscribe')
    if not area_id:
        abort(400)
    area = EchoArea.query.get_or_404(area_id)

    if action == 'subscribe':
        existing = EchoAreaNode.query.filter_by(
            node_id=node_id, echo_area_id=area_id).first()
        if not existing:
            db.session.add(EchoAreaNode(node_id=node_id, echo_area_id=area_id))
            db.session.commit()
            flash(f'Subscribed {node.ftn_address} to {area.tag}.', 'success')
    elif action == 'unsubscribe':
        row = EchoAreaNode.query.filter_by(
            node_id=node_id, echo_area_id=area_id).first()
        if row:
            db.session.delete(row)
            db.session.commit()
            flash(f'Unsubscribed {node.ftn_address} from {area.tag}.', 'success')

    return redirect(url_for('hub_admin.binkp_node_detail', node_id=node_id))


@hub_admin_bp.route('/binkp/<int:node_id>/catchup', methods=['POST'])
@login_required
@_admin_required
def binkp_catchup(node_id):
    """Queue all existing messages in a subscribed area for this node."""
    node = BinkPNode.query.get_or_404(node_id)
    area_id = request.form.get('area_id', type=int)
    if area_id:
        from ..echomail.tosser import toss_area_messages
        n = toss_area_messages(area_id, node_id=node_id)
        flash(f'Queued {n} message(s) from area backlog for {node.ftn_address}.', 'success')
    return redirect(url_for('hub_admin.binkp_node_detail', node_id=node_id))


@hub_admin_bp.route('/binkp/<int:node_id>/flush', methods=['POST'])
@login_required
@_admin_required
def binkp_flush_queue(node_id):
    """Clear the pending hold queue for a node."""
    node = BinkPNode.query.get_or_404(node_id)
    deleted = (BinkPHoldQueue.query
               .filter_by(node_id=node_id, status='pending')
               .delete())
    db.session.commit()
    flash(f'Flushed {deleted} pending hold-queue entries for {node.ftn_address}.', 'success')
    return redirect(url_for('hub_admin.binkp_node_detail', node_id=node_id))


# ---------------------------------------------------------------------------
# QWK node management
# ---------------------------------------------------------------------------

@hub_admin_bp.route('/qwk/')
@login_required
@_admin_required
def qwk_nodes():
    nodes = QWKNode.query.order_by(QWKNode.packet_id).all()
    return render_template('echomail/admin/hub/qwk_nodes.html', nodes=nodes)


@hub_admin_bp.route('/qwk/new', methods=['GET', 'POST'])
@login_required
@_admin_required
def new_qwk_node():
    form = QWKNodeForm()
    if form.validate_on_submit():
        pid = form.packet_id.data.strip().upper()
        if QWKNode.query.filter_by(packet_id=pid).first():
            flash('A node with that Packet ID already exists.', 'danger')
            return render_template('echomail/admin/hub/qwk_node_form.html',
                                   form=form, node=None)
        node = QWKNode(
            packet_id=pid,
            name=form.name.data.strip(),
            sysop=form.sysop.data.strip() or None,
            email=form.email.data.strip() or None,
            password=form.password.data,
            is_active=form.is_active.data,
            notes=form.notes.data.strip() or None,
        )
        db.session.add(node)
        db.session.commit()
        flash(f'QWK node {pid} created.', 'success')
        return redirect(url_for('hub_admin.qwk_node_detail', node_id=node.id))
    return render_template('echomail/admin/hub/qwk_node_form.html',
                           form=form, node=None)


@hub_admin_bp.route('/qwk/<int:node_id>')
@login_required
@_admin_required
def qwk_node_detail(node_id):
    node = QWKNode.query.get_or_404(node_id)
    subscriptions = (QWKNodeLastSent.query
                     .filter_by(node_id=node_id)
                     .join(EchoArea, QWKNodeLastSent.echo_area_id == EchoArea.id)
                     .order_by(EchoArea.tag)
                     .all())
    subscribed_area_ids = {s.echo_area_id for s in subscriptions}
    all_areas = (EchoArea.query
                 .join(EchomailNetwork, EchoArea.network_id == EchomailNetwork.id)
                 .filter(EchoArea.is_active == True)
                 .order_by(EchomailNetwork.name, EchoArea.tag)
                 .all())
    # Assign conference numbers for display (1-based).
    conf_map = {s.echo_area_id: s.conf_number for s in subscriptions}
    return render_template(
        'echomail/admin/hub/qwk_node_detail.html',
        node=node,
        subscriptions=subscriptions,
        subscribed_area_ids=subscribed_area_ids,
        all_areas=all_areas,
        conf_map=conf_map,
    )


@hub_admin_bp.route('/qwk/<int:node_id>/edit', methods=['GET', 'POST'])
@login_required
@_admin_required
def edit_qwk_node(node_id):
    node = QWKNode.query.get_or_404(node_id)
    form = QWKNodeForm(obj=node)
    if form.validate_on_submit():
        pid = form.packet_id.data.strip().upper()
        conflict = QWKNode.query.filter(
            QWKNode.packet_id == pid, QWKNode.id != node_id).first()
        if conflict:
            flash('Another node already uses that Packet ID.', 'danger')
            return render_template('echomail/admin/hub/qwk_node_form.html',
                                   form=form, node=node)
        node.packet_id = pid
        node.name = form.name.data.strip()
        node.sysop = form.sysop.data.strip() or None
        node.email = form.email.data.strip() or None
        if form.password.data:
            node.password = form.password.data
        node.is_active = form.is_active.data
        node.notes = form.notes.data.strip() or None
        db.session.commit()
        flash('QWK node updated.', 'success')
        return redirect(url_for('hub_admin.qwk_node_detail', node_id=node.id))
    return render_template('echomail/admin/hub/qwk_node_form.html',
                           form=form, node=node)


@hub_admin_bp.route('/qwk/<int:node_id>/delete', methods=['POST'])
@login_required
@_admin_required
def delete_qwk_node(node_id):
    node = QWKNode.query.get_or_404(node_id)
    db.session.delete(node)
    db.session.commit()
    flash(f'QWK node {node.packet_id} deleted.', 'success')
    return redirect(url_for('hub_admin.qwk_nodes'))


@hub_admin_bp.route('/qwk/<int:node_id>/subscribe', methods=['POST'])
@login_required
@_admin_required
def qwk_subscribe(node_id):
    """Add or remove echo area subscriptions for a QWK node."""
    node = QWKNode.query.get_or_404(node_id)
    area_id = request.form.get('area_id', type=int)
    action = request.form.get('action', 'subscribe')
    if not area_id:
        abort(400)
    area = EchoArea.query.get_or_404(area_id)

    if action == 'subscribe':
        existing = QWKNodeLastSent.query.filter_by(
            node_id=node_id, echo_area_id=area_id).first()
        if not existing:
            # Assign next available conference number for this node.
            max_conf = (db.session.query(db.func.max(QWKNodeLastSent.conf_number))
                        .filter_by(node_id=node_id)
                        .scalar() or 0)
            db.session.add(QWKNodeLastSent(
                node_id=node_id,
                echo_area_id=area_id,
                conf_number=max_conf + 1,
            ))
            db.session.commit()
            flash(f'Subscribed {node.packet_id} to {area.tag} (conf #{max_conf + 1}).', 'success')
    elif action == 'unsubscribe':
        row = QWKNodeLastSent.query.filter_by(
            node_id=node_id, echo_area_id=area_id).first()
        if row:
            db.session.delete(row)
            db.session.commit()
            flash(f'Unsubscribed {node.packet_id} from {area.tag}.', 'success')

    return redirect(url_for('hub_admin.qwk_node_detail', node_id=node_id))


@hub_admin_bp.route('/qwk/<int:node_id>/subscribe-all', methods=['POST'])
@login_required
@_admin_required
def qwk_subscribe_all(node_id):
    """Subscribe a node to every active area on QWK-transport networks in
    one click, instead of clicking Subscribe once per area. Scoped to
    QWK-type networks specifically -- a QWK node has no business
    receiving areas that only exist on a BinkP-only network."""
    node = QWKNode.query.get_or_404(node_id)
    already = {s.echo_area_id for s in
              QWKNodeLastSent.query.filter_by(node_id=node_id).all()}
    areas = (EchoArea.query
             .join(EchomailNetwork, EchoArea.network_id == EchomailNetwork.id)
             .filter(EchoArea.is_active == True,
                     EchomailNetwork.network_type == 'qwk')
             .order_by(EchoArea.tag)
             .all())
    next_conf = (db.session.query(db.func.max(QWKNodeLastSent.conf_number))
                .filter_by(node_id=node_id).scalar() or 0) + 1
    added = 0
    for area in areas:
        if area.id in already:
            continue
        db.session.add(QWKNodeLastSent(
            node_id=node_id, echo_area_id=area.id, conf_number=next_conf))
        next_conf += 1
        added += 1
    if added:
        db.session.commit()
        flash(f'Subscribed {node.packet_id} to {added} area(s).', 'success')
    else:
        flash(f'{node.packet_id} is already subscribed to every QWK area.', 'info')
    return redirect(url_for('hub_admin.qwk_node_detail', node_id=node_id))


@hub_admin_bp.route('/qwk/<int:node_id>/reset', methods=['POST'])
@login_required
@_admin_required
def qwk_reset_hwm(node_id):
    """Reset the high-water mark for a specific area (node will re-receive all messages)."""
    node = QWKNode.query.get_or_404(node_id)
    area_id = request.form.get('area_id', type=int)
    if area_id:
        row = QWKNodeLastSent.query.filter_by(
            node_id=node_id, echo_area_id=area_id).first()
        if row:
            row.last_message_id = None
            db.session.commit()
            flash('High-water mark reset — node will receive all messages on next poll.', 'success')
    return redirect(url_for('hub_admin.qwk_node_detail', node_id=node_id))


@hub_admin_bp.route('/qwk/<int:node_id>/preview')
@login_required
@_admin_required
def qwk_preview(node_id):
    """Build the QWK packet this node would receive right now, and hand
    it back as a download -- diagnostic only. Deliberately does NOT call
    mark_qwk_sent(), so previewing never consumes the node's real
    unsent-message queue; the node's actual next download still
    includes everything shown here."""
    import io
    from flask import send_file
    from ..web.qwk_hub import _build_qwk_hub_packet, _hub_id

    node = QWKNode.query.get_or_404(node_id)
    packet_data, _new_hwm, total_msgs = _build_qwk_hub_packet(node)
    flash(f'Preview built: {total_msgs} message(s) -- this download does '
          f'NOT mark them as sent to {node.packet_id}.', 'info')
    filename = f'{_hub_id()}-preview.QWK'
    return send_file(
        io.BytesIO(packet_data),
        mimetype='application/zip',
        as_attachment=True,
        download_name=filename,
    )


# ---------------------------------------------------------------------------
# Hold queue viewer
# ---------------------------------------------------------------------------

@hub_admin_bp.route('/holdqueue')
@login_required
@_admin_required
def hold_queue():
    page = request.args.get('page', 1, type=int)
    status = request.args.get('status', 'pending')
    node_id = request.args.get('node_id', type=int)

    q = BinkPHoldQueue.query
    if status in ('pending', 'sent', 'failed'):
        q = q.filter_by(status=status)
    if node_id:
        q = q.filter_by(node_id=node_id)
    entries = q.order_by(BinkPHoldQueue.queued_at.desc()).paginate(
        page=page, per_page=50, error_out=False)

    nodes = BinkPNode.query.order_by(BinkPNode.ftn_address).all()
    return render_template(
        'echomail/admin/hub/hold_queue.html',
        entries=entries,
        nodes=nodes,
        status=status,
        node_id=node_id,
    )


# ---------------------------------------------------------------------------
# QWK node self-registration requests
# ---------------------------------------------------------------------------

@hub_admin_bp.route('/qwk/requests')
@login_required
@_admin_required
def qwk_node_requests():
    """List all QWK node applications submitted from the BBS terminal."""
    pending  = (QWKNodeRequest.query.filter_by(status='pending')
                .order_by(QWKNodeRequest.created_at.asc()).all())
    reviewed = (QWKNodeRequest.query.filter(QWKNodeRequest.status != 'pending')
                .order_by(QWKNodeRequest.reviewed_at.desc()).limit(50).all())
    return render_template('echomail/admin/hub/qwk_node_requests.html',
                           pending=pending, reviewed=reviewed)


@hub_admin_bp.route('/qwk/requests/<int:req_id>/approve', methods=['POST'])
@login_required
@_admin_required
def approve_qwk_request(req_id):
    """Approve a pending QWK node request — auto-creates the QWKNode record."""
    import secrets, string, datetime
    req = QWKNodeRequest.query.get_or_404(req_id)
    if req.status != 'pending':
        flash('Request is no longer pending.', 'warning')
        return redirect(url_for('hub_admin.qwk_node_requests'))

    pid = req.packet_id.upper()
    if QWKNode.query.filter_by(packet_id=pid).first():
        flash(f'Packet ID {pid} is already taken — deny this request or edit first.', 'danger')
        return redirect(url_for('hub_admin.qwk_node_requests'))

    # Generate a random password (16 chars, alphanumeric)
    alphabet = string.ascii_letters + string.digits
    password = ''.join(secrets.choice(alphabet) for _ in range(16))

    node = QWKNode(
        packet_id=pid,
        name=req.bbs_name,
        sysop=req.sysop_name,
        email=req.email,
        password=password,
        is_active=True,
        notes=(f"Auto-created from node request #{req.id}. "
               f"BBS address: {req.bbs_address or 'not provided'}"),
    )
    db.session.add(node)
    db.session.flush()

    req.status             = 'approved'
    req.reviewed_at        = datetime.datetime.utcnow()
    req.reviewed_by        = current_user.username
    req.generated_password = password
    req.node_id            = node.id
    db.session.commit()

    flash(f'Approved! QWK node {pid} created with auto-generated password. '
          f'Credentials will show in the applicant\'s BBS terminal.', 'success')
    return redirect(url_for('hub_admin.qwk_node_requests'))


@hub_admin_bp.route('/qwk/requests/<int:req_id>/deny', methods=['POST'])
@login_required
@_admin_required
def deny_qwk_request(req_id):
    """Deny a pending QWK node request with an optional reason."""
    import datetime
    req = QWKNodeRequest.query.get_or_404(req_id)
    if req.status != 'pending':
        flash('Request is no longer pending.', 'warning')
        return redirect(url_for('hub_admin.qwk_node_requests'))

    reason = (request.form.get('reason') or '').strip()
    req.status      = 'denied'
    req.reviewed_at = datetime.datetime.utcnow()
    req.reviewed_by = current_user.username
    req.deny_reason = reason or None
    db.session.commit()
    flash(f'Request from {req.bbs_name} ({req.packet_id}) denied.', 'info')
    return redirect(url_for('hub_admin.qwk_node_requests'))


# ---------------------------------------------------------------------------
# Public nodelist endpoint — no login required; BBS software fetches this
# ---------------------------------------------------------------------------

@hub_admin_bp.route('/nodelist')
def nodelist():
    """Serve the ANotherNetwork NODELIST as a plain-text download."""
    import datetime
    from flask import current_app, Response
    from ..echomail.nodelist import generate_nodelist

    cfg = current_app.config
    sysop = cfg.get('SYSOP_NAME') or 'SysOp'
    location = cfg.get('BBS_LOCATION') or 'Internet'

    today = datetime.date.today()
    day_of_year = today.timetuple().tm_yday
    filename = f'NODELIST.{day_of_year:03d}'

    content = generate_nodelist(
        zone=1200,
        net=1,
        hub_node=1,
        hub_name='ANotherNetwork',
        hub_location=location,
        hub_sysop=sysop,
        hub_phone='-Unpublished-',
        hub_speed=115200,
    )
    return Response(
        content,
        mimetype='text/plain',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )
