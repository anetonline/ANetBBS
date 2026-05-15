# Everything ANetBBS Does

A complete inventory of what ships in **ANetBBS v1.0a2.3** (alpha 2).
Auto-derived from the blueprint registry, service units, and game/door
catalogs. Nothing here is roadmap or vapor — every item below is present
in the v1.0a2.3 tarball.

**By the numbers**

| | |
|---|---|
| Web blueprints | 51 |
| Public + admin web routes | ~320 |
| Front-end protocols | 8 (web, telnet, SSH, rlogin, finger, MSP, SYSTAT, BinkP) |
| Door-game types supported | 7 (DOS, native, Synchronet `.js`, Mystic Pascal `.mps`/`.mpx`, Mystic Python `.py`, rlogin out-dial, built-in web) |
| Pre-installed door games | 3 (LORD, BotWars, ANetSIMS) |
| Built-in web games | 10 |
| Pre-loaded FidoNet packs | 3 (fsxnet, spooknet, tqwinfo) |
| Echomail networks supported | 4+ wire formats (FTN binkp, DOVE-Net QWK, TIC file echoes, AreaFix/FileFix) |
| Real-time chat systems | 4 (MRC, IRC, multinode terminal, web shoutbox) |
| Outbound notifiers | 3 (email newsletter, Discord webhooks, Telegram bot) |
| Built-in themes | 7 (plus custom theme builder) |
| Sysop admin endpoints | 60+ |

---

## Front-ends — 8 protocols, one user database

| Protocol | Port | Detail |
|---|---|---|
| **Web** | 5000 (or 80/443 behind nginx) | Flask + SocketIO, gunicorn/eventlet, xterm.js terminal |
| **Telnet** | 2233 | asyncio, optional STARTTLS |
| **SSH** | 2234 | asyncssh, password + pubkey auth |
| **rlogin** | 513 | classic Unix rlogin; both inbound (callers) and outbound (door rlogin to remote BBS game servers) |
| **Finger** | 79 | RFC 1288, per-user `.plan` profile |
| **MSP — Inter-BBS Instant Messaging** | TCP 18 | RFC 1312 wire format; talks to Synchronet IMSG |
| **SYSTAT — who's-online query** | UDP 11 | RFC 866; queryable by peer BBSes |
| **BinkP — FidoNet inbound** | TCP 24554 | FTS-1027 |

All front-ends share **one** SQLite user database and one `UserSession`
presence table — "who's online" sees everyone regardless of how they
connected.

---

## Messaging

### Local
- Local message boards with categories, threading, sticky/lock, quote-reply, ANSI banners
- Full-text search across boards
- Post voting (up/down)
- Per-board moderators with edit/delete/lock
- Per-user last-read tracking
- Drafts
- Soft-delete with restore
- Sysop word filter
- File attachments on posts
- Anonymous post option per board

### Private
- User-to-user private messages
- Inbox / Sent / Drafts folders
- Reply / Reply-all / Forward
- Mass-PM (sysop)
- Block list per user
- "You have new" banner at login

### FidoNet
- **netmail** (binkp) with full kludge support (MSGID, REPLY, INTL, CHRS, FMPT/TOPT, PID, TZUTC)
- **echomail** bidirectional with AreaFix robot (`+TAG` / `-TAG` / `%RESCAN` / `%LIST` / `%QUERY`)
- Per-user FidoNet AKA registry
- Bad-area sysop review queue
- AreaFix password per network
- FTS-0001 wire format with bare-CR line terminators (spec-correct)
- Synchronet-compatible AreaFix subject-line password
- Inbound ZIP-bundle extraction (FTS-5003 Mystic-style)

### DOVE-Net QWK
- Outbound REP packet upload
- Inbound QWK fetch + import
- CONTROL.DAT-driven area auto-create
- Conf# mapping, manual quick-add by conf number
- Idle-hub detection ("550 No QWK packet" = silent, not error)

### TIC (file echoes)
- Ingest hatched files from FTN peers
- Hatch outbound to downstream subscribers
- Per-area FileFix subscribe/unsubscribe with `+TAG`/`-TAG`
- Bulk subscribe-to-all per network
- TIC manifest validation, hash check

### Inter-BBS IM
- MSP-2 (RFC 1312) wire-format compliant
- Receives IMs from Synchronet `sbbsimsg`
- Sends outbound IM to peer BBSes
- Per-user routing (`StingRay@bbs.a-net.fyi`)
- Auto-mirrors Synchronet `sbbsimsg.lst` directory daily

---

## Real-time chat

### MRC (Modern Relay Chat)
- Single bridge connection to `mrc.bottomlessabyss.net` shared across all users
- Multi-network — connect to multiple MRC servers concurrently (configurable)
- **Web client** at `/mrc/` — channel sidebar, user list, scrolling chat
- **Terminal client** — stationary status bar at row 1, scrolling chat, pinned input
- Tab-completion on usernames seen in chat
- Slash-commands: `/identify` `/msg` `/me` `/join` `/list` `/who` `/chatters` `/motd` `/banners` `/topic` `/lastseen` `/afk` `/back` `/status` `/roomconfig` `/termsize` `/trust` `/broadcast` `/raw` `/ctcp`
- Capabilities: MCI, MSGEXT, CTCP
- Per-user identify with passwords
- User profiles + arrival/departure events
- Web users + terminal users share rooms and identity

### IRC
- Web IRC client at `/irc/` (xterm.js)
- MRC↔IRC bridge — sysop can mirror MRC channels to IRC, both ways
- Per-bridge config at `/admin/mrc-irc-bridges/`

### Multinode terminal chat
- Between currently-connected terminal/SSH sessions
- Page-to-chat invite
- `/who` listing

### Shoutbox
- Inline web shoutbox on the front page
- Rate-limited
- Sysop moderate

---

## Files

### File areas
- FidoNet-style file areas with per-area upload permission (`users` / `sysop` / `none`)
- Optional area password
- Sysop moderation queue (uploads quarantined until approved)
- Per-user upload/download ratios (Synchronet/Mystic style; sysop-configurable)
- Per-user shareable expiring links (with hours-valid + max-downloads cap)
- Per-file `[Re-scan descriptions]` sysop button
- Live directory scan — newly hatched/dropped files appear without DB-row management

### File gallery (web)
- Per-user uploads at `/files/`
- Auto-extract description from `FILE_ID.DIZ` / `README.*` / `DESCRIPT.ION` inside the archive (zip, tar.gz/bz2/xz, 7z, rar, lha/lzh)
- CP437 → UTF-8 transcoding of DIZ files
- Optional ClamAV virus scan on upload
- Image-file thumbnail generation (cached PNG)
- Per-area browse + download
- File size caps (configurable)

### FidoNet TIC
- Auto-file inbound TICs into their declared area
- Outbound hatching with TIC manifest generation
- Multi-network: per-network file area + file-echo lists

### Image galleries
- Web gallery at `/gallery/` — paginated thumbnail grid, full-screen modal viewer, lazy-loaded
- Admin at `/admin/galleries/` — add/remove/edit collections, drag-and-drop multi-file upload
- JSON config (`gallery-config.json`) preserved across `update.sh` runs
- Terminal viewer `anet-gallery.sh` using chafa (universal) or img2sixel (sixel-capable terminals)

---

## Door games

### Door types supported
1. **DOS doors** via DOSBox-staging with TCP/serial nullmodem bridge
2. **Native Linux doors** (any executable)
3. **Synchronet `.js` doors** — real `jsexec` if installed, otherwise a built-in Node.js compat shim
4. **Mystic Pascal `.mps`/`.mpx` doors** — auto-compiles `.mps` → `.mpx` via bundled `mplc`
5. **Mystic Python `.py` doors** via a fake `mystic_bbs` module
6. **rlogin out-dial** — connect outbound to remote BBS game servers (Synchronet xtrn, DoorParty, A-Net Online)
7. **Built-in web games** — Flask-routed mini-games

### Pre-installed doors (in `vendor/games/`)
- **LORD** — Legend of the Red Dragon (Synchronet JS port, plays end-to-end out of the box)
- **BotWars** — multiplayer robot combat
- **ANetSIMS** — life-sim by Apam (A-Net Sixel TV included)

### Bundled Mystic runtime
- Full Mystic BBS 1.12 A48 (mystic + mplc + mide + mis + mutil binaries)
- 4.9 MB, no separate download required

### Drop files supported
- DOOR.SYS
- DORINFO1.DEF
- DOOR32.SYS

### Per-node node manager
- Auto-allocates from `GAMES_MAX_NODES` pool
- Per-game `max_nodes` cap
- Per-node scratch dirs at `<install>/data/temp/nodeN/`
- Release on session end
- Drop-file regeneration per call

### Token substitution in door config
- Synchronet `%CODE%` and Mystic `%c` codes interpreted in `executable_path`, `working_directory`, `command_line_args`, `drop_file_path`
- Supported: `%f` (drop-file path), `%P` (per-node dir), `%U`/`%u` (username), `%n`/`%N` (node), `%m`/`%M` (minutes left), and ~25 more

### Synchronet JS compat shim
- ~270 functions/objects covering File / console / bbs / system / user / load / require / dd_lightbar_menu / mouse_getkey
- Pure Node — runs stock SBBS doors that don't need a full Synchronet install

### Door I/O safety
- Ctrl+]q user-abort
- 60-second idle-timeout watchdog (configurable via `DOOR_IDLE_TIMEOUT`)
- Bridge-close detection
- waitpid watcher
- Force-kill of stuck process groups
- Stuck doors always return user to BBS within the timeout
- Per-door working-dir preservation (NODE*.DAT etc. never auto-cleaned)

### Built-in web games (10)
- **Hangman** (puzzle)
- **Trivia Challenge** (puzzle)
- **Number Guesser** (puzzle)
- **Snake** (action)
- **Tic Tac Toe** (strategy)
- **Memory Match** (puzzle)
- **Typing Speed Test** (other)
- **Minesweeper** (puzzle)
- **2048** (puzzle)
- **Text Adventure** (RPG)

### Web door terminal
- Pinned to 80×25 with real CGA 16-color palette
- No auto-fit-to-viewport — `gotoxy` lands where doors expect
- Pre-join output buffer so doors that draw their welcome screen before the first keystroke (ANetSIMS, A-Net Sixel TV) render immediately
- Sixel + iTerm2 image addon for graphical doors
- Per-session keep-alive

---

## Wiki

- Collaborative wiki at `/wiki/`
- Markdown with `[[wiki-links]]`
- Full revision history with diff and revert
- Full-text search across pages
- Wanted-pages and orphan-pages reports
- 41 seeded pages cover the whole BBS
- Per-page edit history with author / timestamp
- Page protection (sysop-only edit) optional

---

## RSS / Atom reader

- Web reader at `/rss/` — feed list with unread counts, combined river view, paginated per-feed item lists, full-content article view
- Per-user mark-as-read tracking
- Terminal reader (`R` from main BBS menu) — same browse model, word-wrapped to 78 cols, unread `*` markers
- Admin at `/admin/rss/` — add/edit/delete feeds, force-refresh, per-feed status, last error
- Background poller — every active feed every 30 min (configurable via `RSS_POLL_INTERVAL`)
- Dedupes by GUID
- X-News feed seeded by default
- Outbound RSS feeds for local boards, bulletins, oneliners

---

## Community / social

- **Personal web pages** — `/_pages/<user>` static HTML per user
- **Site pages** — sysop-edited static pages at `/page/<slug>`
- **Gemini Capsules** — list local users' `gemini://` pages
- **User profiles** with bio, avatar, location, `.plan`
- **Calendar** — per-user events, BBS-wide schedule
- **Groups** — user-formed sub-communities
- **Polls** — sysop and user polls, embedded in pages
- **Saved messages** — bookmark / unread later
- **Oneliners** wall
- **`/finger/<user>`** web profiles
- **Contacts / Buddies** — friend list with arrival/departure notifications
- **Blocks** — per-user mute list
- **Notifications** center with toast popups + bell badge
- **Leaderboard** — ranked by points / posts / files / games activity
- **Permalinks** — short `/m/<id>` for sharing posts off-BBS
- **Bulletins** — sysop announcements with pinned + expiring
- **BBS History** — site-page wiki of the BBS's lore

---

## Outbound notifications

- **Email newsletter** to active users
- **Discord webhooks** for events (new post, new caller, etc.)
- **Telegram bot** notifier
- **MSP outbound IM** to peer BBSes
- **FidoNet netmail** outbound
- **MRC** outbound to bridge

---

## Sysop tools

### Admin dashboard
- At-a-glance counts (users, posts, files, recent activity)
- Recent activity log
- Alerts (failed services, stuck doors, sysop pages, file queue, registration attempts)

### Setup
- First-time **setup wizard** (interactive)
- **Setup checklist** / **Launch checklist** with progress tracking

### Users
- **Users CRUD** — add / edit / delete / bulk import / lock / toggle-ban
- **Pending users** approval queue
- **Inactive users** prune
- **New-user questions** custom signup questionnaire
- **Registration attempts** log
- **Time budgets** — per-user daily/weekly time caps
- **User notes** (sysop-only)
- **Manage user** dialog (sessions, posts, files at-a-glance)

### Content
- **Boards** CRUD + reorder
- **Per-board moderators**
- **Bulletins** CRUD + pin/unpin + expiry
- **MOTD pool** with rotation
- **Word filter**
- **Site pages** editor
- **Menu admin** — every BBS terminal menu is data-driven and editable
- **Themes** CRUD + theme builder
- **Default echo subs** template for new networks
- **ANSI editor** for banners / welcome / goodbye screens

### Networks
- **Echomail dashboard** — poll logs, AreaFix log, bad-areas review
- **Per-network manage** — binkp, AreaFix password, polling interval
- **File-echo subs**
- **Default echo subs**
- **TIC log** + per-TIC detail
- **Subscribe / unsubscribe to all** per network
- **Connection test** to peers (binkp handshake)
- **Dialout** (modem dial)
- **MRC↔IRC bridge** admin

### Files
- **File areas admin**
- **Bulk import** (FILEBONE / BACKBONE format)
- **File queue** (moderation review)
- **Virus scan** admin

### Games
- **Door Games admin** — add / edit / sort / disable
- **Per-game max-nodes** + drop-file type + door type

### Communication
- **Broadcast** to logged-in users
- **Newsletter** email blast to active users
- **Sysop pages** alert with answer/reply flow
- **Chat bans** for MRC/shoutbox
- **Webhooks** outbound notifications config

### Operations
- **NodeSpy** — see what every active terminal user is doing in real time
- **NodeSpy kick** — disconnect stuck terminal sessions cross-process
- **Caller log** — detailed connection records
- **Activity log**
- **Journal viewer** — system journal (systemd) inside the web UI
- **Control panel** — stop/start/restart any service from the web
- **Sysop console** — terminal-style admin shell
- **DB backup**
- **IP bans** (CIDR support)

### Galleries
- **Galleries** CRUD
- **Per-gallery file management** (drag-and-drop upload, delete, reorder)

---

## Front-end UX details

- Single-key hotkey menus (no Enter required) on terminal
- Synchronet `@CODE@` and Mystic `|XX` display codes in welcome / goodbye / menu ANSI screens
- Notification banner at login (`*** You have new: 3 PMs, 1 InterBBS IM`)
- 7 built-in themes — light, dark, amber, green, blue, retro, hacker
- Custom theme builder
- Per-user theme picker
- Mobile-friendly responsive web layout
- CP437/ANSI rendering across all message lists, viewers, sysop tools
- Image rendering in terminal (sixel + iTerm2 protocols)
- ANSI art preservation in echomail bodies (CP437 passthrough)
- 80×25 default terminal sizing with sysop-overridable per-user `/termsize`

---

## Pre-bundled data

- **fsxnet.zip** — fsxNet nodelist + areas info
- **spooknet.zip** — SpookNet config
- **tqwinfo.zip** — TQWnet info pack
- **41 wiki pages** seeded
- **X-News RSS feed** subscribed by default
- **Default themes** (7 pre-built)
- **Default menus** for terminal interface

---

## Deployment / operations

- **`install.sh`** — single-shot wizard installs the full stack in ~10 minutes:
  - Detects OS (Linux Mint / Ubuntu / Debian / Fedora / Arch / OpenSUSE)
  - Installs system packages (python, nginx, certbot, nodejs, dosbox-staging, clamav, lhasa, unrar)
  - Optional Mystic BBS runtime (bundled, no download)
  - Creates service user, venv, secrets, SSH host key, sysop admin account
  - Generates systemd units for every enabled service
  - Configures UFW firewall rules
  - Configures nginx reverse proxy with HTTP-only or HTTPS template
  - Runs certbot for Let's Encrypt SSL (with pre-flight port-forwarding check)
  - Production vs Test mode switch (test mode skips nginx/SSL for behind-NAT setups)
- **`update.sh`** — safe upgrade preserving `.env`, `data/`, `doors/`, MRC config, gallery config, MRC bridge state
- **`install.sh --uninstall`** — clean removal
- **systemd units** for every service with proper `CAP_NET_BIND_SERVICE` for privileged ports
- **UFW rules** auto-added per enabled service
- **`.env`** generation with secure SECRET_KEY
- **Logs** at `/<install>/logs/` plus systemd journal integration
- **DB migrations** via alembic on every start

---

## Code coverage

- 51 web blueprints
- ~320 HTTP routes
- 189 templates
- Door I/O: DOS / Linux / Synchronet JS / Mystic Pascal / Mystic Python / rlogin / built-in web
- 5 background pollers (echomail, RSS, MRC keepalive, IRC keepalive, sysop-page timeout)
- One SQLite database, one user identity, one session table across all front-ends

---

*For installation see [`README.md`](README.md). For per-feature deep
dives see [`docs/`](docs/). For what's-new vs prior alphas see
[`RELEASE.md`](RELEASE.md).*
