# ANetBBS v1.0a2.14 — alpha 2

> A modern multi-node BBS for the FidoNet / Synchronet world.
> Web, telnet, SSH, rlogin, FTP front-ends sharing one user database.
> Federation hub for inter-ANetBBS peer discovery.

The **second public alpha**, point release v1.0a2.14. Changes since
v1.0a2.13:

- **Federation registry — hub side.** First half of the
  ANetBBS-to-ANetBBS federation feature. One BBS designated as the
  central hub (`REGISTRY_MODE_ENABLED=true`) accepts peer registrations
  via `POST /registry/api/v1/register`, sends an email-verify token,
  routes through sysop approval, and emits `GET /anetbbs.lst` — the
  JSON peer directory that other ANetBBS instances will pull (in
  v1.0a2.15 tomorrow).
- New admin UI at `/admin/registry/` — sysop approval queue with
  approve / reject / edit / delete buttons and pending-state filters.
- SYSTAT prober runs hourly against listed peers and auto-drops dead
  ones from `anetbbs.lst` (re-lists on recovery).
- New `RegistryEntry` model + automatic schema migration on next deploy.
- New `User.email_enabled` + `User.email_local_part` columns
  (groundwork for the email feature shipping next week — present but
  inert at v1.0a2.14).
- New LMTP receive module `anetbbs/mail/` (also email groundwork —
  inert until `MAIL_ENABLED=true`).

Changes since v1.0a2.12:

- **Docs catch-up for FTP.** `docs/PORTS.md`, `docs/07-file-areas.md`,
  `docs/01-installing.md`, `docs/00-overview.md`, `README.md`, and
  `FEATURES.md` now all describe the FTP server (port, permissions
  tiers, FTPS, FTN `IFC` flag) so it isn't just a code change with
  no user-facing documentation.

Changes since v1.0a2.11:

- **FTP settings now in `/admin/settings`** alongside the other
  protocols. Eight new editable rows (`FTP_ENABLED`, `FTP_PORT`,
  `FTP_ANON_ENABLED`, `FTP_PASV_PORTS`, `FTP_TLS_CERTFILE`,
  `FTP_TLS_KEYFILE`, `FTP_ROOT_DIR`, `FTP_BANNER`). No more
  hand-editing `.env`.

Changes since v1.0a2.10:

- **FTP listings no longer leak server-side paths.** `LIST` (and the
  client `dir` / `ls` commands) previously rendered each FileArea
  entry as a symlink with its absolute target visible
  (`FILES.GAMES -> /home/stingray/anetbbs/data/files/games`).
  That exposed internal directory layout to anonymous users as well
  as authenticated ones. Each area now appears as a plain directory.

Changes since v1.0a2.9:

- **FTP hotfix — no longer breaks telnet/SSH login.** v1.0a2.9's FTP
  integration imported `create_app()` from `web_app`, which registers
  all blueprints + starts the background pollers. That turned the
  pure-asyncio terminal-server process into a mixed asyncio+threading
  one and broke the `threading.Condition` semantics in the login flow
  ("cannot notify on un-acquired lock"). Replaced with a tiny
  `build_minimal_app()` that initializes only the DB binding — zero
  blueprints, zero pollers. FTP works the same as before; SSH/telnet
  no longer drop you on login.

Changes since v1.0a2.8:

- **FTP server!** The fifth front-end. Serves the existing `FileArea`
  tree to anonymous + authenticated users. Anonymous login (read-only,
  public areas only) is on by default; authenticated users get full
  per-area read/write subject to each area's `upload_permission`.
  Admins additionally see sysop-only areas. Optional FTPS by setting
  `FTP_TLS_CERTFILE` + `FTP_TLS_KEYFILE` (reuse your nginx cert).
  Backed by `pyftpdlib`. Runs in a daemon thread inside the existing
  `anetbbs.service` process — set `FTP_ENABLED=true` in `.env`, open
  port 21 and the passive-mode range `FTP_PASV_PORTS` (default
  `40000-40050`) on your firewall, and you can advertise the FTN
  nodelist `IFC` flag.
- Uploads via FTP create `FileUpload` rows so they show up in the web
  UI's file-area browser too. Per-area `upload_permission` is enforced
  post-write — denied uploads are deleted and logged.

Changes since v1.0a2.7:

- **Nodelist now auto-imports from inbound TICs.** Inspired by
  codefenix's NetLister for Synchronet. Tag a `FileArea` as a nodelist
  source in `/admin/file-areas/` (new "Nodelist source" checkbox +
  domain field). Every TIC dropped into that area gets unwrapped
  (including ZIPs with weird extensions like `tqwnet.z46`) and the
  nodelist text is loaded into the `Nodelist` browser, tagged by your
  configured domain. New entries on `/nodelist/` show a **Send Netmail
  to Sysop** button that pre-fills the existing composer — and because
  netmail compose already auto-picks the FROM AKA by zone, clicking a
  TQWnet entry composes from your TQWnet AKA, fsxnet from fsxnet, etc.
- **Bulk-import admin tool** at `/nodelist/admin/bulk-import` for
  sysops who already have a stash of nodelist archives on disk. Scans
  a directory, shows every plausible candidate (text files + ZIPs),
  and lets you tick + tag → import in one submit. Default scan path
  is `data/nodelists` (overridable via `NODELIST_SCAN_DIR`).

Changes since v1.0a2.6:

- **`/who/` no longer leaks exact URLs to other users.** The "Where"
  column on the Who's Online page used to show the full URL path
  (e.g. `/echomail/53/25407`) of every logged-in user to every other
  logged-in user. Anyone could follow another user around the BBS by
  pasting that path into their address bar. Sysops still see the raw
  path for diagnostics; non-admins now see a coarse area label
  ("Echomail", "Profile", "MRC Chat", …). Telnet/SSH/rlogin sessions
  already show friendly menu names, so they're unaffected.
- **Inbound netmail pulled via poll-out is no longer stuck at `draft`.**
  When ANetBBS dials out to a hub and pulls a netmail (e.g. an AREAFIX
  response), the poller was creating the `NetmailMessage` without
  setting `status`, so it inherited the `draft` default and didn't show
  up in the sysop's inbox UI. Fixed in `poller.py`. The listener path
  (when a peer dials *us*) was always correct. Existing stuck rows on
  upgrading installs can be one-shot fixed with:
  `UPDATE netmail_messages SET status='received', received_at=created_at WHERE direction='inbound' AND status='draft';`

Changes since v1.0a2.5:

- **Profile edits now actually submit.** The profile-edit page had a
  nested `<form>` inside the main form (the Remove Avatar button).
  HTML forbids nesting forms; browsers auto-closed the outer form at
  the inner tag, dropping the Privacy / Signature / Tagline / Theme /
  Submit button outside any form. Clicking Update Profile did nothing.
  Restructured so the avatar-remove form lives outside the main form
  and the button uses the HTML5 `form=` attribute. **This is why
  nobody's theme ever saved.**
- **`anetbbs-deploy-latest.sh` no longer breaks systemd units.** Step
  4.5 (sync unit files) used to drop the source `deploy/*.service`
  files straight into `/etc/systemd/system`, hard-coded to
  `/opt/anetbbs/venv/bin/gunicorn`. Installs that live elsewhere
  (e.g. `/home/stingray/anetbbs`) would crash-loop after the deploy
  with `status=203/EXEC`. Step 4.5 now rewrites the path to whatever
  the script's `$INSTALL` actually is. If a previous deploy already
  clobbered your units, run once:
  `sudo sed -i 's|/opt/anetbbs|/your/install/path|g' /etc/systemd/system/anetbbs*.service && sudo systemctl daemon-reload`.

Changes since v1.0a2.4:

- **Profile saves no longer fail for users with `.local` emails.**
  The `Email()` validator (via `email-validator` 2.x) was rejecting
  RFC 6761 special-use TLDs like `.local` / `.test` / `.invalid`. The
  seeded admin user is `admin@anetbbs.local`, so every profile-edit
  attempt — including the theme dropdown — bounced off email
  validation and never reached the DB. Swapped in a permissive regex
  validator for `auth.py`, `profile.py`, and `admin.py`. FidoNet-style
  aliases and internal-only TLDs are now accepted.
- **nginx `client_max_body_size` default raised to 110 MB.** The
  bundled `deploy/anetbbs-nginx.conf.template` inherited nginx's 1 MB
  default, so 1–2 MB avatar uploads returned 413 Request Entity Too
  Large before Flask ever saw them. New installs get the right limit
  automatically; upgrading sysops should regenerate their nginx
  config (or add the directive by hand to the `server {}` block).

Changes since v1.0a2.3:

- **Unified `anetbbs.service`** — telnet, ssh, and rlogin now run in
  a single systemd service driven by the `.env` `*_ENABLED` flags.
  Replaces the split `anetbbs-telnet.service` + `anetbbs-ssh.service`
  pair that fought each other for ports on every boot because Ubuntu
  systemd treats `EnvironmentFile=` as winning over inline
  `Environment=` directives. `update.sh` migrates legacy installs
  automatically (stops + disables + removes the old units before
  installing the unified one).
- **Three install modes** instead of two — production, behind, test.
  - **production** = full public BBS, nginx + Let's Encrypt cert, sysop
    owns 80/443
  - **behind** = sysop already runs another BBS/web server that owns
    80/443; ANetBBS gunicorn binds `0.0.0.0:8080`, sysop reverse-proxies
    from their existing web server (we don't touch their nginx). Install
    summary prints a copy-pastable nginx server block.
  - **test** = localhost-only sandbox; gunicorn binds `127.0.0.1:8080`
    (not reachable from the LAN at all), no public web. Sysop accesses
    via SSH tunnel from another machine. Summary prints the tunnel
    command.
- **Default web port for non-production modes is now 8080** (was 5000)
  — common alt-HTTP port, less ambiguous, easier for sysops who already
  run other web stuff. Production still uses 5000 (behind nginx, port
  doesn't matter much).

Changes from v1.0b.2 → v1.0a2.3 (renamed from `v1.0b.3`; the `b` was a
versioning slip that read like "beta" — same release line, renamed to
alpha-2 for clarity):

- **Web terminal disconnect fixed** — the outbound-telnet read loop
  had a race with the close path that crashed the eventlet
  greenthread, dropping the WebSocket. Read loop rewritten to snapshot
  the socket and use a clean timeout/EOF distinction.
- **Development docs** — new `docs/17-development.md` covering all 7
  door types with examples, web blueprint authoring, model/migration
  flow, theme building, MRC and echomail extension points. Companion
  community-editable wiki page at `[[Development]]`.
- **Docs index reorganized** — 18 doc pages now grouped into 8
  categories (Getting Started, Daily Operations, Networks & Mail,
  Look & Feel, Doors & Games, Web Features, Development, Reference).
  Sidebar on individual doc pages mirrors the categorized layout.
- **Web terminal palette** — full 16-color CGA palette applied to the
  outbound-telnet terminal; was using xterm.js defaults (where "black"
  is `#2e3436` gray) so block ANSI art looked muted. Now matches the
  door terminal.
- **Web terminal quick-connect** trimmed to just the local BBS.
- **gunicorn timeout 120→300** plus `--graceful-timeout 30` in the
  systemd unit, matching what the live box was already running.
- **gunicorn pinned `<23`** in requirements / setup.py to keep the
  eventlet worker entry point. Fresh installs were pulling gunicorn
  25 which deprecates eventlet and breaks the worker.
- **nginx CVE-2026-42945 check** added to install.sh's final
  health-check stage. Detects vulnerable nginx versions
  (0.6.27 to 1.30.0) and inspects the live config for the
  exploitable `rewrite`+`set` directive combination — warns on
  latent risk, fails loud when actually exploitable.
- **"Active Users" homepage stat fixed** — was counting distinct
  local-board post authors, now matches admin-dashboard definition
  (non-banned users with `last_login` in the last 30 days).

Changes from v1.0b.1 → v1.0b.2:

- **Auto FILE_ID.DIZ in file areas** — listings now extract the
  BBS-standard short description (or README.* fallback) from each
  archive when no TIC description is present. Results cached per file
  in `.descriptions.json`. Sysops get a "Re-scan descriptions" button
  per area.
- **Files nav consolidation** — File Gallery / File Areas / My File
  Shares moved into a single "Files" dropdown in the navbar.
- **Mystic BBS bundled** — full Mystic 1.12 A48 runtime ships in
  `vendor/mystic/`; install.sh no longer downloads.
- **MRC config generated from wizard inputs** — fresh installs get
  their own `bridge_bbs`, `info_sysop`, `info_web`, `info_desc`.
- **install.sh polish** — production mode default, sysop-password
  confirmation prompt, Let's Encrypt port-forward readiness check.

Headline additions over v1.0a:

- **LORD** (Legend of the Red Dragon) ships pre-installed and plays
  end-to-end under our Node + Synchronet compat shim — no external
  jsexec needed.
- **Collaborative wiki** at `/wiki/` — markdown + `[[wiki-links]]`,
  full revision history with diff/revert, full-text search,
  wanted/orphan reports. 41 seeded pages.
- **Built-in RSS reader** — web + terminal, background poller.
- **Web door terminal** locked to 80×25 with real 16-color CGA palette.
- **NodeSpy kick** — sysop can disconnect stuck terminal sessions
  cross-process from `/admin/control/`.
- **Echomail terminal reader** — proper paging with `[Q]=quit`, CP437
  body passthrough so embedded ANSI art renders correctly.
- ~90 polish fixes across messaging, doors, themes, admin UX.

## Download

| Asset | Notes |
| ----- | ----- |
| `ANetBBS-v1.0b.tar.gz` | Source + assets, ~5 MB |

## Install (5 minutes)

```bash
tar xzf ANetBBS-v1.0b.tar.gz
cd ANetBBS-v1.0b
sudo bash install.sh
```

The installer asks for BBS name / domain / ports / which protocols to
enable, drops a systemd unit for each, sets up a Python venv with the
deps from `requirements.txt`, and writes a random initial admin password
to `data/admin_password.txt` (mode `0600`). After it finishes:

```bash
journalctl -fu anetbbs-web      # tail logs
xdg-open http://localhost:5000  # log in as admin, change the password
```

For non-interactive / scripted installs use `sudo bash install.sh --defaults`.
For a step-by-step manual install see [`docs/INSTALL.md`](docs/INSTALL.md).

For an upgrade from v1.0a → v1.0b on an existing install:

```bash
tar xzf ANetBBS-v1.0b.tar.gz
cd ANetBBS-v1.0b
sudo bash update.sh --install-dir /path/to/existing/anetbbs
sudo systemctl restart anetbbs-web anetbbs-telnet
```

## Highlights

### Messaging
- Local message boards with categories, threading, voting, sticky/lock,
  per-board moderators, search, ANSI banners
- **FidoNet netmail** (BinkP) with full kludges (MSGID, REPLY, INTL,
  TZUTC, CHRS, FMPT/TOPT, PID), CRAM-MD5 BinkP auth, optional TLS
- **FidoNet echomail** with AreaFix robot in/out, BadArea sysop review
- **DOVE-Net QWK** — fetch + REP outbound, CONTROL.DAT-driven area
  auto-create
- **Inter-BBS Instant Messaging** via MSP / RFC 1312 (TCP 18) with the
  Synchronet `sbbsimsg.lst` directory mirrored daily and a SYSTAT
  Active-User responder (UDP 11)
- **Private messages** between local users
- **CP437 + ANSI rendering** throughout — proper box-draw, 16-color
  SGR → HTML spans

### Doors
- DOSBox (auto-detect staging / x / vanilla) with TCP nullmodem to PTY
- Mystic Pascal Script (`.mps`) and Mystic Python (`.mpy`)
- Synchronet `.js` doors via real `jsexec` when present; otherwise our
  Node + compat shim. **The shim is now deep enough to run LORD
  fully** (welcome, character creation, town square, Inn, Violet
  flirt, save/load roundtrip, multi-session persistence).
- Native binary doors with DOOR.SYS / DOOR32.SYS / DORINFO drop files
- **Bundled & pre-installed: LORD, BotWars, ANetSIMS**

### Web door terminal
- Pinned to 80×25 — no auto-fit to viewport, so `gotoxy(x,y)` lands
  where doors expect
- Real 16-color CGA palette (DOS bright red is `#ff5555`, not the
  browser default web-pink) so ANSI art matches what authors saw
- Sixel + iTerm2 image protocol via `xterm-addon-image` for graphics
  doors (DSR, Sixel TV)
- Centered shrink-to-fit container, scrolls on phone-narrow viewports

### Wiki
- `/wiki/` — full collaborative wiki
- Markdown bodies with python-markdown + bleach sanitization
- `[[Page Name]]` wiki-link syntax; red-link missing pages
- Full revision history per page, unified-diff compare between
  any two revisions, one-click revert
- Full-text search (title + slug + body)
- **Wanted pages** report (linked-to but not yet created) and
  **Orphan pages** report (not linked from anywhere)
- Admin: lock, soft-delete, rename, restore
- **41 seeded pages** cover the whole BBS — connecting, every messaging
  subsystem, doors (incl. a LORD recipe + DosBridge architecture),
  echomail/BinkP setup, NodeSpy, backup, sysop guide, glossary, FAQ

### RSS Reader
- Web at `/rss/` (river or per-feed), terminal main-menu **R**
- Background `feedparser` poller with per-feed cadence
- Limited HTML inside items, sanitized via `bleach`
- Per-user unread state
- X-News (`x-bit.org/rss/rss.xml`) seeded by default

### MRC chat
- **WebSocket bridge** to the global MRC network — one BBS-level hub
  connection shared across all users
- Web `/mrc/` + terminal MRC sharing the same hub identity
- Status bar, tab-complete, masked `/identify`, color cycling,
  full slash-command roster

### Front-ends
- Web (Flask + SocketIO) on port 5000
- Telnet (2233), SSH (2234), rlogin (513) — share the same user DB
- **Terminal UX**: single-key hotkey menus, screen cleared between
  menus, Q logoff confirms, color rendering across all major flows.
  Synchronet `@CODE@` and Mystic `|XX` codes in welcome/goodbye/menu
  ANSI screens. **Echomail reader** now properly paged with `[Q]=quit`
  on every `--more--` prompt and CP437/ANSI bodies pass through
  unmangled.

### Sysop tools
- `/admin/control/` — services dashboard + **NodeSpy** (view live
  terminal screens, **kick stuck sessions** via cross-process DB-flag)
- `/admin/echomail/`, `/admin/games/`, `/admin/users/`, `/admin/themes/`,
  `/admin/rss/`, `/admin/wiki-admin/`, …

## Known caveats

- **MSP / SYSTAT need privileged ports** (18 / 11). Options:
  `setcap` on the venv python, `AmbientCapabilities` in the systemd
  unit (shipped unit has it), or iptables NAT redirect. See
  `docs/INSTALL.md` step 6.
- **Synchronet stock `.js` doors**: LORD is verified end-to-end under
  the Node shim. Other Synchronet doors mostly work but each tends to
  exercise a different API corner — file an issue if a door dies with
  a `ReferenceError` and the missing stub will get added.
- **Eventlet (gunicorn worker)** is technically deprecated in Gunicorn
  25. We tried gevent + gevent-websocket but the latter (last shipped
  in 2018) hangs WebSocket upgrades against modern gevent. Pinning
  to eventlet until flask-socketio offers a maintained alternative.
- **Synchronet IMSG `<no name>`** in their UI when an inbound IM
  arrives from us — Synchronet does an RFC 1413 IDENT callback on
  port 113. ANetBBS doesn't ship an identd. Cosmetic only.
- **Test coverage is light.** Real-world testing is the alpha's
  purpose — issues + patches welcome.

## Verifying

After install:

```bash
systemctl is-active anetbbs-web anetbbs-telnet anetbbs-ssh
sudo ss -ltnp | grep -E ':(5000|2233|2234|18) '
journalctl -u anetbbs-web --since "1 minute ago" | grep -iE 'listening|error'
```

Should show `active` × 3, four listeners (web, telnet, SSH, MSP), and
clean `… listening on 0.0.0.0:18` lines without bind errors.

To verify LORD plays:
1. Log in to the web UI
2. `/admin/games/` — confirm "Legend of the Red Dragon" is `Active`
3. `/games/` → click **Legend of the Red Dragon**
4. Welcome ANSI should render in 1–2 seconds, then character creation prompt

For an isolated wire test of the MSP path see
[`docs/MSP_LOOPBACK_TEST.md`](docs/MSP_LOOPBACK_TEST.md).

## Documentation

- [`README.md`](README.md) — top-level overview
- [`FEATURES.md`](FEATURES.md) — full feature inventory
- [`docs/CHANGELOG.md`](docs/CHANGELOG.md) — version-by-version notes
  (every internal build v197 → v287.1)
- [`docs/INSTALL.md`](docs/INSTALL.md) — install
- [`docs/14-door-games.md`](docs/14-door-games.md) — door setup
- [`docs/15-synchronet-compat.md`](docs/15-synchronet-compat.md) — Synchronet shim details
- **In-BBS wiki** at `/wiki/` — community-edited; the seeded pages
  cover every subsystem

## License

See [`LICENSE`](LICENSE).
