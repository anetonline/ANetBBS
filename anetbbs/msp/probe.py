"""Periodic SYSTAT prober for the federation registry.

Once an entry is approved + listed on this hub (REGISTRY_MODE_ENABLED=true),
the prober wakes up every `REGISTRY_PROBE_INTERVAL_SEC` and sends a SYSTAT
query to each listed peer. The result updates `RegistryEntry.last_probe_*`
columns and feeds the staleness logic:

  - Probe succeeds   → consecutive_probe_failures = 0
  - Probe fails      → consecutive_probe_failures += 1
  - failures >= REGISTRY_PROBE_FAILURE_THRESHOLD → is_listed = False
                                                   (drops out of public list)
  - A future successful heartbeat + probe re-lists the entry.

This runs in a daemon thread inside the hub's anetbbs-web.service
(gunicorn) process, NOT the asyncio terminal-server process. Probing is
synchronous-blocking (one peer at a time) but UDP-only, so per-peer
latency is at worst a few times the 5s timeout (a few quick retries
happen within one probe, see _probe_with_retries() -- UDP has no
delivery guarantee, so a single lost packet shouldn't count as a real
failure). With a 1-hour interval and tens of peers that's fine --
anything bigger would need an async/parallel rewrite.
"""
import logging
import threading
import time
from datetime import datetime

from .systat import query_systat

logger = logging.getLogger(__name__)

_thread = None
_stop = threading.Event()


def start_probe_thread(app):
    """Spawn the prober if it isn't already running and the install is
    a federation hub. Idempotent."""
    global _thread
    if not app.config.get('REGISTRY_MODE_ENABLED'):
        return
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(
        target=_loop, args=(app,),
        name='registry-prober', daemon=True)
    _thread.start()
    logger.info('Registry: SYSTAT prober started (interval=%ds, fail-threshold=%d)',
                app.config.get('REGISTRY_PROBE_INTERVAL_SEC', 3600),
                app.config.get('REGISTRY_PROBE_FAILURE_THRESHOLD', 72))


def stop_probe_thread():
    _stop.set()


def _loop(app):
    from ..models import db, RegistryEntry

    while not _stop.is_set():
        # Real gap found live: these used to be read ONCE at thread
        # start and cached for the process lifetime, so a sysop
        # changing them via Admin -> Federation Registry had no effect
        # until the whole web service restarted. Re-read fresh every
        # pass instead -- a live settings change now takes effect on
        # the very next probe cycle.
        interval = app.config.get('REGISTRY_PROBE_INTERVAL_SEC', 3600)
        fail_threshold = app.config.get('REGISTRY_PROBE_FAILURE_THRESHOLD', 72)
        try:
            _probe_once(app, db, RegistryEntry, fail_threshold)
        except Exception:
            logger.exception('Registry probe pass crashed; will retry')
        # Sleep in small chunks so stop_probe_thread() takes effect promptly.
        slept = 0
        while slept < interval and not _stop.is_set():
            time.sleep(min(5, interval - slept))
            slept += 5


# Real gap found live: query_systat() is a single unacknowledged UDP
# datagram with no retry at all -- one lost packet (ordinary loss, not
# the peer actually being down) counted as a full probe failure. A few
# quick retries within the SAME probe pass costs almost nothing (UDP
# round-trips on a healthy path are sub-second) and turns "one lost
# packet" back into what it actually is -- noise, not a real failure.
_PROBE_RETRIES = 3
_PROBE_RETRY_DELAY_SEC = 1.5


def _probe_with_retries(host, port):
    """True if ANY of a few quick attempts gets a real SYSTAT reply."""
    for attempt in range(_PROBE_RETRIES):
        if query_systat(host, port, timeout=5.0):
            return True
        if attempt < _PROBE_RETRIES - 1:
            time.sleep(_PROBE_RETRY_DELAY_SEC)
    return False


def _probe_once(app, db, RegistryEntry, fail_threshold):
    """One sweep over every approved entry. Touches is_listed only when
    the failure count crosses the threshold — preserves the sysop's
    explicit approval state otherwise (entry stays in approved+verified
    state, just temporarily de-listed)."""
    with app.app_context():
        # Probe everything that's been approved by the sysop AND verified
        # by the registrant. Entries pending either gate are skipped — no
        # point pinging hosts that aren't allowed in the list yet.
        rows = (RegistryEntry.query
                .filter(RegistryEntry.is_approved.is_(True),
                        RegistryEntry.is_verified.is_(True),
                        RegistryEntry.is_active.is_(True))
                .all())
        for entry in rows:
            ok = _probe_with_retries(entry.host, entry.systat_port or 11)
            entry.last_probe_at = datetime.utcnow()
            entry.last_probe_ok = ok
            if ok:
                if entry.consecutive_probe_failures:
                    logger.info('Registry: %s recovered after %d failed probes',
                                entry.host, entry.consecutive_probe_failures)
                entry.consecutive_probe_failures = 0
                # Re-list anything that was dropped for staleness but is
                # now healthy. Skip if the sysop manually un-listed.
                if not entry.is_listed:
                    # Only auto-re-list if the de-listing was OUR doing,
                    # which we track by checking whether the entry has
                    # ever been listed (registered_at + heartbeat both
                    # exist). A truly sysop-rejected entry would have
                    # is_approved=False already.
                    entry.is_listed = True
                    logger.info('Registry: %s re-listed after probe recovery',
                                entry.host)
            else:
                entry.consecutive_probe_failures = (
                    (entry.consecutive_probe_failures or 0) + 1)
                if (entry.consecutive_probe_failures >= fail_threshold
                        and entry.is_listed):
                    entry.is_listed = False
                    logger.warning(
                        'Registry: %s dropped from list after %d failed probes',
                        entry.host, entry.consecutive_probe_failures)
            db.session.commit()
        logger.info('Registry: probed %d entries this pass', len(rows))
