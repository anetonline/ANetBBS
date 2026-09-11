# anetbbs/features/webhooks.py
"""
Outbound webhook dispatcher. Call `fire(event, payload)` from any feature
that emits a notable event; we look up matching Webhook rows and POST.

Errors are swallowed (logged) so the calling feature is never blocked
by a webhook failure."""
import json
import logging
import threading
from urllib.parse import urlparse

import requests

from ..core.net_safety import resolve_safe_destination


logger = logging.getLogger(__name__)


def _do_post(url, body, headers, timeout=8):
    """POST body/headers to a sysop-configured Webhook.url.

    Real gap found in this round's security/performance audit: unlike
    every other outbound-connect path this codebase already guards
    (dialout.py, the RSS feed poller, finger.py, msp/client.py,
    web_terminal.py, ebooks.py's Gutendex text fetch...), this fired an
    arbitrary POST with no SSRF check at all -- reachable via the admin
    /admin/webhooks/ config, including an already-compromised admin
    session, same reasoning dialout.py's own comment gives for why a
    destination only reachable through an admin-configured field still
    needs the guard. Validates the scheme and resolves the hostname
    once via the same shared core.net_safety.resolve_safe_destination()
    helper used everywhere else, refusing private/loopback/link-local/
    reserved/multicast targets (e.g. cloud metadata endpoints, internal
    admin panels, other services on the host) before ever handing the
    URL to `requests`.

    Residual, lower-priority gap (documented, not closed here -- same
    call ebooks.py's own _curl_fetch_text() makes for its own redirect-
    following residual): requests.post() below still resolves the
    hostname a second time at its own connect step, so a DNS-rebinding
    attacker who could make the SAME hostname resolve differently
    within the brief gap between this check and that connect could
    still reach an internal address. Pinning the actual TCP connect to
    the address validated here (the way dialout.py/ebooks.py do) would
    need a custom urllib3 connection/pool -- `requests` has no built-in
    resolve-once-then-connect knob the way a raw socket or curl's
    --resolve does -- and would also have to keep the webhook's bearer-
    token Authorization header out of curl's subprocess argv (readable
    by other local users via /proc/<pid>/cmdline). Descoped this round
    as more surface than an occasional, backgrounded, admin-configured
    webhook POST justifies; revisit if this ever becomes reachable from
    non-admin input.
    """
    parsed = urlparse(url or '')
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        return 0, f'refused: not a plain http(s) URL: {url!r}'
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    _family, _sockaddr, ssrf_err = resolve_safe_destination(parsed.hostname, port)
    if ssrf_err:
        return 0, f'refused: {ssrf_err}'
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
                # Real Medium finding from a security/performance
                # audit (2026-09-02): a bare threading.Thread has no
                # Flask app context of its own -- unlike every other
                # background-thread DB touch in this codebase (e.g.
                # sysop_paging.py's own webhook-firing call site,
                # which explicitly wraps in `with _app().app_context()`
                # for exactly this reason), this ran Webhook.query.get()/
                # db.session.commit() with no context at all, raising
                # RuntimeError: Working outside of application context
                # -- silently swallowed by the bare except below.
                # Effect: last_called_at/last_status/last_error were
                # NEVER persisted for any webhook delivery, even a
                # successful one; the admin webhook UI would show
                # "never called" forever. _app() is the same lightweight
                # transient Flask+SQLAlchemy context used elsewhere in
                # this codebase for exactly this cross-context need.
                from .bbs_ui import _app as _bbs_app
                with _bbs_app().app_context():
                    row = Webhook.query.get(wid)
                    if row:
                        row.last_called_at = datetime.utcnow()
                        row.last_status = status or 0
                        row.last_error = err
                        db.session.commit()
            except Exception:
                pass

        threading.Thread(target=_runner, daemon=True).start()
