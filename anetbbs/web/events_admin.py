"""Sysop UI for the scheduled-events system.

Routes:
    GET  /admin/events/                — list view
    GET  /admin/events/new             — create form
    POST /admin/events/new             — create submit
    GET  /admin/events/<id>/edit       — edit form
    POST /admin/events/<id>/edit       — edit submit
    POST /admin/events/<id>/delete     — delete
    POST /admin/events/<id>/toggle     — enable/disable
    POST /admin/events/<id>/run-now    — fire immediately

All admin-only. ``run-now`` blocks the request thread while the handler
runs — fine for things like noop / db_vacuum, slow for the TW2 maint
spawn-a-node-process path. That's intentional: the sysop watching the
button gets the result inline instead of polling.
"""
from __future__ import annotations

import json
import logging

from flask import (Blueprint, flash, redirect, render_template, request,
                   url_for, current_app, jsonify)
from flask_login import login_required

from ..models import db, ScheduledEvent
from ..events.runner import compute_next_run, fire
from ..events.handlers import REGISTRY, HANDLER_META


logger = logging.getLogger(__name__)
events_admin_bp = Blueprint('events_admin', __name__, url_prefix='/admin/events')


from .access_control import require_admin_or_403 as _admin_required


def _row_view(ev: ScheduledEvent) -> dict:
    """Render-friendly dict augmented with parsed schedule + next-run."""
    try:
        sched = json.loads(ev.schedule_json or '{}')
    except json.JSONDecodeError:
        sched = {}
    next_run = None
    if ev.is_enabled:
        try:
            next_run = compute_next_run(sched, ev.last_run_at)
        except Exception:
            # Mirrors the same guard in events/runner.py's _tick() --
            # an unevaluable schedule (e.g. a pre-fix row with an
            # out-of-range hour) must not 500 the whole admin list page,
            # just show as "?" for that one row so the sysop can still
            # get in and fix or delete it.
            logger.exception('events admin: %s has an unevaluable '
                             'schedule (%r)', ev.name, ev.schedule_json)
            next_run = None
    return {
        'row': ev,
        'schedule': sched,
        'schedule_human': _human_schedule(sched),
        'next_run': next_run,
        'handler_label': HANDLER_META.get(ev.handler_key, (ev.handler_key, ''))[0],
    }


def _human_schedule(sched: dict) -> str:
    kind = sched.get('kind', '?')
    if kind == 'daily':
        return f'daily at {sched.get("time", "03:00")} UTC'
    if kind == 'hourly':
        return f'hourly at :{int(sched.get("minute", 0)):02d}'
    if kind == 'weekly':
        days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
        day = int(sched.get('day', 6))
        day_name = days[day] if 0 <= day < 7 else f'day{day}'
        return f'{day_name} at {sched.get("time", "04:00")} UTC'
    if kind == 'interval':
        return f'every {sched.get("minutes", 60)} minutes'
    return f'unknown ({kind})'


def _parse_hhmm_strict(t: str) -> tuple[int, int] | None:
    """Validate an "HH:MM" string, returning (h, m) or None. Unlike
    events/runner.py's _parse_hhmm() -- which must fall back silently
    since it runs unattended in the scheduler tick -- this rejects bad
    input outright so the sysop finds out at save time, not by their
    events silently never firing."""
    try:
        h, m = t.split(':')
        h, m = int(h), int(m)
    except (ValueError, AttributeError):
        return None
    if not (0 <= h < 24 and 0 <= m < 60):
        return None
    return h, m


def _parse_form_schedule(form) -> tuple[dict, str | None]:
    """Build a schedule dict from the form fields. Returns (sched, error).

    hourly/weekly-day/interval were already range-validated here; daily
    and weekly's `time` field was taken as a raw string with no format
    or range check at all -- the only reason a bad value could ever
    reach the scheduler in the first place (see events/runner.py's
    _parse_hhmm() docstring for what that caused)."""
    kind = form.get('schedule_kind', 'daily')
    if kind == 'daily':
        t = (form.get('schedule_time') or '03:00').strip()
        if _parse_hhmm_strict(t) is None:
            return {}, 'time must be HH:MM, 00:00-23:59'
        return {'kind': 'daily', 'time': t}, None
    if kind == 'hourly':
        try:
            m = int(form.get('schedule_minute', 0))
        except ValueError:
            return {}, 'minute must be a number'
        if not (0 <= m < 60):
            return {}, 'minute must be 0..59'
        return {'kind': 'hourly', 'minute': m}, None
    if kind == 'weekly':
        try:
            d = int(form.get('schedule_day', 6))
        except ValueError:
            return {}, 'day must be a number'
        if not (0 <= d < 7):
            return {}, 'day must be 0..6 (0=Mon, 6=Sun)'
        t = (form.get('schedule_time') or '04:00').strip()
        if _parse_hhmm_strict(t) is None:
            return {}, 'time must be HH:MM, 00:00-23:59'
        return {'kind': 'weekly', 'day': d, 'time': t}, None
    if kind == 'interval':
        try:
            n = int(form.get('schedule_minutes', 60))
        except ValueError:
            return {}, 'minutes must be a number'
        if n < 1:
            return {}, 'minutes must be ≥ 1'
        return {'kind': 'interval', 'minutes': n}, None
    return {}, f'unknown schedule kind {kind!r}'


def _validate_params_json(s: str) -> tuple[str, str | None]:
    s = (s or '').strip()
    if not s:
        return '{}', None
    try:
        v = json.loads(s)
        if not isinstance(v, dict):
            return s, 'params must be a JSON object'
        return json.dumps(v), None
    except json.JSONDecodeError as exc:
        return s, f'invalid JSON: {exc}'


# ── Routes ────────────────────────────────────────────────────────────────
@events_admin_bp.route('/', methods=['GET'])
@login_required
def index():
    _admin_required()
    rows = (ScheduledEvent.query
            .order_by(ScheduledEvent.is_enabled.desc(),
                      ScheduledEvent.name.asc())
            .all())
    return render_template('admin/events.html',
                           rows=[_row_view(r) for r in rows],
                           handler_meta=HANDLER_META)


@events_admin_bp.route('/new', methods=['GET', 'POST'])
@login_required
def new():
    _admin_required()
    if request.method == 'POST':
        return _save(None)
    return render_template('admin/event_form.html',
                           ev=None, handler_meta=HANDLER_META,
                           schedule={'kind': 'daily', 'time': '03:00'},
                           params_json='{}')


@events_admin_bp.route('/<int:eid>/edit', methods=['GET', 'POST'])
@login_required
def edit(eid):
    _admin_required()
    ev = ScheduledEvent.query.get_or_404(eid)
    if request.method == 'POST':
        return _save(ev)
    try:
        sched = json.loads(ev.schedule_json or '{}')
    except json.JSONDecodeError:
        sched = {'kind': 'daily', 'time': '03:00'}
    return render_template('admin/event_form.html',
                           ev=ev, handler_meta=HANDLER_META,
                           schedule=sched,
                           params_json=ev.params_json or '{}')


def _save(ev):
    name = (request.form.get('name') or '').strip()
    handler_key = (request.form.get('handler_key') or '').strip()
    is_enabled = request.form.get('is_enabled') == 'on'
    params_json, perr = _validate_params_json(request.form.get('params_json', ''))
    schedule, serr = _parse_form_schedule(request.form)
    if not name:
        flash('Name is required.', 'danger')
    elif handler_key not in REGISTRY:
        flash(f'Unknown handler: {handler_key!r}', 'danger')
    elif perr:
        flash(f'Params error: {perr}', 'danger')
    elif serr:
        flash(f'Schedule error: {serr}', 'danger')
    else:
        if ev is None:
            ev = ScheduledEvent()
            db.session.add(ev)
        ev.name = name
        ev.handler_key = handler_key
        ev.is_enabled = is_enabled
        ev.params_json = params_json
        ev.schedule_json = json.dumps(schedule)
        try:
            db.session.commit()
            flash('Event saved.', 'success')
            return redirect(url_for('events_admin.index'))
        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            flash(f'Save failed: {exc}', 'danger')
    # Validation failure — re-render with what they typed.
    return render_template('admin/event_form.html',
                           ev=ev, handler_meta=HANDLER_META,
                           schedule=schedule or {'kind': 'daily', 'time': '03:00'},
                           params_json=request.form.get('params_json', '{}'))


@events_admin_bp.route('/<int:eid>/delete', methods=['POST'])
@login_required
def delete(eid):
    _admin_required()
    ev = ScheduledEvent.query.get_or_404(eid)
    db.session.delete(ev)
    db.session.commit()
    flash(f'Deleted event "{ev.name}".', 'success')
    return redirect(url_for('events_admin.index'))


@events_admin_bp.route('/<int:eid>/toggle', methods=['POST'])
@login_required
def toggle(eid):
    _admin_required()
    ev = ScheduledEvent.query.get_or_404(eid)
    ev.is_enabled = not ev.is_enabled
    db.session.commit()
    flash(f'"{ev.name}" is now {"enabled" if ev.is_enabled else "disabled"}.', 'info')
    return redirect(url_for('events_admin.index'))


@events_admin_bp.route('/<int:eid>/run-now', methods=['POST'])
@login_required
def run_now(eid):
    _admin_required()
    ok, out = fire(current_app._get_current_object(), eid)
    return jsonify({'ok': ok, 'output': out})
