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
    """Any logged-in user can submit an event.
    Admin submissions go live immediately; user submissions are held pending
    sysop review (is_published=False) and appear in Admin > Events."""
    if request.method == 'POST':
        title = (request.form.get('title') or '').strip()
        starts = (request.form.get('starts_at') or '').strip()
        ends = (request.form.get('ends_at') or '').strip()
        loc = (request.form.get('location') or '').strip() or None
        desc = (request.form.get('description') or '').strip() or None
        if not title:
            flash('Title is required.', 'danger')
            return redirect(url_for('calendar.new_event'))
        # The <input type="datetime-local"> field is timezone-agnostic
        # by spec -- just wall-clock numbers -- and a submitter picks a
        # time thinking in Eastern (this deployment's assumed local
        # time), same as the calendar templates now display starts_at/
        # ends_at in Eastern. Parse as Eastern, not UTC, or a
        # submitted "7pm" event would display back as "7pm" but
        # actually fire ~4-5 hours off.
        from ..core.tz import from_eastern_input
        try:
            starts_dt = from_eastern_input(starts, '%Y-%m-%dT%H:%M')
        except ValueError:
            flash('Invalid start date.', 'danger')
            return redirect(url_for('calendar.new_event'))
        ends_dt = None
        if ends:
            try:
                ends_dt = from_eastern_input(ends, '%Y-%m-%dT%H:%M')
            except ValueError:
                pass
        is_admin = getattr(current_user, 'is_admin', False)
        ev = CalendarEvent(title=title, description=desc, starts_at=starts_dt,
                           ends_at=ends_dt, location=loc,
                           created_by_id=current_user.id,
                           is_published=bool(is_admin))
        db.session.add(ev)
        db.session.commit()
        if is_admin:
            flash(f'Event "{title}" published.', 'success')
        else:
            flash(f'Event "{title}" submitted for sysop review.', 'success')
        return redirect(url_for('calendar.index'))
    return render_template('calendar/new.html')


@cal_bp.route('/<int:event_id>/delete', methods=['POST'])
@login_required
def delete(event_id):
    ev = CalendarEvent.query.get_or_404(event_id)
    is_admin = getattr(current_user, 'is_admin', False)
    # Owner can retract their own pending submission; sysop can delete anything.
    if not is_admin and ev.created_by_id != current_user.id:
        abort(403)
    db.session.delete(ev)
    db.session.commit()
    flash('Event deleted.', 'success')
    return redirect(url_for('calendar.index'))


# ---------------------------------------------------------------------------
# Sysop review routes
# ---------------------------------------------------------------------------

@cal_bp.route('/pending')
@login_required
def pending_events():
    """Sysop-only: list events awaiting review."""
    if not getattr(current_user, 'is_admin', False):
        abort(403)
    events = (CalendarEvent.query
              .filter(CalendarEvent.is_published.is_(False))
              .order_by(CalendarEvent.starts_at).all())
    return render_template('calendar/pending.html', events=events)


@cal_bp.route('/<int:event_id>/approve', methods=['POST'])
@login_required
def approve_event(event_id):
    """Sysop approves a pending event submission."""
    if not getattr(current_user, 'is_admin', False):
        abort(403)
    ev = CalendarEvent.query.get_or_404(event_id)
    ev.is_published = True
    db.session.commit()
    flash(f'Event "{ev.title}" approved and published.', 'success')
    return redirect(url_for('calendar.pending_events'))


@cal_bp.route('/<int:event_id>/reject', methods=['POST'])
@login_required
def reject_event(event_id):
    """Sysop rejects (deletes) a pending event submission."""
    if not getattr(current_user, 'is_admin', False):
        abort(403)
    ev = CalendarEvent.query.get_or_404(event_id)
    title = ev.title
    db.session.delete(ev)
    db.session.commit()
    flash(f'Event "{title}" rejected and removed.', 'success')
    return redirect(url_for('calendar.pending_events'))
