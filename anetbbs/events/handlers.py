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
        r = subprocess.run(cmd, shell=True, capture_output=True,
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
    'hub_generate_nodelist': hub_generate_nodelist,
    'shell':                shell,
}

# Friendly names + descriptions for the admin form dropdown.
HANDLER_META = {
    'noop':           ('No-op (test)',           'Does nothing. Use to verify scheduling is firing.'),
    'tw2_maint':      ('Trade Wars 2002 maint',  'Daily Cabal move + inactive-player sweep.'),
    'db_vacuum':      ('SQLite VACUUM',          'Reclaim free pages + refresh planner stats. Off-peak.'),
    'log_rotate':     ('Rotate large logs',      'Roll any logs/*.log over the threshold to .1. Params: max_mb (default 50).'),
    'security_check': ('Security update check',  'apt + pip outdated scan, tags Ubuntu-security rows. Report at /admin/security/.'),
    'hub_generate_nodelist': ('ANotherNetwork: generate nodelist', 'Publish the ANotherNetwork nodelist into ANN.FILES.NODELIST. Only meaningful on the hub install.'),
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
]
