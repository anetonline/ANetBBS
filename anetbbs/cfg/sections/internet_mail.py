"""Internet Mail policy settings for anetbbs-cfg.

These values live in the singleton MailConfig database row, alongside the
WebUI's Admin → Settings → Internet Mail page. SMTP listener bind details
remain in System / Network Settings → Internet Mail (SMTP receiver), backed
by the .env file and read when services start.
"""
from anetbbs.cfg import ui
from anetbbs.models import (db, MailConfig, MAILBOX_QUOTA_MAX_BYTES,
                            MAX_ATTACHMENTS_CEILING)

MAX_RECIPIENTS_PER_TRANSACTION = 100  # hard SMTP safety ceiling


FIELDS = [
    {"key": "enabled", "label": "Internet Mail Enabled", "kind": "bool"},
    {"key": "domain", "label": "Internet Mail Domain", "kind": "text"},
    {"key": "min_access_level", "label": "Minimum access level for user opt-in (0-255)", "kind": "int"},
    {"key": "max_message_size", "label": "Maximum inbound message size (bytes)", "kind": "int"},
    {"key": "max_recipients", "label": "Maximum unique recipients per message (1-100)", "kind": "int"},
    {"key": "outbound_per_hour", "label": "Outbound sends per user per hour (0=off)", "kind": "int"},
    {"key": "outbound_per_day", "label": "Outbound sends per user per day (0=off)", "kind": "int"},
    {"key": "mailbox_quota_bytes", "label": "Mailbox quota per user (bytes)", "kind": "int"},
    {"key": "max_attachments", "label": "Maximum attachments per message (0=off)", "kind": "int"},
    {"key": "max_attachment_bytes", "label": "Maximum decoded attachment size (bytes)", "kind": "int"},
]

HELP = [
    "Feature settings are shared with Admin -> Settings -> Internet Mail in the WebUI.",
    "Set the domain and verify DNS/MX, inbound port 25, and the outbound relay before enabling.",
    "Admins/Sysops are always eligible; other users need the configured access level. Opt in at Profile -> Edit.",
    "Outbound mail reuses the relay configured at Admin -> Settings -> SMTP / Email.",
    "Listener host/port/concurrency/DNSBL are in System / Network Settings -> Internet Mail (SMTP receiver).",
    "Limits are finite; the inbound unique-recipient ceiling cannot exceed 100.",
]


def run(stdscr):
    cfg = MailConfig.get()
    values = {field["key"]: getattr(cfg, field["key"]) for field in FIELDS}
    data = ui.run_form(stdscr, "Internet Mail Settings", FIELDS, values,
                       help_lines=HELP)
    if data is None:
        return

    # Mirror the web admin's floors/ceilings and SMTP's hard safety cap.
    data["domain"] = (data.get("domain") or "").strip()[:255]
    data["min_access_level"] = min(255, max(0, data["min_access_level"]))
    data["max_message_size"] = max(1, data["max_message_size"])
    data["max_recipients"] = min(MAX_RECIPIENTS_PER_TRANSACTION,
                                  max(1, data["max_recipients"]))
    data["outbound_per_hour"] = max(0, data["outbound_per_hour"])
    data["outbound_per_day"] = max(0, data["outbound_per_day"])
    data["mailbox_quota_bytes"] = min(MAILBOX_QUOTA_MAX_BYTES,
                                      max(1, data["mailbox_quota_bytes"]))
    data["max_attachments"] = min(MAX_ATTACHMENTS_CEILING,
                                   max(0, data["max_attachments"]))
    data["max_attachment_bytes"] = min(MAILBOX_QUOTA_MAX_BYTES,
                                       max(1, data["max_attachment_bytes"]))
    for key, value in data.items():
        setattr(cfg, key, value)
    db.session.commit()
    ui.show_message(stdscr, "Internet Mail settings saved.\nRestart anetbbs-mail after changing listener settings in .env.")
