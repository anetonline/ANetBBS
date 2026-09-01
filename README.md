# ANetBBS

**Status: stable** (`v1.0.65`, September 2026)

A modern multi-node BBS for the classic FidoNet world. Web, telnet, SSH,
rlogin, **FTP, and PETSCII (C64/128)** front-ends; FidoNet binkp + DOVE-Net
QWK echomail; inter-BBS instant messaging via MSP (RFC 1312); a built-in
collaborative wiki and RSS reader; door-game support including stock
Synchronet `.js` doors (LORD ships pre-installed and plays out of the box).

## Quick install (Linux)

**No root/sudo access, or would rather not?** See
[`docs/01b-no-root-install.md`](docs/01b-no-root-install.md) — a
complete, verified path to a fully working BBS (web UI, telnet, SSH)
using nothing but your own account, no `sudo` anywhere. The rest of
this section covers the full-featured `install.sh` path, which does
need root.

You'll want a domain pointed at this box if you want the web admin and
public web pages working with HTTPS — modern browsers refuse plain-HTTP
logins, so the wizard's **production** mode pulls a Let's Encrypt cert.
Telnet, SSH, rlogin, BinkP, MRC, and MSP work fine without a domain;
pick **test** mode at the prompt if you're behind NAT or just kicking
the tires (web admin runs on `http://localhost:5000`).

```
tar xzf ANetBBS-v1.0.65.tar.gz
cd ANetBBS-v1.0.65
sudo bash install.sh
```

The wizard asks for BBS name, sysop, ports, services, and SSL/nginx
preferences, then sets up the venv, systemd units, ufw rules, and (if
enabled) a Let's Encrypt cert. A random admin password is generated and
written to `data/admin_password.txt` — log in to the web admin and
change it on first use.

For details on individual services and post-install configuration see
[`docs/INSTALL.md`](docs/INSTALL.md).

### Updating and uninstalling

To upgrade an existing install in place, download the new release tarball
and run `update.sh` from inside it (backs up `.env`/database/systemd units
first, then syncs files and restarts services):

```
tar xzf ANetBBS-v1.0.65.tar.gz
cd ANetBBS-v1.0.65
sudo bash update.sh
```

`install.sh` itself also takes a few flags:

```
sudo bash install.sh --uninstall  # Stops/disables services, then (after one
                                   # y/N confirmation) deletes the ENTIRE
                                   # install directory -- including data/ (DB,
                                   # uploads, echomail) -- with no separate
                                   # prompt or backup for that. Back up
                                   # data/ first if you want to keep it.
sudo bash install.sh --defaults   # Non-interactive install, all defaults
sudo bash install.sh --force      # Re-run install, overwriting an existing
                                   # .env and database (normally preserved)
```

## Quick install (Docker)

Prefer containers? Two options — a single-container quick start, or a
proper multi-container `docker-compose` deployment (recommended for
anything beyond kicking the tires). No pre-built image is published
anywhere yet, so build from source first:

```
docker build -f docker/Dockerfile -t anetbbs:local .
cp .env.docker.example .env      # fill in SECRET_KEY, BBS_NAME, etc.
# then set ANETBBS_IMAGE=anetbbs / ANETBBS_IMAGE_TAG=local in .env
cp docker/compose/mrc-bridge-config.json.example mrc-bridge-config.json
docker compose -f docker/compose/docker-compose.yml up -d
```

New to Docker? [`docs/22-containers.md`](docs/22-containers.md) has a
full hand-held walkthrough (including the single-container option,
what each command actually does, and troubleshooting). Multi-arch
images (amd64 + arm64, works on a Raspberry Pi) are built via `docker
buildx` once there's a registry to publish to.

## Features

### Messaging
- **Local boards** with categories, sticky/lock, threading, quote-reply,
  search, voting, ANSI banners, last-read tracking, moderators
- **FidoNet netmail** (binkp) — full kludge support (MSGID, REPLY, INTL,
  CHRS, FMPT/TOPT, PID, TZUTC), per-user AKAs, drafts, soft-delete
- **FidoNet echomail** with AreaFix robot (in/out), bad-area sysop review
- **Hub mode** — act as a BinkP echomail hub for downstream nodes; per-node
  echo area subscriptions, outbound hold queue, SEEN-BY aware fan-out tosser.
  Also a QWK hub: HTTP download/upload endpoints, per-node conference
  subscriptions with high-water mark tracking. Admin → Echomail → Hub.
- **ANotherNetwork** (Zone 1200) — ANetBBS's own echomail network, seeded on
  every fresh install. 26 message areas across 9 categories (General,
  Technology, BBS Scene, Retro, Hobby, Trading, Data, SysOp, Test) plus 9
  TIC file-echo areas (nodelists, infopacks, BBS software, doors, ebooks,
  Linux, retro, ANSI art). Join via BinkP (`1200:1/1`) or QWK/FTP (hub ID:
  `ANET`). Hub: `bbs.a-net.fyi`. Apply for a node number.
  Public nodelist at `/admin/echomail/hub/nodelist`.
- **DOVE-Net QWK** — fetch + REP packet upload, CONTROL.DAT-driven area
  auto-create, conf# mapping, manual quick-add for known confs
- **Private messages** between local users
- **Inter-BBS Instant Messaging** (MSP / RFC 1312) — TCP/18, with the
  Synchronet `sbbsimsg.lst` directory mirrored daily and a SYSTAT
  (UDP/11) Active-User responder
- **CP437 + ANSI rendering** in every message body (proper box-draw,
  16-color SGR → HTML spans)

### Doors
- **Game Center** — 21 built-in browser games (puzzle, action, cards/
  casino, strategy, RPG) playable with no telnet client or per-game
  setup: Hangman, Trivia, Number Guesser, Snake, Tic Tac Toe, Memory
  Match, Typing Speed Test, Minesweeper, 2048, a text adventure,
  Klondike Solitaire, Video Poker, Texas Hold'em, Blackjack, Slot
  Machines, Galaga, Tetris, Breakout, ANetDarkForces (a 10-sector
  raycaster FPS), an Ebook Reader (Project Gutenberg), and
  **Meadowlark Valley** — an original town/farm-builder sim with
  auto-harvesting farmer NPCs and real-time co-op building.
  13 of them (Hangman through Breakout) can be played with **no
  account at all** — a per-game admin toggle, off for anything using
  the wallet economy or a persistent save.
  See [docs/24-game-center.md](docs/24-game-center.md).
- DOSBox-staging / dosbox-x / vanilla DOSBox auto-detect, TCP nullmodem
  bridge to the user's PTY
- Mystic Pascal Script (`.mps`) and Mystic Python (`.mpy`) doors
- Synchronet `.js` doors — runs via real `jsexec` when found at
  `$SBBSEXEC/jsexec` or `/sbbs/exec/jsexec`; otherwise falls back to a
  Node.js + compatibility shim that the alpha 2 work expanded enough to
  fully run **LORD** (Legend of the Red Dragon — Synchronet's JS port,
  pre-installed, no Synchronet required)
- Native binary doors with DOOR.SYS / DOOR32.SYS / DORINFO / CHAIN.TXT
  / SFDOORS.DAT drop files
- **rlogin door bridge** (`door_rlogin`) — connects out to a remote
  BBS's own door server (Synchronet xtrn, DoorParty, etc.) instead of
  running a local subprocess. **A-Net Online's game server ships
  pre-installed and active by default**, same as LORD.
- **Web terminal pinned to 80×25 with the full CGA palette** so doors
  draw the same way as on a 1990s VGA terminal — Violet's portrait in
  LORD looks like Violet's portrait in LORD

### Wiki
- Full collaborative wiki at `/wiki/` with markdown bodies, `[[Page]]`
  cross-links, per-page revision history, unified-diff compare,
  revert, full-text search, recent changes, wanted/orphan pages
- 52 seeded pages cover connecting, every messaging subsystem, doors
  (including a LORD recipe + the DosBridge), echomail/BinkP setup,
  NodeSpy, backup, full architecture, sysop guide, glossary, FAQ
- Anyone can read; logged-in users can edit (configurable minimum post count + account age); admins can lock/delete/rename

### RSS Reader
- Built-in feed aggregator for BBS-scene news + anything else
- Web at `/rss/` (river or per-feed), terminal main-menu **R**
- Background poller on a single shared interval, sanitized HTML via bleach
- Per-user unread state; X-News (`x-bit.org`) seeded by default

### Files
- Per-area file bases with `users / sysop / none` upload permissions
- TIC ingest from FTN peers + outbound TIC hatching to downstream subscribers
- Auto-extract description from `FILE_ID.DIZ`, `README.md`/`README.txt`,
  `DESCRIPT.ION` inside zip / tar.{gz,bz2,xz} / 7z / rar / lha archives
- Optional ClamAV virus scan on upload

### Image galleries
- **Web gallery** at `/gallery/` — paginated thumbnail grid, full-screen
  modal viewer, lazy-loaded thumbnails. Browser-native rendering for
  full image quality (no terminal-art middleman).
- **Admin** at `/admin/galleries/` — add / remove / edit collections
  (label, slug, filesystem path, sort order, active flag) and per-gallery
  file management with drag-and-drop multi-file upload, individual file
  delete, pagination.
- **Storage**: galleries are listed in `gallery-config.json` at the BBS
  install root (auto-seeded on first run). Files live wherever you point
  the path; the same path can be served by any other tool too.
- **Terminal viewer** at `$INSTALL_DIR/anet-gallery.sh` (default
  `/opt/anetbbs/anet-gallery.sh`) — bash script
  using `chafa` (Unicode blocks, works in any terminal) or `img2sixel`
  (sixel-capable terminals like SyncTERM, foot, mlterm). Standalone —
  not wired into the games menu.

### MRC
- **WebSocket bridge** to the global MRC network (mrc.bottomlessabyss.net) —
  one BBS-level hub connection shared across all users
- **Web MRC chat** at `/mrc/` (browser tab)
- **Terminal MRC chat** for telnet/SSH/rlogin — talks through the same
  local bridge so terminal + web users share rooms and a single trust
  identity. anetmrc-style **stationary status bar at row 1** (room,
  topic, mention badge, latency), chat scrolls below it, input line
  pinned to the bottom row. Full slash-command roster (`/identify /msg
  /me /join /list /who /chatters /motd /banners /topic /lastseen
  /afk /back /status /roomconfig /termsize /trust /broadcast /raw …`).
  **Tab-completes usernames** seen in chat or via `/who`. ←/→ arrow keys
  for outgoing text color, masked password input on bare `/identify`,
  @mention highlighting with bell, 140-char outgoing cap with
  word-boundary auto-split, HH:MM timestamps on every line, ANSI-aware
  word-wrap with continuation indent

### Front-ends
- Web (Flask + SocketIO) on port 5000
- Telnet (port 2233), SSH (2234), rlogin (513) — share the same user database
- **FTP (port 21)** — serves every active `FileArea` as a top-level
  directory. Anonymous read access to public areas; authenticated users
  upload subject to per-area `upload_permission`. Optional FTPS via the
  same Let's Encrypt cert nginx uses. Earns the FTN nodelist `IFC` flag.
- **Terminal UX** — single-key hotkey menus (no Enter required), screen
  cleared between menus, Q logoff confirms Y/N, color rendering across
  Boards / Echomail / Bulletins / Who's Online / Sysop Tools / Profile
  / PM compose / Inter-BBS IM compose / inboxes. Paged area picker for
  long echomail networks. Notification banner at login (`*** You have
  new: 3 PMs, 1 InterBBS IM`). Synchronet `@CODE@` and Mystic `|XX`
  display codes in welcome / goodbye / menu ANSI screens.
- **PETSCII (C64/128)** — dedicated plain-text rendering path for real
  Commodore hardware and PETSCII terminal emulators, two opt-in ports
  (40-col / 80-col, off by default). Boards, echomail (network-first
  picker), PMs, file browsing + XMODEM download, who's-online, profile,
  Number Guessing, and sysop-buildable custom menus at
  `/admin/petscii-menus/`. See [docs/25-petscii.md](docs/25-petscii.md).
- **Live "X just logged in/out" presence alerts** — classic multi-node
  BBS behavior: every other currently-online user sees it in real time,
  wherever they are (a menu, a board, chat) — terminal and web alike,
  regardless of which side someone connects from.
- **Watch It Live** (`/watch`, off by default) — a public, no-login
  page showing real-time who's-online activity, styled as a retro
  terminal display, meant to be shared or embedded off-site. See
  [docs/29-watch-live.md](docs/29-watch-live.md).
- **Postcards** (`/postcards`) — any logged-in user composes retro
  CP437 art with the same grid editor as the admin ANSI Editor, then
  gets a public share link and a downloadable PNG for sharing off
  platform. See [docs/30-postcards.md](docs/30-postcards.md).

### Sysop tools
- **ANetBBS Pulse** — read-only, mobile-first status dashboard at
  `/admin/pulse/`, installable to a phone's home screen (Android +
  iOS). Live callers, service health, disk/uptime, 24-hour activity —
  no service-control actions, no shell, nothing writable.
- **Auto-social-posting queue** (`/admin/social/`, off by default) —
  a new #1 leaderboard score or a round-number BBS milestone queues a
  draft Bluesky/Mastodon post (rendered image + editable caption) for
  a sysop to review, plus a manual compose option for anything else
  (a version bump, a new feature); nothing posts automatically, and
  queuing a draft notifies every admin. See
  [docs/31-social-posting.md](docs/31-social-posting.md).
- Echomail dashboard with poll logs, AreaFix log, bad-areas review
- File-area admin
- Manual `+TAG`/`-TAG` AreaFix-to-hub form
- BBS directory browser with live `who's online` queries
- `anetbbs-cfg` — standalone full-screen terminal admin tool (SCFG /
  `mystic -cfg` style), run directly on the server console, near-full
  parity with the web admin: boards & message areas, echomail networks/
  areas/hub (AreaFix log, poll log, QWK node requests), file areas &
  bulletins, users & security (access levels, password reset, IP bans,
  word filters, login auto-ban, registration log), games (door games,
  categories, active sessions), image galleries, BBS/PETSCII menu
  editors, scheduled events, graffiti wall moderation, login modules,
  last-callers log, backup browsing, and `.env` system settings — no
  browser required. `python -m anetbbs.cfg` from a checkout, or
  `anetbbs-cfg` once installed.
- `anetbbs-monitor` — live, auto-refreshing node monitor (uMonitor /
  nodespy style): who's connected, on what protocol, from where, doing
  what, with a kick action, refreshing every second in a terminal — no
  browser required. `python -m anetbbs.monitor.app` from a checkout, or
  `anetbbs-monitor` once installed.

## Documentation

- [`docs/00-overview.md`](docs/00-overview.md) — architecture + table of contents
- [`docs/INSTALL.md`](docs/INSTALL.md) — full install
- [`docs/INSTALL-PI.md`](docs/INSTALL-PI.md) — Raspberry Pi install guide (hardware, DDNS, SSD, troubleshooting)
- [`docs/PORTS.md`](docs/PORTS.md) — every port the BBS uses
- [`docs/SECURITY.md`](docs/SECURITY.md) — security defaults + production hardening
- [`docs/CHANGELOG.md`](docs/CHANGELOG.md) — version history
- [`docs/MSP_LOOPBACK_TEST.md`](docs/MSP_LOOPBACK_TEST.md) — verify MSP works
- [`docs/06-echomail.md`](docs/06-echomail.md) — echomail config
- [`docs/07-file-areas.md`](docs/07-file-areas.md) — file area config
- [`docs/13-image-galleries.md`](docs/13-image-galleries.md) — image galleries
- [`docs/25-petscii.md`](docs/25-petscii.md) — PETSCII (C64/128) terminal support
- [`docs/26-synchronet-json-rpc-doors.md`](docs/26-synchronet-json-rpc-doors.md) — 17 confirmed-working Synchronet door games (download/setup)
- [`docs/28-anetbbs-cfg.md`](docs/28-anetbbs-cfg.md) — `anetbbs-cfg`, the SSH/console terminal config tool
- [`docs/32-node-monitor.md`](docs/32-node-monitor.md) — `anetbbs-monitor`, the SSH/console live node monitor

## License

See [LICENSE](LICENSE). ANetBBS bundles some third-party components
(fonts, a Synchronet JS compatibility layer used to run Synchronet
door games, and the LORD door game) under their own separate terms —
see [NOTICE](NOTICE) for the full breakdown.

## Status / Caveats

Known rough edges:

- MSP/SYSTAT need privileged ports (11/18) — see
  [`docs/INSTALL.md`](docs/INSTALL.md) for `setcap` / systemd /
  iptables-redirect options.
- Synchronet stock `.js` doors: LORD is verified end-to-end under the
  Node shim. Other Synchronet doors mostly work but each one tends to
  exercise a different corner of the API — file an issue if a door
  dies with a `ReferenceError` and the missing stub will get added.
- Single-process rate limits — fine for one gunicorn worker; needs an
  external store for multi-worker setups.

Issues and patches welcome. Chat/discussion: IRC at `irc.a-net.online`
(port `6667` plain, `6697` SSL), room `#ANetBBS`.
