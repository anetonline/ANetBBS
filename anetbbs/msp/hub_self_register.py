"""Hub-side self-registration — ensure the federation hub's OWN BBS
appears in its own `/anetbbs.lst`.

The companion module ``anetbbs/msp/registry_client.py`` handles the
*peer* side: a regular ANetBBS install posts ``POST /register`` to the
hub and waits for the sysop's approval. That round-trip makes no sense
for the hub talking to itself — the hub already trusts itself, so
this module just writes the row directly to the database the hub
serves from.

Behavior (only runs when ``REGISTRY_MODE_ENABLED=true``):

  1. On startup, INSERT or UPDATE a ``RegistryEntry`` keyed on
     ``BBS_DOMAIN`` (host header fallback if that's blank). The row
     ships pre-verified and pre-approved with ``is_listed=true``.
  2. A daemon thread refreshes ``last_heartbeat_at`` every
     ``REGISTRY_HUB_SELF_HEARTBEAT_SEC`` (default 6 hours) so the
     hub's own entry never trips the 48-hour stale cutoff in
     ``_listed_rows()``.

Without this module, fresh hub installs ship an empty
``/anetbbs.lst`` — the hub knows about peers but not about itself,
which leaves the hub's own ``/imsg/directory`` blank until a sysop
manually creates the row in the admin UI.
"""
import logging
import os
import threading
import time
from datetime import datetime

logger = logging.getLogger(__name__)

_thread = None
_stop = threading.Event()


def _read_version():
    """Best-effort: read the VERSION file shipped with the source."""
    try:
        here = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
        with open(os.path.join(here, 'VERSION'), 'r') as fh:
            return fh.read().strip()
    except OSError:
        return ''


def _hub_host(app):
    """The hostname the hub considers its own. BBS_DOMAIN is the source
    of truth; we don't fall back to request.host here because this
    runs at startup outside any request context."""
    return (app.config.get('BBS_DOMAIN') or '').strip().lower()


def _seed_or_update(app):
    """Idempotently ensure the hub's own RegistryEntry exists, is
    verified+approved+listed, and has a fresh heartbeat."""
    from ..models import db, RegistryEntry

    host = _hub_host(app)
    if not host:
        logger.info('hub-self-register: BBS_DOMAIN is blank — skipping. '
                    'Set BBS_DOMAIN to your public hostname so the hub '
                    'can publish itself.')
        return None

    with app.app_context():
        entry = (RegistryEntry.query
                 .filter(db.func.lower(RegistryEntry.host) == host)
                 .first())
        created = False
        if entry is None:
            entry = RegistryEntry(host=host,
                                  name=app.config.get('BBS_NAME') or 'ANetBBS',
                                  contact_email=(app.config.get('SYSOP_EMAIL')
                                                 or 'sysop@' + host))
            db.session.add(entry)
            created = True

        # Soft metadata — refresh from config every tick so the row
        # reflects what's currently in .env.
        entry.name = app.config.get('BBS_NAME') or entry.name or 'ANetBBS'
        entry.sysop = app.config.get('SYSOP_NAME') or entry.sysop or ''
        entry.location = app.config.get('BBS_LOCATION') or entry.location or ''
        entry.software = 'ANetBBS'
        entry.software_version = _read_version() or entry.software_version
        entry.msp_port = int(app.config.get('MSP_PORT', 18))
        entry.systat_port = int(app.config.get('SYSTAT_PORT', 11))
        if app.config.get('SYSOP_EMAIL'):
            entry.contact_email = app.config.get('SYSOP_EMAIL')

        # The hub trusts itself — both gates pass and the row is listed.
        entry.is_verified = True
        entry.is_approved = True
        entry.is_listed = True
        entry.is_active = True
        entry.last_heartbeat_at = datetime.utcnow()
        # SYSTAT probe doesn't need to run against ourselves; mark fresh.
        entry.last_probe_at = datetime.utcnow()
        entry.last_probe_ok = True
        entry.consecutive_probe_failures = 0

        try:
            db.session.commit()
            logger.info('hub-self-register: %s entry for %s '
                        '(name=%r, version=%s)',
                        'created' if created else 'updated',
                        host, entry.name, entry.software_version or '?')
        except Exception:
            db.session.rollback()
            logger.exception('hub-self-register: commit failed')
            return None
        return entry


def start_hub_self_register_thread(app):
    """Spawn the heartbeat thread. Idempotent. No-op unless this install
    is the federation hub."""
    global _thread
    if not app.config.get('REGISTRY_MODE_ENABLED'):
        return
    # Seed once synchronously so the entry exists before the first
    # incoming /anetbbs.lst request.
    try:
        _seed_or_update(app)
    except Exception:
        logger.exception('hub-self-register: initial seed crashed')

    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(
        target=_loop, args=(app,),
        name='hub-self-register', daemon=True)
    _thread.start()


def stop_hub_self_register_thread():
    _stop.set()


def _loop(app):
    interval = int(app.config.get('REGISTRY_HUB_SELF_HEARTBEAT_SEC',
                                   6 * 3600))
    while not _stop.is_set():
        # Interruptible sleep
        slept = 0
        while slept < interval and not _stop.is_set():
            time.sleep(min(30, interval - slept))
            slept += 30
        if _stop.is_set():
            return
        try:
            _seed_or_update(app)
        except Exception:
            logger.exception('hub-self-register: heartbeat tick crashed')
