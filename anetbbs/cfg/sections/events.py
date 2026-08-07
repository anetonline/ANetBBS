"""Scheduled Events section (anetbbs-cfg).

params_json/schedule_json are edited as raw JSON text (validated on
save) rather than building per-handler/per-schedule-kind sub-forms --
see ScheduledEvent's own docstring in models.py for the schedule JSON
shapes and events/handlers.py's HANDLER_META for what params each
handler_key accepts. [R]un Now reuses anetbbs.events.runner.fire() --
the same function the web admin's "Run now" button calls -- rather than
re-implementing handler dispatch.
"""
import json

from flask import current_app

from anetbbs.cfg import ui
from anetbbs.models import db, ScheduledEvent

FIELDS = [
    {"key": "name", "label": "Name", "kind": "text"},
    {"key": "handler_key", "label": "Handler", "kind": "choice", "choices": []},
    {"key": "schedule_json", "label": "Schedule (JSON)", "kind": "text"},
    {"key": "params_json", "label": "Params (JSON)", "kind": "text"},
    {"key": "is_enabled", "label": "Enabled", "kind": "bool"},
]

HELP = [
    "Schedule shapes: {\"kind\":\"daily\",\"time\":\"03:00\"} / \"hourly\" / \"weekly\" / \"interval\".",
    "See Admin -> Scheduled Events (web) for full handler param docs.",
]

NEW_DEFAULTS = {
    "name": "", "handler_key": "noop",
    "schedule_json": '{"kind": "daily", "time": "03:00"}',
    "params_json": "{}", "is_enabled": True,
}

COLUMNS = [
    ("Name", 26, lambda e: e.name),
    ("Handler", 20, lambda e: e.handler_key),
    ("Enabled", 8, lambda e: "Yes" if e.is_enabled else "No"),
    ("LastStatus", 10, lambda e: e.last_status or ""),
    ("LastRun", 17, lambda e: e.last_run_at.strftime("%Y-%m-%d %H:%M") if e.last_run_at else ""),
]


def _fields_with_handler_choices():
    from anetbbs.events.handlers import REGISTRY
    fields = [dict(f) for f in FIELDS]
    for f in fields:
        if f["key"] == "handler_key":
            f["choices"] = sorted(REGISTRY.keys())
    return fields


def list_events():
    return ScheduledEvent.query.order_by(ScheduledEvent.name).all()


def values_from_event(e):
    return {f["key"]: getattr(e, f["key"]) for f in FIELDS}


def _validate_json_fields(data):
    for key in ("schedule_json", "params_json"):
        try:
            json.loads(data.get(key) or "{}")
        except ValueError:
            return f"{key} is not valid JSON."
    return None


def create_event(data):
    e = ScheduledEvent(**data)
    db.session.add(e)
    db.session.commit()
    return e


def update_event(e, data):
    for k, v in data.items():
        setattr(e, k, v)
    db.session.commit()


def delete_event(e):
    db.session.delete(e)
    db.session.commit()


def run_event_now(event_id):
    app = current_app._get_current_object()
    from anetbbs.events.runner import fire
    return fire(app, event_id)


def _add(stdscr):
    data = ui.run_form(stdscr, "New Scheduled Event", _fields_with_handler_choices(),
                        dict(NEW_DEFAULTS), help_lines=HELP)
    if data is None:
        return
    if not data.get("name"):
        ui.show_message(stdscr, "Name is required.", error=True)
        return
    err = _validate_json_fields(data)
    if err:
        ui.show_message(stdscr, err, error=True)
        return
    create_event(data)


def _edit(stdscr, e):
    data = ui.run_form(stdscr, f"Edit Event: {e.name}", _fields_with_handler_choices(),
                        values_from_event(e), help_lines=HELP)
    if data is None:
        return
    err = _validate_json_fields(data)
    if err:
        ui.show_message(stdscr, err, error=True)
        return
    update_event(e, data)


def _delete(stdscr, e):
    if ui.confirm(stdscr, f"Delete scheduled event '{e.name}'?"):
        delete_event(e)


def _run_now(stdscr, e):
    if not ui.confirm(stdscr, f"Run '{e.name}' now?"):
        return
    ok, out = run_event_now(e.id)
    ui.show_message(stdscr, f"{'OK' if ok else 'FAILED'}:\n{out}"[:1000], error=not ok)


def run(stdscr):
    ui.run_list(
        stdscr, "Scheduled Events", COLUMNS, list_events,
        on_add=_add, on_edit=_edit, on_delete=_delete,
        extra_actions={"r": ("RunNow", _run_now)},
    )
