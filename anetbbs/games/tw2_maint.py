"""Daily TW2 maintenance scheduler.

Upstream tw2's ``RunMaint()`` (Cabal move, inactive-player sweep, daily
log entry) only fires when a player logs in AND the last-run date is
older than today. On a quiet day, maintenance lags. The scheduler here
nudges it on a fixed daily cadence, headlessly.

Mechanism: spawn ``node`` with a combined script consisting of:
  1. Our regular Synchronet compat shim (so the TW2 sources find the
     Synchronet-flavoured globals they expect).
  2. ``tw2/maint_headless.js`` — replaces console.* with stderr stubs
     and pre-populates a sysop-flavoured ``user`` global.
  3. The bundled ``tw2.js`` and friends, source-rewritten so its
     ``main()`` body becomes ``RunMaint(); exit_tw2 = true;`` — that is,
     we re-use the same module-loading chain that the live door uses
     (so player and game data round-trip through the json-client
     correctly), but skip the interactive Menu loop.

Lock + guard: the runner takes a Python ``threading.Lock`` so two
overlapping daily ticks can't both spawn. We also re-check on entry
that ``MaintLastRan`` in TW2's game.ini is older than today — if a
player already logged in and triggered maintenance, we just no-op.
"""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import threading
import time
from datetime import date, datetime

logger = logging.getLogger(__name__)

_RUN_LOCK = threading.Lock()
_DEFAULT_INTERVAL_SEC = 24 * 3600   # daily


def _install_root():
    # this file is anetbbs/games/tw2_maint.py — install root is two parents up.
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _tw2_dir():
    return os.path.join(_install_root(), 'anetbbs', 'games', 'sbbs_doors', 'tw2')


def _game_ini():
    return os.path.join(_tw2_dir(), 'game.ini')


def _last_ran_date():
    """Parse MaintLastRan from TW2's game.ini. tw2 writes it as
    ``%Y:%m:%d`` (colons, not dashes — Synchronet ini quirk)."""
    p = _game_ini()
    try:
        with open(p, 'r') as f:
            for line in f:
                line = line.strip()
                if not line.lower().startswith('maintlastran'):
                    continue
                _, _, raw = line.partition('=')
                raw = raw.strip().strip('"').strip()
                if not raw:
                    return None
                try:
                    return datetime.strptime(raw, '%Y:%m:%d').date()
                except ValueError:
                    try:
                        return datetime.strptime(raw, '%Y-%m-%d').date()
                    except ValueError:
                        return None
    except OSError:
        return None
    return None


def _spawn_headless_maint(app):
    """Build and exec the combined headless-maintenance script.

    Returns (ok: bool, log: str). Errors are logged but never raised —
    daily maintenance failing should never crash the BBS.
    """
    tw2_dir = _tw2_dir()
    if not os.path.isdir(tw2_dir):
        return False, f'tw2 dir missing: {tw2_dir}'
    tw2_js = os.path.join(tw2_dir, 'tw2.js')
    if not os.path.isfile(tw2_js):
        return False, f'tw2.js missing: {tw2_js}'

    from .synchronet_compat import write_compat_script

    # Build a fake Game/user payload — write_compat_script only reads a
    # small surface (script path + exec dir + user dict).
    import types
    fake_game = types.SimpleNamespace(
        game_type='door_synchronet',
        synchronet_script_path=tw2_js,
        synchronet_exec_dir=tw2_dir,
    )
    fake_user = {'id': 1, 'username': 'sysop', 'display_name': 'Sysop',
                 'is_admin': True, 'login_count': 0}
    compat_path = write_compat_script(fake_game, fake_user,
                                      node_number=1, bbs_name='ANetBBS')

    try:
        with open(compat_path) as f:
            compat_src = f.read()
        with open(os.path.join(tw2_dir, 'maint_headless.js')) as f:
            maint_prologue = f.read()
        with open(tw2_js) as f:
            tw2_src = f.read()
    except OSError as exc:
        return False, f'read failed: {exc}'

    marker = '// === Execute the actual game ==='
    if marker in compat_src:
        compat_src = compat_src.split(marker)[0]

    # Replace tw2.js's `main();` invocation tail with a call to RunMaint
    # under our headless prologue. tw2.js ends with a `main();` line.
    runner_tail = (
        "\ntry {\n"
        "  if (typeof RunMaint === 'function') {\n"
        "    if (typeof Settings === 'undefined' || !Settings) {\n"
        "      // Settings is created in main(); we never call main() in\n"
        "      // headless mode, so re-create the minimum we need to write\n"
        "      // MaintLastRan back out.\n"
        "      Settings = new GameSettings();\n"
        "    }\n"
        "    RunMaint();\n"
        "    if (typeof db !== 'undefined' && db && typeof db._saveAll === 'function')\n"
        "      db._saveAll();\n"
        "    process.exit(0);\n"
        "  } else {\n"
        "    process.stderr.write('headless maint: RunMaint() not defined\\n');\n"
        "    process.exit(2);\n"
        "  }\n"
        "} catch (e) {\n"
        "  process.stderr.write('headless maint error: ' + (e && e.stack ? e.stack : e) + '\\n');\n"
        "  process.exit(3);\n"
        "}\n"
    )
    # tw2.js calls `main()` at top level (last non-blank line). Strip it
    # so we don't enter the interactive door.
    tw2_src_no_main = []
    for line in tw2_src.splitlines():
        s = line.strip()
        if s == 'main();' or s == 'main()':
            continue
        tw2_src_no_main.append(line)
    tw2_src_clean = '\n'.join(tw2_src_no_main)

    combined = (
        "// === ANetBBS Synchronet compat ===\n"
        + compat_src + "\n"
        "// === Headless maint prologue ===\n"
        + maint_prologue + "\n"
        "// === tw2.js (main() stripped) ===\n"
        "try { module.exports = null; } catch(e) {}\n"
        "try {\n"
        + tw2_src_clean + "\n"
        + runner_tail +
        "} catch (eOuter) {\n"
        "  process.stderr.write('headless maint outer: '\n"
        "    + (eOuter && eOuter.stack ? eOuter.stack : eOuter) + '\\n');\n"
        "  process.exit(4);\n"
        "}\n"
    )

    fd, run_path = tempfile.mkstemp(suffix='_tw2_maint.js', prefix='anetbbs_')
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(combined)

        # Make sure the tw2 DB env var is set the same way door_runner
        # sets it — so the headless run hits the same JSON file.
        env = os.environ.copy()
        db_dir = os.path.join(_install_root(), 'data', 'sbbs_doors', 'tw2', 'db')
        os.makedirs(db_dir, exist_ok=True)
        env['ANETBBS_TW2_DB_DIR'] = db_dir

        node = os.environ.get('NODEJS_PATH', '/usr/bin/node')
        try:
            proc = subprocess.run(
                [node, run_path],
                cwd=tw2_dir, env=env,
                capture_output=True, timeout=180, text=True,
            )
        except subprocess.TimeoutExpired:
            return False, 'headless maint timed out after 180s'
        ok = (proc.returncode == 0)
        log = ('stdout:\n' + proc.stdout + '\n\nstderr:\n' + proc.stderr).strip()
        if not ok:
            logger.warning('TW2 maint exit=%s: %s', proc.returncode, log[:500])
        else:
            logger.info('TW2 maint ran ok (%s lines stderr)',
                        len(proc.stderr.splitlines()))
        return ok, log
    finally:
        try:
            os.remove(run_path)
        except OSError:
            pass


def _maybe_run(app):
    """Run maintenance iff it hasn't run today."""
    today = date.today()
    last = _last_ran_date()
    if last == today:
        logger.debug('TW2 maint: already ran today (%s); skipping', today)
        return
    with _RUN_LOCK:
        ok, log = _spawn_headless_maint(app)
    if ok:
        logger.info('TW2 maint: completed for %s', today)
    else:
        logger.warning('TW2 maint: failed for %s', today)


def start_tw2_maint_scheduler(app, interval_sec: int = _DEFAULT_INTERVAL_SEC):
    """Background thread firing _maybe_run() once per interval.

    First fire is 5 minutes after boot — long enough for the BBS to
    settle, short enough that a same-day install still gets one
    maintenance pass.
    """
    def _loop():
        # Initial delay: let other startup work finish.
        time.sleep(300)
        while True:
            try:
                with app.app_context():
                    _maybe_run(app)
            except Exception:
                logger.exception('TW2 maint scheduler crashed; backing off')
            time.sleep(interval_sec)
    t = threading.Thread(target=_loop, name='tw2-maint', daemon=True)
    t.start()
    logger.info('TW2 maint scheduler started (interval=%ss)', interval_sec)
    return t
