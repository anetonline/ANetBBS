"""Echomail Networks & Areas section (anetbbs-cfg).

Two-level list: pick a network, then drill into that network's echo
areas. Enter on the network list opens the area list (matching how a
sysop actually spends their time -- areas change constantly, network
transport settings rarely); editing the network's own transport settings
is the separate [S]ettings hotkey.

Advanced/rare EchomailNetwork fields (TLS, CRAM-MD5, packet_password,
hub identity) are intentionally left out of this form -- web admin only
for now, same as boards.py's ansi_banner gap.
"""
from sqlalchemy.exc import IntegrityError

from anetbbs.cfg import ui
from anetbbs.models import db, EchomailNetwork, EchoArea

NETWORK_FIELDS = [
    {"key": "name", "label": "Name", "kind": "text"},
    {"key": "network_type", "label": "Type", "kind": "choice", "choices": ["binkp", "qwk"]},
    {"key": "our_address", "label": "Our Address", "kind": "text_nullable"},
    {"key": "hub_address", "label": "Hub Address", "kind": "text_nullable"},
    {"key": "binkp_host", "label": "BinkP Host", "kind": "text_nullable"},
    {"key": "binkp_port", "label": "BinkP Port", "kind": "int"},
    {"key": "binkp_password", "label": "BinkP Password", "kind": "text_nullable"},
    {"key": "areafix_password", "label": "AreaFix Password", "kind": "text_nullable"},
    {"key": "poll_interval_minutes", "label": "Poll Interval (min)", "kind": "int"},
    {"key": "is_active", "label": "Active", "kind": "bool"},
]

NETWORK_HELP = [
    "TLS / CRAM-MD5 / packet password / hub identity: web admin only.",
]

NETWORK_NEW_DEFAULTS = {
    "name": "", "network_type": "binkp", "our_address": None, "hub_address": None,
    "binkp_host": None, "binkp_port": 24554, "binkp_password": None,
    "areafix_password": None, "poll_interval_minutes": 60, "is_active": True,
}

NETWORK_COLUMNS = [
    ("Name", 22, lambda n: n.name),
    ("Type", 6, lambda n: n.network_type),
    ("Our Address", 16, lambda n: n.our_address or ""),
    ("Hub Address", 16, lambda n: n.hub_address or ""),
    ("Active", 6, lambda n: "Yes" if n.is_active else "No"),
]

AREA_FIELDS = [
    {"key": "tag", "label": "Tag", "kind": "text"},
    {"key": "name", "label": "Name", "kind": "text"},
    {"key": "description", "label": "Description", "kind": "text_nullable"},
    {"key": "category", "label": "Category", "kind": "text_nullable"},
    {"key": "order", "label": "Sort Order", "kind": "int"},
    {"key": "min_access_level", "label": "Min Access Level", "kind": "int"},
    {"key": "is_sysop_only", "label": "Sysop Only", "kind": "bool"},
    {"key": "is_active", "label": "Active", "kind": "bool"},
    {"key": "is_subscribed", "label": "Subscribed (receive from upstream)", "kind": "bool"},
    {"key": "require_real_name", "label": "Require Real Name", "kind": "bool"},
]

AREA_NEW_DEFAULTS = {
    "tag": "", "name": "", "description": None, "category": None, "order": 0,
    "min_access_level": 10, "is_sysop_only": False, "is_active": True,
    "is_subscribed": True, "require_real_name": False,
}

AREA_COLUMNS = [
    ("Order", 6, lambda a: a.order),
    ("Tag", 20, lambda a: a.tag),
    ("Name", 24, lambda a: a.name),
    ("MinLvl", 7, lambda a: a.min_access_level),
    ("Active", 6, lambda a: "Yes" if a.is_active else "No"),
]


def list_networks():
    return EchomailNetwork.query.order_by(EchomailNetwork.name).all()


def values_from_network(n):
    return {f["key"]: getattr(n, f["key"]) for f in NETWORK_FIELDS}


def create_network(data):
    n = EchomailNetwork(**data)
    db.session.add(n)
    db.session.commit()
    return n


def update_network(n, data):
    for k, v in data.items():
        setattr(n, k, v)
    db.session.commit()


def delete_network(n):
    db.session.delete(n)
    db.session.commit()


def list_areas(network):
    return EchoArea.query.filter_by(network_id=network.id).order_by(EchoArea.order, EchoArea.name).all()


def values_from_area(a):
    return {f["key"]: getattr(a, f["key"]) for f in AREA_FIELDS}


def create_area(network, data):
    a = EchoArea(network_id=network.id, **data)
    db.session.add(a)
    db.session.commit()
    return a


def update_area(a, data):
    for k, v in data.items():
        setattr(a, k, v)
    db.session.commit()


def delete_area(a):
    db.session.delete(a)
    db.session.commit()


def reorder_area(network, a, direction):
    ordered = list_areas(network)
    idx = ordered.index(a)
    new_idx = idx + direction
    if 0 <= new_idx < len(ordered):
        other = ordered[new_idx]
        a.order, other.order = other.order, a.order
        db.session.commit()


def _add_network(stdscr):
    data = ui.run_form(stdscr, "New Network", NETWORK_FIELDS, dict(NETWORK_NEW_DEFAULTS), help_lines=NETWORK_HELP)
    if data is None:
        return
    if not data.get("name"):
        ui.show_message(stdscr, "Name is required.", error=True)
        return
    try:
        create_network(data)
    except IntegrityError:
        db.session.rollback()
        ui.show_message(stdscr, "Could not create network (constraint violation).", error=True)


def _edit_network_settings(stdscr, n):
    data = ui.run_form(stdscr, f"Network Settings: {n.name}", NETWORK_FIELDS, values_from_network(n), help_lines=NETWORK_HELP)
    if data is None:
        return
    try:
        update_network(n, data)
    except IntegrityError:
        db.session.rollback()
        ui.show_message(stdscr, "Could not save (constraint violation).", error=True)


def _delete_network(stdscr, n):
    area_count = EchoArea.query.filter_by(network_id=n.id).count()
    warn = f"\nWARNING: this deletes {area_count} echo area(s) and all their messages too." if area_count else ""
    if ui.confirm(stdscr, f"Delete network '{n.name}'?{warn}"):
        delete_network(n)


def _add_area(network):
    def _cb(stdscr):
        data = ui.run_form(stdscr, f"New Area in {network.name}", AREA_FIELDS, dict(AREA_NEW_DEFAULTS))
        if data is None:
            return
        if not data.get("tag") or not data.get("name"):
            ui.show_message(stdscr, "Tag and Name are required.", error=True)
            return
        try:
            create_area(network, data)
        except IntegrityError:
            db.session.rollback()
            ui.show_message(stdscr, "Could not create area (duplicate tag?).", error=True)
    return _cb


def _edit_area(stdscr, a):
    data = ui.run_form(stdscr, f"Edit Area: {a.tag}", AREA_FIELDS, values_from_area(a))
    if data is None:
        return
    try:
        update_area(a, data)
    except IntegrityError:
        db.session.rollback()
        ui.show_message(stdscr, "Could not save (duplicate tag?).", error=True)


def _delete_area(stdscr, a):
    msg_count = a.messages.count()
    warn = f"\nWARNING: this deletes {msg_count} message(s) in this area too." if msg_count else ""
    if ui.confirm(stdscr, f"Delete area '{a.tag}'?{warn}"):
        delete_area(a)


def _manage_areas(stdscr, n):
    def _reorder_cb(stdscr, a, direction):
        reorder_area(n, a, direction)

    ui.run_list(
        stdscr, f"Echo Areas: {n.name}", AREA_COLUMNS, lambda: list_areas(n),
        on_add=_add_area(n), on_edit=_edit_area, on_delete=_delete_area,
        on_reorder=_reorder_cb,
    )


def run(stdscr):
    ui.run_list(
        stdscr, "Echomail Networks & Areas", NETWORK_COLUMNS, list_networks,
        on_add=_add_network, on_edit=_manage_areas, on_delete=_delete_network,
        extra_actions={"s": ("Settings", _edit_network_settings)},
    )
