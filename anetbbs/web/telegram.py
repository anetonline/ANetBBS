# anetbbs/web/telegram.py
"""
Inter-BBS Telegrams.

In FidoNet/Synchronet-land a "Telegram" is a one-line attention-getting
message sent across BBSes. We model it as a netmail with:
    - subject prefixed "TELEGRAM:"
    - is_crash=True (direct delivery, no uplink dwell)
    - is_immediate=True
    - is_private=True

Inbound netmails with subject starting "TELEGRAM:" are detected by the
NetmailMessage import path and surfaced as a separate folder for users.
"""
import datetime as _dt
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user

from ..models import (db, NetmailMessage, EchomailNetwork, UserAka)
from ..echomail.kludges import make_msgid
from ..echomail.routing import (parse_address, find_network_for_address,
                                find_aka_for_network)


telegram_bp = Blueprint('telegram', __name__, url_prefix='/telegram')


def _is_telegram(m):
    return (m.subject or '').upper().startswith('TELEGRAM:')


@telegram_bp.route('/')
@login_required
def index():
    """Inbox of telegrams sent TO this user."""
    addrs = []
    for net in EchomailNetwork.query.filter_by(is_active=True).all():
        if net.our_address:
            addrs.append(net.our_address)
    addrs.extend(a.address for a in UserAka.query
                 .filter_by(user_id=current_user.id).all())

    q = NetmailMessage.query.filter(
        NetmailMessage.subject.ilike('TELEGRAM:%'),
        NetmailMessage.deleted_by_recipient.is_(False),
    )
    name_clauses = [
        NetmailMessage.to_user_id == current_user.id,
        NetmailMessage.to_name.ilike(current_user.username),
    ]
    if addrs:
        name_clauses.append(NetmailMessage.to_address.in_(addrs))
    q = q.filter(NetmailMessage.direction == 'inbound') \
         .filter(db.or_(*name_clauses))
    msgs = q.order_by(NetmailMessage.created_at.desc()).limit(100).all()
    return render_template('telegram/index.html', messages=msgs, folder='inbox')


@telegram_bp.route('/sent')
@login_required
def sent():
    addrs = [a.address for a in UserAka.query
             .filter_by(user_id=current_user.id).all()]
    q = NetmailMessage.query.filter(
        NetmailMessage.direction == 'outbound',
        NetmailMessage.subject.ilike('TELEGRAM:%'),
        NetmailMessage.deleted_by_sender.is_(False),
    )
    name_clauses = [NetmailMessage.from_user_id == current_user.id]
    if addrs:
        name_clauses.append(NetmailMessage.from_address.in_(addrs))
    q = q.filter(db.or_(*name_clauses))
    msgs = q.order_by(NetmailMessage.created_at.desc()).limit(100).all()
    return render_template('telegram/index.html', messages=msgs, folder='sent')


@telegram_bp.route('/send', methods=['GET', 'POST'])
@login_required
def send():
    networks = EchomailNetwork.query.filter_by(is_active=True).all()
    user_akas = UserAka.query.filter_by(user_id=current_user.id).all()

    if request.method == 'POST':
        to_address = (request.form.get('to_address') or '').strip()
        to_name = (request.form.get('to_name') or '').strip()
        body = (request.form.get('body') or '').strip()

        if not parse_address(to_address):
            flash(f'Invalid FTN address: {to_address!r}', 'danger')
            return render_template('telegram/send.html',
                                   networks=networks, user_akas=user_akas,
                                   form=request.form)
        if not to_name:
            flash('Recipient name required.', 'danger')
            return render_template('telegram/send.html',
                                   networks=networks, user_akas=user_akas,
                                   form=request.form)
        if not body:
            flash('Empty telegram.', 'warning')
            return render_template('telegram/send.html',
                                   networks=networks, user_akas=user_akas,
                                   form=request.form)
        if len(body) > 1000:
            flash('Telegram too long (max 1000 chars).', 'danger')
            return render_template('telegram/send.html',
                                   networks=networks, user_akas=user_akas,
                                   form=request.form)

        network = find_network_for_address(to_address)
        if network is None:
            flash(f'No active FTN network covers zone of {to_address}.', 'danger')
            return render_template('telegram/send.html',
                                   networks=networks, user_akas=user_akas,
                                   form=request.form)

        from_aka = find_aka_for_network(current_user, network)
        from_address = from_aka.address if from_aka else network.our_address
        from_name = current_user.display_name or current_user.username
        msgid_value = make_msgid(from_address)

        # Telegrams should arrive flagged: crash, immediate, private.
        kludges = [
            f'MSGID {msgid_value}',
            'CHRS UTF-8 4',
            'PID anetbbs/telegram',
        ]
        from_p = parse_address(from_address)
        to_p = parse_address(to_address)
        if from_p and to_p and from_p[0] != to_p[0]:
            kludges.append(
                f'INTL {to_p[0]}:{to_p[1]}/{to_p[2]} '
                f'{from_p[0]}:{from_p[1]}/{from_p[2]}')

        m = NetmailMessage(
            network_id=network.id,
            from_user_id=current_user.id,
            from_address=from_address,
            to_address=to_address,
            from_name=from_name,
            to_name=to_name,
            subject=f'TELEGRAM: {body[:60]}',
            body=body,
            kludges=__import__('json').dumps(kludges),
            msgid=msgid_value,
            chrs='UTF-8 4',
            is_private=True,
            is_crash=True,
            is_immediate=True,
            is_local=True,
            direction='outbound',
            status='queued',
            created_at=_dt.datetime.utcnow(),
        )
        db.session.add(m)
        db.session.commit()
        flash('Telegram queued for next outbound poll.', 'success')
        return redirect(url_for('telegram.sent'))

    return render_template('telegram/send.html',
                           networks=networks, user_akas=user_akas, form={})
