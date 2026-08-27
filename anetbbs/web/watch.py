# anetbbs/web/watch.py
"""
"Watch It Live" -- a public, no-login page showing coarse real-time BBS
activity (who's on, roughly what they're doing, across every protocol).
Meant to be shared/embedded off-site (social posts, a BBS's own landing
page) as a demonstration of ANetBBS's terminal-meets-web pitch.

Deliberately built on the SAME UserSession data and coarse labeling as
web/who.py (via core.presence_labels), not on NodeActivity.last_screen --
last_screen is currently unpopulated everywhere in the codebase and has no
privacy scoping built for it (see project notes); wiring that up safely is
its own separate piece of work, not part of this page. This page only
ever shows a friendly area label (e.g. "MRC Chat"), never a raw URL path,
a specific room/board name, or message content.

Off by default (PUBLIC_WATCH_ENABLED, see config.py) -- unlike /who, this
page shows the same presence data to the anonymous public internet rather
than only other logged-in BBS users, so it's opt-in for the sysop, and any
individual user can exclude themselves via User.public_watch_optout in
their profile settings.

No CSP / X-Frame-Options exists anywhere in this app today, so nothing
extra is needed here to allow iframe embedding. If site-wide clickjacking
protection is ever added later, this route needs an explicit carve-out to
stay embeddable.
"""
from datetime import datetime, timedelta

from flask import Blueprint, abort, current_app, jsonify, render_template

from ..core.presence_labels import classify, friendly_where
from ..models import User, UserSession, db

watch_bp = Blueprint('watch', __name__, url_prefix='/watch')


def _enabled():
    return current_app.config.get('PUBLIC_WATCH_ENABLED', False)


def _snapshot():
    cutoff = datetime.utcnow() - timedelta(minutes=5)
    rows = (db.session.query(UserSession, User)
            .join(User, User.id == UserSession.user_id)
            .filter(UserSession.last_seen >= cutoff)
            .filter(User.is_locked.is_(False))
            .filter(User.public_watch_optout.is_(False))
            .all())

    rich = []
    counts = {}
    for session_row, user in rows:
        proto, where = classify(session_row.page)
        counts[proto] = counts.get(proto, 0) + 1
        rich.append({
            'username': user.username,
            'protocol': proto,
            'where_label': friendly_where(proto, where),
            'last_seen': session_row.last_seen.isoformat() + 'Z',
        })
    rich.sort(key=lambda x: (x['protocol'], x['username'].lower()))
    return {'total': len(rich), 'counts': counts, 'sessions': rich}


@watch_bp.route('/')
def index():
    if not _enabled():
        abort(404)
    return render_template('watch/index.html',
                           bbs_name=current_app.config.get('BBS_NAME', 'ANetBBS'))


@watch_bp.route('/api/snapshot')
def api_snapshot():
    if not _enabled():
        abort(404)
    resp = jsonify(_snapshot())
    resp.headers['Cache-Control'] = 'no-store'
    return resp
