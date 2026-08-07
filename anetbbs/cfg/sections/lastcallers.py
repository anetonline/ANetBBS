"""Last Callers section (anetbbs-cfg) -- read-only login log viewer.
InterBBS Last Callers sharing settings (which network relays it) are
web-admin-only (Admin -> Last Callers), same reasoning as the Wall
section's InterBBS settings gap -- involves .env writes plus echomail
area provisioning together.
"""
from anetbbs.cfg import ui
from anetbbs.models import CallerLog

COLUMNS = [
    ("Date", 17, lambda c: c.started_at.strftime("%Y-%m-%d %H:%M") if c.started_at else ""),
    ("User", 16, lambda c: c.username or ""),
    ("Service", 10, lambda c: c.service or ""),
    ("IP", 16, lambda c: c.ip_address or ""),
    ("Origin", 12, lambda c: c.origin_bbs or "(local)"),
]


def list_recent(limit=300):
    return CallerLog.query.order_by(CallerLog.started_at.desc()).limit(limit).all()


def run(stdscr):
    ui.run_list(stdscr, "Last Callers (most recent 300)", COLUMNS, list_recent,
                empty_hint="(no callers logged yet)")
