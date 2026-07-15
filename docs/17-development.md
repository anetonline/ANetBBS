# Development

So you want to extend ANetBBS — write a door game, add a web feature,
ship a theme, hook into the message system. This page walks through
each major extension point with working examples.

## Project layout

```
anetbbs/
├── anetbbs/              ← Python package (importable)
│   ├── web/              ← Flask blueprints (one file per feature area)
│   ├── core/             ← terminal session, menu engine
│   ├── echomail/         ← FidoNet binkp / QWK / TIC
│   ├── games/            ← door runner + Synchronet JS shim
│   │   ├── sbbs_stubs/   ← Synchronet API surface (pure Node)
│   │   ├── sbbs_doors/   ← bundled doors (LORD lives here)
│   │   └── web_games.py  ← in-browser mini-game registry
│   ├── mrc/              ← MRC chat client + namespace
│   ├── msp/              ← Inter-BBS IM (RFC 1312)
│   ├── rss/              ← RSS poller + reader
│   ├── wiki/             ← collaborative wiki engine
│   ├── features/         ← cross-cutting helpers
│   ├── templates/        ← Jinja2 HTML templates (mirrors blueprint names)
│   ├── static/           ← CSS, JS, images
│   ├── models.py         ← SQLAlchemy models (single source of truth)
│   └── web_app.py        ← create_app() factory + extension wiring
├── deploy/               ← systemd units, nginx template, wsgi wrapper
├── docs/                 ← these markdown files
├── vendor/               ← shipped third-party binaries (Mystic, BotWars…)
└── mrc/                  ← standalone MRC bridge daemon (aiohttp-based)
```

Everything in `anetbbs/` is one Python package. Blueprints register
themselves via `create_app()` in `web_app.py` — that's where to wire
in a new feature.

---

## Doors — 10 ways in

ANetBBS supports ten distinct **door types** out of the box. Each
maps to a `Game` model row (sysop creates via `/admin/games/`) with a
`game_type` discriminator.

| `game_type` | Run path | Drop file | Use when |
|---|---|---|---|
| `door_dos` | DOSBox-staging bridge with TCP nullmodem on 127.0.0.1:5001-N | `DOOR.SYS` / `DORINFO1.DEF` | Original DOS doors (TradeWars, LORD-DOS, Usurper…) |
| `door_dosemu` | dosemu2, virtual COM1 bridged to caller's PTY (no FOSSIL) | `DOOR.SYS` | DOS doors that need dosemu2 specifically instead of DOSBox |
| `door_native` | Direct exec, stdio piped to caller | configurable | Linux-native doors (Java, Go, …) |
| `door_synchronet` | Real `jsexec` if present, otherwise Node + our compat shim | `DOOR32.SYS` | Synchronet `.js` doors (LORD-JS, MajorMUD-JS, dorkit-based doors) |
| `door_mystic` | `mystic` binary running compiled `.mpx` script | Mystic-style | Mystic Pascal-script doors (`.mpx` precompiled) |
| `door_mystic_mps` | Auto-compile `.mps` → `.mpx` via `mplc`, then run | Mystic-style | Mystic source doors (`.mps`) |
| `door_rlogin` | Outbound rlogin to remote BBS game-server | none — passes user identity | DoorParty, A-Net Online, Synchronet xtrn servers |
| `door_telnet` | Outbound telnet to remote game server, no pre-auth handshake | none | TWGS (Trade Wars Game Server) and similar telnet-only remotes |
| `builtin_web` | Flask-routed browser game (xterm.js not needed) | none | Pure-web mini-games (Snake, 2048, Hangman…) |
| `door_dos_browser` | EmulatorJS + dosbox_pure, runs entirely client-side in the browser | none | DOS shareware bundles playable with no telnet/SSH client at all |

### Door entrypoints (config fields)

Every door row has these knobs (sysop sees them at `/admin/games/`):

| Field | Tokens supported | Example |
|---|---|---|
| `executable_path` | `%U` `%n` `%P` (per-node dir) … | `/home/anetbbs/doors/lord/lord.sh` |
| `working_directory` | same | `/home/anetbbs/doors/lord/` |
| `command_line_args` | same + `%f` (drop file path) | `-l %f -node %n` |
| `drop_file_path` | `%P` … | `%P/door.sys` |
| `drop_file_type` | — | `DOOR.SYS`, `DORINFO1.DEF`, `DOOR32.SYS` |
| `max_nodes` | — | `4` |
| `min_time_required` | minutes | `5` |
| `enabled` | bool | — |
| `sort_order` | int | for menu ordering |

Full token table is in [`14-door-games.md`](14-door-games.md).

### Per-node scratch dirs

The node manager allocates one of `GAMES_MAX_NODES` slots (default 10)
on each door launch and creates `data/temp/nodeN/` for that session.
Use `%P` in any path field to point at it. Multiple concurrent players
each get isolated dirs — never coordinate by writing to
`<door>/NODE1.DAT` and hoping; use `%P`.

### Writing a Synchronet JS door

The compat shim is at `anetbbs/games/sbbs_stubs/`. ~270 functions/objects
covering `bbs.*`, `console.*`, `system.*`, `user.*`, `File`, `load()`,
`require()`, `dd_lightbar_menu`, `mouse_getkey`. If you write a door
that uses only those APIs, it'll run on our shim **without needing
Synchronet installed**.

Example minimal door (`hello.js`):

```javascript
load('sbbsdefs.js');
console.clear(ANSI_NORMAL);
console.putmsg('\x01n\x01c\x01hHello from a JS door!\r\n');
console.putmsg('Your handle: \x01y' + user.alias + '\r\n');
console.putmsg('\r\n\x01wPress any key...\x01n');
console.getkey();
```

Drop this in `~/doors/hello/hello.js`, create a Game row with:
- `game_type`: `door_synchronet` (label "Synchronet JS Game" in the
  admin dropdown)
- **Synchronet Script Path (.js)**: `~/doors/hello/hello.js`
- **Synchronet Exec Dir**: leave blank unless the script expects to
  run from a specific working directory
- `drop_file_type`: `door32.sys`
- `drop_file_path`: `%Pdoor32.sys`

There's no separate wrapper shell script to point at — `executable_path`
and `command_line_args` aren't used for this door type at all. The
launcher auto-detects a real `jsexec` if present, otherwise falls back
to the Node.js compat shim, using the two Synchronet-specific fields
above to find your script. Drop-file generation, terminal I/O,
ANSI/CP437 passthrough, and user info injection are all handled the
same generic way every other door type gets them.

**When the shim isn't enough:** drop a real Synchronet install in
`/usr/local/sbbs/` and set `SBBS_PATH` in `.env`. The launcher detects
`jsexec` and prefers it over the shim.

See [`15-synchronet-compat.md`](15-synchronet-compat.md) for the
complete shim API surface and known gaps.

### Writing a Mystic Pascal door

ANetBBS ships the full Mystic 1.12 A48 runtime at
`<install>/vendor/mystic/` — sysop doesn't need to install Mystic
separately. The runtime includes `mplc` (compiler), `mystic` (script
interpreter), `mide` (script editor), and the full data/menus/themes.

For a `.mps` source door:
- `game_type`: `door_mystic_mps`
- `executable_path`: `/usr/local/bin/mystic`
- `command_line_args`: `-d %f -e <path-to-script.mps>`
- The wrapper auto-runs `mplc script.mps` on launch if `.mpx` is older
  than `.mps`.

For a pre-compiled `.mpx`:
- `game_type`: `door_mystic`
- `command_line_args`: `-d %f -e <path-to-script.mpx>`

Mystic Python (`.py`) doors run via a fake `mystic_bbs` module that
maps Mystic's BBS API onto our compat helpers. Drop a `.py` script
that does `from mystic_bbs import *` and you're a few function calls
from a working door.

### rlogin out-dial doors

Connect to a remote BBS's game server without re-authenticating:
- `game_type`: `door_rlogin`
- `executable_path` field repurposed: `host:port`
- `command_line_args` repurposed: rlogin server-key (some servers want
  a shared secret)

**Important quirk:** Synchronet's rlogin uses INVERTED field order
vs RFC 1282 — sends `password\0username\0` (not `client\0server\0`).
Our connection class handles this automatically; if you're writing
against a non-Synchronet target, see `anetbbs/games/door_rlogin.py`.

### Built-in web games

Add a new web-only mini-game in `anetbbs/games/web_games.py`:

```python
WEB_GAMES.append({
    'name': 'My Cool Game',
    'slug': 'mycoolgame',
    'description': 'It is very cool.',
    'category': 'puzzle',          # puzzle | action | strategy | rpg | cards | casino | other
    'icon': 'bi-controller',        # Bootstrap icon class
    'web_game_module': 'mycoolgame',
    'sort_order': 110,
})
```

Then create `anetbbs/templates/games/web_games/mycoolgame.html` with
your game's HTML/JS. The game lobby auto-picks it up; the play route
is `/games/play/<slug>`.

---

## Web extensions

### Adding a new blueprint

A "blueprint" is Flask's term for a self-contained feature. To add one:

1. Create `anetbbs/web/myfeature.py`:

```python
from flask import Blueprint, render_template
from flask_login import login_required, current_user

myfeature_bp = Blueprint('myfeature', __name__, url_prefix='/myfeature')

@myfeature_bp.route('/')
@login_required
def index():
    return render_template('myfeature/index.html')
```

2. Register it in `anetbbs/web_app.py` next to the other `register_blueprint(...)` calls:

```python
from anetbbs.web.myfeature import myfeature_bp
app.register_blueprint(myfeature_bp)
```

3. Add `anetbbs/templates/myfeature/index.html` extending `base.html`:

```jinja
{% extends "base.html" %}
{% block title %}My Feature{% endblock %}
{% block content %}
  <h1>Hello, {{ current_user.username }}!</h1>
{% endblock %}
```

4. (Optional) Add a nav-bar link by editing the appropriate dropdown
   in `templates/base.html`.

That's the whole flow. ~50 lines of code for a working feature.

### Database / models

`anetbbs/models.py` is the single source of truth for SQLAlchemy
models. Add a new table by appending a class:

```python
class MyThing(db.Model):
    __tablename__ = 'my_things'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    note = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', backref='my_things')
```

Then generate a migration:

```bash
cd <install>
venv/bin/flask db migrate -m "add my_things"
venv/bin/flask db upgrade
```

Migrations are stored in `migrations/` and run automatically on every
service start.

### Adding a sysop admin page

Admin pages live behind `admin.dashboard` and require `current_user.is_admin`.
Two patterns:

1. **Drop a route into `anetbbs/web/admin.py`** for simple pages.
2. **Make a sub-blueprint** for larger features (see `echomail_admin.py`
   or `gallery_admin.py`).

Either way, add the page to the admin nav at
`templates/admin/_nav.html` (auto-included by every admin page) and
make sure the route has both `@login_required` and your `is_admin` gate.

### Real-time / WebSocket features

Flask-SocketIO is set up in `web_app.py`. Pattern:

```python
from anetbbs.web_app import socketio
from flask_socketio import emit

@socketio.on('connect', namespace='/mything')
def on_connect():
    if not current_user.is_authenticated:
        return False  # reject
    emit('hello', {'user': current_user.username})

@socketio.on('do_thing', namespace='/mything')
def on_do_thing(data):
    ...
    emit('thing_done', {'result': 'ok'}, to=request.sid)
```

Client side: `const sock = io('/mything')` then bind handlers.

The Web Terminal (`anetbbs/web/web_terminal.py`) and MRC client
(`anetbbs/web/mrc.py`) are good reference implementations — both
spawn background eventlet greenthreads, pump bytes between a backend
socket and the browser, and handle reconnect.

---

## Themes

Themes are rows in the `themes` table with CSS variable values. The
sysop edits them visually at `/admin/theme-builder/`.

To ship a new built-in theme:

1. Open the theme builder, tweak colors, save with a name.
2. Export the resulting `Theme` row's values as SQL (or note them).
3. Add it to the default-theme seed block in `_create_default_data()`
   in `anetbbs/web_app.py` (there's no separate `seed_data.py` — it's
   all in `web_app.py`) so fresh installs get your theme.

CSS variables driving the theme include `--theme-bg-dark`,
`--theme-primary`, `--theme-accent`, etc. — see `templates/base.html`
for the full list.

---

## MRC integration

The MRC bridge runs as a **separate aiohttp daemon** (`mrc/bridge/`)
on its own port (`WEB_PORT + 1` — 5001 on the standard `WEB_PORT=5000`,
derived and written to `mrc/bridge/config.json` by `install.sh`; see
`docs/PORTS.md`) and is reverse-proxied at `/mrcws` and `/mrcweb/`. The
web BBS talks to it via socket.io at `/mrc/`.

If you want to add a slash-command, capability, or output format:

- The terminal client lives in `anetbbs/features/mrc_chat.py`
- The web client is `anetbbs/static/mrc/client.js`
- The bridge daemon's command surface is `mrc/bridge/main.py`

For an IRC↔MRC mirror, see `anetbbs/features/mrc_irc_bridge.py` — this
one is CLI/systemd-only, no web admin UI exists for it yet. A sysop
creates `MrcIrcBridge` rows directly and runs one instance per bridge
via `python -m anetbbs.features.mrc_irc_bridge --bridge-id N`, wrapped
by the `anetbbs-mrc-irc-bridge@.service` systemd template.

---

## Echomail / FidoNet plugins

The pollers live in `anetbbs/echomail/`. Each network type has its
own module (`binkp.py`, `qwk.py`, `tic.py`). If you're adding a new
network protocol, the cleanest pattern is to write a new module
exposing `poll_outbound(network)` and `poll_inbound(network)`
functions, then add a row to the `echomail_networks` table with the
matching `protocol` discriminator.

Echomail message bodies go through `anetbbs/echomail/kludges.py` for
FTS-0001-compliant CR-terminated kludge handling. Don't reinvent —
the spec is hostile to bytes.

On the hub side, `anetbbs/web/hub_admin.py` (the `/admin/echomail/hub/`
blueprint), the `HatchQueue`/TIC hatch pipeline, and the
`ScheduledEvent` handler registry (`anetbbs/events/handlers.py`) are
the extension points if you're adding hub-side infrastructure —
distributing a new file type over TIC, adding another handler to the
scheduler, etc. — rather than a new peer-side protocol. See
[`20-federation.md`](20-federation.md) and
[`21-scheduled-events.md`](21-scheduled-events.md) for how those fit
together.

---

## Outbound notifications / webhooks

Three outbound channels are built in:
- **Webhooks** — generic POST to a URL, configured at `/admin/webhooks/`
- **Telegram** — set `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` in `.env`
- **Email** — sysop newsletter, SMTP via `EMAIL_*` env vars

To fire one from your feature code:

```python
from anetbbs.features.webhooks import fire
fire('post', {'board': 'general', 'title': 'Hello'})
```

`fire()` spins up a plain thread and makes one POST attempt with an
8-second timeout — it won't block your request, but there's no queue
and no retry on failure. See [`23-webhooks.md`](23-webhooks.md) for
the full list of the 8 real event types and the exact payload shape
each one sends.

---

## Testing

```bash
cd <install>
venv/bin/pip install -r requirements-dev.txt   # one-time, pulls in pytest
venv/bin/pytest tests/
# or:
venv/bin/python -m unittest discover -s tests -p "test_*.py"
```

`requirements-dev.txt` is needed either way: `tests/test_mrc_integration.py`
uses real `@pytest.fixture` decorators, so pytest has to be installed
for that one file to even be *importable* — `unittest discover` will
fail to load it (not just skip it) without pytest present, same as
running it under `pytest` directly.

The suite is a mix of `unittest.TestCase`-based files (most of them)
and a single pytest-fixture-based file — there's no shared
`conftest.py`; each test file that needs a Flask app/test client
builds its own (see any `test_*.py` file's `setUpClass`/fixture
functions for the pattern most commonly used — create a fresh
`create_app('testing')`, point `TestingConfig.SQLALCHEMY_DATABASE_URI`
at a scratch file or `tempfile.TemporaryDirectory()`, and restore any
shared state in `tearDownClass`). Add tests for new features under
`tests/`.

---

## Where to ask questions

- **Wiki: [[Development]]** — community-editable companion to this doc
- **Email** — `a-net-online@proton.me`
- **GitHub issues** — bug reports + feature requests on the
  `anetonline/anetbbs` repo

If you build something cool, send a PR and we'll bundle it.
