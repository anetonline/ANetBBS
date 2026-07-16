"""Scheduler thread for ``ScheduledEvent`` rows.

Every minute (clamped — see ``_TICK_SEC``), the loop:

1. Loads all enabled events.
2. Computes ``next_run_at`` for each from ``schedule_json`` + ``last_run_at``.
3. Fires the ones whose next-run time is now-or-earlier.
4. Records ``last_run_at`` / ``last_status`` / ``last_duration_ms`` /
   ``last_output`` back on the row.

The handler is run in the scheduler thread (no per-event subprocess).
Handlers that need isolation should fork their own subprocess internally
— see :func:`anetbbs.events.handlers.shell` as the pattern. The runner
does NOT enforce a wall-clock timeout because handlers are sysop-owned
code; a runaway is a sysop bug, not a security threat.

We re-load the row fresh each tick so admin edits land within one
minute. Disabled or deleted rows simply stop firing on the next sweep.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# How often the scheduler wakes to check for due events. One minute is
# the coarsest cadence we expose to sysops (no sub-minute scheduling),
# so polling more often is wasted CPU. One minute also limits the
# "edit lag" — disable a misbehaving event and it stops within 60s.
_TICK_SEC = 60


# ── Schedule math ────────────────────────────────────────────────────────
def _parse_hhmm(s: str) -> tuple[int, int]:
    """Parse "HH:MM", falling back to 03:00 for anything unparseable OR
    out of range. The out-of-range guard matters more than it looks --
    'daily'/'weekly' schedules are the only two kinds whose time field
    was never range-validated at save time (events_admin.py validates
    hourly/weekly-day/interval strictly but took daily/weekly's `time`
    as a raw string). A stray out-of-range hour (e.g. from an old row,
    a direct DB edit, or a client that bypasses the <input type=time>
    widget) reaches datetime.replace(hour=..)  uncaught, and that
    propagates out of is_due()/compute_next_run() with nothing in
    _tick()'s per-row loop to catch it -- aborting the ENTIRE sweep for
    that tick and silently starving every other scheduled event ordered
    after the bad one, forever, with only a single generic "event
    scheduler crashed" log line to go on."""
    try:
        h, m = s.split(':')
        h, m = int(h), int(m)
        if not (0 <= h < 24 and 0 <= m < 60):
            return 3, 0
        return h, m
    except (ValueError, AttributeError):
        return 3, 0


def _latest_at_or_before(seed: datetime, now: datetime,
                         period: timedelta) -> datetime:
    """Walk ``seed`` by ``period`` until it lands at-or-before ``now``
    (the most recent occurrence). Handles seeds in the future or
    multiple-periods-in-the-past."""
    while seed > now:
        seed -= period
    while seed + period <= now:
        seed += period
    return seed


def compute_next_run(schedule: dict, last_run_at: Optional[datetime],
                     *, now: Optional[datetime] = None) -> datetime:
    """Return the UTC datetime of the next firing for ``schedule``.

    Algorithm: compute ``latest`` = most recent scheduled occurrence
    at-or-before ``now``. If we haven't run since ``latest``, the next
    run is ``latest`` (right now, so :func:`is_due` flips True). If we
    have, the next run is ``latest + period``.

    Fresh events (``last_run_at`` is ``None``) intentionally skip the
    "catch up the past target" path — newly-added events shouldn't
    backfill, they should wait for their next future occurrence.

    Unknown / unparseable schedules fire immediately so the failure is
    visible in the admin UI instead of vanishing.
    """
    now = now or datetime.utcnow()
    kind = (schedule or {}).get('kind', 'daily')

    if kind == 'daily':
        h, m = _parse_hhmm(schedule.get('time', '03:00'))
        seed = now.replace(hour=h, minute=m, second=0, microsecond=0)
        period = timedelta(days=1)
    elif kind == 'hourly':
        m = int(schedule.get('minute', 0)) % 60
        seed = now.replace(minute=m, second=0, microsecond=0)
        period = timedelta(hours=1)
    elif kind == 'weekly':
        # day: 0=Monday..6=Sunday (matches Python's weekday())
        day = int(schedule.get('day', 6)) % 7
        h, m = _parse_hhmm(schedule.get('time', '04:00'))
        days_ahead = (day - now.weekday()) % 7
        seed = (now + timedelta(days=days_ahead)).replace(
            hour=h, minute=m, second=0, microsecond=0)
        period = timedelta(days=7)
    elif kind == 'interval':
        mins = max(1, int(schedule.get('minutes', 60)))
        if last_run_at is None:
            return now  # first run = ASAP after enable
        return last_run_at + timedelta(minutes=mins)
    else:
        return now  # unknown — fire so the failure is loud

    latest = _latest_at_or_before(seed, now, period)
    if last_run_at is None:
        # Never run: next FUTURE occurrence. Don't backfill on add.
        return latest if latest > now else latest + period
    if last_run_at >= latest:
        return latest + period
    return latest


def is_due(schedule: dict, last_run_at: Optional[datetime],
           *, now: Optional[datetime] = None) -> bool:
    now = now or datetime.utcnow()
    return compute_next_run(schedule, last_run_at, now=now) <= now


# ── Firing ───────────────────────────────────────────────────────────────
def fire(app, event_id: int) -> tuple[bool, str]:
    """Run one event by id. Importable from the admin "Run now" button
    + the scheduler tick loop."""
    from ..models import db, ScheduledEvent
    from .handlers import REGISTRY

    ev = ScheduledEvent.query.get(event_id)
    if ev is None:
        return False, f'no event {event_id}'
    handler = REGISTRY.get(ev.handler_key)
    if handler is None:
        ok, out = False, f'unknown handler {ev.handler_key!r}'
    else:
        try:
            params = json.loads(ev.params_json or '{}')
            if not isinstance(params, dict):
                params = {}
        except json.JSONDecodeError as exc:
            ok, out = False, f'bad params_json: {exc}'
            handler = None
        if handler is not None:
            t0 = time.monotonic()
            try:
                ok, out = handler(app, params)
            except Exception as exc:  # noqa: BLE001
                logger.exception('event %s handler crashed', ev.handler_key)
                ok, out = False, f'handler crashed: {exc!r}'
            dur_ms = int((time.monotonic() - t0) * 1000)
            ev.last_duration_ms = dur_ms
    ev.last_run_at = datetime.utcnow()
    ev.last_status = 'ok' if ok else 'fail'
    ev.last_output = (out or '')[:4096]
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
    return ok, ev.last_output or ''


def _tick(app) -> None:
    """One sweep. Loads enabled events, runs each due one."""
    from ..models import ScheduledEvent

    rows = (ScheduledEvent.query
            .filter(ScheduledEvent.is_enabled.is_(True))
            .all())
    for ev in rows:
        try:
            schedule = json.loads(ev.schedule_json or '{}')
        except json.JSONDecodeError:
            schedule = {}
        try:
            due = is_due(schedule, ev.last_run_at)
        except Exception:
            # One row's bad schedule must never take the whole sweep
            # down with it -- previously an uncaught exception here
            # (e.g. datetime.replace() rejecting an out-of-range hour)
            # propagated straight out of the `for` loop, skipping every
            # other event for the rest of this tick (and every tick
            # after, since the same row fails the same way every time).
            logger.exception('events: %s has an unevaluable schedule '
                             '(%r) -- skipping it, other events unaffected',
                             ev.name, ev.schedule_json)
            continue
        if due:
            logger.info('events: firing %s (%s)', ev.name, ev.handler_key)
            ok, _ = fire(app, ev.id)
            if not ok:
                logger.warning('events: %s failed', ev.name)


# ── Public API ────────────────────────────────────────────────────────────
_started = False
_lock = threading.Lock()


def start_event_scheduler(app, tick_sec: int = _TICK_SEC) -> threading.Thread:
    """Background sweeper. Idempotent — calling twice doesn't spawn two
    threads. Returns the live thread either way."""
    global _started
    with _lock:
        if _started:
            return _scheduler_thread  # type: ignore
        def _loop():
            # Initial delay so other startup work finishes first.
            time.sleep(45)
            while True:
                try:
                    with app.app_context():
                        _tick(app)
                except Exception:
                    logger.exception('event scheduler crashed; backing off')
                time.sleep(tick_sec)
        t = threading.Thread(target=_loop, name='anetbbs-events',
                             daemon=True)
        t.start()
        _started = True
        globals()['_scheduler_thread'] = t
        logger.info('Event scheduler started (tick=%ss)', tick_sec)
        return t


def ensure_default_events(app):
    """Seed the bundled events on a fresh install. Idempotent — only
    inserts rows whose handler_key isn't already present."""
    from ..models import db, ScheduledEvent
    from .handlers import DEFAULT_EVENTS

    with app.app_context():
        existing = {ev.handler_key for ev in ScheduledEvent.query.all()}
        added = 0
        for d in DEFAULT_EVENTS:
            if d['handler_key'] in existing:
                continue
            db.session.add(ScheduledEvent(**d))
            added += 1
        if added:
            try:
                db.session.commit()
                logger.info('events: seeded %s default rows', added)
            except Exception:
                db.session.rollback()
                logger.exception('events: seeding default rows failed')
