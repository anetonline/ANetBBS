# anetbbs/web/who.py
"""
Who's-online listing — combined view of all logged-in users across protocols.

Reads UserSession (web + telnet/SSH/rlogin via core.presence) and tags rows by
service so users can see who's chatting on telnet vs hanging out on the web.
"""
from datetime import datetime, timedelta
from flask import Blueprint, render_template
from flask_login import login_required, current_user

from ..core.presence_labels import classify as _classify_page, friendly_where as _friendly_where
from ..models import UserSession, User

who_bp = Blueprint('who', __name__, url_prefix='/who')


def _classify(row):
    return _classify_page(row.page)


@who_bp.route('/')
@login_required
def index():
    cutoff = datetime.utcnow() - timedelta(minutes=5)
    rows = (UserSession.query
            .filter(UserSession.last_seen >= cutoff)
            .join(User, User.id == UserSession.user_id)
            .all())
    # Real gap found in a security/performance audit: every logged-in
    # user's IP address was shown to every OTHER logged-in user here --
    # unlike web/watch.py's public no-login activity ticker, which
    # deliberately never leaks IPs (see its own module docstring), this
    # page had no equivalent restriction despite being just as visible
    # to any ordinary account, not just admins. Restricted to admins,
    # matching how connection/node info is scoped everywhere else in
    # this codebase (the web NodeSpy panel and in-BBS Node Monitor are
    # both admin/sysop-only).
    show_ip = getattr(current_user, 'is_admin', False)
    rich = []
    counts = {}
    for r in rows:
        proto, where = _classify(r)
        counts[proto] = counts.get(proto, 0) + 1
        rich.append({
            'user': r.user, 'protocol': proto, 'where': where,
            'where_label': _friendly_where(proto, where),
            'last_seen': r.last_seen,
            'ip': (r.ip_address or '') if show_ip else '',
        })
    rich.sort(key=lambda x: (x['protocol'], (x['user'].username or '').lower()))
    return render_template('who/index.html', rows=rich, counts=counts,
                           total=len(rich))
