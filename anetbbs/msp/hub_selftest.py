"""Daily hub self-test.

Runs ONLY on REGISTRY_MODE_ENABLED=true installs. Once a day, the hub
fetches its OWN public surfaces and verifies they still work:

* GET /anetbbs.lst        - federation directory peers pull
* GET /api/releases/latest - upgrade endpoint other installs hit
* GET /healthz            - generic liveness

Failures land in logs/hub-selftest.log. We intentionally probe via
REGISTRY_URL (external) rather than 127.0.0.1, because the failure
mode we care about is "my reverse proxy returns 502 while gunicorn
is fine" - only an external GET catches that.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime

logger = logging.getLogger(__name__)

_DEFAULT_INTERVAL_SEC = 24 * 3600


def _hub_log_path(app):
    install_root = app.config.get('INSTALL_DIR') or os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(install_root, 'logs', 'hub-selftest.log')


def _log_line(app, msg, level='INFO'):
    p = _hub_log_path(app)
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'a') as f:
            f.write(f'[{datetime.utcnow().isoformat()}Z] {level} {msg}\n')
    except OSError:
        pass
    if level == 'FAIL':
        logger.error('hub-selftest: %s', msg)
    elif level == 'WARN':
        logger.warning('hub-selftest: %s', msg)
    else:
        logger.info('hub-selftest: %s', msg)


def _probe(url, expect_substr=None):
    import requests
    try:
        r = requests.get(url, timeout=15,
                         headers={'User-Agent': 'ANetBBS/hub-selftest'})
        if r.status_code != 200:
            return False, f'{url} -> {r.status_code}'
        if expect_substr and expect_substr not in r.text:
            return False, f'{url} 200 but body missing {expect_substr!r}'
        return True, f'{url} ok ({len(r.text)} bytes)'
    except Exception as exc:  # noqa: BLE001
        return False, f'{url} {exc.__class__.__name__}: {exc}'


def _run_once(app):
    base = (app.config.get('REGISTRY_URL') or '').rstrip('/')
    if not base:
        _log_line(app, 'no REGISTRY_URL configured', 'WARN')
        return
    results = []
    results.append(_probe(base + '/anetbbs.lst', expect_substr='ANetBBS'))
    results.append(_probe(base + '/api/releases/latest'))
    results.append(_probe(base + '/healthz', expect_substr='version'))
    fails = [d for ok, d in results if not ok]
    for ok, d in results:
        _log_line(app, d, 'INFO' if ok else 'FAIL')
    if fails:
        # Best-effort: surface to a SysopPage if the model exists.
        try:
            from ..models import db
            try:
                from ..models import SysopPage
            except ImportError:
                SysopPage = None
            if SysopPage:
                for d in fails:
                    db.session.add(SysopPage(
                        category='hub_selftest',
                        message=f'Hub self-test failed: {d}',
                        created_at=datetime.utcnow(),
                    ))
                db.session.commit()
        except Exception:
            try:
                db.session.rollback()
            except Exception:
                pass


def start_hub_selftest_thread(app, interval_sec: int = _DEFAULT_INTERVAL_SEC):
    """Background daily probe. First fire 10 min after boot."""
    def _loop():
        time.sleep(600)
        while True:
            try:
                with app.app_context():
                    _run_once(app)
            except Exception:
                logger.exception('hub selftest crashed; backing off')
            time.sleep(interval_sec)
    t = threading.Thread(target=_loop, name='anetbbs-hub-selftest',
                         daemon=True)
    t.start()
    logger.info('Hub self-test thread started (interval=%ss)', interval_sec)
    return t
