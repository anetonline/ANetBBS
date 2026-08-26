"""Built-in handlers for ``ScheduledEvent`` rows.

A handler is a callable ``f(app, params: dict) -> (ok: bool, output: str)``.

* ``app`` is the Flask app (already in app_context when called from the
  runner thread).
* ``params`` is the parsed ``params_json`` of the event row.
* Return ``(True, "first lines of output")`` on success or
  ``(False, "error message")`` on failure.

Adding a handler:

1. Define a function below with the signature above.
2. Register it in :data:`REGISTRY` with a stable ``key``. The key is
   what sysops pick from the dropdown in the admin UI; renaming it
   breaks existing rows so treat it as a public name.
3. Optionally add a row to :data:`DEFAULT_EVENTS` so fresh installs get
   a sensible cadence out of the box.
"""
from __future__ import annotations

import logging
import os
import subprocess
import time
from typing import Callable, Dict, Tuple

logger = logging.getLogger(__name__)


# ── Bundled handlers ──────────────────────────────────────────────────────
def noop(app, params):
    """Does nothing. Useful as a "sysop is testing the scheduler" target."""
    return True, 'noop'


def tw2_maint(app, params):
    """Run TW2 daily maintenance — Cabal move + inactive-player sweep.

    Wraps the existing :mod:`anetbbs.games.tw2_maint` headless runner.
    Returns ok=False if the node subprocess exits non-zero.
    """
    try:
        from ..games.tw2_maint import _spawn_headless_maint
    except ImportError as exc:
        return False, f'tw2_maint module unavailable: {exc}'
    ok, log = _spawn_headless_maint(app)
    return ok, (log or '')[:4096]


def db_vacuum(app, params):
    """Run SQLite VACUUM on the configured DB.

    Reclaims free pages, defragments the file, and updates the page-level
    statistics that the query planner uses. Cheap on a tidy DB, but
    on a multi-GB anetbbs install it can rewrite a lot of pages, so
    schedule for off-peak.

    No-op on non-SQLite backends — postgres handles its own vacuuming
    via autovacuum.
    """
    try:
        from ..models import db
        from sqlalchemy import text
        url = str(db.engine.url)
        if 'sqlite' not in url:
            return True, 'skipped: not a SQLite DB'
        # SQLite's VACUUM can't run inside a transaction. AUTOCOMMIT
        # isolation tells the pysqlite dialect to skip its implicit
        # BEGIN, so there's no transaction for VACUUM to conflict with.
        eng = db.engine
        with eng.connect().execution_options(isolation_level='AUTOCOMMIT') as conn:
            t0 = time.monotonic()
            conn.execute(text('VACUUM'))
            ms = (time.monotonic() - t0) * 1000
        return True, f'VACUUM ok in {ms:.0f} ms'
    except Exception as exc:  # noqa: BLE001
        return False, f'VACUUM failed: {exc!r}'


def log_rotate(app, params):
    """Rotate log files in ``logs/`` larger than ``max_mb`` (default 50).

    Each oversize ``foo.log`` is renamed to ``foo.log.1`` (overwriting
    any existing rotation) and a fresh empty file is created. The
    running gunicorn/anetbbs services don't reopen on signal, but they
    create new file handles on next write — that's fine for our
    append-only logs.
    """
    install_root = app.config.get('INSTALL_DIR') or os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    logs_dir = os.path.join(install_root, 'logs')
    if not os.path.isdir(logs_dir):
        return True, 'skipped: logs/ dir not found'
    max_mb = int(params.get('max_mb', 50))
    rotated = []
    for name in os.listdir(logs_dir):
        if not name.endswith('.log'):
            continue
        src = os.path.join(logs_dir, name)
        try:
            sz = os.path.getsize(src)
        except OSError:
            continue
        if sz < max_mb * 1024 * 1024:
            continue
        dst = src + '.1'
        try:
            os.replace(src, dst)
            open(src, 'a').close()  # recreate empty
            rotated.append(f'{name} ({sz / 1024 / 1024:.1f} MB)')
        except OSError as exc:
            rotated.append(f'{name} FAILED: {exc}')
    if not rotated:
        return True, 'nothing rotated (all under threshold)'
    return True, 'rotated: ' + ', '.join(rotated)


def security_check(app, params):
    """Check for pending OS + Python-package updates and flag security-
    relevant ones.

    Outputs:
        * ``logs/security-report.json`` — structured snapshot consumed
          by the ``/admin/security/`` page.
        * Return value's ``output`` string — one-line summary for the
          event list.

    What it checks:
        * ``apt list --upgradable``  (no sudo needed). Rows whose source
          line includes ``-security`` (Ubuntu's convention) are tagged
          ``security: true``. This covers nginx, dosbox-staging,
          openssl, python3 itself, anything coming via apt — exactly
          the system-component patches the sysop asked to be warned
          about.
        * ``pip list --outdated --format=json`` against the install
          venv. Pip doesn't expose CVE info natively; flagged as
          security only if ``pip-audit`` is installed AND lists the
          package. For most installs this is just an "outdated"
          listing — still useful to see at a glance.

    Soft-no-ops on a machine without apt or without the venv; the
    handler always returns ok=True so a non-Ubuntu box doesn't
    permanently mark the event as failing.
    """
    import json as _json
    install_root = app.config.get('INSTALL_DIR') or os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    report_path = os.path.join(install_root, 'logs', 'security-report.json')
    report = {
        'generated_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'apt': [], 'pip': [],
        'apt_total': 0, 'apt_security': 0,
        'pip_total': 0,
        'errors': [],
    }

    # ── apt ────────────────────────────────────────────────────────
    try:
        r = subprocess.run(
            ['apt', 'list', '--upgradable'],
            capture_output=True, text=True, timeout=30,
            env={'LANG': 'C', 'LC_ALL': 'C', 'PATH': '/usr/bin:/bin'},
        )
        for line in r.stdout.splitlines():
            # Example: "nginx/jammy-updates,jammy-security 1.18.0-6ubuntu14.4 amd64 [upgradable from: 1.18.0-6ubuntu14.3]"
            if '/' not in line or '[upgradable' not in line:
                continue
            try:
                name_part, rest = line.split(' ', 1)
                pkg, _, src = name_part.partition('/')
                version = rest.split(' ', 1)[0]
                cur = (rest.split('[upgradable from: ', 1)[1]
                          .rstrip(']').strip())
            except (ValueError, IndexError):
                continue
            is_sec = '-security' in src
            report['apt'].append({
                'name': pkg, 'current': cur, 'latest': version,
                'source': src, 'security': is_sec,
            })
            report['apt_total'] += 1
            if is_sec:
                report['apt_security'] += 1
    except FileNotFoundError:
        report['errors'].append('apt not installed — skipping OS-level scan')
    except subprocess.TimeoutExpired:
        report['errors'].append('apt list --upgradable timed out')
    except Exception as exc:  # noqa: BLE001
        report['errors'].append(f'apt scan failed: {exc!r}')

    # ── pip (against the install venv if present) ──────────────────
    # Packages intentionally pinned below their latest release.
    # These are excluded from the "outdated" count so the sysop isn't
    # prompted to upgrade something that would break the service.
    PIP_PINNED = {
        # gunicorn 23+ dropped the eventlet worker entry point that
        # ANetBBS uses. Safe ceiling is <23; do NOT upgrade past 22.x.
        'gunicorn': 'pinned <23 — gunicorn 23+ dropped eventlet worker support',
    }
    venv_pip = os.path.join(install_root, 'venv', 'bin', 'pip')
    if os.path.isfile(venv_pip):
        try:
            r = subprocess.run(
                [venv_pip, 'list', '--outdated', '--format=json'],
                capture_output=True, text=True, timeout=60,
            )
            data = _json.loads(r.stdout or '[]')
            pip_rows = []
            pip_pinned_rows = []
            for d in data:
                if not isinstance(d, dict):
                    continue
                name = d.get('name', '')
                row = {'name': name,
                       'current': d.get('version'),
                       'latest': d.get('latest_version'),
                       'security': False}
                pin_note = PIP_PINNED.get(name) or PIP_PINNED.get(name.lower())
                if pin_note:
                    row['pinned'] = pin_note
                    pip_pinned_rows.append(row)
                else:
                    pip_rows.append(row)
            report['pip'] = pip_rows
            report['pip_pinned'] = pip_pinned_rows
            report['pip_total'] = len(pip_rows)
        except subprocess.TimeoutExpired:
            report['errors'].append('pip list --outdated timed out')
        except Exception as exc:  # noqa: BLE001
            report['errors'].append(f'pip scan failed: {exc!r}')
    else:
        report['errors'].append(f'venv pip not found at {venv_pip}')

    # ── persist + summarise ────────────────────────────────────────
    try:
        os.makedirs(os.path.dirname(report_path), exist_ok=True)
        tmp = report_path + '.tmp'
        with open(tmp, 'w') as f:
            _json.dump(report, f, indent=2)
        os.replace(tmp, report_path)
    except OSError as exc:
        report['errors'].append(f'write report failed: {exc}')

    summary = (f'{report["apt_total"]} apt updates '
               f'({report["apt_security"]} security), '
               f'{report["pip_total"]} pip outdated. '
               f'Report: {report_path}')
    if report['errors']:
        summary += ' Errors: ' + '; '.join(report['errors'])[:200]
    return True, summary


def cleanup_stale_sessions(app, params):
    """Delete long-stale UserSession rows.

    UserSession.user_id used to be unique=True (one row per user,
    self-overwriting on every new connection), so this table could
    never grow past the user count. That constraint was removed so
    simultaneous connections for the same account (e.g. web + SSH at
    once) each get their own row for "who's online" -- real bug found
    live, reported by Jerry -- and a clean disconnect now DELETES its
    own row (core/presence.py::SessionPresence.disconnect(),
    web/auth.py's logout route) rather than just marking it stale.
    Without a bound, a connection that never gets a clean disconnect
    (browser closed, dropped carrier, killed process) would leave a
    permanent row behind forever on a long-running install. This is
    the backstop: anything untouched for `stale_days` (default 1) is
    long past the 5-minute online window and safe to remove outright.
    """
    try:
        from ..models import db, UserSession
        from datetime import datetime, timedelta
        stale_days = int((params or {}).get('stale_days', 1))
        cutoff = datetime.utcnow() - timedelta(days=stale_days)
        deleted = UserSession.query.filter(UserSession.last_seen < cutoff).delete()
        db.session.commit()
        return True, f'Deleted {deleted} stale session row(s) older than {stale_days}d'
    except Exception as exc:  # noqa: BLE001
        return False, f'cleanup_stale_sessions failed: {exc!r}'


def cleanup_stale_game_sessions(app, params):
    """Delete/close long-stale GameSession rows and release their
    game-center node slots.

    Real gap found in a security/performance audit: anetbbs/games/
    node_manager.py's own cleanup_stale_sessions() (a distinct
    function from the UserSession one above -- same name, different
    table) already existed to sweep GameSession rows a crashed/killed
    door process left stuck at status='active' forever, permanently
    holding a node slot other players can never reclaim -- but nothing
    anywhere ever called it. Wired in here the same way the
    UserSession backstop above is, rather than adding a second,
    parallel scheduling mechanism.
    """
    try:
        from ..games.node_manager import cleanup_stale_sessions as _impl
        timeout_seconds = int((params or {}).get('timeout_seconds', 3600))
        _impl(timeout_seconds=timeout_seconds)
        return True, f'Swept GameSession rows stale past {timeout_seconds}s'
    except Exception as exc:  # noqa: BLE001
        return False, f'cleanup_stale_game_sessions failed: {exc!r}'


def cleanup_stale_registry_entries(app, params):
    """Delete long-stale, never-verified RegistryEntry rows.

    Real gap found in a security/performance audit: POST /registry/api/
    v1/register requires no authentication (that's the protocol — any
    BBS can announce itself) and the only real brake on registration
    churn is a per-source-IP rate cap. An attacker rotating source IPs
    (cheap — no CAPTCHA, no auth) can accumulate unbounded junk rows in
    registry_entries indefinitely; nothing anywhere swept them. This is
    the backstop: any row that has NEVER been email-verified and hasn't
    heartbeated in `stale_days` (default 3) is safe to drop outright --
    a legitimate registrant who's mid-verification will simply
    re-register (idempotent, cheap) if they come back after that
    window. Verified/approved/listed rows are never touched here.

    Only meaningful on the install designated as the federation hub
    (REGISTRY_MODE_ENABLED) — auto-seeded on every install anyway
    (matching cleanup_stale_sessions' reasoning: every install should
    have this backstop the moment REGISTRY_MODE_ENABLED is turned on,
    not just fresh ones), but returns immediately as a no-op elsewhere.
    """
    if not app.config.get('REGISTRY_MODE_ENABLED'):
        return True, 'not a federation hub install — nothing to clean up'
    try:
        from ..models import db, RegistryEntry
        from datetime import datetime, timedelta
        stale_days = int((params or {}).get('stale_days', 3))
        cutoff = datetime.utcnow() - timedelta(days=stale_days)
        # Verification status + original registration age, deliberately
        # NOT gated on last_heartbeat_at: heartbeat requires the same
        # per-entry key as everything else in this fix, so churning
        # heartbeats can't be used to dodge cleanup while staying
        # unverified forever -- an unverified row can never become
        # publicly listed regardless of how "fresh" its heartbeat looks.
        deleted = (RegistryEntry.query
                  .filter(RegistryEntry.is_verified.is_(False))
                  .filter(RegistryEntry.registered_at < cutoff)
                  .delete(synchronize_session=False))
        db.session.commit()
        return True, f'Deleted {deleted} stale unverified registry row(s) older than {stale_days}d'
    except Exception as exc:  # noqa: BLE001
        return False, f'cleanup_stale_registry_entries failed: {exc!r}'


def cleanup_stale_presence_events(app, params):
    """Delete old PresenceEvent rows (the "X just logged in/out" alert
    queue -- see models.PresenceEvent's docstring).

    These rows are a delivery queue, not history -- once every active
    terminal watchdog (polls every 5s) and the web relay thread (polls
    every 2s) have had a chance to see a row, it has no further
    purpose. `stale_minutes` (default 60) is generous specifically to
    tolerate a temporary consumer outage (e.g. the web process
    restarting) without losing rows before they're ever delivered;
    it's not meant to imply these are worth keeping an hour on
    purpose.
    """
    try:
        from ..models import db, PresenceEvent
        from datetime import datetime, timedelta
        stale_minutes = int((params or {}).get('stale_minutes', 60))
        cutoff = datetime.utcnow() - timedelta(minutes=stale_minutes)
        deleted = (PresenceEvent.query
                  .filter(PresenceEvent.created_at < cutoff)
                  .delete(synchronize_session=False))
        db.session.commit()
        return True, f'Deleted {deleted} stale presence event row(s) older than {stale_minutes}m'
    except Exception as exc:  # noqa: BLE001
        return False, f'cleanup_stale_presence_events failed: {exc!r}'


def hub_generate_nodelist(app, params):
    """Generate the ANotherNetwork nodelist and publish it into the
    ANN.FILES.NODELIST file area, replacing the prior copy, so peers can
    pull it like any other file-echo entry.

    Only meaningful on the install designated as the ANotherNetwork hub
    (REGISTRY_MODE_ENABLED) -- on any other install, ANN.FILES.NODELIST
    won't have real downstream BinkPNode data to publish, but the
    handler still runs harmlessly (writes a nodelist with just the hub
    entry, no downstream nodes).
    """
    try:
        from ..echomail.nodelist import write_nodelist_to_area
        summary = write_nodelist_to_area()
        return True, summary
    except Exception as exc:  # noqa: BLE001
        return False, f'nodelist generation failed: {exc}'


def sync_wall_inbound(app, params):
    """Materialize new inbound InterBBS Wall messages into local
    WallPost rows. Thin wrapper -- see anetbbs/echomail/interbbs_sync.py
    for the real logic (dedup, loop-prevention, etc.)."""
    from ..echomail.interbbs_sync import sync_wall_inbound as _impl
    return _impl(app, params)


def sync_lastcallers_inbound(app, params):
    """Materialize new inbound InterBBS Last Callers messages into local
    CallerLog rows. Thin wrapper -- see
    anetbbs/echomail/interbbs_sync.py."""
    from ..echomail.interbbs_sync import sync_lastcallers_inbound as _impl
    return _impl(app, params)


def sync_scores_inbound(app, params):
    """Materialize new inbound InterBBS game-score messages into local
    GameScore rows. Thin wrapper -- see
    anetbbs/echomail/interbbs_sync.py."""
    from ..echomail.interbbs_sync import sync_scores_inbound as _impl
    return _impl(app, params)


def shell(app, params):
    """Run an arbitrary shell command. Dangerous — only for sysops who
    know what they're typing. Runs as the service user (no sudo).

    Params:
        ``command``  shell line to execute
        ``timeout``  optional, default 60 seconds
    """
    cmd = (params.get('command') or '').strip()
    if not cmd:
        return False, 'shell handler: command param is required'
    timeout = int(params.get('timeout', 60))
    try:
        # cmd is sysop-authored config on an admin_required-gated
        # ScheduledEvent row (see this handler's own docstring) -- same
        # "no sandbox, sysop-trust" scope as bbs_ui.py's exec action.
        r = subprocess.run(cmd, shell=True, capture_output=True,  # nosec B602
                           timeout=timeout)
        stdout = r.stdout.decode('utf-8', errors='replace')
        stderr = r.stderr.decode('utf-8', errors='replace')
        out = ('stdout:\n' + stdout + '\nstderr:\n' + stderr).strip()
        return (r.returncode == 0), out[:4096]
    except subprocess.TimeoutExpired:
        return False, f'shell handler: timed out after {timeout}s'
    except Exception as exc:  # noqa: BLE001
        return False, f'shell handler crashed: {exc!r}'


# ── Registry ──────────────────────────────────────────────────────────────
HandlerFn = Callable[[object, dict], Tuple[bool, str]]

REGISTRY: Dict[str, HandlerFn] = {
    'noop':                 noop,
    'tw2_maint':            tw2_maint,
    'db_vacuum':            db_vacuum,
    'log_rotate':           log_rotate,
    'security_check':       security_check,
    'cleanup_stale_sessions': cleanup_stale_sessions,
    'cleanup_stale_game_sessions': cleanup_stale_game_sessions,
    'cleanup_stale_registry_entries': cleanup_stale_registry_entries,
    'cleanup_stale_presence_events': cleanup_stale_presence_events,
    'hub_generate_nodelist': hub_generate_nodelist,
    'sync_wall_inbound':     sync_wall_inbound,
    'sync_lastcallers_inbound': sync_lastcallers_inbound,
    'sync_scores_inbound':   sync_scores_inbound,
    'shell':                shell,
}

# Friendly names + descriptions for the admin form dropdown.
HANDLER_META = {
    'noop':           ('No-op (test)',           'Does nothing. Use to verify scheduling is firing.'),
    'tw2_maint':      ('Trade Wars 2002 maint',  'Daily Cabal move + inactive-player sweep.'),
    'db_vacuum':      ('SQLite VACUUM',          'Reclaim free pages + refresh planner stats. Off-peak.'),
    'log_rotate':     ('Rotate large logs',      'Roll any logs/*.log over the threshold to .1. Params: max_mb (default 50).'),
    'security_check': ('Security update check',  'apt + pip outdated scan, tags Ubuntu-security rows. Report at /admin/security/.'),
    'cleanup_stale_sessions': ('Clean up stale online-presence rows', "Delete UserSession rows untouched for stale_days (default 1) -- catches connections that never got a clean disconnect (dropped carrier, killed process). Params: stale_days."),
    'cleanup_stale_game_sessions': ('Clean up stale game-center node slots', "Close GameSession rows stuck at status='active' and release their node slot, for doors whose process crashed/was killed without a clean exit. Params: timeout_seconds (default 3600)."),
    'cleanup_stale_registry_entries': ('Federation registry: clean up unverified entries', 'Delete RegistryEntry rows that never completed email verification within stale_days (default 3). No-op on non-hub installs. Params: stale_days.'),
    'cleanup_stale_presence_events': ('Clean up old login/logout alert events', 'Delete PresenceEvent rows (the real-time "X just logged in/out" delivery queue) older than stale_minutes (default 60).'),
    'hub_generate_nodelist': ('ANotherNetwork: generate nodelist', 'Publish the ANotherNetwork nodelist into ANN.FILES.NODELIST. Only meaningful on the hub install.'),
    'sync_wall_inbound': ('InterBBS Wall: import inbound posts', 'Materialize new ANET_WALL echomail into local Wall posts. Auto-created when InterBBS Wall is enabled.'),
    'sync_lastcallers_inbound': ('InterBBS Last Callers: import inbound entries', 'Materialize new ANET_LASTCALLERS echomail into local Last Callers entries. Auto-created when InterBBS Last Callers is enabled.'),
    'sync_scores_inbound': ('InterBBS Game Scores: import inbound scores', 'Materialize new ANET_GAMESCORES echomail into local game high scores. Auto-created when InterBBS Score Sharing is enabled.'),
    'shell':          ('Shell command',          'Run an arbitrary command as the service user. Params: command, timeout.'),
}


# Seeded by ensure_default_events() — sysops can disable, delete, or
# add their own. ``schedule_json`` strings are stored as-is.
DEFAULT_EVENTS = [
    {
        'name': 'TW2 daily maintenance',
        'handler_key': 'tw2_maint',
        'params_json': '{}',
        'schedule_json': '{"kind": "daily", "time": "03:30"}',
    },
    {
        'name': 'Weekly SQLite VACUUM',
        'handler_key': 'db_vacuum',
        'params_json': '{}',
        # Sunday 04:15 UTC — late enough that all the daily 03-04 jobs
        # have finished, early enough to be off-peak everywhere.
        'schedule_json': '{"kind": "weekly", "day": 6, "time": "04:15"}',
    },
    {
        'name': 'Rotate oversize logs',
        'handler_key': 'log_rotate',
        'params_json': '{"max_mb": 50}',
        'schedule_json': '{"kind": "daily", "time": "04:45"}',
    },
    {
        'name': 'Daily security update check',
        'handler_key': 'security_check',
        'params_json': '{}',
        # 04:00 UTC — runs after the universe maint but before the
        # log rotation. Sysop can see the morning's apt + pip status
        # on the admin dashboard on first login of the day.
        'schedule_json': '{"kind": "daily", "time": "04:00"}',
    },
    {
        'name': 'Clean up stale online-presence rows',
        'handler_key': 'cleanup_stale_sessions',
        'params_json': '{"stale_days": 1}',
        # 05:00 UTC — after the other daily 03:30/04:00/04:45 jobs.
        # Auto-seeded on every install (not just fresh ones, since
        # ensure_default_events() only inserts rows whose handler_key
        # isn't already present) because UserSession.user_id losing its
        # unique=True constraint (see models.UserSession's docstring)
        # means this table is no longer implicitly bounded to one row
        # per user -- every install needs this backstop, not just new
        # ones.
        'schedule_json': '{"kind": "daily", "time": "05:00"}',
    },
    {
        'name': 'Clean up stale game-center node slots',
        'handler_key': 'cleanup_stale_game_sessions',
        'params_json': '{"timeout_seconds": 3600}',
        # 05:15 UTC — right after the UserSession backstop above, same
        # "every install needs this, not just new ones" reasoning:
        # this existed as dead code (nothing ever called it) until
        # this audit wired it in, so every existing install is
        # missing it just as much as a fresh one.
        'schedule_json': '{"kind": "daily", "time": "05:15"}',
    },
    {
        'name': 'Federation registry: clean up unverified entries',
        'handler_key': 'cleanup_stale_registry_entries',
        'params_json': '{"stale_days": 3}',
        # 05:30 UTC — right after the other cleanup backstops. Seeded
        # on every install (the handler itself is a fast no-op unless
        # REGISTRY_MODE_ENABLED) so a hub install is protected the
        # moment that flag is turned on, with no separate admin step.
        'schedule_json': '{"kind": "daily", "time": "05:30"}',
    },
    {
        'name': 'Clean up old login/logout alert events',
        'handler_key': 'cleanup_stale_presence_events',
        'params_json': '{"stale_minutes": 60}',
        # Every 15 minutes -- this table is meant to stay small and
        # short-lived (a delivery queue, not history), unlike the
        # once-daily cleanups above.
        'schedule_json': '{"kind": "interval", "minutes": 15}',
    },
]
