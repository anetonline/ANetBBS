# anetbbs/web/echomail.py
"""
User-facing echomail blueprint for reading and composing echomail messages.
"""
from flask import Blueprint, render_template, redirect, url_for, flash, request, abort
from flask_login import login_required, current_user
from wtforms import StringField, TextAreaField, SubmitField, SelectField
from wtforms.validators import DataRequired, Length
from flask_wtf import FlaskForm
from sqlalchemy.exc import OperationalError

from ..models import (db, EchoArea, EchomailMessage, EchomailReadStatus,
                       EchomailNetwork, EchomailLastRead, BinkPHoldQueue,
                       QWKNodeLastSent, maybe_tag_ansi_subject)
from ..features.access_control import evaluate_access

echomail_bp = Blueprint('echomail', __name__, url_prefix='/echomail')


def _check_area_access(echo_area):
    """Abort 403 if the current user cannot access this echo area."""
    if not evaluate_access(current_user, echo_area.min_access_level,
                           is_sysop_only=echo_area.is_sysop_only,
                           bypass_admin=True):
        abort(403)


def _owns_netmail_echomail(msg, user):
    """Real gap found in a full application-wide access-control audit,
    same class of bug as web/netmail.py's now-fixed _user_addresses():
    QWK-style 1-on-1 netmail routed into an EchomailMessage row (any
    EchoArea tagged 'NETMAIL' -- a separate mechanism from the real
    NetmailMessage table web/netmail.py uses) had NO per-user scoping
    at all -- netmail_inbox() showed the last 100 messages from every
    network's NETMAIL area to any logged-in user, and read() had no
    ownership check either, so even after adding the list-view filter
    the message would still be readable directly by URL. Only admins
    get the "see everything" catch-all -- learned from the netmail.py
    bug not to extend that to a blanket address match for regular
    users."""
    if getattr(user, 'is_admin', False):
        return True
    # Match only against a non-empty candidate name -- an empty
    # msg.to_name/from_name must never match an empty uname/dname (a
    # user with no display_name set), which would otherwise falsely
    # "own" every netmail with a blank recipient/sender field.
    names = {n for n in ((user.username or '').lower(),
                        (getattr(user, 'display_name', None) or '').lower())
            if n}
    if names and (msg.to_name or '').lower() in names:
        return True
    if names and (msg.from_name or '').lower() in names:
        return True
    from ..models import UserAka
    addrs = {a.address for a in UserAka.query.filter_by(user_id=user.id).all()}
    if addrs and (msg.to_address in addrs or msg.from_address in addrs):
        return True
    return False


class ComposeForm(FlaskForm):
    """Form for composing or replying to echomail messages."""
    area_id = SelectField('Echo Area', coerce=int)
    to_name = StringField('To', validators=[DataRequired(), Length(max=200)])
    subject = StringField('Subject', validators=[DataRequired(), Length(max=200)])
    body = TextAreaField('Message', validators=[DataRequired(), Length(min=1)])
    submit = SubmitField('Send')


class NetmailForm(FlaskForm):
    """Form for composing 1-on-1 QWK netmail (private messages)."""
    network_id = SelectField('Network', coerce=int, validators=[DataRequired()])
    to_name = StringField('Recipient', validators=[DataRequired(), Length(max=100)])
    to_address = StringField('Recipient FTN address (optional)',
                             validators=[Length(max=50)])
    subject = StringField('Subject', validators=[DataRequired(), Length(max=200)])
    body = TextAreaField('Message', validators=[DataRequired(), Length(min=1)])
    submit = SubmitField('Send Netmail')


def _accessible_areas_query(network_id):
    """Return a filtered EchoArea query for the current user on one network.

    Same predicate as evaluate_access(), kept as a SQL-level filter rather
    than a per-row Python call -- this backs area-listing pages, and
    downgrading a set-based .filter() into N evaluate_access() calls after
    fetching every row would be a real (if usually small) perf regression
    for no behavior change. _check_area_access() above is the single-item
    equivalent and does use evaluate_access() directly.
    """
    q = EchoArea.query.filter_by(
        network_id=network_id, is_active=True, is_subscribed=True)
    if not getattr(current_user, 'is_admin', False):
        user_lvl = getattr(current_user, 'access_level', 10) or 10
        q = q.filter(EchoArea.is_sysop_only.is_(False))
        q = q.filter(EchoArea.min_access_level <= user_lvl)
    return q


def _unread_counts(area_ids):
    """Return {area_id: unread_count} for the given area IDs."""
    from sqlalchemy import func
    if not area_ids:
        return {}
    read_msg_ids = {
        rs.message_id
        for rs in EchomailReadStatus.query.filter_by(user_id=current_user.id).all()
    }
    return dict(
        db.session.query(
            EchomailMessage.area_id,
            func.count(EchomailMessage.id)
        ).filter(
            EchomailMessage.area_id.in_(area_ids),
            ~EchomailMessage.id.in_(read_msg_ids) if read_msg_ids else db.true()
        ).group_by(EchomailMessage.area_id).all()
    )


@echomail_bp.route('/')
@login_required
def index():
    """Network chooser — list networks that have accessible areas."""
    try:
        networks = EchomailNetwork.query.filter_by(is_active=True).all()
    except OperationalError:
        flash('Echomail is not configured yet.', 'info')
        return render_template('echomail/index.html', network_list=[])

    network_list = []
    for net in networks:
        areas = _accessible_areas_query(net.id).all()
        if not areas:
            continue
        area_ids = [a.id for a in areas]
        uc = _unread_counts(area_ids)
        total_unread = sum(uc.values())
        network_list.append({
            'network':      net,
            'area_count':   len(areas),
            'total_unread': total_unread,
        })

    return render_template('echomail/index.html', network_list=network_list)


@echomail_bp.route('/network/<int:network_id>')
@login_required
def network(network_id):
    """List areas in one network, grouped by category."""
    from collections import OrderedDict
    net = EchomailNetwork.query.get_or_404(network_id)
    areas = (_accessible_areas_query(network_id)
             .order_by(EchoArea.category, EchoArea.order, EchoArea.name)
             .all())

    area_ids = [a.id for a in areas]
    uc = _unread_counts(area_ids)

    groups = OrderedDict()
    for area in areas:
        cat = area.category or 'General'
        groups.setdefault(cat, []).append((area, uc.get(area.id, 0)))

    return render_template('echomail/network.html',
                           net=net, groups=groups, area_ids=area_ids,
                           total_unread=sum(uc.values()))


@echomail_bp.route('/<int:area_id>')
@login_required
def area(area_id):
    """List messages in an echo area."""
    echo_area = EchoArea.query.get_or_404(area_id)
    if not echo_area.is_active or not echo_area.is_subscribed:
        abort(404)
    _check_area_access(echo_area)

    page = request.args.get('page', 1, type=int)
    per_page = 25

    pagination = EchomailMessage.query.filter_by(area_id=area_id)\
        .order_by(EchomailMessage.created_at.desc())\
        .paginate(page=page, per_page=per_page, error_out=False)

    read_ids = {
        rs.message_id for rs in EchomailReadStatus.query.filter_by(user_id=current_user.id).all()
    }

    return render_template('echomail/area.html',
                           echo_area=echo_area,
                           messages=pagination.items,
                           pagination=pagination,
                           read_ids=read_ids)


@echomail_bp.route('/<int:area_id>/thread/<int:message_id>')
@login_required
def thread(area_id, message_id):
    """Show a message as the root of a thread + all descendants.

    Walks up via reply_id -> parent.msg_id to find the root, then descends
    via children's reply_id -> root.msg_id breadth-first."""
    echo_area = EchoArea.query.get_or_404(area_id)
    if not echo_area.is_active:
        abort(404)
    _check_area_access(echo_area)

    # Real gap found in a full echomail-subsystem audit: every sibling
    # message-by-ID lookup in this file (read(), compose()'s reply_to_id)
    # scopes the query by area_id -- this one used a bare get_or_404(),
    # so a logged-in user could seed a thread view with a message_id from
    # ANY area (sysop-only, restricted min_access_level, or a NETMAIL
    # area they don't own), bypassing both _check_area_access() above
    # (which only validates THIS route's own area_id) and the NETMAIL
    # ownership check read() enforces. The seed message's from/to/subject/
    # body get rendered regardless of which area it actually belongs to.
    seed = EchomailMessage.query.filter_by(
        id=message_id, area_id=area_id).first_or_404()
    if echo_area.tag == 'NETMAIL' and not _owns_netmail_echomail(seed, current_user):
        abort(403)
    # Walk up to find root.
    root = seed
    seen_ids = {root.id}
    while root.reply_id:
        parent = (EchomailMessage.query
                  .filter_by(area_id=area_id, msg_id=root.reply_id)
                  .first())
        if parent is None or parent.id in seen_ids:
            break
        seen_ids.add(parent.id)
        root = parent

    # BFS descendants
    out = [root]
    queue = [root]
    while queue:
        node = queue.pop(0)
        if not node.msg_id:
            continue
        children = (EchomailMessage.query
                    .filter_by(area_id=area_id, reply_id=node.msg_id)
                    .order_by(EchomailMessage.created_at)
                    .all())
        for c in children:
            if c.id in seen_ids:
                continue
            seen_ids.add(c.id)
            # Annotate depth based on parent
            c._depth = (getattr(node, '_depth', 0) or 0) + 1
            out.append(c)
            queue.append(c)
    root._depth = 0
    return render_template('echomail/thread.html',
                           echo_area=echo_area, messages=out, seed=seed)


@echomail_bp.route('/<int:area_id>/<int:message_id>')
@login_required
def read(area_id, message_id):
    """Read a single echomail message."""
    echo_area = EchoArea.query.get_or_404(area_id)
    # Real gap found in a full application-wide access-control audit:
    # every OTHER route in this file (area, thread, compose, next_unread)
    # calls _check_area_access() -- this direct-by-message-ID route never
    # did, so a sysop-only or high-min_access_level area was correctly
    # hidden from every listing but still readable by anyone who could
    # guess/iterate a sequential message_id.
    _check_area_access(echo_area)
    msg = EchomailMessage.query.filter_by(id=message_id, area_id=area_id).first_or_404()
    # NETMAIL-tagged areas carry 1-on-1 private mail (QWK-routed), not
    # broadcast echomail -- see _owns_netmail_echomail()'s own docstring
    # for the real bug this closes.
    if echo_area.tag == 'NETMAIL' and not _owns_netmail_echomail(msg, current_user):
        abort(403)

    # Mark as read + advance lastread pointer
    if not EchomailReadStatus.query.filter_by(
            user_id=current_user.id, message_id=message_id).first():
        rs = EchomailReadStatus(user_id=current_user.id, message_id=message_id)
        db.session.add(rs)
    lr = EchomailLastRead.query.filter_by(
        user_id=current_user.id, area_id=area_id).first()
    if lr is None:
        lr = EchomailLastRead(user_id=current_user.id, area_id=area_id,
                              last_message_id=message_id)
        db.session.add(lr)
    elif (lr.last_message_id or 0) < message_id:
        lr.last_message_id = message_id
    db.session.commit()

    # Previous / next navigation
    prev_msg = EchomailMessage.query.filter(
        EchomailMessage.area_id == area_id,
        EchomailMessage.id < message_id
    ).order_by(EchomailMessage.id.desc()).first()

    next_msg = EchomailMessage.query.filter(
        EchomailMessage.area_id == area_id,
        EchomailMessage.id > message_id
    ).order_by(EchomailMessage.id.asc()).first()

    # For OUR OWN outbound messages, msg.origin_line is stored WITHOUT
    # the FTN address -- that gets appended separately, per network, at
    # actual send time (anetbbs/echomail/binkp.py's _build_ftn_packet).
    # Showing the raw stored value here is misleading: a sysop reading
    # their own just-composed message sees no address at all and
    # reasonably assumes it's missing from what actually gets sent,
    # when it isn't -- confirmed live, this was mistaken for the address
    # fix not working. Mirror _build_ftn_packet's own logic so the
    # preview matches the real wire content. Inbound messages need no
    # such adjustment: their origin_line came from the sender already
    # fully formed.
    display_origin_line = msg.origin_line
    if msg.direction == 'outbound' and msg.origin_line:
        text = msg.origin_line.strip()
        if not text.endswith(')'):
            network = EchomailNetwork.query.get(msg.network_id)
            our_addr = (network.our_address if network else None) or '1:1/1'
            display_origin_line = f'{text} ({our_addr})'

    return render_template('echomail/read.html',
                           echo_area=echo_area,
                           msg=msg,
                           display_origin_line=display_origin_line,
                           prev_msg=prev_msg,
                           next_msg=next_msg)


@echomail_bp.route('/<int:area_id>/<int:message_id>/delete', methods=['POST'])
@login_required
def delete_message(area_id, message_id):
    """Admin-only: permanently remove a single echomail message.

    Real gap found live: a sysop composed/received a message that never
    should have gone out (garbled ANSI art) and had no way to pull it
    from the local view or the still-pending outbound BinkPHoldQueue
    short of hand-written SQL against the live database -- exactly the
    kind of one-off, error-prone, unrepeatable operation a real UI
    button exists to prevent. Admin-only: echomail is shared FTN network
    content, not a personal post -- unlike boards.delete_post (author OR
    admin OR moderator), a regular user has no standing to remove a
    network message even if they authored it, since once tossed it's
    already out of local control anyway. This only ever removes the
    LOCAL copy/any not-yet-sent outbound queue entries -- it cannot
    recall a copy that's already reached a peer.
    """
    if not getattr(current_user, 'is_admin', False):
        abort(403)
    echo_area = EchoArea.query.get_or_404(area_id)
    msg = EchomailMessage.query.filter_by(id=message_id, area_id=area_id).first_or_404()

    # Stop any not-yet-delivered copies from going out before removing
    # the message itself -- this is the exact step that otherwise
    # requires hand-written SQL against BinkPHoldQueue, found live.
    pending_count = BinkPHoldQueue.query.filter_by(message_id=message_id).count()
    BinkPHoldQueue.query.filter_by(message_id=message_id).delete()
    EchomailReadStatus.query.filter_by(message_id=message_id).delete()
    # last_message_id is a nullable high-water-mark pointer, not a hard
    # reference -- null it out rather than deleting the subscription row
    # itself (that row also carries the node's QWK conf_number mapping).
    QWKNodeLastSent.query.filter_by(last_message_id=message_id).update(
        {'last_message_id': None})

    db.session.delete(msg)
    db.session.commit()

    if pending_count:
        flash(f'Message deleted, including {pending_count} not-yet-sent '
              f'outbound queue entr{"y" if pending_count == 1 else "ies"}. '
              f'Any copies already delivered to a peer cannot be recalled.',
              'success')
    else:
        flash('Message deleted.', 'success')
    return redirect(url_for('echomail.area', area_id=echo_area.id))


@echomail_bp.route('/<int:area_id>/compose', methods=['GET', 'POST'])
@echomail_bp.route('/<int:area_id>/reply/<int:reply_to_id>', methods=['GET', 'POST'])
@login_required
def compose(area_id, reply_to_id=None):
    """Compose a new message or reply."""
    from flask import current_app

    echo_area = EchoArea.query.get_or_404(area_id)
    if not echo_area.is_active or not echo_area.is_subscribed:
        abort(404)
    _check_area_access(echo_area)

    original = None
    if reply_to_id:
        original = EchomailMessage.query.filter_by(id=reply_to_id, area_id=area_id).first_or_404()

    # Build area choices for the select field — filtered by user access level.
    user_lvl = getattr(current_user, 'access_level', 10) or 10
    is_admin = getattr(current_user, 'is_admin', False)
    q = EchoArea.query.filter_by(is_active=True, is_subscribed=True)
    if not is_admin:
        q = q.filter(EchoArea.is_sysop_only.is_(False))
        q = q.filter(EchoArea.min_access_level <= user_lvl)
    all_areas = q.all()
    area_choices = [(a.id, f'[{a.network.name}] {a.name}') for a in all_areas]

    form = ComposeForm()
    form.area_id.choices = area_choices

    if request.method == 'GET':
        form.area_id.data = area_id
        if original:
            form.to_name.data = original.from_name or ''
            subj = original.subject or ''
            form.subject.data = subj if subj.startswith('Re:') else f'Re: {subj}'
            quoted = '\n'.join(f'> {line}' for line in (original.body or '').splitlines())
            form.body.data = f'\n\n--- {original.from_name} wrote:\n{quoted}\n'

    if form.validate_on_submit():
        tear = current_app.config.get('ECHOMAIL_TEAR_LINE', '--- ANetBBS v1.0')
        origin = current_app.config.get('ECHOMAIL_ORIGIN_LINE', 'ANetBBS')

        # The user may have changed the target area via the dropdown --
        # resolve the ACTUAL destination area (not just the one the URL
        # was opened against) so the real-name check below can't be
        # bypassed by switching to a different area after loading the
        # form. Falls back to echo_area if the picked id is somehow gone.
        target_area = EchoArea.query.get(form.area_id.data) or echo_area
        from ..features.access_control import resolve_post_name
        post_name, name_error = resolve_post_name(
            current_user, target_area.require_real_name)
        if name_error:
            flash(name_error, 'danger')
            from ..models import get_active_taglines
            return render_template('echomail/compose.html',
                                   form=form,
                                   echo_area=echo_area,
                                   original=original,
                                   taglines=get_active_taglines())

        # Append the user's tagline (FTN footer convention) if set.
        body = form.body.data
        if getattr(current_user, 'tagline', None):
            body = body.rstrip() + '\n\n... ' + current_user.tagline + '\n'

        # Optional random pool tagline (opt-in checkbox) -- distinct from
        # the fixed profile tagline above; classic "-- " signature
        # separator so the two are visually distinguishable.
        _tagline_id = request.form.get('tagline_id', type=int)
        if _tagline_id:
            from ..models import Tagline, format_tagline_append
            _t = Tagline.query.filter_by(id=_tagline_id, is_active=True).first()
            if _t:
                body = body.rstrip('\n') + format_tagline_append(_t.text)

        msg = EchomailMessage(
            area_id=form.area_id.data,
            network_id=echo_area.network_id,
            from_name=post_name,
            to_name=form.to_name.data,
            subject=maybe_tag_ansi_subject(form.subject.data, body),
            body=body,
            tear_line=tear,
            origin_line=origin,
            direction='outbound',
        )
        db.session.add(msg)
        db.session.commit()

        # Real gap found live: a hub with real downstream nodes (a real
        # sysop reported never receiving a single packet despite dozens
        # of real local messages existing in areas they're correctly
        # subscribed to) -- toss_message() was ONLY ever called from the
        # inbound-import paths (binkp_server.py/poller.py/qwk_hub_ftp.py/
        # web/qwk_hub.py), despite the tosser's own module docstring
        # explicitly saying it handles "locally composed" messages too.
        # None of the three local composers (this route, bbs_ui.py's
        # _compose_echomail, petscii_ui.py's _echo_compose) ever actually
        # called it -- a message composed directly on the hub just sat
        # there, visible locally, but never queued for any downstream node.
        from ..echomail.tosser import toss_message
        toss_message(msg.id)

        # Real gap found live: a LOCAL user posting/replying directly in a
        # shared echo area never went through any of the three inbound-
        # network-import notify hooks (poller.py/binkp_server.py/
        # qwk_hub_ftp.py) -- those only fire when a message arrives FROM
        # an external network transfer. Two ANetBBS accounts replying to
        # each other in the same area is a fourth, previously-unhooked
        # write path into EchomailMessage. maybe_notify_recipient() is
        # already best-effort/self-swallowing, same as every other call
        # site -- no extra try/except needed here.
        from ..echomail.notify_reply import maybe_notify_recipient
        from ..models import EchomailNetwork
        network = EchomailNetwork.query.get(echo_area.network_id)
        if network is not None:
            maybe_notify_recipient(msg, echo_area, network)

        flash('Message queued for sending.', 'success')
        return redirect(url_for('echomail.area', area_id=form.area_id.data))

    from ..models import get_active_taglines
    return render_template('echomail/compose.html',
                           form=form,
                           echo_area=echo_area,
                           original=original,
                           taglines=get_active_taglines())


@echomail_bp.route('/netmail', methods=['GET'])
@login_required
def netmail_inbox():
    """List netmail (private 1-on-1 messages) the user can see.

    Real gap found in a full application-wide access-control audit --
    same bug class as web/netmail.py's now-fixed _user_addresses(): this
    used to show the last 100 messages from EVERY network's NETMAIL area
    to ANY logged-in user with zero recipient filtering at all. Now
    scoped via _owns_netmail_echomail() (admins still see everything --
    the historical "sysop reviews inbound mail" catch-all, but only for
    admins now, not every user).
    """
    networks = EchomailNetwork.query.filter_by(is_active=True).all()
    netmail_areas = []
    for net in networks:
        area = EchoArea.query.filter_by(network_id=net.id, tag='NETMAIL').first()
        if area is None:
            continue
        msgs = (EchomailMessage.query
                .filter_by(area_id=area.id)
                .order_by(EchomailMessage.created_at.desc())
                .limit(200).all())
        msgs = [m for m in msgs if _owns_netmail_echomail(m, current_user)][:100]
        netmail_areas.append({'network': net, 'area': area, 'messages': msgs})
    return render_template('echomail/netmail_inbox.html',
                           netmail_areas=netmail_areas)


@echomail_bp.route('/netmail/compose', methods=['GET', 'POST'])
@login_required
def netmail_compose():
    """Compose a 1-on-1 QWK netmail to a user on another DOVE-Net BBS."""
    from flask import current_app

    qwk_networks = EchomailNetwork.query.filter_by(
        is_active=True, network_type='qwk').all()
    if not qwk_networks:
        flash('No QWK networks configured.', 'warning')
        return redirect(url_for('echomail.index'))

    form = NetmailForm()
    form.network_id.choices = [(n.id, n.name) for n in qwk_networks]

    if form.validate_on_submit():
        network = EchomailNetwork.query.get_or_404(form.network_id.data)

        # Find or create the virtual NETMAIL area for this network so
        # outbound netmail has somewhere to live in the DB.
        area = EchoArea.query.filter_by(network_id=network.id, tag='NETMAIL').first()
        if area is None:
            area = EchoArea(network_id=network.id, tag='NETMAIL',
                            name='Netmail (Private)', is_subscribed=True,
                            is_active=True)
            db.session.add(area)
            db.session.flush()

        from ..features.access_control import resolve_post_name
        post_name, name_error = resolve_post_name(
            current_user, network.require_real_name_netmail)
        if name_error:
            flash(name_error, 'danger')
            from ..models import get_active_taglines
            return render_template('echomail/netmail_compose.html', form=form,
                                   taglines=get_active_taglines())

        tear = current_app.config.get('ECHOMAIL_TEAR_LINE', '--- ANetBBS v1.0')
        origin = current_app.config.get('ECHOMAIL_ORIGIN_LINE', 'ANetBBS')

        # Append the user's tagline (FTN convention) if set.
        body = form.body.data
        if getattr(current_user, 'tagline', None):
            body = body.rstrip() + '\n\n... ' + current_user.tagline + '\n'

        # Optional random pool tagline (opt-in checkbox) -- distinct from
        # the fixed profile tagline above; classic "-- " signature
        # separator so the two are visually distinguishable.
        _tagline_id = request.form.get('tagline_id', type=int)
        if _tagline_id:
            from ..models import Tagline, format_tagline_append
            _t = Tagline.query.filter_by(id=_tagline_id, is_active=True).first()
            if _t:
                body = body.rstrip('\n') + format_tagline_append(_t.text)

        msg = EchomailMessage(
            area_id=area.id,
            network_id=network.id,
            from_name=post_name,
            to_name=form.to_name.data.strip(),
            to_address=(form.to_address.data or '').strip() or None,
            subject=maybe_tag_ansi_subject(form.subject.data, body),
            body=body,
            tear_line=tear,
            origin_line=origin,
            direction='netmail',
        )
        db.session.add(msg)
        db.session.commit()

        flash(f'Netmail to {msg.to_name} queued — will go out on next poll.',
              'success')
        return redirect(url_for('echomail.netmail_inbox'))

    from ..models import get_active_taglines
    return render_template('echomail/netmail_compose.html', form=form,
                           taglines=get_active_taglines())


@echomail_bp.route('/<int:area_id>/next-unread')
@login_required
def next_unread(area_id):
    """Jump to the next unread message in this area for the current user.

    Uses EchomailLastRead as a fast pointer; if none, falls back to scanning
    EchomailReadStatus to find the first unseen message id."""
    echo_area = EchoArea.query.get_or_404(area_id)
    _check_area_access(echo_area)

    lr = (EchomailLastRead.query.filter_by(
        user_id=current_user.id, area_id=area_id).first())
    last_id = lr.last_message_id if lr and lr.last_message_id else 0

    nxt = (EchomailMessage.query
           .filter(EchomailMessage.area_id == area_id,
                   EchomailMessage.id > last_id)
           .order_by(EchomailMessage.id)
           .first())
    if nxt is None:
        # Fall back: maybe the lastread is stale; check read-status table.
        read_ids = {rs.message_id for rs in EchomailReadStatus.query.filter_by(
            user_id=current_user.id).all()}
        nxt = (EchomailMessage.query
               .filter(EchomailMessage.area_id == area_id,
                       ~EchomailMessage.id.in_(read_ids) if read_ids else db.true())
               .order_by(EchomailMessage.created_at)
               .first())
    if nxt is None:
        flash('No unread messages in this area.', 'info')
        return redirect(url_for('echomail.area', area_id=area_id))
    return redirect(url_for('echomail.read',
                            area_id=area_id, message_id=nxt.id))


@echomail_bp.route('/<int:area_id>/mark_read', methods=['POST'])
@login_required
def mark_all_read(area_id):
    """Mark all messages in an area as read for the current user."""
    # Real gap found in a full echomail-subsystem audit: every sibling
    # area-scoped route in this file (area, thread, read, compose,
    # next_unread) calls _check_area_access() -- this one didn't, letting
    # a user without access to a sysop-only/high-min_access_level area
    # write EchomailReadStatus/EchomailLastRead rows scoped to its
    # message IDs, and confirm the area exists via 200-vs-404.
    echo_area = EchoArea.query.get_or_404(area_id)
    _check_area_access(echo_area)

    # Find messages not yet read
    already_read = {
        rs.message_id
        for rs in EchomailReadStatus.query.filter_by(user_id=current_user.id).all()
    }
    msgs = EchomailMessage.query.filter(
        EchomailMessage.area_id == area_id,
        ~EchomailMessage.id.in_(already_read) if already_read else db.true()
    ).all()

    for msg in msgs:
        db.session.add(EchomailReadStatus(user_id=current_user.id, message_id=msg.id))
    db.session.commit()
    flash('All messages marked as read.', 'success')
    return redirect(url_for('echomail.area', area_id=area_id))
