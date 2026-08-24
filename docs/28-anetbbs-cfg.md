# `anetbbs-cfg` — the terminal config tool

A full-screen curses admin tool for sysops who'd rather not leave a
terminal — think Synchronet's `SCFG` or Mystic's `mystic -cfg`, but
for ANetBBS. It edits the same database and `.env` file the running
BBS reads, so there's nothing separate to keep in sync with the web
admin — changes made here take effect the same way a web admin change
would (immediately for DB-backed settings; after a service restart for
`.env` settings, exactly like the web admin's own Settings page says).

It is **not** a superset of the web admin. Sixteen sections cover most
of the day-to-day surface (message areas, echomail, files, users,
games, menus, events, and more), but anything that needs a rich
canvas (the ANSI art/theme editor), a file upload, or a genuinely
dangerous multi-step flow (backup *restore*, in-place upgrades, the
full network-join applicant-approval email flow) is deliberately left
web-only. Every section that has a gap like this says so directly in
its own help text or docstring — this doc calls out each one too, so
you know before you go looking.

## Launching it

Two ways in, both landing in the exact same tool:

1. **Directly, from a real shell** (SSH into the box, or console
   access):
   ```bash
   anetbbs-cfg
   # or, if the console-script entry point isn't on PATH for some reason:
   python -m anetbbs.cfg
   ```
   No login prompt of its own — whoever can already run a command on
   the box has at least as much access as this tool could grant them.

2. **From inside a live BBS session**, via the in-BBS Sysop Tools menu
   (`Sysop Tools → [X] Config Tool`). This runs the *same* `anetbbs-cfg`
   program, bridged into your terminal session through the same PTY
   machinery every other door game uses — so it looks and feels
   identical to launching it from a shell.

   **This menu option is SSH-only.** It's gated twice over: the sysop
   menu only ever *adds* the `[X]` entry when your current session is
   SSH (telnet users never see the key at all, so there's no key that
   could dispatch to it), and the launcher function re-checks the same
   thing independently before doing anything. The reason is direct:
   this tool edits user security levels and echomail/hub credentials,
   and telnet carries all of that in plaintext. If you try it over
   telnet, or the menu entry isn't there, that's why — connect over
   SSH instead.

Startup is fast on purpose — a couple of seconds even on a Raspberry
Pi 3. It deliberately skips loading the full web server (eventlet,
Flask-SocketIO, ~76 blueprints, the background service starters) and
only initializes a plain database connection, since a config screen
doesn't need any of that.

**One real prerequisite worth knowing:** this tool assumes the
database schema is already current. It calls `db.create_all()` (a
no-op on tables that already exist, so it's harmless — this covers
the one edge case of a brand-new install where the main service has
never booted yet), but it does **not** run the main service's own
column-migration sweep. In practice this is never an issue, because
the real `anetbbs`/`anetbbs-web` service applies that sweep on every
boot, and by the time you reach for this tool it's already run at
least once — but after an in-place *code* upgrade (`update.sh` /
`anetbbs-upgrade`) that adds new database columns, make sure the main
service has actually restarted at least once before relying on
`anetbbs-cfg` to see brand-new fields.

## How to drive it

Everything in this tool is one of two screen types, navigated the same
way everywhere:

**List screens** (pick a board, a network, a user, a game, ...):

| Key | Action |
|---|---|
| `Up`/`Down` (or `j`/`k`) | Move the selection |
| `A` | Add a new row (where adding makes sense) |
| `Enter` or `E` | Edit the selected row |
| `D` | Delete the selected row |
| `+` / `-` | Move the row up/down (where manual ordering applies) |
| *(section-specific keys)* | Shown right in the footer — e.g. `/` to search users, `R` to reset a password, `C` to run a scheduled event now |
| `Esc` or `Q` | Back out to the previous screen |

**Edit forms** (editing one row's fields):

| Key | Action |
|---|---|
| `Up`/`Down` | Move between fields |
| `Enter` | Edit a text or number field inline |
| `Space` | Toggle a yes/no field |
| `Left`/`Right` | Cycle a multiple-choice field |
| `F2` | Save and return |
| `Esc` | Cancel (discards changes to this form only) |

A blank value on a "nullable" field (shown as `(none)`) means
"unset," not the empty string — that distinction matters for a few
fields (e.g. a file area's `Min Upload Level` blank vs. `0`).

## The 16 sections

### Boards & Message Areas
Local message boards: name, description, category, sort order, read/write
access levels, active flag. **Web admin only:** the ANSI banner shown
above the board (needs the rich art editor).

### Echomail Networks & Areas
Two-level: pick a network (BinkP or QWK transport), then drill into
its echo areas. Network-level editing (`[S]ettings` from the network
list) covers host/port/password, AreaFix password, poll interval, and
active flag. **Web admin only:** TLS, CRAM-MD5, the FTS-0001 packet
password, hub-identity assignment, and per-network outbound-bundle
compression (`compress_outbound`) — all grouped as "advanced/rare"
fields on this same network-settings form, deliberately kept out of
the terminal form to keep it from growing unwieldy. If you need those,
use the web admin's Echomail → Networks page.

Per-area fields here: tag, name, description, category, sort order,
minimum access level, sysop-only flag, active/subscribed flags,
require-real-name flag.

### Echomail Hub (AreaFix/Poll Log/QWK Requests)
Read-only log browsing for the AreaFix request log and the echomail
poll log, plus full approve/deny handling for incoming QWK node
requests (mirrors the web admin's own approval logic exactly — packet
ID validation, uniqueness check, random password generation).

**Known gap:** this section does not manage `BinkPNode` rows
(downstream peers who poll you as a hub) at all — creating, editing,
or deleting a downstream node, including its per-node outbound
compression preference and dial-out schedule, is web-admin-only today
(Admin → Hub Management). A peer's own sysop can still turn
compression on/off remotely for themselves via the AreaFix
`%COMPRESS GZIP` / `%COMPRESS OFF` command without any admin action on
either side — see [06 — Echomail](06-echomail.md).

### File Areas
Tag, name, description, storage path, access/upload levels, upload
permission (users/sysop/none), active/subscribed/sysop-only flags,
area password, and WaZOO FREQ settings (allow-FREQ toggle + optional
FREQ password — see [06 — Echomail](06-echomail.md) for what FREQ is).
**Web admin only:** which echomail network a file area's TIC routing
belongs to (reassigning it is rare and interacts with routing, so it's
read-only here).

### File Bulletins
Files dropped into `FILE_BULLETINS_DIR` are auto-registered here the
same way the web admin's own bulletins page does it — this section
just edits the metadata (title, sort order, active flag, minimum
access level) for files already found on disk; it doesn't manage file
content itself.

### Users & Security
Five sub-screens under one menu:
- **Users** — search by username, edit display name/access
  level/admin flag/active flag/lock flag, reset a password (generates
  and shows a random temporary one, once).
- **IP Bans** — add/edit/delete, with an optional expiry date.
- **Word Filters** — pattern/replacement pairs, active flag.
- **Login Auto-Ban Settings** — the single shared config row (enabled,
  attempt limit, time window, ban duration).
- **Registration Attempts** — read-only log.

**Web admin only:** the `IpWhitelist` table (rarely touched).

### Games
Three sub-screens: **Games** (the full `Game` field set — type, drop
file, executable/script paths, DOSBox/FOSSIL flags, InterBBS
score-sharing area, web/terminal enablement, and more — mirrors the
web admin's own game form field-for-field, including all eleven
`game_type` values), **Categories** (name, slug, sort order, submenu
flag), and **Active Sessions** (disconnect one, or mark every active
session stale after an unclean shutdown).

**Known gap:** the InterBBS score-sharing area is entered as a raw
numeric `EchoArea` id here rather than a picker — check the Echomail
section for the id you want first.

### Image Galleries
Not database-backed — this edits the same `gallery-config.json` file
the web admin's gallery pages read/write directly (label, slug,
directory path, description, active flag, sort order), so there's
exactly one implementation of that file format shared between both
UIs.

### BBS Menus
Two-level, like Echomail: pick a menu (name, title, prompt, default
flag, minimum access), drill into its items (hotkey, label, action
type, action args, minimum access, sort order, visible flag). All 18
action types are covered. **Web admin only:** the raw ANSI art banner
shown above the menu prompt (same reasoning as the Boards section's
gap — needs the art editor).

### PETSCII Menus
The equivalent tree for Commodore 64/128 sessions — a completely
separate, simpler menu system from the ANSI one above (PETSCII
sessions can't use most ANSI action types, so this has its own
smaller, purpose-built action-type list: goto, boards, echo, pm,
files, who, profile, games, logoff).

### Scheduled Events
Name, handler (picked from every currently-registered handler, so this
list can never drift from what the scheduler actually supports),
schedule and params as raw JSON (validated on save — see
[21 — Scheduled events](21-scheduled-events.md) for the JSON shapes),
enabled flag, and a `[R]un Now` action that fires the exact same
code path the web admin's "Run now" button uses.

### Graffiti Wall
Post moderation: browse/delete/restore individual posts, or clear
every active post at once. **Web admin only:** InterBBS Wall-sharing
settings (which BinkP network relays it, color scheme) — turning this
on provisions a real echomail area as part of the same flow, which
isn't something to partially reimplement here.

### Login Modules
Logon/logoff action chains (wall prompt, ANSI screen, file bulletin,
shell command, native door, Python door) — name, description,
event type, module type, params (raw JSON, with per-type example
shapes shown in the form's help text), minimum access, sort order,
active flag.

### Last Callers
Read-only login-history viewer (most recent 300). **Web admin only:**
InterBBS Last-Callers sharing settings, same reasoning as the Wall
section's gap.

### Backups
Browse and delete `update.sh`'s pre-update snapshots (created/from
version/to version/size/file list). **Deliberately excluded:**
*restoring* a backup — that's a genuine "can corrupt a live install"
operation gated behind a privileged, sudoers-controlled helper script,
and stays web-admin-only (Admin → Backups) where that confirmation
flow already lives. A backup created by an older `update.sh` version
may also be root-owned on disk; deleting one of those here will fail
with a clear message pointing you at the web admin's privileged-helper
fallback instead.

### System / Network Settings
The odd one out — not database-backed at all, this is a grouped editor
directly over your install's `.env` file, organized into: Server
Ports, Application Settings, Logging, BinkP, MSP/Finger, MRC Bridge,
Files/FTP, Games, Echomail, New User Verification, and Wiki Edit Gate.
Editing here writes straight to `.env` with the same round-trip
guarantee the web admin's Settings page has — comments and unrelated
lines elsewhere in the file are never disturbed.

**This tool does not restart any service.** Like Synchronet's `SCFG`
or Mystic's `-cfg`, most of these settings are read once at process
start — after saving a change here, restart the affected systemd
unit(s) yourself (the tool tells you this on every save).

## Known gaps, all in one place

For quick reference, everything currently web-admin-only across every
section:
- Board/menu ANSI art banners (needs the rich canvas editor)
- `BinkPNode` (downstream hub-peer) management — create/edit/delete, including per-node outbound compression
- EchomailNetwork's advanced fields: TLS, CRAM-MD5, packet password, hub-identity assignment, per-network outbound compression
- File area → echomail network (TIC routing) reassignment
- `IpWhitelist` management
- Wall and Last-Callers InterBBS-sharing settings (each provisions a real echomail area as part of turning it on)
- Backup *restore* (backup browsing/deletion works fine here)
- The ANSI art/theme editor, avatar/file uploads, in-place upgrades, and the full network-join applicant-approval email flow

None of these are oversights — each is a deliberate scope call made
when that section was built, usually because the excluded thing needs
either a rich canvas UI a terminal can't provide, or because it's
genuinely dangerous enough to want the web admin's confirmation flow
specifically. If one of these becomes a real day-to-day need for
console-only/no-root use, it's worth raising rather than working
around.
