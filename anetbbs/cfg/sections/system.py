"""System / Network Settings section (anetbbs-cfg).

Unlike the other sections, this isn't DB-backed -- it's a grouped editor
over the .env file at anetbbs.config.BASE_DIR / '.env' (the same path
anetbbs/config.py already loads via load_dotenv). Groups mirror
.env.example's own `#` comment headers.

The parse/round-trip functions (load_env_lines/env_dict/apply_updates/
render_lines/write_env) are plain, curses-free, and unit-tested directly
in tests/test_cfg_env_editor.py -- a no-op load+render must reproduce the
original file byte-for-byte, since editing one group must never disturb
comments or unrelated keys elsewhere in the file.

This tool only edits config -- it does not restart any service. Most of
these settings are read once at process start, same as SCFG/mystic -cfg:
edit here, then restart the relevant systemd unit(s) yourself.
"""
import re

from anetbbs.cfg import ui
from anetbbs.config import BASE_DIR

ENV_PATH = BASE_DIR / ".env"

_KV_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")

GROUPS = [
    {"key": "ports", "label": "Server Ports", "fields": [
        {"key": "TELNET_ENABLED", "label": "Telnet Enabled", "kind": "bool"},
        {"key": "TELNET_HOST", "label": "Telnet Host", "kind": "text"},
        {"key": "TELNET_PORT", "label": "Telnet Port", "kind": "int"},
        {"key": "SSH_ENABLED", "label": "SSH Enabled", "kind": "bool"},
        {"key": "SSH_HOST", "label": "SSH Host", "kind": "text"},
        {"key": "SSH_PORT", "label": "SSH Port", "kind": "int"},
        {"key": "RLOGIN_ENABLED", "label": "rlogin Enabled (insecure)", "kind": "bool"},
        {"key": "RLOGIN_HOST", "label": "rlogin Host", "kind": "text"},
        {"key": "RLOGIN_PORT", "label": "rlogin Port", "kind": "int"},
        {"key": "PETSCII40_ENABLED", "label": "PETSCII 40-col Enabled", "kind": "bool"},
        {"key": "PETSCII40_PORT", "label": "PETSCII 40-col Port", "kind": "int"},
        {"key": "PETSCII80_ENABLED", "label": "PETSCII 80-col Enabled", "kind": "bool"},
        {"key": "PETSCII80_PORT", "label": "PETSCII 80-col Port", "kind": "int"},
        {"key": "WEB_BIND", "label": "Web Bind Address", "kind": "text"},
        {"key": "WEB_PORT", "label": "Web Port", "kind": "int"},
        {"key": "FINGER_LISTEN_HOST", "label": "Finger Host", "kind": "text"},
        {"key": "FINGER_LISTEN_PORT", "label": "Finger Port", "kind": "int"},
    ]},
    {"key": "app", "label": "Application Settings", "fields": [
        {"key": "BBS_NAME", "label": "BBS Name", "kind": "text"},
        {"key": "BBS_DESCRIPTION", "label": "BBS Description", "kind": "text"},
        {"key": "BBS_DOMAIN", "label": "Public Domain", "kind": "text"},
        {"key": "BBS_PUBLIC_HOST", "label": "Public Hostname Override", "kind": "text"},
        {"key": "SYSOP_NAME", "label": "Sysop Name", "kind": "text"},
        {"key": "BBS_EMAIL", "label": "Sysop Email", "kind": "text"},
        {"key": "BBS_LOCATION", "label": "Location", "kind": "text"},
        {"key": "BBS_NODES", "label": "Concurrent Nodes (1-100)", "kind": "int"},
        {"key": "IDLE_TIMEOUT_SECONDS", "label": "Idle Kick (seconds, 0=never)", "kind": "int"},
        {"key": "AFK_WARNING_SECONDS", "label": "AFK Warning (seconds, 0=off)", "kind": "int"},
    ]},
    {"key": "logging", "label": "Logging", "fields": [
        {"key": "LOG_LEVEL", "label": "Log Level", "kind": "choice",
         "choices": ["DEBUG", "INFO", "WARNING", "ERROR"]},
    ]},
    {"key": "binkp", "label": "BinkP (FidoNet Mailer)", "fields": [
        {"key": "BINKP_LISTEN_HOST", "label": "Listen Host", "kind": "text"},
        {"key": "BINKP_LISTEN_PORT", "label": "Listen Port", "kind": "int"},
        {"key": "BINKP_OUR_ADDRESS", "label": "Our Primary AKA", "kind": "text"},
        {"key": "BINKP_SYSTEM_NAME", "label": "System Name", "kind": "text"},
    ]},
    {"key": "files", "label": "Files / FTP", "fields": [
        {"key": "FTP_ENABLED", "label": "FTP Enabled", "kind": "bool"},
        {"key": "FTP_HOST", "label": "FTP Host", "kind": "text"},
        {"key": "FTP_PORT", "label": "FTP Port", "kind": "int"},
        {"key": "FTP_ANON_ENABLED", "label": "Anonymous FTP", "kind": "bool"},
        {"key": "FTP_PASV_PORTS", "label": "Passive Port Range", "kind": "text"},
        {"key": "FTP_ROOT_DIR", "label": "FTP Root Dir", "kind": "text"},
        {"key": "FTP_BANNER", "label": "FTP Banner", "kind": "text"},
        {"key": "RATIO_MIN", "label": "Min Upload:Download Ratio (0=off)", "kind": "int"},
        {"key": "CLAMSCAN_PATH", "label": "ClamAV clamscan Path", "kind": "text"},
    ]},
    {"key": "games", "label": "Games", "fields": [
        {"key": "GAMES_ENABLED", "label": "Games Enabled", "kind": "bool"},
        {"key": "GAMES_MAX_NODES", "label": "Max Concurrent Game Nodes", "kind": "int"},
        {"key": "GAMES_SESSION_TIMEOUT", "label": "Session Timeout (seconds)", "kind": "int"},
        {"key": "DOSBOX_PATH", "label": "DOSBox Path", "kind": "text"},
        {"key": "DOSEMU_PATH", "label": "DOSEMU Path", "kind": "text"},
        {"key": "NODEJS_PATH", "label": "Node.js Path", "kind": "text"},
        {"key": "MYSTIC_PYTHON_PATH", "label": "Mystic Python Interpreter", "kind": "text"},
        {"key": "MYSTIC_BBS_PATH", "label": "Mystic BBS Runtime Path", "kind": "text"},
    ]},
    {"key": "echomail", "label": "Echomail", "fields": [
        {"key": "ECHOMAIL_ENABLED", "label": "Echomail Enabled", "kind": "bool"},
        {"key": "ECHOMAIL_POLL_ENABLED", "label": "Auto-Poll Enabled", "kind": "bool"},
        {"key": "ECHOMAIL_ORIGIN_LINE", "label": "Origin Line", "kind": "text"},
        {"key": "ECHOMAIL_TEAR_LINE", "label": "Tear Line", "kind": "text"},
    ]},
    {"key": "nuv", "label": "New User Verification", "fields": [
        {"key": "NUV_ENABLED", "label": "NUV Enabled", "kind": "bool"},
    ]},
]

_DEFAULTS = {"bool": False, "int": 0, "text": "", "choice": ""}


def load_env_lines(path=None):
    """Returns an ordered list of tuples describing every line in the
    file: ('kv', KEY, value) for a KEY=value line, or ('raw', text) for
    anything else (comments, blanks). Missing file -> empty list."""
    path = path or ENV_PATH
    if not path.exists():
        return []
    lines = []
    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh.read().split("\n"):
            m = _KV_RE.match(raw)
            if m:
                lines.append(("kv", m.group(1), m.group(2)))
            else:
                lines.append(("raw", raw))
    # split("\n") on a trailing-newline file produces one extra '' entry
    if lines and lines[-1] == ("raw", ""):
        lines.pop()
    return lines


def env_dict(lines):
    return {key: val for item in lines if item[0] == "kv" for _, key, val in [item]}


def apply_updates(lines, updates):
    """Returns a NEW lines list with `updates` (dict key->str) applied in
    place where the key already exists; unknown keys are appended at the
    end. Does not mutate the input list."""
    lines = list(lines)
    seen = set()
    out = []
    for item in lines:
        if item[0] == "kv" and item[1] in updates:
            out.append(("kv", item[1], updates[item[1]]))
            seen.add(item[1])
        else:
            out.append(item)
    for k, v in updates.items():
        if k not in seen:
            out.append(("kv", k, v))
    return out


def render_lines(lines):
    out = [(f"{item[1]}={item[2]}" if item[0] == "kv" else item[1]) for item in lines]
    return ("\n".join(out) + "\n") if out else ""


def write_env(lines, path=None):
    path = path or ENV_PATH
    path.write_text(render_lines(lines), encoding="utf-8")


def _to_form_value(kind, raw):
    if raw is None:
        return _DEFAULTS.get(kind, "")
    if kind == "bool":
        return raw.strip().lower() == "true"
    if kind == "int":
        try:
            return int(raw)
        except ValueError:
            return _DEFAULTS["int"]
    return raw


def _to_env_value(kind, value):
    if kind == "bool":
        return "true" if value else "false"
    return str(value)


def _edit_group(stdscr, group):
    lines = load_env_lines()
    current = env_dict(lines)
    fields = []
    values = {}
    for f in group["fields"]:
        form_field = {"key": f["key"], "label": f["label"], "kind": f["kind"]}
        if f["kind"] == "choice":
            form_field["choices"] = f["choices"]
        fields.append(form_field)
        values[f["key"]] = _to_form_value(f["kind"], current.get(f["key"]))

    data = ui.run_form(stdscr, group["label"], fields, values)
    if data is None:
        return

    kind_by_key = {f["key"]: f["kind"] for f in group["fields"]}
    updates = {k: _to_env_value(kind_by_key[k], v) for k, v in data.items()}
    write_env(apply_updates(lines, updates))
    ui.show_message(
        stdscr,
        "Saved to .env.\nRestart the affected service(s) to apply "
        "(e.g. systemctl restart anetbbs-web anetbbs-telnet ...).",
    )


def run(stdscr):
    items = [(g["key"], g["label"]) for g in GROUPS]
    while True:
        choice = ui.run_menu(stdscr, "System / Network Settings", items)
        if choice is None:
            return
        group = next(g for g in GROUPS if g["key"] == choice)
        _edit_group(stdscr, group)
