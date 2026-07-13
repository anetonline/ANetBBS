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
import os

from flask import (Blueprint, render_template, redirect, url_for,
                   flash, request, abort, jsonify, current_app)
from flask_login import login_required, current_user
from wtforms import (StringField, TextAreaField, SubmitField, IntegerField,
                     BooleanField, PasswordField, SelectMultipleField)
from wtforms.validators import DataRequired, Length, Optional, NumberRange, Regexp
from flask_wtf import FlaskForm

from ..models import (db, BinkPNode, EchoAreaNode, BinkPHoldQueue,
                       QWKNode, QWKNodeLastSent, EchoArea, EchomailNetwork,
                       QWKNodeRequest, NetworkJoinConfig, NetworkJoinRequest,
                       HubIdentity)

hub_admin_bp = Blueprint('hub_admin', __name__, url_prefix='/admin/echomail/hub')


@hub_admin_bp.before_request
def _require_hub_mode():
    """404 the entire blueprint on any install that isn't the designated
    hub -- see the module docstring for why this matters."""
    if not current_app.config.get('REGISTRY_MODE_ENABLED'):
        abort(404)


from .access_control import require_admin as _admin_required


# ---------------------------------------------------------------------------
# Forms
# ---------------------------------------------------------------------------

class BinkPNodeForm(FlaskForm):
    name = StringField('Friendly Name', validators=[DataRequired(), Length(max=100)])
    ftn_address = StringField('FTN Address (e.g. 1:337/100)',
                              validators=[DataRequired(), Length(max=60)])
    # Optional, not DataRequired: on the edit route (see edit_binkp_node)
    # leaving this blank means "keep the current password" -- a
    # DataRequired() here silently blocked EVERY edit (including fixing a
    # typo'd name/address) unless the admin retyped a new password,
    # forcibly rotating credentials the node's own sysop might not have
    # been told about. Node creation (new_binkp_node) enforces its own
    # "password required" check explicitly since this validator no longer
    # does that for it.
    password = PasswordField('Session Password', validators=[Optional(), Length(max=255)])
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


class HubIdentityForm(FlaskForm):
    """A distinct echomail/QWK hub identity this install operates -- see
    models.HubIdentity. Most sysops only ever have the one default row
    created automatically by the multi-hub-identity migration and never
    see this form; it exists for sysops who run more than one real hub
    network (own zone:net, own QWK hub ID, own downstream node pool)."""
    name = StringField('Name', validators=[DataRequired(), Length(max=100)])
    slug = StringField('URL slug (e.g. mynet)',
                       validators=[DataRequired(), Length(max=50),
                                  Regexp(r'^[a-z0-9][a-z0-9-]*$',
                                         message='Lowercase letters, digits, and hyphens only.')])
    qwk_hub_id = StringField('QWK Hub ID (e.g. ANET)',
                             validators=[Optional(), Length(max=16)])
    binkp_zone = IntegerField('BinkP Zone', validators=[Optional(), NumberRange(min=1)])
    binkp_net = IntegerField('BinkP Net', validators=[Optional(), NumberRange(min=1)])
    binkp_hub_node = IntegerField('BinkP Hub Node #', default=1,
                                  validators=[Optional(), NumberRange(min=1)])
    binkp_domain = StringField('FTN Domain Suffix (e.g. anet)',
                               validators=[Optional(), Length(max=8)])
    nodelist_sysop = StringField('Nodelist Sysop Name', validators=[Optional(), Length(max=100)])
    nodelist_location = StringField('Nodelist Location', validators=[Optional(), Length(max=100)])
    nodelist_phone = StringField('Nodelist Phone', validators=[Optional(), Length(max=50)])
    nodelist_speed = IntegerField('Nodelist Speed', default=115200,
                                  validators=[Optional(), NumberRange(min=300)])
    # Starts unchecked -- same reasoning as the seeded default identity
    # (see _seed_default_hub_identity in web_app.py): a freshly created
    # identity shouldn't look live until the sysop has actually
    # confirmed its zone:net/QWK hub ID are correct and deliberately
    # flips it on.
    is_active = BooleanField('Active', default=False)
    is_default = BooleanField('Default identity')
    submit = SubmitField('Save Hub Identity')


class QWKNodeForm(FlaskForm):
    packet_id = StringField('Packet ID (up to 8 chars, e.g. MYNODE)',
                            validators=[DataRequired(), Length(max=8)])
    name = StringField('BBS Name', validators=[DataRequired(), Length(max=100)])
    sysop = StringField('Sysop Name', validators=[Optional(), Length(max=100)])
    email = StringField('Sysop Email', validators=[Optional(), Length(max=200)])
    # Optional, not DataRequired -- see BinkPNodeForm.password above for
    # why (same bug, same fix, same reasoning).
    password = PasswordField('Download Password', validators=[Optional(), Length(max=255)])
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
    join_cfg        = NetworkJoinConfig.get()
    pending_join_requests = NetworkJoinRequest.query.filter_by(status='pending').count()
    from ..echomail.network_join import list_txt_members
    join_txt_members = []
    if join_cfg.infopack_filename:
        join_zip_path = os.path.join(
            current_app.config.get('NETWORK_JOIN_DIR',
                                   os.path.join(current_app.config['DATA_DIR'], 'network_join')),
            join_cfg.infopack_filename)
        join_txt_members = list_txt_members(join_zip_path)
    gen_tab = request.args.get('gen_tab', 'nodelist')
    if gen_tab not in ('nodelist', 'qwk', 'tic', 'join'):
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
        join_cfg=join_cfg,
        pending_join_requests=pending_join_requests,
        join_txt_members=join_txt_members,
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
# Hub identity management
#
# Most installs have exactly one HubIdentity (created automatically by
# the multi-hub-identity migration -- see _default_hub_identity_id in
# models.py) and sysops never need to visit these screens at all. This
# CRUD exists for sysops who run more than one real hub network, each
# with its own zone:net, QWK hub ID, downstream node pool, nodelist, and
# join form.
# ---------------------------------------------------------------------------

@hub_admin_bp.route('/identities/')
@login_required
@_admin_required
def hub_identities():
    identities = HubIdentity.query.order_by(HubIdentity.name).all()
    return render_template('echomail/admin/hub/hub_identities.html', identities=identities)


@hub_admin_bp.route('/identities/new', methods=['GET', 'POST'])
@login_required
@_admin_required
def new_hub_identity():
    form = HubIdentityForm()
    if form.validate_on_submit():
        slug = form.slug.data.strip().lower()
        if HubIdentity.query.filter_by(slug=slug).first():
            flash('That URL slug is already in use by another hub identity.', 'danger')
            return render_template('echomail/admin/hub/hub_identity_form.html',
                                   form=form, identity=None)
        identity = HubIdentity(
            name=form.name.data.strip(),
            slug=slug,
            qwk_hub_id=(form.qwk_hub_id.data or '').strip().upper() or None,
            binkp_zone=form.binkp_zone.data,
            binkp_net=form.binkp_net.data,
            binkp_hub_node=form.binkp_hub_node.data or 1,
            binkp_domain=(form.binkp_domain.data or '').strip().lower() or None,
            nodelist_sysop=(form.nodelist_sysop.data or '').strip() or None,
            nodelist_location=(form.nodelist_location.data or '').strip() or None,
            nodelist_phone=(form.nodelist_phone.data or '').strip() or None,
            nodelist_speed=form.nodelist_speed.data or 115200,
            is_active=form.is_active.data,
            is_default=False,
        )
        db.session.add(identity)
        db.session.commit()
        if form.is_default.data:
            _set_default_hub_identity(identity)
        flash(f'Hub identity {identity.name!r} created.', 'success')
        return redirect(url_for('hub_admin.hub_identities'))
    return render_template('echomail/admin/hub/hub_identity_form.html',
                           form=form, identity=None)


@hub_admin_bp.route('/identities/<int:identity_id>/edit', methods=['GET', 'POST'])
@login_required
@_admin_required
def edit_hub_identity(identity_id):
    identity = HubIdentity.query.get_or_404(identity_id)
    form = HubIdentityForm(obj=identity)
    if request.method == 'GET':
        form.is_default.data = identity.is_default
    if form.validate_on_submit():
        slug = form.slug.data.strip().lower()
        existing = HubIdentity.query.filter(
            HubIdentity.slug == slug, HubIdentity.id != identity.id).first()
        if existing:
            flash('That URL slug is already in use by another hub identity.', 'danger')
            return render_template('echomail/admin/hub/hub_identity_form.html',
                                   form=form, identity=identity)
        identity.name = form.name.data.strip()
        identity.slug = slug
        identity.qwk_hub_id = (form.qwk_hub_id.data or '').strip().upper() or None
        identity.binkp_zone = form.binkp_zone.data
        identity.binkp_net = form.binkp_net.data
        identity.binkp_hub_node = form.binkp_hub_node.data or 1
        identity.binkp_domain = (form.binkp_domain.data or '').strip().lower() or None
        identity.nodelist_sysop = (form.nodelist_sysop.data or '').strip() or None
        identity.nodelist_location = (form.nodelist_location.data or '').strip() or None
        identity.nodelist_phone = (form.nodelist_phone.data or '').strip() or None
        identity.nodelist_speed = form.nodelist_speed.data or 115200
        identity.is_active = form.is_active.data
        db.session.commit()
        if form.is_default.data and not identity.is_default:
            _set_default_hub_identity(identity)
        flash(f'Hub identity {identity.name!r} updated.', 'success')
        return redirect(url_for('hub_admin.hub_identities'))
    return render_template('echomail/admin/hub/hub_identity_form.html',
                           form=form, identity=identity)


def _set_default_hub_identity(identity):
    """Enforce the is_default invariant: exactly one HubIdentity row has
    it set. Not a DB constraint (this codebase's SQLite migration helper
    only ever adds columns, never adds/loosens real constraints on an
    existing table) -- enforced here, the one place a row can become
    the default."""
    HubIdentity.query.filter(HubIdentity.id != identity.id).update({'is_default': False})
    identity.is_default = True
    db.session.commit()


@hub_admin_bp.route('/identities/<int:identity_id>/set-default', methods=['POST'])
@login_required
@_admin_required
def set_default_hub_identity(identity_id):
    identity = HubIdentity.query.get_or_404(identity_id)
    _set_default_hub_identity(identity)
    flash(f'{identity.name!r} is now the default hub identity.', 'success')
    return redirect(url_for('hub_admin.hub_identities'))


@hub_admin_bp.route('/identities/<int:identity_id>/delete', methods=['POST'])
@login_required
@_admin_required
def delete_hub_identity(identity_id):
    identity = HubIdentity.query.get_or_404(identity_id)
    if HubIdentity.query.count() <= 1:
        flash('Cannot delete the only hub identity.', 'danger')
        return redirect(url_for('hub_admin.hub_identities'))
    if identity.is_default:
        flash('Cannot delete the default hub identity — set another one as '
              'default first.', 'danger')
        return redirect(url_for('hub_admin.hub_identities'))
    in_use = (
        EchomailNetwork.query.filter_by(hub_identity_id=identity.id).count()
        or BinkPNode.query.filter_by(hub_identity_id=identity.id).count()
        or QWKNode.query.filter_by(hub_identity_id=identity.id).count()
    )
    if in_use:
        flash('Cannot delete a hub identity that still has networks or '
              'downstream nodes assigned to it — reassign or remove those '
              'first.', 'danger')
        return redirect(url_for('hub_admin.hub_identities'))
    db.session.delete(identity)
    db.session.commit()
    flash(f'Hub identity {identity.name!r} deleted.', 'success')
    return redirect(url_for('hub_admin.hub_identities'))


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
        if not form.password.data:
            flash('Session password is required when creating a new node.', 'danger')
            return render_template('echomail/admin/hub/binkp_node_form.html',
                                   form=form, node=None)
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
    binkp_networks = (EchomailNetwork.query
                      .filter_by(is_active=True, network_type='binkp')
                      .order_by(EchomailNetwork.name).all())
    return render_template(
        'echomail/admin/hub/binkp_node_detail.html',
        node=node,
        subscribed_ids=subscribed_ids,
        all_areas=all_areas,
        hold_entries=hold_entries,
        pending_count=pending_count,
        binkp_networks=binkp_networks,
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


@hub_admin_bp.route('/binkp/<int:node_id>/subscribe-all', methods=['POST'])
@login_required
@_admin_required
def binkp_subscribe_all(node_id):
    """Subscribe a node to every active area on the selected network(s)
    in one click, instead of clicking Subscribe once per area. Mirrors
    qwk_subscribe_all() -- BinkP had no bulk-subscribe at all before
    this, only one-at-a-time via binkp_subscribe()."""
    node = BinkPNode.query.get_or_404(node_id)
    network_ids = request.form.getlist('network_ids', type=int)
    if not network_ids:
        flash('Pick at least one network before subscribing to all its areas.', 'danger')
        return redirect(url_for('hub_admin.binkp_node_detail', node_id=node_id))

    already = {s.echo_area_id for s in node.subscriptions.all()}
    areas = (EchoArea.query
             .join(EchomailNetwork, EchoArea.network_id == EchomailNetwork.id)
             .filter(EchoArea.is_active == True,
                     EchomailNetwork.network_type == 'binkp',
                     EchomailNetwork.id.in_(network_ids))
             .order_by(EchoArea.tag)
             .all())
    added = 0
    for area in areas:
        if area.id in already:
            continue
        db.session.add(EchoAreaNode(node_id=node_id, echo_area_id=area.id))
        added += 1
    if added:
        db.session.commit()
        flash(f'Subscribed {node.ftn_address} to {added} area(s).', 'success')
    else:
        flash(f'{node.ftn_address} is already subscribed to every area on the selected network(s).', 'info')
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
        if not form.password.data:
            flash('Download password is required when creating a new node.', 'danger')
            return render_template('echomail/admin/hub/qwk_node_form.html',
                                   form=form, node=None)
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
    qwk_networks = (EchomailNetwork.query
                    .filter_by(is_active=True, network_type='qwk')
                    .order_by(EchomailNetwork.name).all())
    return render_template(
        'echomail/admin/hub/qwk_node_detail.html',
        node=node,
        subscriptions=subscriptions,
        subscribed_area_ids=subscribed_area_ids,
        all_areas=all_areas,
        conf_map=conf_map,
        qwk_networks=qwk_networks,
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


def _qwk_conf_number_for(node_id, area):
    """Resolve the conference number a subscription to *area* should use.

    Must be a FIXED, STABLE number shared by every node subscribed to
    this area -- not something reassigned per-subscriber. QWK's own
    wire format only ever carries a numeric conference number (no field
    for a symbolic tag), so `EchoArea.tag` on a QWK-network area IS that
    number (enforced at area-creation time; see echomail_admin.py's
    validation). Live-caught bug this fixes: subscriptions used to get
    an auto-incremented, per-node conf number independent of the area's
    own identity, so two different nodes could get two different numbers
    for "the same" area, and a node posting into a fresh area got
    silently dropped because outbound resolution derives the conference
    number from the area's tag directly, with no connection at all to
    whatever number subscribe() had assigned.

    Falls back to the old per-node auto-increment ONLY for the
    unexpected case of a pre-existing non-numeric-tag QWK area slipping
    through (legacy data predating the tag validation) -- logs a warning
    since that's a sign the area itself needs fixing, not proof this
    function is wrong.
    """
    tag = (area.tag or '').strip()
    if tag.isdigit():
        return int(tag)
    current_app.logger.warning(
        'QWK subscribe: area %r (id=%s) has a non-numeric tag %r -- '
        'falling back to per-node auto-increment. This area needs its '
        'tag fixed to a real conference number; posts into it will be '
        'silently dropped as conference 0 otherwise.',
        area.name, area.id, area.tag)
    max_conf = (db.session.query(db.func.max(QWKNodeLastSent.conf_number))
                .filter_by(node_id=node_id)
                .scalar() or 0)
    return max_conf + 1


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
            conf_num = _qwk_conf_number_for(node_id, area)
            db.session.add(QWKNodeLastSent(
                node_id=node_id,
                echo_area_id=area_id,
                conf_number=conf_num,
            ))
            db.session.commit()
            flash(f'Subscribed {node.packet_id} to {area.tag} (conf #{conf_num}).', 'success')
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
    receiving areas that only exist on a BinkP-only network.

    `network_ids` (checkbox values, one or more) further scopes this to
    specific QWK networks -- without it, this used to sweep in every
    QWK network on the install at once (confirmed real: a sysop running
    more than one QWK network had no way to add just one node's home
    network without also pulling in every other QWK network's areas)."""
    node = QWKNode.query.get_or_404(node_id)
    network_ids = request.form.getlist('network_ids', type=int)
    if not network_ids:
        flash('Pick at least one network before subscribing to all its areas.', 'danger')
        return redirect(url_for('hub_admin.qwk_node_detail', node_id=node_id))

    already = {s.echo_area_id for s in
              QWKNodeLastSent.query.filter_by(node_id=node_id).all()}
    areas = (EchoArea.query
             .join(EchomailNetwork, EchoArea.network_id == EchomailNetwork.id)
             .filter(EchoArea.is_active == True,
                     EchomailNetwork.network_type == 'qwk',
                     EchomailNetwork.id.in_(network_ids))
             .order_by(EchoArea.tag)
             .all())
    added = 0
    for area in areas:
        if area.id in already:
            continue
        db.session.add(QWKNodeLastSent(
            node_id=node_id, echo_area_id=area.id,
            conf_number=_qwk_conf_number_for(node_id, area)))
        added += 1
    if added:
        db.session.commit()
        flash(f'Subscribed {node.packet_id} to {added} area(s).', 'success')
    else:
        flash(f'{node.packet_id} is already subscribed to every area on the selected network(s).', 'info')
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
# Network join form — public "apply to join this network" page config +
# application review queue. See anetbbs/web/network_join.py for the
# public-facing side.
# ---------------------------------------------------------------------------

def _join_dir():
    return current_app.config.get(
        'NETWORK_JOIN_DIR',
        os.path.join(current_app.config['DATA_DIR'], 'network_join'))


def _join_archive_dir():
    return current_app.config.get(
        'NETWORK_JOIN_ARCHIVE_DIR',
        os.path.join(current_app.config['DATA_DIR'], 'network_join_archive'))


def _next_binkp_node_address(zone, net):
    """1200:1/2, then /3, ... Node 1 is the hub itself (see nodelist()'s
    hardcoded hub_node=1 below -- same convention), so numbering starts
    at 2 when no downstream nodes exist yet in this zone:net."""
    from ..echomail.routing import parse_address, format_address

    highest = 1
    for (addr,) in db.session.query(BinkPNode.ftn_address).all():
        parsed = parse_address(addr)
        if parsed and parsed[0] == zone and parsed[1] == net:
            highest = max(highest, parsed[2])
    return format_address(zone, net, highest + 1)


@hub_admin_bp.route('/join/config', methods=['POST'])
@login_required
@_admin_required
def join_config_save():
    cfg = NetworkJoinConfig.get()
    cfg.enabled = bool(request.form.get('enabled'))
    cfg.network_name = (request.form.get('network_name') or '').strip()[:100]
    cfg.intro_text = (request.form.get('intro_text') or '').strip()
    zone_raw = (request.form.get('binkp_zone') or '').strip()
    net_raw = (request.form.get('binkp_net') or '').strip()
    cfg.binkp_zone = int(zone_raw) if zone_raw.isdigit() else None
    cfg.binkp_net = int(net_raw) if net_raw.isdigit() else None
    db.session.commit()
    flash('Join form settings saved.', 'success')
    return redirect(url_for('hub_admin.index', gen_tab='join'))


@hub_admin_bp.route('/join/upload', methods=['POST'])
@login_required
@_admin_required
def join_upload_infopack():
    import datetime
    from werkzeug.utils import secure_filename
    from ..echomail.network_join import auto_pick_rules_member, extract_member_text

    f = request.files.get('infopack')
    if not f or not f.filename:
        flash('No file selected.', 'warning')
        return redirect(url_for('hub_admin.index', gen_tab='join'))
    if not f.filename.lower().endswith('.zip'):
        flash('Infopack must be a .zip file.', 'danger')
        return redirect(url_for('hub_admin.index', gen_tab='join'))

    cfg = NetworkJoinConfig.get()
    original_name = f.filename
    stored_name = secure_filename(f.filename) or 'infopack.zip'
    join_dir = _join_dir()
    os.makedirs(join_dir, exist_ok=True)
    dest = os.path.join(join_dir, stored_name)
    f.save(dest)

    cfg.infopack_filename = stored_name
    cfg.infopack_original_filename = original_name
    cfg.infopack_uploaded_at = datetime.datetime.utcnow()
    cfg.infopack_size = os.path.getsize(dest)

    picked = auto_pick_rules_member(dest)
    if picked:
        cfg.rules_member_name = picked
        cfg.rules_text = extract_member_text(dest, picked)
        cfg.rules_text_extracted_at = datetime.datetime.utcnow()
        flash(f'Infopack uploaded. Auto-picked "{picked}" as the rules text '
              f'— override below if that\'s wrong.', 'success')
    else:
        cfg.rules_member_name = None
        cfg.rules_text = None
        flash('Infopack uploaded, but it has no .txt files inside — '
              'pick a rules file manually is not possible without one.', 'warning')
    db.session.commit()
    return redirect(url_for('hub_admin.index', gen_tab='join'))


@hub_admin_bp.route('/join/rules-file', methods=['POST'])
@login_required
@_admin_required
def join_pick_rules_file():
    import datetime
    from ..echomail.network_join import extract_member_text
    cfg = NetworkJoinConfig.get()
    member = (request.form.get('member_name') or '').strip()
    if not cfg.infopack_filename or not member:
        flash('No infopack uploaded yet.', 'warning')
        return redirect(url_for('hub_admin.index', gen_tab='join'))

    zip_path = os.path.join(_join_dir(), cfg.infopack_filename)
    text = extract_member_text(zip_path, member)
    if text is None:
        flash(f'Could not extract "{member}" from the infopack.', 'danger')
        return redirect(url_for('hub_admin.index', gen_tab='join'))

    cfg.rules_member_name = member
    cfg.rules_text = text
    cfg.rules_text_extracted_at = datetime.datetime.utcnow()
    db.session.commit()
    flash(f'Rules text now sourced from "{member}".', 'success')
    return redirect(url_for('hub_admin.index', gen_tab='join'))


@hub_admin_bp.route('/join/requests')
@login_required
@_admin_required
def join_requests():
    pending  = (NetworkJoinRequest.query.filter_by(status='pending')
               .order_by(NetworkJoinRequest.created_at.asc()).all())
    reviewed = (NetworkJoinRequest.query.filter(NetworkJoinRequest.status != 'pending')
               .order_by(NetworkJoinRequest.reviewed_at.desc()).limit(50).all())
    return render_template('echomail/admin/hub/join_requests.html',
                           pending=pending, reviewed=reviewed)


@hub_admin_bp.route('/join/requests/<int:req_id>')
@login_required
@_admin_required
def join_request_detail(req_id):
    """Full detail view of a single application -- every submitted
    field, generated credentials once approved, and Approve/Deny
    actions available directly from this page."""
    req = NetworkJoinRequest.query.get_or_404(req_id)
    return render_template('echomail/admin/hub/join_request_detail.html', req=req)


@hub_admin_bp.route('/join/requests/<int:req_id>/archive', methods=['POST'])
@login_required
@_admin_required
def archive_join_request(req_id):
    """Snapshot the full application to a JSON file under
    DATA_DIR/network_join_archive/ -- a deliberate, sysop-triggered
    action, not automatic. That directory has no web route pointing at
    it (unlike NETWORK_JOIN_DIR's infopack, which is served through
    network_join.download_infopack), so it's unreachable from outside
    -- see _join_archive_dir()."""
    import json
    import datetime
    from werkzeug.utils import secure_filename

    req = NetworkJoinRequest.query.get_or_404(req_id)

    def _iso(dt):
        return dt.isoformat() if dt else None

    payload = {
        'id': req.id,
        'status': req.status,
        'applicant': {
            'name': req.name,
            'location': req.location,
            'email': req.email,
            'bbs_name': req.bbs_name,
            'bbs_software': req.bbs_software,
            'bbs_os': req.bbs_os,
            'telnet_address': req.telnet_address,
            'website_url': req.website_url,
        },
        'binkp': {
            'ftn_address_requested': req.binkp_ftn_address,
            'crash_or_hold': req.binkp_crash_or_hold,
            'assigned_node_id': req.binkp_node_id,
            'generated_password': req.generated_binkp_password,
        },
        'qwk': {
            'packet_id': req.qwk_packet_id,
            'assigned_node_id': req.qwk_node_id,
            'generated_password': req.generated_qwk_password,
        },
        'notes': req.notes,
        'rules_ack': req.rules_ack,
        'submission_meta': {
            'ip_address': req.ip_address,
            'user_agent': req.user_agent,
            'created_at': _iso(req.created_at),
        },
        'review': {
            'reviewed_at': _iso(req.reviewed_at),
            'reviewed_by': req.reviewed_by,
            'deny_reason': req.deny_reason,
        },
        'archived_at': _iso(datetime.datetime.utcnow()),
        'archived_by': current_user.username,
    }

    archive_dir = _join_archive_dir()
    os.makedirs(archive_dir, exist_ok=True)
    stamp = datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    safe_name = secure_filename(req.bbs_name) or 'application'
    filename = f'{req.id:05d}_{safe_name}_{stamp}.json'
    dest = os.path.join(archive_dir, filename)
    with open(dest, 'w', encoding='utf-8') as f:
        json.dump(payload, f, indent=2)

    flash(f'Application archived to {filename}.', 'success')
    return redirect(url_for('hub_admin.join_request_detail', req_id=req.id))


@hub_admin_bp.route('/join/requests/<int:req_id>/approve', methods=['POST'])
@login_required
@_admin_required
def approve_join_request(req_id):
    """Approve a pending network join request. Creates a BinkPNode
    and/or a QWKNode depending on which transport section(s) the
    applicant filled in -- driven entirely by the data, not an admin
    choice in the UI. Passwords are always hub-generated (never
    applicant-supplied), mirroring approve_qwk_request()'s rule."""
    import datetime
    import secrets
    import string

    req = NetworkJoinRequest.query.get_or_404(req_id)
    if req.status != 'pending':
        flash('Request is no longer pending.', 'warning')
        return redirect(url_for('hub_admin.join_requests'))

    if not req.binkp_ftn_address and not req.qwk_packet_id:
        flash('Request has neither a BinkP address nor a QWK packet ID '
              '-- nothing to approve.', 'danger')
        return redirect(url_for('hub_admin.join_requests'))

    alphabet = string.ascii_letters + string.digits

    cfg = NetworkJoinConfig.get()
    binkp_address = None
    if req.binkp_ftn_address:
        binkp_address = (_next_binkp_node_address(cfg.binkp_zone, cfg.binkp_net)
                          if cfg.binkp_zone and cfg.binkp_net
                          else req.binkp_ftn_address.strip())
        if BinkPNode.query.filter_by(ftn_address=binkp_address).first():
            flash(f'BinkP address {binkp_address} is already taken '
                  f'-- deny this request or edit first.', 'danger')
            return redirect(url_for('hub_admin.join_requests'))
    if req.qwk_packet_id and QWKNode.query.filter_by(
            packet_id=req.qwk_packet_id).first():
        flash(f'QWK packet ID {req.qwk_packet_id} is already taken '
              f'-- deny this request or edit first.', 'danger')
        return redirect(url_for('hub_admin.join_requests'))

    binkp_password = qwk_password = None
    if req.binkp_ftn_address:
        binkp_password = ''.join(secrets.choice(alphabet) for _ in range(16))
        binkp_node = BinkPNode(
            name=req.bbs_name,
            ftn_address=binkp_address,
            password=binkp_password,
            sysop=req.name,
            system_name=req.bbs_name,
            location=req.location,
            email=req.email,
            is_active=True,
            notes=f'Auto-created from network join request #{req.id}.',
        )
        db.session.add(binkp_node)
        db.session.flush()
        req.binkp_node_id = binkp_node.id
        req.generated_binkp_password = binkp_password

    if req.qwk_packet_id:
        qwk_password = ''.join(secrets.choice(alphabet) for _ in range(16))
        qwk_node = QWKNode(
            packet_id=req.qwk_packet_id,
            name=req.bbs_name,
            sysop=req.name,
            email=req.email,
            password=qwk_password,
            is_active=True,
            notes=f'Auto-created from network join request #{req.id}.',
        )
        db.session.add(qwk_node)
        db.session.flush()
        req.qwk_node_id = qwk_node.id
        req.generated_qwk_password = qwk_password

    req.status = 'approved'
    req.reviewed_at = datetime.datetime.utcnow()
    req.reviewed_by = current_user.username
    db.session.commit()

    # Best-effort credentials email -- never blocks the approval itself
    # if SMTP isn't configured or the send fails.
    from ..mailer import smtp_enabled, send_email
    if smtp_enabled():
        lines = [f'Hi {req.name},', '',
                 f'Your application to join as {req.bbs_name} has been approved!', '']
        if binkp_password:
            lines += [f'BinkP address: {binkp_address}',
                      f'BinkP session password: {binkp_password}', '']
        if qwk_password:
            lines += [f'QWK packet ID: {req.qwk_packet_id}',
                      f'QWK password: {qwk_password}', '']
        ok, err = send_email(req.email, 'Your network join application was approved',
                             '\n'.join(lines))
        if ok:
            flash(f'Approved! Credentials emailed to {req.email}.', 'success')
        else:
            flash(f'Approved, but the credentials email failed to send '
                  f'({err}) -- relay them to {req.email} manually.', 'warning')
    else:
        flash('Approved! SMTP relay is not configured, so you\'ll need to '
              'relay the generated credentials to the applicant yourself.', 'warning')
    return redirect(url_for('hub_admin.join_requests'))


@hub_admin_bp.route('/join/requests/<int:req_id>/deny', methods=['POST'])
@login_required
@_admin_required
def deny_join_request(req_id):
    """Deny a pending network join request with an optional reason."""
    import datetime
    req = NetworkJoinRequest.query.get_or_404(req_id)
    if req.status != 'pending':
        flash('Request is no longer pending.', 'warning')
        return redirect(url_for('hub_admin.join_requests'))

    reason = (request.form.get('reason') or '').strip()
    req.status = 'denied'
    req.reviewed_at = datetime.datetime.utcnow()
    req.reviewed_by = current_user.username
    req.deny_reason = reason or None
    db.session.commit()

    from ..mailer import smtp_enabled, send_email
    if smtp_enabled():
        body = (f'Hi {req.name},\n\nYour application to join as '
                f'{req.bbs_name} was not approved.'
                + (f'\n\nReason: {reason}' if reason else ''))
        send_email(req.email, 'Your network join application', body)

    flash(f'Request from {req.bbs_name} denied.', 'info')
    return redirect(url_for('hub_admin.join_requests'))


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
