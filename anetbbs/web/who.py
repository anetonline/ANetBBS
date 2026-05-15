# anetbbs/web/who.py
"""
Who's-online listing — combined view of all logged-in users across protocols.

Reads UserSession (web + telnet/SSH/rlogin via core.presence) and tags rows by
service so users can see who's chatting on telnet vs hanging out on the web.
"""
import re
from datetime import datetime, timedelta
from flask import Blueprint, render_template
from flask_login import login_required

from ..models import UserSession, User

who_bp = Blueprint('who', __name__, url_prefix='/who')


_PROTO_RE = re.compile(r'^\[([a-z0-9_-]+)\](.*)$')


# Maps the first URL segment of a web session to a friendly area name shown
# to non-admins on /who/. Sysops still see the raw path so they can debug.
# Anything not matched here falls through to "Browsing" — broad enough that
# we don't leak intent but specific enough that admins still get the path.
_WEB_AREA_LABELS = {
    'admin': 'Admin',
    'boards': 'Boards',
    'bulletins': 'Bulletins',
    'calendar': 'Calendar',
    'contacts': 'Contacts',
    'docs': 'Documentation',
    'echomail': 'Echomail',
    'files': 'Files',
    'file-areas': 'Files',
    'gallery': 'Gallery',
    'games': 'Games',
    'groups': 'Groups',
    'imsg': 'Inter-BBS IM',
    'irc': 'IRC',
    'leaderboard': 'Leaderboard',
    'messages': 'Messaging',
    'mrc': 'MRC Chat',
    'netmail': 'Netmail',
    'notifications': 'Notifications',
    'oneliners': 'Oneliners',
    'page': 'Personal Pages',
    'polls': 'Polls',
    'profile': 'Profile',
    'rss': 'RSS Reader',
    'saved': 'Saved Posts',
    'shoutbox': 'Shoutbox',
    'stats': 'Stats',
    'terminal': 'Web Terminal',
    'who': "Who's Online",
    'wiki': 'Wiki',
}


def _classify(row):
    """Pull the protocol out of UserSession.page (set by core.presence)."""
    page = row.page or ''
    m = _PROTO_RE.match(page)
    if m:
        return m.group(1), m.group(2).strip()
    # Web sessions don't use the [proto] prefix — they store the URL path.
    return 'web', page


def _friendly_where(protocol, where):
    """Sanitize a web URL path to a coarse area label for non-admin viewers.

    For non-web protocols (telnet / ssh / rlogin) UserSession.page already
    holds a friendly string (menu name, game name) — pass it through.
    For web sessions, collapse `/echomail/53/25407` → "Echomail" etc. so
    one user can't follow another around by copy-pasting their URL.
    """
    if protocol != 'web':
        return where or '—'
    if not where or where == '/':
        return 'Home'
    parts = where.lstrip('/').split('/', 1)
    return _WEB_AREA_LABELS.get(parts[0], 'Browsing')


@who_bp.route('/')
@login_required
def index():
    cutoff = datetime.utcnow() - timedelta(minutes=5)
    rows = (UserSession.query
            .filter(UserSession.last_seen >= cutoff)
            .join(User, User.id == UserSession.user_id)
            .all())
    rich = []
    counts = {}
    for r in rows:
        proto, where = _classify(r)
        counts[proto] = counts.get(proto, 0) + 1
        rich.append({
            'user': r.user, 'protocol': proto, 'where': where,
            'where_label': _friendly_where(proto, where),
            'last_seen': r.last_seen,
            'ip': r.ip_address or '',
        })
    rich.sort(key=lambda x: (x['protocol'], (x['user'].username or '').lower()))
    return render_template('who/index.html', rows=rich, counts=counts,
                           total=len(rich))
