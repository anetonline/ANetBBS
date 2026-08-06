# anetbbs/web/netmail.py
"""
FidoNet netmail blueprint — user-facing inbox/sent/compose/read/reply.

Netmail (point-to-point FTN mail) is separate from echomail (broadcast areas)
and from PMs (local-only direct messages between users on this BBS).

Routes:
    /netmail/                           inbox (received netmail)
    /netmail/sent                       sent items
    /netmail/<id>                       read a message
    /netmail/compose                    new message
    /netmail/<id>/reply                 reply to an existing message
    /netmail/<id>/delete                soft-delete (per-side)
"""
import datetime
from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, abort, jsonify)
from flask_login import login_required, current_user

from ..models import (db, NetmailMessage, EchomailNetwork, UserAka,
                      maybe_tag_ansi_subject)
from ..echomail.kludges import make_msgid
from ..echomail.routing import (parse_address, find_network_for_address,
                                find_aka_for_network)


netmail_bp = Blueprint('netmail', __name__, url_prefix='/netmail')

# Same alias list binkp_server.py's dispatch uses to recognize AreaFix/
# FileFix robot netmail (anetbbs/echomail/binkp_server.py, ~line 1560).
_ROBOT_NAMES = ('areafix', 'area fix', 'areamgr', 'filefix', 'file fix', 'filemgr')


def _not_robot_netmail(name_column):
    """Real gap found live: every AreaFix/FileFix command Craig's node
    sent landed in the sysop's personal Netmail Inbox as ordinary mail
    (the admin catch-all address in _user_addresses() matches the hub's
    own bare address, which robot netmail is addressed to/from). This
    got a lot more visible after the v1.0b2.201 dedup-exemption fix,
    which correctly stopped silently dropping repeat AreaFix commands --
    previously the clutter was accidentally hidden by the same bug that
    dropped real commands. AreafixLog already captures every request +
    response for admin review (see web/echomail_admin.py's areafix_log
    view), so robot netmail doesn't need to also appear as 1-on-1 mail.
    NULL-safe: a message with no name recorded is never a robot message.
    """
    return db.or_(name_column.is_(None),
                  db.func.lower(name_column).notin_(_ROBOT_NAMES))


def _user_addresses(user):
    """All FTN addresses associated with this user — used to filter inbox/sent.

    Returns a list of strings. The user's AKAs are primary; the username also
    matches if the netmail was addressed by name rather than by address.

    SECURITY FIX (real live bug reported by the sysop): this used to add
    every active network's own `our_address` to EVERY user's list
    unconditionally, not just admins. Since _user_owns() (gating read/
    delete/reply-authorization) and the inbox()/sent() list queries all
    call this function, ANY regular user could read -- and delete -- a
    netmail addressed to the BBS's own raw hub address (exactly what an
    unresolved "to the sysop" netmail from a new/unrecognized node looks
    like) purely because they happened to be logged in, with no
    connection to the message at all. Only an admin acts as the
    catch-all identity for the BBS's own bare FTN address now. A
    genuinely unresolved netmail with no other match still lands in the
    admin-only "Unclaimed Netmail" review queue
    (/admin/echomail/unclaimed_netmail) instead of leaking to everyone.
    """
    addrs = [a.address for a in (UserAka.query
                                  .filter_by(user_id=user.id).all())]
    if getattr(user, 'is_admin', False):
        for net in EchomailNetwork.query.filter_by(is_active=True).all():
            if net.our_address and net.our_address not in addrs:
                addrs.append(net.our_address)
    return addrs


@netmail_bp.route('/')
@login_required
def inbox():
    """Show netmail addressed to this user (by name or any of their AKAs).

    We match on either:
      - to_user_id == current_user.id  (an internal pointer set when the
        netmail came in to a known local user), or
      - to_name matches the username (case-insensitive — common for
        first-time arrivals before we link them to an account)
    """
    addrs = _user_addresses(current_user)
    q = NetmailMessage.query.filter(
        NetmailMessage.direction == 'inbound',
        NetmailMessage.deleted_by_recipient.is_(False),
        _not_robot_netmail(NetmailMessage.to_name),
    )
    name_clauses = [
        NetmailMessage.to_user_id == current_user.id,
        NetmailMessage.to_name.ilike(current_user.username),
    ]
    if current_user.display_name:
        name_clauses.append(NetmailMessage.to_name.ilike(current_user.display_name))
    if addrs:
        name_clauses.append(NetmailMessage.to_address.in_(addrs))
    q = q.filter(db.or_(*name_clauses))
    msgs = q.order_by(NetmailMessage.created_at.desc()).limit(200).all()
    return render_template('netmail/list.html',
                           messages=msgs, folder='inbox')


@netmail_bp.route('/drafts')
@login_required
def drafts():
    addrs = _user_addresses(current_user)
    q = NetmailMessage.query.filter(
        NetmailMessage.direction == 'outbound',
        NetmailMessage.status == 'draft',
        NetmailMessage.deleted_by_sender.is_(False),
    )
    name_clauses = [NetmailMessage.from_user_id == current_user.id]
    if addrs:
        name_clauses.append(NetmailMessage.from_address.in_(addrs))
    q = q.filter(db.or_(*name_clauses))
    # inbox()/sent() both cap at .limit(200) -- this one didn't, found in
    # a full echomail-subsystem audit. Scoped to the current user only so
    # low practical risk, but inconsistent with its siblings.
    msgs = q.order_by(NetmailMessage.created_at.desc()).limit(200).all()
    return render_template('netmail/list.html', messages=msgs, folder='drafts')


@netmail_bp.route('/sent')
@login_required
def sent():
    addrs = _user_addresses(current_user)
    q = NetmailMessage.query.filter(
        NetmailMessage.direction == 'outbound',
        NetmailMessage.deleted_by_sender.is_(False),
        _not_robot_netmail(NetmailMessage.from_name),
    )
    name_clauses = [
        NetmailMessage.from_user_id == current_user.id,
        NetmailMessage.from_name.ilike(current_user.username),
    ]
    if addrs:
        name_clauses.append(NetmailMessage.from_address.in_(addrs))
    q = q.filter(db.or_(*name_clauses))
    msgs = q.order_by(NetmailMessage.created_at.desc()).limit(200).all()
    return render_template('netmail/list.html',
                           messages=msgs, folder='sent')


@netmail_bp.route('/<int:msg_id>')
@login_required
def read(msg_id):
    m = NetmailMessage.query.get_or_404(msg_id)
    # Authorize: this user must be the sender or recipient.
    if not _user_owns(m):
        abort(403)
    if m.direction == 'inbound' and m.read_at is None:
        m.read_at = datetime.datetime.utcnow()
        m.status = 'read'
        db.session.commit()
    return render_template('netmail/read.html', m=m)


@netmail_bp.route('/<int:msg_id>/markdown')
@login_required
def read_markdown(msg_id):
    """Lazy JSON endpoint backing read.html's "Toggle Markdown view" --
    see echomail.py's read_markdown() for why this moved off the
    always-on render path. Same ownership check as read() above."""
    m = NetmailMessage.query.get_or_404(msg_id)
    if not _user_owns(m):
        abort(403)
    from .markdown_render import render_markdown
    return jsonify({'html': str(render_markdown(m.body))})


@netmail_bp.route('/compose', methods=['GET', 'POST'])
@netmail_bp.route('/<int:reply_to>/reply', methods=['GET', 'POST'])
@login_required
def compose(reply_to=None):
    parent = NetmailMessage.query.get(reply_to) if reply_to else None
    if parent and not _user_owns(parent):
        abort(403)

    # A reply to netmail that itself arrived with no EchomailNetwork match
    # (an anonymous/unrecognized-peer BinkP session -- see binkp_server.py's
    # nodelist crashmail-compliance fix) can't be routed the normal way:
    # there's no hub_address to send it through, because there's no hub in
    # this relationship at all -- the sender crash-delivered straight to
    # us. The correct, symmetric reply is to crash-deliver straight back,
    # dialing the sender's own real IP (captured at receive time) directly
    # instead of requiring the sysop to be a formal member of their zone's
    # network. `origin_ip` is a server-set column, never user input, so
    # this can't be used to make the server dial an arbitrary attacker-
    # chosen host -- see NetmailMessage.origin_ip's own model docstring.
    is_direct_crash_reply = bool(
        parent and parent.direction == 'inbound'
        and parent.network_id is None and parent.origin_ip)

    networks = EchomailNetwork.query.filter_by(is_active=True).all()
    user_akas = UserAka.query.filter_by(user_id=current_user.id).all()

    if request.method == 'POST':
        to_address = (request.form.get('to_address') or '').strip()
        to_name = (request.form.get('to_name') or '').strip()
        subject = (request.form.get('subject') or '').strip()
        body = request.form.get('body') or ''
        from_aka = (request.form.get('from_aka') or '').strip()
        is_crash = bool(request.form.get('is_crash'))
        is_private = bool(request.form.get('is_private'))
        save_as_draft = bool(request.form.get('save_draft'))

        # For a direct crash-reply, the destination is fixed to who
        # actually delivered the original message -- never trust the
        # posted to_address/to_name for this case (the compose template
        # renders them readonly, but the real boundary is here: only
        # parent.from_address/from_name, read from the DB, ever reach
        # the delivery-target fields below).
        if is_direct_crash_reply:
            to_address = parent.from_address or ''
            to_name = parent.from_name or ''

        if not parse_address(to_address):
            flash(f'Invalid FTN address: {to_address!r}', 'danger')
            from ..models import get_active_taglines
            return render_template('netmail/compose.html',
                                   networks=networks, user_akas=user_akas,
                                   parent=parent,
                                   form=request.form,
                                   is_direct_crash_reply=is_direct_crash_reply,
                                   taglines=get_active_taglines())
        if not to_name:
            flash('Recipient name is required.', 'danger')
            from ..models import get_active_taglines
            return render_template('netmail/compose.html',
                                   networks=networks, user_akas=user_akas,
                                   parent=parent,
                                   form=request.form,
                                   is_direct_crash_reply=is_direct_crash_reply,
                                   taglines=get_active_taglines())

        if is_direct_crash_reply:
            network = None
        elif parent and parent.network_id:
            # Reply via the SAME network the original arrived on, not a
            # fresh zone-based lookup. Real gap found live: a netmail
            # can legitimately cross zones via ordinary FTN store-and-
            # forward routing through a hub with a zone gate -- the
            # network that just delivered it already proved it can
            # carry traffic for that zone, so find_network_for_address()'s
            # "our_address zone must match the destination zone" test
            # (correct for picking a network to compose a FRESH message
            # through, with no prior routing evidence either way) was
            # the wrong test for a reply and blocked one that had
            # already-proven working delivery moments earlier.
            network = EchomailNetwork.query.filter_by(
                id=parent.network_id, is_active=True).first()
            if network is None:
                flash('The network this netmail arrived on is no longer '
                     'active.', 'danger')
                from ..models import get_active_taglines
                return render_template('netmail/compose.html',
                                       networks=networks, user_akas=user_akas,
                                       parent=parent,
                                       form=request.form,
                                       is_direct_crash_reply=is_direct_crash_reply,
                                       taglines=get_active_taglines())
        else:
            # Pick the network that matches the destination zone
            network = find_network_for_address(to_address)
            if network is None:
                flash(f'No active FTN network covers zone of {to_address}.', 'danger')
                from ..models import get_active_taglines
                return render_template('netmail/compose.html',
                                       networks=networks, user_akas=user_akas,
                                       parent=parent,
                                       form=request.form,
                                       is_direct_crash_reply=is_direct_crash_reply,
                                       taglines=get_active_taglines())

        from ..features.access_control import resolve_post_name
        # No EchomailNetwork exists for a direct crash-reply, so there's
        # no require_real_name_netmail policy to enforce -- matches how
        # every other network-scoped policy in this codebase defaults to
        # permissive when unset/not applicable.
        post_name, name_error = resolve_post_name(
            current_user,
            network.require_real_name_netmail if network else False)
        if name_error:
            flash(name_error, 'danger')
            from ..models import get_active_taglines
            return render_template('netmail/compose.html',
                                   networks=networks, user_akas=user_akas,
                                   parent=parent,
                                   form=request.form,
                                   is_direct_crash_reply=is_direct_crash_reply,
                                   taglines=get_active_taglines())

        # Pick the FROM address: explicit AKA, network-matched AKA, or
        # network's `our_address` -- for a direct crash-reply (no
        # network), fall back to the AKA the original was addressed to,
        # so the reply comes from the same identity that received it.
        if from_aka:
            from_address = from_aka
        elif network:
            aka = find_aka_for_network(current_user, network)
            from_address = aka.address if aka else network.our_address
        else:
            from_address = parent.to_address or ''

        # Append the user's tagline + FTN trailer to the body. Tagline appears
        # as the line right above the tear "---" so downstream readers see it
        # in canonical FTN format.
        if current_user.tagline:
            body = body.rstrip() + '\n\n... ' + current_user.tagline + '\n'

        # Optional random pool tagline (opt-in checkbox) -- distinct from
        # the user's own fixed profile tagline above; uses the classic
        # "-- " signature-separator format instead of the FTN "... " tear
        # line so the two are visually distinguishable.
        _tagline_id = request.form.get('tagline_id', type=int)
        if _tagline_id:
            from ..models import Tagline, format_tagline_append
            _t = Tagline.query.filter_by(id=_tagline_id, is_active=True).first()
            if _t:
                body = body.rstrip('\n') + format_tagline_append(_t.text)

        # Generate kludges
        from_name = post_name
        msgid_value = make_msgid(from_address)
        # Kludge formats:
        # - MSGID/CHRS/PID/TZUTC/REPLYADDR/REPLYTO: colon form is the
        #   canonical FTS spec and accepted everywhere.
        # - INTL/FMPT/TOPT: SPACE form (no colon) per FTS-4001 / FSC-4009.
        #   Mystic AreaFix matches strictly per spec; the colon form makes
        #   the point address invisible to the bot, so the per-point
        #   AreaFix password lookup fails. binkterm-php's reference
        #   (BinkdProcessor.php:1965) is the canonical no-colon form.
        kludges = [f'MSGID: {msgid_value}']
        non_ascii = any(ord(c) > 127 for c in body)
        chrs = 'UTF-8 4' if non_ascii else 'CP437 2'
        kludges.append(f'CHRS: {chrs}')
        kludges.append('PID: anetbbs/0.1')
        try:
            local_offset = datetime.datetime.now().astimezone().strftime('%z')
            if not local_offset:
                local_offset = '+0000'
        except Exception:
            local_offset = '+0000'
        kludges.append(f'TZUTC: {local_offset}')
        # INTL — required for inter-zone netmail (FTS-4001)
        from_p = parse_address(from_address)
        to_p = parse_address(to_address)
        if from_p and to_p and from_p[0] != to_p[0]:
            kludges.append(
                f'INTL {to_p[0]}:{to_p[1]}/{to_p[2]} {from_p[0]}:{from_p[1]}/{from_p[2]}')
        # FMPT/TOPT — point routing (FSC-4009 — space form, no colon)
        if from_p and from_p[3]:
            kludges.append(f'FMPT {from_p[3]}')
        if to_p and to_p[3]:
            kludges.append(f'TOPT {to_p[3]}')
        # REPLYADDR / REPLYTO — explicit reply-to (FSC-0035).
        kludges.append(f'REPLYADDR: {from_address}')
        kludges.append(f'REPLYTO: {from_address} {from_name}')
        # REPLY — if this is a reply with a parent that has a MSGID
        reply_msgid = ''
        if parent:
            from ..echomail.kludges import kludges_from_json, find_kludge
            parent_kludges = kludges_from_json(parent.kludges)
            reply_msgid = find_kludge(parent_kludges, 'MSGID')
            if reply_msgid:
                kludges.append(f'REPLY: {reply_msgid}')

        m = NetmailMessage(
            network_id=(network.id if network else None),
            from_user_id=current_user.id,
            from_address=from_address,
            to_address=to_address,
            from_name=from_name,
            to_name=to_name,
            subject=maybe_tag_ansi_subject(subject, body),
            body=body,
            kludges=__import__('json').dumps(kludges),
            msgid=msgid_value,
            reply_msgid=reply_msgid,
            chrs=chrs,
            is_private=is_private,
            # A direct crash-reply genuinely IS crash-delivered (no hub
            # store-and-forward involved at all), regardless of whether
            # the sysop happened to also tick the checkbox.
            is_crash=(is_crash or is_direct_crash_reply),
            is_local=True,
            direction='outbound',
            status='draft' if save_as_draft else 'queued',
            origin_ip=(parent.origin_ip if is_direct_crash_reply else None),
        )
        db.session.add(m)
        db.session.commit()
        if save_as_draft:
            flash('Saved as draft.', 'info')
            return redirect(url_for('netmail.drafts'))

        if is_direct_crash_reply:
            # Crash mail means immediate delivery attempt, not waiting
            # for a scheduled poll -- and there IS no scheduled poll for
            # an ad-hoc unlisted address anyway (no EchomailNetwork row
            # to attach a schedule to). Same established pattern as the
            # admin "Poll Now" button (echomail_admin.py's poll_now()):
            # background thread, request returns immediately.
            import threading
            from flask import current_app
            from ..echomail.poller import send_netmail_direct_now
            _app = current_app._get_current_object()
            _msg_id = m.id

            def _run():
                try:
                    send_netmail_direct_now(_app, _msg_id)
                except Exception as exc:
                    _app.logger.error('Direct crash-send thread error: %s', exc)

            threading.Thread(target=_run, daemon=True).start()
            flash(f'Crash-dialing {to_address} directly to deliver this reply '
                 f'— check the message for delivery status shortly.', 'info')
            return redirect(url_for('netmail.read', msg_id=m.id))
        flash('Netmail queued for next outbound poll.', 'success')
        return redirect(url_for('netmail.read', msg_id=m.id))

    # GET — render form. Sources of pre-fill, in priority order:
    #   1. ?draft_id=N  — load a saved draft for further editing
    #   2. parent (reply) — quoted body + Re: subject
    #   3. ?to_address / ?to_name querystring (compose-from-contact links)
    initial = {}
    draft_id = request.args.get('draft_id', type=int)
    draft = None
    if draft_id:
        draft = NetmailMessage.query.get(draft_id)
        if draft and (draft.from_user_id != current_user.id
                      or draft.status != 'draft'):
            draft = None
    if draft:
        initial = {
            'to_address': draft.to_address or '',
            'to_name': draft.to_name or '',
            'subject': draft.subject or '',
            'body': draft.body or '',
            'is_crash': draft.is_crash,
            'is_private': draft.is_private,
        }
    elif parent:
        initial = {
            'to_address': parent.from_address,
            'to_name': parent.from_name,
            'subject': parent.subject if (parent.subject or '').lower().startswith('re:')
                       else f'Re: {parent.subject}',
            'body': '\n'.join('> ' + l for l in (parent.body or '').splitlines())
                    + '\n\n',
        }
    else:
        initial = {
            'to_address': request.args.get('to_address', ''),
            'to_name': request.args.get('to_name', ''),
            'subject': request.args.get('subject', ''),
        }
    from ..models import get_active_taglines
    return render_template('netmail/compose.html',
                           networks=networks, user_akas=user_akas,
                           parent=parent, form=initial,
                           is_direct_crash_reply=is_direct_crash_reply,
                           taglines=get_active_taglines())


@netmail_bp.route('/<int:msg_id>/delete', methods=['POST'])
@login_required
def delete(msg_id):
    m = NetmailMessage.query.get_or_404(msg_id)
    if not _user_owns(m):
        abort(403)
    if m.direction == 'inbound':
        m.deleted_by_recipient = True
    else:
        m.deleted_by_sender = True
    db.session.commit()
    flash('Message deleted from your view.', 'success')
    return redirect(url_for('netmail.inbox' if m.direction == 'inbound'
                            else 'netmail.sent'))


def _user_owns(m):
    """Authorization helper — is `m` visible to the current user?

    Either they're the linked sender/recipient (DB-side foreign key), or the
    name field matches their username, or the address matches one of their
    AKAs / a network's our_address."""
    if m.from_user_id == current_user.id or m.to_user_id == current_user.id:
        return True
    addrs = set(_user_addresses(current_user))
    if m.to_address in addrs or m.from_address in addrs:
        return True
    uname = (current_user.username or '').lower()
    if (m.to_name or '').lower() == uname:
        return True
    if (m.from_name or '').lower() == uname:
        return True
    return False
