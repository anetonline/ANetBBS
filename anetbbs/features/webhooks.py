# anetbbs/features/webhooks.py
"""
Outbound webhook dispatcher. Call `fire(event, payload)` from any feature
that emits a notable event; we look up matching Webhook rows and POST.

Errors are swallowed (logged) so the calling feature is never blocked
by a webhook failure."""
import json
import logging
import threading

import requests


logger = logging.getLogger(__name__)


def _do_post(url, body, headers, timeout=8):
    try:
        resp = requests.post(url, data=body, headers=headers, timeout=timeout)
        return resp.status_code, None
    except requests.RequestException as exc:
        return 0, str(exc)


def _render(template, payload):
    """Substitute {key} placeholders in `template` with payload values."""
    if not template:
        return json.dumps(payload, default=str)
    out = template
    for k, v in payload.items():
        out = out.replace(f'{{{k}}}', str(v))
    return out


def fire(event, payload):
    """Look up active webhooks for `event`, POST in a background thread."""
    from datetime import datetime
    from ..models import db, Webhook

    try:
        rows = (Webhook.query
                .filter_by(event=event, is_active=True)
                .all())
    except Exception:
        return

    for w in rows:
        # Real gap found in a full message-boards security audit: a
        # 'post' webhook fired for every board with no way to scope it
        # to one -- see Webhook.board_id's own comment. Only enforced
        # for 'post' (the only event carrying a board_id in its
        # payload); other event types ignore board_id entirely.
        if event == 'post' and w.board_id is not None \
                and payload.get('board_id') != w.board_id:
            continue

        url = w.url
        secret = w.secret
        template = w.template
        body = _render(template, payload)
        headers = {'Content-Type': 'application/json'}
        if secret:
            headers['Authorization'] = f'Bearer {secret}'

        wid = w.id

        def _runner():
            status, err = _do_post(url, body, headers)
            try:
                row = Webhook.query.get(wid)
                if row:
                    row.last_called_at = datetime.utcnow()
                    row.last_status = status or 0
                    row.last_error = err
                    db.session.commit()
            except Exception:
                pass

        threading.Thread(target=_runner, daemon=True).start()
