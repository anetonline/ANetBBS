"""Service Control Center — per-PID CPU% / memory / threads sampler.

Phase 2 of the Service Control Center. Runs a single daemon thread
inside the gunicorn worker that samples each known systemd unit's
``MainPID`` every ``METRICS_SAMPLE_SEC`` (default 2 s) and stores the
last ``METRICS_HISTORY_POINTS`` (default 150 — 5 min @ 2 s) values
in a per-(unit, metric) ring buffer. ``/admin/control/metrics.json``
serializes the buffer for the front-end's Chart.js sparklines + the
aggregate area chart at the top of the panel.

Key choices:

* **psutil** is the only new dependency. It reads ``/proc/<pid>/stat``
  + ``/proc/<pid>/status`` — no root, no ptrace, no special caps.
* **MainPID lookup via systemctl** instead of pgrep so we always
  match the systemd-owned process rather than a stray child.
* **CPU% baseline reset on PID change.** When ``systemctl restart``
  swaps the process, ``psutil.Process.cpu_percent`` would otherwise
  return a meaningless delta against the dead PID — we rebuild the
  ``Process`` handle when the MainPID changes so the next sample
  starts from a fresh baseline.
* **Soft-fail samples are recorded as ``None``** rather than dropped,
  so the front-end can plot a gap where the service was down without
  needing to align timestamps after the fact.

This module is intentionally tolerant of psutil being absent — if
the import fails the sampler becomes a no-op and the JSON endpoint
returns empty buffers. That keeps the rest of the panel working on
hosts where psutil hasn't been pip-installed yet.
"""
import logging
import os
import subprocess
import threading
import time
from collections import deque
from datetime import datetime
from typing import Dict, Optional

logger = logging.getLogger(__name__)

try:
    import psutil  # type: ignore
    _PSUTIL_AVAILABLE = True
except ImportError:
    psutil = None  # type: ignore
    _PSUTIL_AVAILABLE = False

try:
    import eventlet.tpool  # type: ignore
    _TPOOL_AVAILABLE = True
except ImportError:
    eventlet = None  # type: ignore
    _TPOOL_AVAILABLE = False


SAMPLE_INTERVAL_SEC = float(os.environ.get('METRICS_SAMPLE_SEC', '2'))
HISTORY_POINTS = int(os.environ.get('METRICS_HISTORY_POINTS', '150'))


_lock = threading.Lock()
# buf[unit][metric] -> deque[(unix_ts, value_or_None)]
_buf: Dict[str, Dict[str, deque]] = {}
# Cache the psutil.Process per unit so cpu_percent has continuity.
_proc_handles: Dict[str, "psutil.Process"] = {}
_pid_cache: Dict[str, int] = {}
_thread: Optional[threading.Thread] = None
_stop = threading.Event()


def _known_units():
    """Return KNOWN_UNITS lazily to avoid an import cycle with control.py."""
    from .control import KNOWN_UNITS
    return KNOWN_UNITS


def _init_buffers():
    for spec in _known_units():
        unit = spec['unit']
        if unit not in _buf:
            _buf[unit] = {
                'cpu_pct': deque(maxlen=HISTORY_POINTS),
                'rss_mb':  deque(maxlen=HISTORY_POINTS),
                'threads': deque(maxlen=HISTORY_POINTS),
            }


def _get_main_pid(unit: str) -> Optional[int]:
    """MainPID via systemctl. Returns None for inactive units.

    Real bug found live: under gunicorn+eventlet (the actual runtime
    here — web_app.py calls eventlet.monkey_patch() unconditionally),
    a plain subprocess.run() runs through eventlet's greened subprocess
    module, whose blocking read on the child's errpipe cooperates via
    the shared epoll hub. This sampler calls it once per known unit
    every SAMPLE_INTERVAL_SEC in a tight sequential loop -- any single
    transient hiccup here can leave that read's fd-listener registered
    in the hub past the point its fd number gets recycled by the next
    subprocess.run() call, which then collides with it: "Second
    simultaneous read on fileno N detected", crash-looping every tick
    from that point on (confirmed live via journalctl -- looping every
    ~2s indefinitely). tpool.execute() runs the call on a real native
    OS thread instead, bypassing eventlet's greened subprocess/os.read
    machinery (and its shared hub bookkeeping) entirely -- the
    officially recommended eventlet pattern for blocking calls like
    this. Falls back to a plain call when eventlet isn't active (e.g.
    running this module outside the gunicorn worker, such as in tests).
    """
    try:
        if _TPOOL_AVAILABLE:
            r = eventlet.tpool.execute(
                subprocess.run,
                ['systemctl', 'show', unit, '--property=MainPID'],
                capture_output=True, text=True, timeout=5)
        else:
            r = subprocess.run(
                ['systemctl', 'show', unit, '--property=MainPID'],
                capture_output=True, text=True, timeout=5)
        out = (r.stdout or '').strip()
        if '=' in out:
            pid = int(out.split('=', 1)[1])
            return pid if pid > 0 else None
    except (subprocess.TimeoutExpired, ValueError, FileNotFoundError,
            OSError):
        return None
    return None


def _ensure_proc(unit: str):
    """Return a fresh-or-cached psutil.Process for the unit's MainPID.
    Rebuilds the handle when the PID changes so cpu_percent baselines
    reset cleanly across restarts."""
    if not _PSUTIL_AVAILABLE:
        return None
    pid = _get_main_pid(unit)
    if pid is None:
        _proc_handles.pop(unit, None)
        _pid_cache.pop(unit, None)
        return None

    cached = _proc_handles.get(unit)
    if cached is not None and _pid_cache.get(unit) == pid:
        try:
            if cached.is_running():
                return cached
        except psutil.Error:
            pass

    try:
        p = psutil.Process(pid)
        # Prime the cpu_percent baseline. The first call always
        # returns 0.0 — subsequent calls return % since prior call.
        p.cpu_percent(interval=None)
        _proc_handles[unit] = p
        _pid_cache[unit] = pid
        return p
    except psutil.Error:
        return None


def _sample_one(unit: str):
    """Append one sample to each metric deque. Soft-fails as None."""
    now = time.time()
    proc = _ensure_proc(unit)
    if proc is None:
        cpu = mem = thr = None
    else:
        try:
            cpu = proc.cpu_percent(interval=None)
            mem = proc.memory_info().rss / (1024.0 * 1024.0)
            thr = proc.num_threads()
        except Exception:  # pylint: disable=broad-except
            cpu = mem = thr = None

    with _lock:
        buf = _buf.get(unit)
        if buf is None:
            return
        buf['cpu_pct'].append((now, cpu))
        buf['rss_mb'].append((now, mem))
        buf['threads'].append((now, thr))


def _loop():
    _init_buffers()
    while not _stop.is_set():
        try:
            for spec in _known_units():
                _sample_one(spec['unit'])
        except Exception:  # pylint: disable=broad-except
            logger.exception('metrics sampler tick crashed; will retry')
        if _stop.wait(SAMPLE_INTERVAL_SEC):
            break


def start_sampler():
    """Spawn the sampler thread. Idempotent; no-op without psutil."""
    global _thread
    if not _PSUTIL_AVAILABLE:
        logger.info('metrics sampler: psutil not installed — graphs '
                    'will be empty. Add `psutil>=5.9.0` to '
                    'requirements.txt and rerun update.sh.')
        return
    if _thread is not None and _thread.is_alive():
        return
    _init_buffers()
    _stop.clear()
    _thread = threading.Thread(target=_loop, name='scc-metrics-sampler',
                                daemon=True)
    _thread.start()
    logger.info('metrics sampler: started '
                '(sample=%.1fs, history=%d points)',
                SAMPLE_INTERVAL_SEC, HISTORY_POINTS)


def stop_sampler():
    _stop.set()


# ---------------------------------------------------------------------------
# Read-side helpers (called from the /metrics.json route)
# ---------------------------------------------------------------------------

def snapshot():
    """Return a JSON-serializable copy of every ring buffer. Missing
    samples (sampler not running, process gone, etc.) come through as
    ``null`` in the values array; the front-end renders those as gaps."""
    out = {
        'available': _PSUTIL_AVAILABLE,
        'sample_interval_sec': SAMPLE_INTERVAL_SEC,
        'history_points': HISTORY_POINTS,
        'updated': datetime.utcnow().isoformat() + 'Z',
        'units': {},
    }
    with _lock:
        for unit, metrics in _buf.items():
            out['units'][unit] = {}
            for name, dq in metrics.items():
                ts = [t for t, _ in dq]
                vals = [v for _, v in dq]
                latest = next((v for v in reversed(vals) if v is not None),
                              None)
                out['units'][unit][name] = {
                    'ts': ts,
                    'values': vals,
                    'latest': latest,
                }
    return out


def latest_per_unit():
    """Compact latest-value-only view — handy for the card pill labels."""
    snap = snapshot()
    out = {}
    for unit, metrics in snap['units'].items():
        out[unit] = {name: m['latest'] for name, m in metrics.items()}
    return out
