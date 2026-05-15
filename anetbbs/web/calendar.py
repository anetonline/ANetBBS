# anetbbs/web/calendar.py
"""Calendar / events listing."""
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user

from ..models import db, CalendarEvent


cal_bp = Blueprint('calendar', __name__, url_prefix='/calendar')


@cal_bp.route('/')
def index():
    now = datetime.utcnow()
    upcoming = (CalendarEvent.query
                .filter(CalendarEvent.is_published.is_(True))
                .filter(CalendarEvent.starts_at >= now)
                .order_by(CalendarEvent.starts_at).limit(50).all())
    past = (CalendarEvent.query
            .filter(CalendarEvent.is_published.is_(True))
            .filter(CalendarEvent.starts_at < now)
            .order_by(CalendarEvent.starts_at.desc()).limit(20).all())
    return render_template('calendar/index.html', upcoming=upcoming, past=past)


@cal_bp.route('/new', methods=['GET', 'POST'])
@login_required
def new_event():
    """Sysops can add events directly. Regular users post to a future
    'submit-for-review' endpoint when that's wired in."""
    if not getattr(current_user, 'is_admin', False):
        abort(403)
    if request.method == 'POST':
        title = (request.form.get('title') or '').strip()
        starts = (request.form.get('starts_at') or '').strip()
        ends = (request.form.get('ends_at') or '').strip()
        loc = (request.form.get('location') or '').strip() or None
        desc = (request.form.get('description') or '').strip() or None
        try:
            starts_dt = datetime.strptime(starts, '%Y-%m-%dT%H:%M')
        except ValueError:
            flash('Invalid start date.', 'danger')
            return redirect(url_for('calendar.new_event'))
        ends_dt = None
        if ends:
            try:
                ends_dt = datetime.strptime(ends, '%Y-%m-%dT%H:%M')
            except ValueError:
                pass
        ev = CalendarEvent(title=title, description=desc, starts_at=starts_dt,
                           ends_at=ends_dt, location=loc,
                           created_by_id=current_user.id, is_published=True)
        db.session.add(ev); db.session.commit()
        flash(f'Event "{title}" added.', 'success')
        return redirect(url_for('calendar.index'))
    return render_template('calendar/new.html')


@cal_bp.route('/<int:event_id>/delete', methods=['POST'])
@login_required
def delete(event_id):
    if not getattr(current_user, 'is_admin', False):
        abort(403)
    ev = CalendarEvent.query.get_or_404(event_id)
    db.session.delete(ev); db.session.commit()
    flash('Event deleted.', 'success')
    return redirect(url_for('calendar.index'))
