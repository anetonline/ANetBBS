"""Pull `anetbbs.lst` from the federation hub into `BbsDirectoryEntry`.

Companion to the existing Vertrauen `sbbsimsg.lst` puller (which feeds
the Synchronet half of the directory). This module owns the ANetBBS
half: GET REGISTRY_URL/anetbbs.lst, parse JSON, upsert one
BbsDirectoryEntry per published peer, mark rows with source='anetbbs'.

Sysops that don't want any federation can leave REGISTRY_URL blank —
the refresh loop becomes a no-op. Sysops running their own federation
hub (REGISTRY_MODE_ENABLED=true) still benefit from pulling the public
list: it lets them mirror peer-known-peers without manually approving
every entry on their own hub.
"""
import logging
import threading
import time
from datetime import datetime

import requests

logger = logging.getLogger(__name__)

_thread = None
_stop = threading.Event()


def start_anetbbs_directory_refresher(app):
    """Spawn the daily-refresh thread. Idempotent. No-op if
    REGISTRY_URL isn't configured."""
    global _thread
    if not app.config.get('REGISTRY_URL'):
        return
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(
        target=_loop, args=(app,),
        name='anetbbs-directory-refresh', daemon=True)
    _thread.start()


def stop_anetbbs_directory_refresher():
    _stop.set()


def _loop(app):
    interval = app.config.get('ANETBBS_DIRECTORY_REFRESH_SEC', 86400)  # daily
    # One pull immediately on startup so brand-new installs aren't blank
    # for a day. Then sleep + repeat.
    while not _stop.is_set():
        try:
            refresh(app)
        except Exception:
            logger.exception('anetbbs.lst refresh crashed; will retry')
        slept = 0
        while slept < interval and not _stop.is_set():
            time.sleep(min(30, interval - slept))
            slept += 30


def refresh(app):
    """One-shot pull. Returns the number of rows upserted, or 0 on
    network failure (existing rows left in place — better than wiping
    the directory if the hub is briefly down)."""
    from ..models import db, BbsDirectoryEntry

    base = (app.config.get('REGISTRY_URL') or '').rstrip('/')
    if not base:
        return 0
    url = f'{base}/anetbbs.lst'

    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception as exc:
        logger.warning('anetbbs.lst pull from %s failed: %s', url, exc)
        return 0

    bbses = data.get('bbses') or []
    if not isinstance(bbses, list):
        logger.warning('anetbbs.lst from %s has no bbses array', url)
        return 0

    seen_hosts = set()
    with app.app_context():
        for b in bbses:
            host = (b.get('host') or '').strip().lower()
            if not host:
                continue
            seen_hosts.add(host)
            entry = (BbsDirectoryEntry.query
                     .filter(BbsDirectoryEntry.hostname.ilike(host))
                     .first())
            if entry is None:
                entry = BbsDirectoryEntry(
                    hostname=host,
                    source='anetbbs',
                )
                db.session.add(entry)
            entry.name = (b.get('name') or host)[:160]
            entry.sysop = (b.get('sysop') or '')[:120] or None
            entry.location = (b.get('location') or '')[:120] or None
            entry.software = (b.get('software') or 'ANetBBS')[:40]
            entry.software_version = (b.get('software_version') or '')[:40] or None
            try:
                entry.msp_port = int(b.get('msp_port', 18))
                entry.systat_port = int(b.get('systat_port', 11))
            except (TypeError, ValueError):
                entry.msp_port = 18
                entry.systat_port = 11
            entry.notes = (b.get('notes') or '')[:4000] or None
            entry.last_seen_at = datetime.utcnow()
            # Preserve `source` if a row already exists as 'sbbsimsg' — we
            # don't want to clobber Synchronet entries that happen to
            # have the same hostname. New rows we create are 'anetbbs'.
            if entry.source not in ('anetbbs', 'sbbsimsg', 'manual'):
                entry.source = 'anetbbs'

        # Prune anetbbs-sourced rows that disappeared from the upstream
        # list (sysop unlisted, host went stale). Leave sbbsimsg + manual
        # rows alone — they're owned by other refresh paths.
        stale = (BbsDirectoryEntry.query
                 .filter(BbsDirectoryEntry.source == 'anetbbs')
                 .filter(~BbsDirectoryEntry.hostname.in_(seen_hosts))
                 .all())
        for s in stale:
            db.session.delete(s)

        db.session.commit()
        logger.info('anetbbs.lst: upserted %d, pruned %d (source=%s)',
                    len(bbses), len(stale), url)
    return len(bbses)
