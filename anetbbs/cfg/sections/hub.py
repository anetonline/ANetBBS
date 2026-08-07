"""Echomail Hub extras section (anetbbs-cfg) -- AreaFix log, poll log,
and QWK node requests. Read-only log browsing for the first two; QWK
node request approve/deny mirrors anetbbs/web/hub_admin.py's
approve_qwk_request()/deny_qwk_request() logic exactly (packet_id
validation, uniqueness check, random password generation, QWKNode
creation) since that's real security-relevant credential-issuing logic,
not something to reimplement loosely. `reviewed_by` records
'sysop (terminal)' -- there's no logged-in user identity in a local CLI
tool the way there is in the web admin.
"""
import re
import secrets
import string
from datetime import datetime

from anetbbs.cfg import ui
from anetbbs.models import db, AreafixLog, EchomailPollLog, QWKNodeRequest, QWKNode

REVIEWER_LABEL = "sysop (terminal)"

AREAFIX_COLUMNS = [
    ("Date", 17, lambda r: r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else ""),
    ("From", 18, lambda r: r.from_address or ""),
    ("Type", 12, lambda r: r.request_type or ""),
    ("Areas", 24, lambda r: (r.area_tags or "")[:24]),
    ("OK", 4, lambda r: "Yes" if r.success else "No"),
]

POLL_LOG_COLUMNS = [
    ("Started", 17, lambda p: p.started_at.strftime("%Y-%m-%d %H:%M") if p.started_at else ""),
    ("Network", 18, lambda p: p.network.name if p.network else "?"),
    ("Type", 8, lambda p: p.poll_type),
    ("Status", 10, lambda p: p.status),
    ("Sent", 6, lambda p: p.messages_sent or 0),
    ("Recv", 6, lambda p: p.messages_received or 0),
]

QWK_REQUEST_COLUMNS = [
    ("Date", 17, lambda r: r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else ""),
    ("BBS Name", 22, lambda r: r.bbs_name),
    ("Packet ID", 10, lambda r: r.packet_id),
    ("Sysop", 16, lambda r: r.sysop_name or ""),
    ("Status", 10, lambda r: r.status),
]


def list_areafix_log(limit=200):
    return AreafixLog.query.order_by(AreafixLog.created_at.desc()).limit(limit).all()


def list_poll_log(limit=200):
    return EchomailPollLog.query.order_by(EchomailPollLog.started_at.desc()).limit(limit).all()


def list_qwk_requests():
    pending = QWKNodeRequest.query.filter_by(status="pending").order_by(QWKNodeRequest.created_at.asc()).all()
    reviewed = (QWKNodeRequest.query.filter(QWKNodeRequest.status != "pending")
                .order_by(QWKNodeRequest.reviewed_at.desc()).limit(50).all())
    return pending + reviewed


def approve_qwk_request(req):
    """Returns (ok: bool, message: str). Mirrors hub_admin.py's
    approve_qwk_request() route logic exactly."""
    if req.status != "pending":
        return False, "Request is no longer pending."

    pid = req.packet_id.upper()
    if not re.match(r'^[A-Z0-9]{2,8}$', pid):
        return False, f"Packet ID {pid!r} is not valid (2-8 letters/digits only) -- deny this request."
    if QWKNode.query.filter_by(packet_id=pid).first():
        return False, f"Packet ID {pid} is already taken -- deny this request or edit first."

    alphabet = string.ascii_letters + string.digits
    password = ''.join(secrets.choice(alphabet) for _ in range(16))

    from anetbbs.models import _default_hub_identity_id
    node = QWKNode(
        packet_id=pid,
        name=req.bbs_name,
        sysop=req.sysop_name,
        email=req.email,
        password=password,
        is_active=True,
        hub_identity_id=req.hub_identity_id or _default_hub_identity_id(),
        notes=(f"Auto-created from node request #{req.id}. "
               f"BBS address: {req.bbs_address or 'not provided'}"),
    )
    db.session.add(node)
    db.session.flush()

    req.status = "approved"
    req.reviewed_at = datetime.utcnow()
    req.reviewed_by = REVIEWER_LABEL
    req.generated_password = password
    req.node_id = node.id
    db.session.commit()
    return True, f"Approved! QWK node {pid} created with password: {password}"


def deny_qwk_request(req, reason=""):
    if req.status != "pending":
        return False, "Request is no longer pending."
    req.status = "denied"
    req.reviewed_at = datetime.utcnow()
    req.reviewed_by = REVIEWER_LABEL
    req.deny_reason = reason.strip()
    db.session.commit()
    return True, "Denied."


def _view_areafix_log(stdscr):
    ui.run_list(stdscr, "AreaFix Log", AREAFIX_COLUMNS, list_areafix_log,
                empty_hint="(no AreaFix activity yet)")


def _view_poll_log(stdscr):
    ui.run_list(stdscr, "Echomail Poll Log", POLL_LOG_COLUMNS, list_poll_log,
                empty_hint="(no polls logged yet)")


def _approve(stdscr, req):
    if req.status != "pending":
        ui.show_message(stdscr, "Request is no longer pending.", error=True)
        return
    if not ui.confirm(stdscr, f"Approve QWK node request from '{req.bbs_name}' "
                               f"(packet id {req.packet_id})?"):
        return
    ok, msg = approve_qwk_request(req)
    ui.show_message(stdscr, msg, error=not ok)


def _deny(stdscr, req):
    if req.status != "pending":
        ui.show_message(stdscr, "Request is no longer pending.", error=True)
        return
    reason = ui.prompt_text(stdscr, "Deny reason (optional): ", "")
    if reason is None:
        return
    ok, msg = deny_qwk_request(req, reason)
    ui.show_message(stdscr, msg, error=not ok)


def _view_qwk_requests(stdscr):
    ui.run_list(
        stdscr, "QWK Node Requests (pending first, then recent history)",
        QWK_REQUEST_COLUMNS, list_qwk_requests,
        extra_actions={"a": ("Approve", _approve), "n": ("deNy", _deny)},
        empty_hint="(no QWK node requests)",
    )


def run(stdscr):
    items = [
        ("areafix", "AreaFix Log"),
        ("polllog", "Echomail Poll Log"),
        ("qwkreq", "QWK Node Requests"),
    ]
    while True:
        choice = ui.run_menu(stdscr, "Echomail Hub", items)
        if choice is None:
            return
        if choice == "areafix":
            _view_areafix_log(stdscr)
        elif choice == "polllog":
            _view_poll_log(stdscr)
        elif choice == "qwkreq":
            _view_qwk_requests(stdscr)
