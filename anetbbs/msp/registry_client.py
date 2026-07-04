"""Self-registration client for the ANetBBS federation registry.

Companion to `anetbbs/web/registry.py` (the hub side) and
`anetbbs/msp/anetbbs_directory.py` (the daily pull of peer hosts).
This module handles the *outbound* side: telling the hub "I exist,
here's my metadata, ping me back if you care."

Behavior:
1. On startup, if REGISTRY_SELF_REGISTER=true, POST /register with
   our metadata. The hub returns a verify_url + token.
2. We persist the verify_url to a state file so the sysop can find
   it later (instead of digging through gunicorn logs). Eventually
   when the email system is up, the hub will email it directly to
   SYSOP_EMAIL — but until then, the local state file is the source
   of truth.
3. Daily heartbeat keeps last_seen current and lets us update soft
   metadata (e.g. the sysop changed the BBS name).
4. If a heartbeat returns 404 (hub doesn't know us — could happen
   if the hub sysop deleted our entry, or we changed hosts), we
   fall back to a full register.

Self-registration is **opt-in** (REGISTRY_SELF_REGISTER=false by
default). Sysops who don't want their BBS published on a federation
list just leave it off; they can still pull the public list via
the anetbbs_directory refresher.
"""
import json
import logging
import os
import threading
import time
from datetime import datetime

import requests

logger = logging.getLogger(__name__)

_thread = None
_stop = threading.Event()


def _state_path(app):
    """Where we persist our verify_token + last-register-status. Living
    inside data/ keeps it sysop-private (not pushed to git, not in
    backups by default)."""
    data_dir = app.config.get('DATA_DIR') or 'data'
    if not os.path.isabs(data_dir):
        base = app.config.get('BASE_DIR') or os.getcwd()
        data_dir = os.path.join(base, data_dir)
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, 'registry_state.json')


def _load_state(app):
    try:
        with open(_state_path(app), 'r') as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save_state(app, state):
    try:
        with open(_state_path(app), 'w') as fh:
            json.dump(state, fh, indent=2)
    except OSError as exc:
        logger.warning('registry-client: could not write state file: %s', exc)


def _read_version():
    """Best-effort: read the VERSION file shipped with the source."""
    try:
        here = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
        with open(os.path.join(here, 'VERSION'), 'r') as fh:
            return fh.read().strip()
    except OSError:
        return ''


def _our_metadata(app):
    """Build the payload we send to /register and /heartbeat."""
    return {
        'host': (app.config.get('BBS_DOMAIN') or '').strip().lower(),
        'name': app.config.get('BBS_NAME') or 'ANetBBS',
        'sysop': app.config.get('SYSOP_NAME') or '',
        'location': app.config.get('BBS_LOCATION') or '',
        'contact_email': app.config.get('SYSOP_EMAIL') or '',
        'software': 'ANetBBS',
        'software_version': _read_version(),
        'msp_port': int(app.config.get('MSP_PORT', 18)),
        'systat_port': int(app.config.get('SYSTAT_PORT', 11)),
    }


def start_self_register_thread(app):
    """Spawn the daemon thread. Idempotent. No-op if self-register is
    not enabled OR if required metadata (BBS_DOMAIN, SYSOP_EMAIL) is
    blank — we don't want to spam the hub with malformed registers."""
    global _thread
    if not app.config.get('REGISTRY_SELF_REGISTER'):
        return
    meta = _our_metadata(app)
    if not meta['host']:
        logger.info('registry-client: SELF_REGISTER on but BBS_DOMAIN '
                    'is blank — skipping. Set BBS_DOMAIN to your '
                    'public hostname to enable.')
        return
    if not meta['contact_email']:
        logger.info('registry-client: SELF_REGISTER on but SYSOP_EMAIL '
                    'is blank — skipping. Set SYSOP_EMAIL so the hub '
                    'can send you a verify token.')
        return
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(
        target=_loop, args=(app,),
        name='registry-self-register', daemon=True)
    _thread.start()


def stop_self_register_thread():
    _stop.set()


def _loop(app):
    interval = app.config.get('REGISTRY_HEARTBEAT_INTERVAL_SEC', 86400)
    # One register-or-heartbeat right away so a fresh install lands
    # in the hub's queue immediately rather than after a day.
    while not _stop.is_set():
        try:
            _tick(app)
        except Exception:
            logger.exception('registry-client tick crashed; will retry')
        slept = 0
        while slept < interval and not _stop.is_set():
            time.sleep(min(30, interval - slept))
            slept += 30


def _tick(app):
    """One iteration: try heartbeat, fall back to register on 404.

    Returns a dict describing what happened -- {'ok': bool, 'action':
    'heartbeat'|'register'|'skipped', 'reason': str (on failure),
    'verify_url': str (on a fresh register)}. Callers that only care
    about the daily background loop can ignore the return value, but
    anything showing the sysop a result (the setup wizard, the
    "register now" admin button) needs it: previously this function
    just `return`d silently on every failure path -- including when
    REGISTRY_URL was blank -- so callers had no way to tell success
    from a silent no-op, and ended up showing a flat "success" message
    regardless of what (if anything) actually happened.
    """
    base = (app.config.get('REGISTRY_URL') or '').rstrip('/')
    if not base:
        return {'ok': False, 'action': 'skipped',
                'reason': 'REGISTRY_URL is not configured'}
    meta = _our_metadata(app)
    state = _load_state(app)
    sess = requests.Session()

    # Heartbeat first (cheaper than re-register). If the hub returns
    # 404 we don't exist there yet — fall through to register.
    if state.get('host_registered') == meta['host']:
        try:
            r = sess.post(f'{base}/registry/api/v1/heartbeat',
                          json={'host': meta['host'],
                                'name': meta['name'],
                                'sysop': meta['sysop'],
                                'location': meta['location'],
                                'software_version': meta['software_version']},
                          timeout=15)
            if r.status_code == 200:
                state['last_heartbeat_at'] = datetime.utcnow().isoformat() + 'Z'
                state['last_response'] = r.json()
                _save_state(app, state)
                logger.info('registry-client: heartbeat to %s ok (%s)',
                            base, r.json().get('is_listed', '?'))
                return {'ok': True, 'action': 'heartbeat'}
            elif r.status_code == 404:
                logger.info('registry-client: hub %s says we are unknown '
                            '— re-registering', base)
            else:
                logger.warning('registry-client: heartbeat %s returned %d: %s',
                               base, r.status_code, r.text[:200])
                return {'ok': False, 'action': 'heartbeat',
                        'reason': f'hub returned HTTP {r.status_code}'}
        except requests.RequestException as exc:
            logger.warning('registry-client: heartbeat failed: %s', exc)
            return {'ok': False, 'action': 'heartbeat', 'reason': str(exc)}

    # Full register (first time, or after a 404 fallback).
    try:
        r = sess.post(f'{base}/registry/api/v1/register', json=meta,
                      timeout=20)
        body = r.json() if r.headers.get('content-type', '').startswith(
            'application/json') else {}
        if r.status_code == 200 and body.get('ok'):
            state['host_registered'] = meta['host']
            state['last_register_at'] = datetime.utcnow().isoformat() + 'Z'
            state['last_response'] = body
            state['verify_url'] = body.get('verify_url') or ''
            _save_state(app, state)
            logger.info(
                'registry-client: registered with %s — status=%s '
                'verify_url=%s', base, body.get('status'),
                body.get('verify_url'))
            return {'ok': True, 'action': 'register',
                    'verify_url': body.get('verify_url') or ''}
        else:
            logger.warning('registry-client: register %s returned %d: %s',
                           base, r.status_code, r.text[:200])
            return {'ok': False, 'action': 'register',
                    'reason': f'hub returned HTTP {r.status_code}: {r.text[:200]}'}
    except requests.RequestException as exc:
        logger.warning('registry-client: register failed: %s', exc)
        return {'ok': False, 'action': 'register', 'reason': str(exc)}
