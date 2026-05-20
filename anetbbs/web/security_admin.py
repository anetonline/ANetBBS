"""Security update report viewer.

Renders the latest report produced by the ``security_check`` event
handler (``logs/security-report.json``). Plus a "Run scan now" button
that fires the handler synchronously so the sysop doesn't have to wait
for the daily 04:00 UTC tick.

The actual scan logic lives in
:mod:`anetbbs.events.handlers.security_check` — the page here just
visualizes its output.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Optional

from flask import (Blueprint, abort, current_app, flash, jsonify,
                   redirect, render_template, url_for)
from flask_login import current_user, login_required


logger = logging.getLogger(__name__)
security_bp = Blueprint('security_admin', __name__,
                        url_prefix='/admin/security')


def _admin_required():
    if not current_user.is_authenticated or not getattr(current_user, 'is_admin', False):
        abort(403)


def _report_path() -> str:
    install_root = current_app.config.get('INSTALL_DIR') or os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(install_root, 'logs', 'security-report.json')


def _load_report() -> Optional[dict]:
    try:
        with open(_report_path(), 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning('security-report.json unreadable: %s', exc)
        return None


def _security_event_id() -> Optional[int]:
    """Find the ScheduledEvent row driving the security check, if any.
    Used to expose its last-run timestamp + a "Run now" link."""
    try:
        from ..models import ScheduledEvent
        ev = (ScheduledEvent.query
              .filter_by(handler_key='security_check')
              .first())
        return ev.id if ev else None
    except Exception:
        return None


@security_bp.route('/', methods=['GET'])
@login_required
def index():
    _admin_required()
    report = _load_report()
    event_id = _security_event_id()
    return render_template('admin/security.html',
                           report=report,
                           event_id=event_id)


@security_bp.route('/run-now', methods=['POST'])
@login_required
def run_now():
    """Synchronously fire the security_check handler and re-render.

    Blocks the request while apt + pip run (typically 5-30 seconds);
    fine for sysop-initiated single clicks.
    """
    _admin_required()
    try:
        # Prefer firing via the ScheduledEvent runner so the row's
        # last_run_at / last_status / last_output stay in sync.
        eid = _security_event_id()
        if eid:
            from ..events.runner import fire as _fire
            ok, summary = _fire(current_app._get_current_object(), eid)
        else:
            from ..events.handlers import security_check
            ok, summary = security_check(current_app._get_current_object(), {})
        flash(('Security scan: ' + summary)[:400],
              'success' if ok else 'warning')
    except Exception as exc:  # noqa: BLE001
        logger.exception('security scan failed')
        flash(f'Security scan crashed: {exc!r}', 'danger')
    return redirect(url_for('security_admin.index'))
