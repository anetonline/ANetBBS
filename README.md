# ANetBBS

**Status: alpha 2** (`v1.0a2.164`, June 2026)

A modern multi-node BBS for the FidoNet/Synchronet world. Web, telnet, SSH,
rlogin, **and FTP** front-ends; FidoNet binkp + DOVE-Net QWK echomail;
inter-BBS instant messaging via MSP (RFC 1312); a built-in collaborative
wiki and RSS reader; door-game support including stock Synchronet `.js`
doors (LORD ships pre-installed and plays out of the box).

## Quick install (Linux)

You'll want a domain pointed at this box if you want the web admin and
public web pages working with HTTPS — modern browsers refuse plain-HTTP
logins, so the wizard's **production** mode pulls a Let's Encrypt cert.
Telnet, SSH, rlogin, BinkP, MRC, and MSP work fine without a domain;
pick **test** mode at the prompt if you're behind NAT or just kicking
the tires (web admin runs on `http://localhost:5000`).

```
tar xzf ANetBBS-v1.0a2.164.tar.gz
cd ANetBBS-v1.0a2.164
sudo bash install.sh
```

The wizard asks for BBS name, sysop, ports, services, and SSL/nginx
preferences, then sets up the venv, systemd units, ufw rules, and (if
enabled) a Let's Encrypt cert. A random admin password is generated and
written to `data/admin_password.txt` — log in to the web admin and
change it on first use.

For details on individual services and post-install configuration see
[`docs/INSTALL.md`](docs/INSTALL.md).

## Features

### Messaging
- **Local boards** with categories, sticky/lock, threading, quote-reply,
  search, voting, ANSI banners, last-read tracking, moderators
- **FidoNet netmail** (binkp) — full kludge support (MSGID, REPLY, INTL,
  CHRS, FMPT/TOPT, PID, TZUTC), per-user AKAs, drafts, soft-delete
- **FidoNet echomail** with AreaFix robot (in/out), bad-area sysop review
- **DOVE-Net QWK** — fetch + REP packet upload, CONTROL.DAT-driven area
  auto-create, conf# mapping, manual quick-add for known confs
- **Private messages** between local users
- **Inter-BBS Instant Messaging** (MSP / RFC 1312) — TCP/18, with the
  Synchronet `sbbsimsg.lst` directory mirrored daily and a SYSTAT
  (UDP/11) Active-User responder
- **CP437 + ANSI rendering** in every message body (proper box-draw,
  16-color SGR → HTML spans)

### Doors
- DOSBox-staging / dosbox-x / vanilla DOSBox auto-detect, TCP nullmodem
  bridge to the user's PTY
- Mystic Pascal Script (`.mps`) and Mystic Python (`.mpy`) doors
- Synchronet `.js` doors — runs via real `jsexec` when found at
  `$SBBSEXEC/jsexec` or `/sbbs/exec/jsexec`; otherwise falls back to a
  Node.js + compatibility shim that the alpha 2 work expanded enough to
  fully run **LORD** (Legend of the Red Dragon — Synchronet's JS port,
  pre-installed, no Synchronet required)
- Native binary doors with DOOR.SYS / DOOR32.SYS / DORINFO drop files
- **Web terminal pinned to 80×25 with the full CGA palette** so doors
  draw the same way as on a 1990s VGA terminal — Violet's portrait in
  LORD looks like Violet's portrait in LORD

### Wiki
- Full collaborative wiki at `/wiki/` with markdown bodies, `[[Page]]`
  cross-links, per-page revision history, unified-diff compare,
  revert, full-text search, recent changes, wanted/orphan pages
- 41 seeded pages cover connecting, every messaging subsystem, doors
  (including a LORD recipe + the DosBridge), echomail/BinkP setup,
  NodeSpy, backup, full architecture, sysop guide, glossary, FAQ
- Anyone can read; logged-in users can edit; admins can lock/delete/rename

### RSS Reader
- Built-in feed aggregator for BBS-scene news + anything else
- Web at `/rss/` (river or per-feed), terminal main-menu **R**
- Background poller with per-feed cadence, sanitized HTML via bleach
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
- **Terminal viewer** at `/home/<user>/anet-gallery.sh` — bash script
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

### Sysop tools
- Echomail dashboard with poll logs, AreaFix log, bad-areas review
- File-area admin
- Manual `+TAG`/`-TAG` AreaFix-to-hub form
- BBS directory browser with live `who's online` queries

## Documentation

- [`FEATURES.md`](FEATURES.md) — full feature inventory for the alpha
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

## License

See [LICENSE](LICENSE).

## Status / Caveats

This is an **alpha** release. Known rough edges:

- MSP/SYSTAT need privileged ports (11/18) — see
  [`docs/INSTALL.md`](docs/INSTALL.md) for `setcap` / systemd /
  iptables-redirect options.
- Synchronet stock `.js` doors: LORD is verified end-to-end under the
  Node shim. Other Synchronet doors mostly work but each one tends to
  exercise a different corner of the API — file an issue if a door
  dies with a `ReferenceError` and the missing stub will get added.
- Single-process rate limits — fine for one gunicorn worker; needs an
  external store for multi-worker setups.
- Test coverage is light. Real-world testing is the alpha's purpose.

Issues and patches welcome.
