# anetbbs/core/presence_labels.py
"""
Shared coarse, privacy-conscious labeling for "what is this session doing"
— used by both the login-gated /who page and the public /watch page, so
there is exactly one place that decides how much detail about a session's
current location gets shown to someone else, rather than two copies that
can quietly drift apart.

Extracted from web/who.py, which was the original (and until now, only)
consumer.
"""
import re

_PROTO_RE = re.compile(r'^\[([a-z0-9_-]+)\](.*)$')


# Maps the first URL segment of a web session to a friendly area name.
# Anything not matched here falls through to "Browsing" — broad enough that
# we don't leak intent but specific enough to still be useful.
WEB_AREA_LABELS = {
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


def classify(page):
    """Pull the protocol out of a UserSession.page value (set by
    core.presence). Returns (protocol, where)."""
    page = page or ''
    m = _PROTO_RE.match(page)
    if m:
        return m.group(1), m.group(2).strip()
    # Web sessions don't use the [proto] prefix — they store the URL path.
    return 'web', page


def friendly_where(protocol, where):
    """Sanitize a web URL path to a coarse area label.

    For non-web protocols (telnet / ssh / rlogin) the page value already
    holds a friendly string (menu name, game name) — pass it through.
    For web sessions, collapse `/echomail/53/25407` -> "Echomail" etc. so
    one viewer can't follow a specific user around by their URL.
    """
    if protocol != 'web':
        return where or '—'
    if not where or where == '/':
        return 'Home'
    parts = where.lstrip('/').split('/', 1)
    return WEB_AREA_LABELS.get(parts[0], 'Browsing')
