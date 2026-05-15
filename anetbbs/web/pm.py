# anetbbs/web/pm.py
"""
Private messaging blueprint
"""
from flask import Blueprint, render_template, redirect, url_for, flash, request, abort
from flask_login import login_required, current_user
from wtforms import StringField, TextAreaField, SubmitField
from wtforms.validators import DataRequired, Length
from flask_wtf import FlaskForm
from datetime import datetime, timedelta

from ..models import db, User, PrivateMessage

pm_bp = Blueprint('pm', __name__, url_prefix='/messages')

# Per-user PM rate limit (sysops exempt). 10 PMs per 5-minute window
# is generous for normal users but blocks runaway loops or spam scripts.
_PM_LIMIT = 10
_PM_WINDOW_SECONDS = 300


class ComposeForm(FlaskForm):
    recipient = StringField('To (username)', validators=[DataRequired()])
    subject = StringField('Subject', validators=[DataRequired(), Length(max=200)])
    content = TextAreaField('Message', validators=[DataRequired()])
    submit = SubmitField('Send')


@pm_bp.route('/')
@login_required
def inbox():
    messages = PrivateMessage.query.filter_by(
        recipient_id=current_user.id,
        is_deleted_recipient=False
    ).order_by(PrivateMessage.created_at.desc()).all()
    return render_template('pm/inbox.html', messages=messages)


@pm_bp.route('/sent')
@login_required
def sent():
    messages = PrivateMessage.query.filter_by(
        sender_id=current_user.id,
        is_deleted_sender=False
    ).order_by(PrivateMessage.created_at.desc()).all()
    return render_template('pm/sent.html', messages=messages)


@pm_bp.route('/compose', methods=['GET', 'POST'])
@pm_bp.route('/compose/<username>', methods=['GET', 'POST'])
@login_required
def compose(username=None):
    form = ComposeForm()
    if username and request.method == 'GET':
        form.recipient.data = username
        # If quote=<pm-id> is supplied, pre-fill the body with quoted text.
        qid = request.args.get('quote')
        if qid and qid.isdigit():
            try:
                src = PrivateMessage.query.get(int(qid))
                if src and (src.recipient_id == current_user.id
                            or src.sender_id == current_user.id):
                    quoted = '\n'.join('> ' + ln
                                       for ln in (src.content or '').splitlines())
                    form.subject.data = ('Re: ' + (src.subject or '')).strip()
                    form.content.data = (
                        f'{src.sender.username if src.sender else "?"} wrote:\n'
                        f'{quoted}\n\n')
            except Exception:
                pass

    if form.validate_on_submit():
        recipient = User.query.filter_by(username=form.recipient.data).first()
        if not recipient:
            flash('User not found.', 'danger')
            return render_template('pm/compose.html', form=form)
        if recipient.id == current_user.id:
            flash('You cannot message yourself.', 'danger')
            return render_template('pm/compose.html', form=form)

        # Honor recipient's block list — sysops bypass.
        try:
            from .blocks import is_blocked
            if is_blocked(recipient.id, current_user.id) and not getattr(current_user, 'is_admin', False):
                flash('That user has blocked you.', 'danger')
                return render_template('pm/compose.html', form=form)
        except Exception:
            pass

        # Rate limit (sysops exempt) — prevents PM spam scripts.
        if not getattr(current_user, 'is_admin', False):
            window_start = datetime.utcnow() - timedelta(seconds=_PM_WINDOW_SECONDS)
            recent = (PrivateMessage.query
                      .filter_by(sender_id=current_user.id)
                      .filter(PrivateMessage.created_at >= window_start)
                      .count())
            if recent >= _PM_LIMIT:
                flash(f'PM rate limit exceeded — max {_PM_LIMIT} per '
                      f'{_PM_WINDOW_SECONDS // 60} minutes.', 'danger')
                return render_template('pm/compose.html', form=form)

        try:
            from ..features import word_filter as _wf
            clean_subject = _wf.apply(form.subject.data or '')
            clean_content = _wf.apply(form.content.data or '')
        except Exception:
            clean_subject = form.subject.data
            clean_content = form.content.data
        msg = PrivateMessage(
            sender_id=current_user.id,
            recipient_id=recipient.id,
            subject=clean_subject,
            body=clean_content,
        )
        db.session.add(msg)
        db.session.commit()
        # All post-send side effects: notifications, achievements,
        # mention scanning. Each is best-effort — none of them must
        # ever 500 the response. The PM itself is already in the DB.
        try:
            from ..features.notify import notify, notify_mentions
            notify(recipient.id, 'pm',
                   title=f'New PM from {current_user.username}',
                   body=clean_subject or (clean_content or '')[:120],
                   target_url=url_for('pm.read', message_id=msg.id))
            notify_mentions(clean_content or '', current_user.username,
                            target_url=url_for('pm.read', message_id=msg.id))
        except Exception:
            try:
                db.session.rollback()
            except Exception:
                pass
        try:
            from ..features.achievements import check_for_user
            check_for_user(current_user)
        except Exception:
            try:
                db.session.rollback()
            except Exception:
                pass
        flash('Message sent!', 'success')
        return redirect(url_for('pm.sent'))

    return render_template('pm/compose.html', form=form)


@pm_bp.route('/<int:message_id>')
@login_required
def read(message_id):
    message = PrivateMessage.query.get_or_404(message_id)

    if message.recipient_id != current_user.id and message.sender_id != current_user.id:
        abort(403)

    if message.recipient_id == current_user.id and not message.read_at:
        message.read_at = datetime.utcnow()
        db.session.commit()

    # Conversation thread: all messages between these two users.
    other_id = (message.sender_id if message.sender_id != current_user.id
                else message.recipient_id)
    conversation = (PrivateMessage.query
                    .filter(db.or_(
                        db.and_(PrivateMessage.sender_id == current_user.id,
                                PrivateMessage.recipient_id == other_id),
                        db.and_(PrivateMessage.sender_id == other_id,
                                PrivateMessage.recipient_id == current_user.id)))
                    .order_by(PrivateMessage.created_at).all())
    other = User.query.get(other_id)
    return render_template('pm/read.html', message=message,
                           conversation=conversation, other=other)


@pm_bp.route('/<int:message_id>/delete', methods=['POST'])
@login_required
def delete(message_id):
    message = PrivateMessage.query.get_or_404(message_id)

    if message.recipient_id == current_user.id:
        message.is_deleted_recipient = True
        db.session.commit()
        flash('Message deleted from inbox.', 'success')
        return redirect(url_for('pm.inbox'))
    elif message.sender_id == current_user.id:
        message.is_deleted_sender = True
        db.session.commit()
        flash('Message deleted from sent.', 'success')
        return redirect(url_for('pm.sent'))
    else:
        abort(403)
