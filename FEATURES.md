# ANetBBS Alpha Feature List

A comprehensive inventory of what's in `v1.0b` (alpha 2, May 2026 —
internal build v287.1). Auto-derived from the blueprint registry +
service units; check the linked docs for configuration details.

> **What's new since v1.0a**
> - **LORD** (Legend of the Red Dragon) ships pre-installed and plays
>   end-to-end under the Node + Synchronet compat shim — no external
>   jsexec needed. The shim got the deep additions to support it:
>   `Queue` (name-cached), `strftime`, `file_mutex`, scope-form
>   `require()`, `js.load_path_list`, full `File.*` API (`readBin /
>   writeBin / readStr / writeStr / lock / unlock / flush / truncate /
>   position`), full fopen-mode parsing, `server` / `client` globals
>   so dorkit picks sbbs-mode, dorkit subdir resolved before flat
>   stubs, Node-friendly `sbbs_input.js` callback replacement.
> - **Collaborative wiki** at `/wiki/` — markdown with `[[wiki-links]]`,
>   revision history, diff/revert, search, wanted/orphan reports.
>   41 seeded pages cover the whole BBS.
> - **Built-in RSS reader** — web at `/rss/`, terminal main-menu R.
> - **Web door terminal** pinned to 80×25 with the real CGA palette
>   (16 colors); no auto-fit-to-viewport so gotoxy lands where doors
>   expect.
> - **Pre-join output buffer** in the web games-socket so doors that
>   draw their welcome screen before the first keystroke (ANetSIMS,
>   A-Net Sixel TV, etc.) render immediately instead of waiting for
>   Enter.
> - **NodeSpy kick** — sysop can disconnect stuck terminal sessions
>   from `/admin/control/`, cross-process via a DB-flag/poll signal.

## Front-ends

| Service | Port | Notes |
|---------|------|-------|
| Web (Flask + SocketIO via gunicorn/eventlet) | 5000 | Behind nginx in production |
| Telnet | 2233 | asyncio-based, async TLS optional |
| SSH | 2234 | asyncssh; password + pubkey |
| rlogin | 513 | classic Unix rlogin |
| FTP | 21 | pyftpdlib; serves `FileArea` tree, anon + auth, optional FTPS (earns FTN `IFC` flag) |
| Finger (RFC 1288) | 79 | per-user info via `/finger/<user>` web UI too |
| MSP / Inter-BBS IM (RFC 1312) | 18 | inbound IMs from peer BBSes |
| SYSTAT (RFC 866) | 11 | who's-online query for peers |
| BinkP (FidoNet inbound) | 24554 | TCP, FTS-1027 |

All front-ends share the same SQLite DB and the same UserSession
presence table — "who's online" sees everyone regardless of which
front-end they used.

## Messaging

- **Local message boards** — categories, threading, sticky/lock, quote-reply,
  ANSI banners, search, voting, last-read tracking, per-board moderators
- **FidoNet netmail (binkp)** — full kludge support (MSGID, REPLY, INTL,
  CHRS, FMPT/TOPT, PID, TZUTC). Per-user AKA registry. Drafts. Soft-delete.
- **FidoNet echomail** — bidirectional with AreaFix robot. `+TAG`/`-TAG`/
  `%RESCAN`/`%LIST`/`%QUERY` support. Bad-area sysop review queue.
- **DOVE-Net QWK** — fetch + REP packet upload, CONTROL.DAT-driven area
  auto-create, conf# mapping, manual quick-add by conf number.
- **TIC processor** — ingest hatched files from FTN peers, hatch outbound
  to downstream subscribers
- **Private messages** — local user-to-user PMs with Inbox/Sent/Drafts
- **Inter-BBS Instant Messaging (MSP / RFC 1312)** — TCP/18, with
  Synchronet `sbbsimsg.lst` directory mirrored daily

## Real-time chat

- **MRC bridge** — single bridge connection to mrc.bottomlessabyss.net
  shared across all users. Web `/mrc/` + terminal MRC client through the
  same bridge so local + web users share rooms and identity.
- **Terminal MRC** — anetmrc-style stationary status bar at row 1, chat
  scrolls below, input line pinned to bottom. Full slash-command roster
  (`/identify /msg /me /join /list /who /chatters /motd /banners /topic
  /lastseen /afk /back /status /roomconfig /termsize /trust /broadcast
  /raw /ctcp …`). Tab-completion on usernames seen in chat.
- **IRC client** — web IRC at `/irc/`, plus an MRC↔IRC bridge admin so
  sysops can mirror channels.
- **Multinode terminal chat** — between currently-connected sessions.
- **Shoutbox** — inline web shoutbox.

## Files

- **File areas** — per-area upload permissions (users / sysop / none),
  optional moderation queue, ratios, shareable expiring links
- **TIC ingest + hatching** — bidirectional FTN file echoes
- **DIZ / README / DESCRIPT.ION extraction** from zip / tar.{gz,bz2,xz} /
  7z / rar / lha
- **Optional ClamAV virus scan** on upload

## Image galleries

- **Web gallery** at `/gallery/` — paginated thumbnail grid, full-screen
  modal viewer, lazy-loaded. Browser-native rendering.
- **Admin** at `/admin/galleries/` — add/remove/edit collections, manage
  individual files (delete, drag-and-drop multi-file upload).
- **Storage** — JSON config (`gallery-config.json`); deploy excludes it
  so updates don't clobber sysop's list.
- **Terminal viewer** — standalone `anet-gallery.sh` using chafa
  (universal) or img2sixel (sixel-capable terminals).
- See [`docs/13-image-galleries.md`](docs/13-image-galleries.md).

## RSS reader

- **Web reader** at `/rss/` — feed list with unread counts, combined
  river view, paginated per-feed item lists, full-content article
  view, mark-as-read tracking per user.
- **Terminal reader** — `R` from the Main BBS Menu. Same browse model
  as web (feed list / river / per-feed / item view), word-wrapped to
  78 cols, unread `*` markers.
- **Admin** at `/admin/rss/` — add/edit/delete feeds, force-refresh,
  per-feed status (last fetched, last error if any).
- **Background poller** auto-fetches every active feed every 30 min
  (configurable via `RSS_POLL_INTERVAL`). Dedupes by GUID.
- **X-News seeded by default** so a fresh install ships with at least
  one feed populated.
- See [`docs/16-rss-reader.md`](docs/16-rss-reader.md).

## Door games

- **Game types**: door_dos (DOSBox bridge), door_native (any executable),
  door_synchronet (Synchronet `.js` doors via real `jsexec` if installed,
  otherwise a Node.js compat shim), door_mystic / door_mystic_mps
  (Mystic Pascal Script), door_rlogin (outbound rlogin to a remote BBS
  game server like Synchronet xtrn / DoorParty / A-Net Online),
  builtin_web (Flask-routed mini-games)
- **Synchronet compat shim** — pure-Node implementation of Synchronet's
  JS API surface (~270 functions / objects covering File / console / bbs
  / system / user / load / require / dd_lightbar_menu / mouse_getkey).
  Runs stock doors that don't need full SBBS.
- **Mystic Python compat** — runs Mystic Python (.py) doors via a fake
  `mystic_bbs` module pointing at our compat helpers
- **Drop-files** — DOOR.SYS / DORINFO1.DEF / DOOR32.SYS supported
- **Per-node scratch dirs** — `<install>/data/temp/nodeN/` auto-created
  for each active node so concurrent callers don't fight over drop files
- **Synchronet + Mystic %-token substitution** in `executable_path`,
  `working_directory`, `command_line_args`, `drop_file_path`. Use `%f`
  (Synchronet drop-file path), `%P` (Mystic per-node dir), `%U`/`%u`
  (username), `%n`/`%N` (node), `%m`/`%M` (minutes left), and ~25 more.
  See `docs/14-door-games.md` for the full table.
- **Multi-node** — node manager allocates nodes from `GAMES_MAX_NODES`
  pool, releases on session end, per-game `max_nodes` cap
- **Mystic `.mps` auto-compile** — if `mplc` is installed, source files
  are compiled to bytecode automatically before launch
- **Door I/O safety** — Ctrl+]q user-abort, 60-second idle-timeout
  watchdog (configurable via `DOOR_IDLE_TIMEOUT`), bridge-close
  detection, waitpid watcher, force-kill of stuck process groups.
  Stuck doors always return the user to the BBS within the timeout.

## Sysop tools

- **Dashboard** with at-a-glance counts, recent activity, alerts
- **Setup wizard** for first-time configuration
- **Caller log** with detailed connection records
- **Broadcast** to logged-in users
- **MOTD pool** with rotation
- **NodeSpy** — see what every active terminal user is doing in real time
- **Control panel** — stop/start/restart any service from the web
- **Sysop console** — terminal-style admin shell
- **Setup checklist / Launch checklist**
- **IP bans** (CIDR), word filter, file queue, virus scan admin
- **Webhook** outbound notifications (Discord, etc.)
- **Sysop pages** — alert when paged from BBS, with answer flow
- **Newsletter** — email blast to active users
- **Inactive user** prune
- **DB backup** + theme builder + per-user theme picker
- **Users / Boards / Bulletins / Themes** CRUD
- **Echomail dashboard** — poll logs, AreaFix log, bad-areas review,
  per-network manage
- **Door Games admin** — add / edit / sort / disable
- **Gallery admin** — galleries + per-gallery file management
- **Menu admin** — every BBS terminal menu is data-driven and editable
- **MRC↔IRC bridge admin**
- **File-Echo subs**, **Default Echo subs**, **Connection Test**
- **Webhooks**, **Activity log**, **Registration attempts**

## Tools / community

- **Web terminal** — full BBS via xterm.js + socket.io (with sixel/iTerm2
  image addon for graphical doors)
- **Who's online**, **Bulletins**, **BBS history**
- **Gemini Capsules** — list local users' gemini:// pages
- **Personal web pages** — each user can host static HTML at `/_pages/<user>`
- **Site pages** — sysop-edited static pages at `/page/<slug>`
- **Calendar**, **Groups**, **Polls**, **Saved messages**, **Shoutbox**
- **Oneliners** wall + `/finger/<user>` profiles
- **Contacts / Buddies** — friend list + arrival/departure notifications
- **Blocks** — block list (per-user mute)
- **Notifications** center with toast + bell badge
- **Leaderboard** — points / posts / files / games activity ranking
- **Polls + Votes API** for embedded polls in pages
- **RSS / Atom feeds** for boards, bulletins, oneliners
- **Telegram** outbound notifier
- **Permalinks** — short `/m/<id>` for sharing posts off-BBS
- **File areas browser**, **My File Shares**
- **Nodelist browser** with live who's-online queries
- **Statistics**, **Tour**, **Documentation viewer**

## Front-end UX details

- Single-key hotkey menus (no Enter required) on terminal
- Synchronet `@CODE@` and Mystic `|XX` display codes in welcome /
  goodbye / menu ANSI screens
- Notification banner at login (`*** You have new: 3 PMs, 1 InterBBS IM`)
- 7 built-in themes + custom theme builder
- Mobile-friendly responsive web layout
- ANSI rendering across all message lists, viewers, sysop tools

## Deployment

- systemd unit files for every service
- `install.sh` for fresh installs (creates anetbbs user, venv, secrets)
  with **production / test mode** switch — test mode skips
  nginx/SSL/privileged-port services for local-only / no-static-IP
  setups
- Optional: `install.sh` can fetch Mystic BBS Linux + `mplc` automatically
- `update.sh` for upgrades (with `--exclude` rules that protect
  customized doors and gallery configs)
- See [`docs/INSTALL.md`](docs/INSTALL.md), [`docs/12-upgrading.md`](docs/12-upgrading.md), [`docs/14-door-games.md`](docs/14-door-games.md)

---

For installation, configuration, and per-feature deep-dives, see
[`docs/`](docs/).
