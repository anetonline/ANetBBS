# anetbbs/web/mail.py
"""
Internet Mail blueprint -- real internet email (username@domain),
sent/received through anetbbs/mail/outbound.py and
anetbbs/mail/smtp_server.py. Structured on anetbbs/web/pm.py's shape
(inbox/sent/compose/read/delete, real .paginate() rather than
netmail.py's unbounded .limit(200) anti-pattern pm.py's own comments
already flag), with one real difference: the recipient is an arbitrary
external address (free-text, validated), not a BBS username lookup.
"""
from flask import Blueprint, render_template, redirect, url_for, flash, request, abort
from flask_login import login_required, current_user
from wtforms import StringField, TextAreaField, SubmitField
from wtforms.validators import DataRequired, Length
from flask_wtf import FlaskForm
from datetime import datetime

from ..models import db, InternetMail, MailConfig
from ..mail.outbound import send_internet_mail
from ..mail.policy import (mark_inbound_read, mark_inbound_notification_read,
                           sender_warning)
from ..mail.presentation import render_body_html
from .validators import PermissiveEmail
from ..core.activity_log import web_log_activity

mail_bp = Blueprint('mail', __name__, url_prefix='/mail')

MAIL_PER_PAGE = 50


class ComposeForm(FlaskForm):
    to_address = StringField('To (email address)',
                             validators=[DataRequired(), PermissiveEmail()])
    subject = StringField('Subject', validators=[DataRequired(), Length(max=200)])
    content = TextAreaField('Message', validators=[DataRequired()])
    submit = SubmitField('Send')


@mail_bp.before_request
def _require_mail_enabled():
    """Every Internet Mail route needs a logged-in user, the whole
    feature on, AND this user opted in -- not just able to
    (can_enable_mail() only gates the opt-in TOGGLE itself, checked
    separately in profile.py). Same before_request-does-its-own-auth-
    check shape as docs.py/hub_admin.py in this blueprint style, not
    @login_required stacked onto a before_request hook."""
    if not current_user.is_authenticated:
        return redirect(url_for('auth.login', next=request.path))
    cfg = MailConfig.get()
    if not cfg.enabled or not current_user.email_enabled:
        flash('Internet Mail is not enabled on your account.', 'warning')
        return redirect(url_for('profile.edit'))


@mail_bp.route('/')
@login_required
def inbox():
    page = request.args.get('page', 1, type=int)
    pagination = InternetMail.query.filter_by(
        user_id=current_user.id, direction='inbound'
    ).order_by(InternetMail.created_at.desc()).paginate(
        page=page, per_page=MAIL_PER_PAGE, error_out=False)
    return render_template('mail/inbox.html', messages=pagination.items,
                           pagination=pagination)


@mail_bp.route('/sent')
@login_required
def sent():
    page = request.args.get('page', 1, type=int)
    pagination = InternetMail.query.filter_by(
        user_id=current_user.id, direction='outbound'
    ).order_by(InternetMail.created_at.desc()).paginate(
        page=page, per_page=MAIL_PER_PAGE, error_out=False)
    return render_template('mail/sent.html', messages=pagination.items,
                           pagination=pagination)


@mail_bp.route('/compose', methods=['GET', 'POST'])
@login_required
def compose():
    form = ComposeForm()
    if request.method == 'GET':
        to = request.args.get('to')
        if to:
            form.to_address.data = to
        subject = request.args.get('subject')
        if subject:
            form.subject.data = subject
        qid = request.args.get('quote')
        if qid and qid.isdigit():
            src = InternetMail.query.get(int(qid))
            if src and src.user_id == current_user.id:
                quoted = '\n'.join('> ' + ln
                                   for ln in (src.body or '').splitlines())
                form.content.data = f'{src.from_addr or "?"} wrote:\n{quoted}\n\n'

    if form.validate_on_submit():
        ok, err = send_internet_mail(
            current_user, form.to_address.data, form.subject.data,
            form.content.data, received_via='web')
        if not ok:
            flash(f'Could not send: {err}', 'danger')
            return render_template('mail/compose.html', form=form)
        # Recipient only: the subject is private message content with no
        # operational use in the sysop activity log (audit F-20).
        web_log_activity(current_user.id, 'internet_mail_sent',
                         details=f'to {form.to_address.data}')
        flash('Message accepted by the outbound relay. Final delivery can take a little longer.', 'success')
        return redirect(url_for('mail.sent'))

    return render_template('mail/compose.html', form=form)


@mail_bp.route('/<int:message_id>')
@login_required
def read(message_id):
    message = InternetMail.query.get_or_404(message_id)
    if message.user_id != current_user.id:
        abort(403)

    if mark_inbound_read(message, datetime.utcnow()):
        db.session.commit()

    return render_template('mail/read.html', message=message,
                           body_html=render_body_html(message.body),
                           sender_warning=sender_warning(message))


@mail_bp.route('/<int:message_id>/delete', methods=['POST'])
@login_required
def delete(message_id):
    message = InternetMail.query.get_or_404(message_id)
    if message.user_id != current_user.id:
        abort(403)
    was_inbound = message.direction == 'inbound'
    mark_inbound_notification_read(message)
    db.session.delete(message)
    db.session.commit()
    flash('Message deleted.', 'success')
    return redirect(url_for('mail.inbox' if was_inbound else 'mail.sent'))
