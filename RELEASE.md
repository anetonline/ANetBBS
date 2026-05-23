# ANetBBS v1.0a2.62 — Installer fix: pip failure on Python 3.13 / ARM64

Fixed: `install.sh` would silently swallow `pip install` errors (using
`--quiet 2>/dev/null`), then continue into admin setup and service starts
against an empty venv — producing a cascade of misleading errors (dotenv,
gunicorn, sqlalchemy not found). Reported by Ultra_Magnus on Pi 5 (Debian
trixie / Python 3.13.5).

Changes:
- `--prefer-binary` flag added to `pip install` — uses pre-built wheels
  instead of compiling from source (prevents ARM64 / Pi compile failures).
- Actual pip error output is now shown when install fails.
- Admin account setup is skipped (with a clear message) when pip failed.
- Service starts are skipped (with a clear message) when pip failed.

---

# ANetBBS v1.0a2.61 — Newsletter bugfix

Fixed: newsletter "Send to all users" crashed with a 500 error due to a
wrong field name when creating private messages (`content` → `body`).
No data was lost — the newsletter table was written but no PMs were sent.

---

# ANetBBS v1.0a2.60 — In-browser DOS games: DOOM + Duke Nukem 3D

New `door_dos_browser` game type — play classic DOS games directly in
the web browser via EmulatorJS (dosbox_pure core). No DOSBox on the
server; no telnet required.

**Pre-bundled shareware titles** (included in the tarball):
- **DOOM (Shareware)** — id Software. SoundBlaster audio.
- **Duke Nukem 3D (Shareware)** — 3D Realms. GUS/UltraSound audio
  (only FX device supported by the v1.3D Build Engine binary under
  dosbox_pure).

**What's new:**
- `anetbbs/web/games.py` — `/games/dos-frame/<slug>` with COOP/COEP
  headers; `/games/dos-data/<file>` ZIP server.
- `anetbbs/templates/games/play_jsdos_frame.html` — EmulatorJS frame
  with pointer-lock exit overlay and GAME OVER replay loop.
- `anetbbs/features/games.py` — terminal door menu now excludes
  browser-only game types (`door_dos_browser`, `builtin_web`).
- `tools/prepare_dos_games.py` — bundle any DOS game dir into a
  dosbox_pure ZIP. New `--exclude` and `--gus` flags.
- `data/dos-games/doom.zip` + `data/dos-games/duke3d.zip` — the
  pre-built shareware bundles.
- `docs/14-door-games.md` — full `door_dos_browser` setup guide.
- `install.sh` — now creates `data/dos-games/` directory.

**Upgrade from v1.0.x:**
1. Run `update.sh` as usual — it rsyncs the new files including
   `data/dos-games/*.zip`.
2. Add two Game rows in **Admin → Door Games → Add Game**:
   - DOOM: slug `doom`, type `door_dos_browser`,
     URL `/games/dos-data/doom.zip`
   - Duke3D: slug `duke3d`, type `door_dos_browser`,
     URL `/games/dos-data/duke3d.zip`
3. `sudo systemctl restart anetbbs-web`

See `docs/14-door-games.md` → "In-browser DOS games" for full setup
and the `prepare_dos_games.py` tool reference.

---

# ANetBBS v1.0a2.59 — /docs 500 fix

Changes since v1.0a2.58:

- **`/docs/` 500 fix.** The docs index template referenced
  `url_for('main.healthz')`, but the `/healthz` route was moved
  into its own blueprint (`healthz_bp`) in .43. The stale
  endpoint name caused every `/docs/` page load to raise
  `werkzeug.routing.BuildError`. Template now uses
  `url_for('healthz.healthz')`. A small bug with a big footprint —
  the entire docs section was unreachable since .43.

---

# ANetBBS v1.0a2.58 — deletable pre-update backups

Changes since v1.0a2.57:

- **`update.sh` chowns new backup dirs to the service user.** Pre-
  update backup directories under `/tmp/anetbbs-backup-*` were created
  by `update.sh` running as root, leaving every file inside root-
  owned. The `/admin/backups/` Delete button (running as the gunicorn
  service user) hit "[Errno 13] Permission denied" on every attempt.
  Backups created by .58+ are owned by the service user from the
  moment they're written — direct `shutil.rmtree()` works.
- **Delete falls back to a sudoers helper for older backups.** Pre-.58
  backups stay root-owned and can't be deleted without privilege.
  The Delete endpoint now tries direct removal first and falls
  through to `deploy/run_restore.sh delete` (which has been extended
  with a `delete` action under the existing sudoers grant) when the
  direct path errors with `PermissionError`. Sysops can finally
  clean up the accumulated `/tmp/anetbbs-backup-*` dirs from earlier
  this week.

---

# ANetBBS v1.0a2.57 — install log "Update Complete" cosmetic fix

Changes since v1.0a2.56:

- **Remove the doubled CSS border on the "Update Complete!" banner.**
  update.sh draws its own box with `╔═╗║╚═╝` characters; applying a
  CSS top+bottom border to each of those three lines produced three
  stacked frames. The CSS now uses only the green text + subtle dark
  background, letting the ASCII frame speak for itself.

---

# ANetBBS v1.0a2.56 — preflight: targeted sudo + hardening probe

Changes since v1.0a2.55:

- **Preflight "sudo escalation" check rewritten.** The .55 version
  ran `sudo -n /bin/true` and reported "sudoers grant missing" on
  every install — because the sudoers grant is intentionally narrow
  (just the upgrade wrapper + systemctl on anetbbs-*) and
  `/bin/true` isn't on the list. New probe is two-pronged:
  1. Read `/etc/systemd/system/anetbbs-web.service` directly and
     flag `NoNewPrivileges=true` or any narrow
     `CapabilityBoundingSet=` that would block sudo's setuid
     escalation. No sudo invoked.
  2. Run `sudo -n -l <wrapper>` — sudo's "would I be allowed?"
     query, no execution — to confirm the sudoers grant actually
     covers the auto-update path.
  Either failure surfaces with the exact unit file directives to
  remove. Catches the friend's hardening regression AND a missing
  sudoers grant, with zero false positives on properly configured
  boxes.

---

# ANetBBS v1.0a2.55 — preflight: sudo escalation probe

Changes since v1.0a2.54:

- **New preflight check: "sudo escalation from web service".**
  Tries `sudo -n /bin/true` from the running gunicorn worker. If
  the unit is hardened with `NoNewPrivileges=true` or a
  restrictive `CapabilityBoundingSet=`, sudo can't escalate, and
  the auto-update endpoint would fail later with the cryptic
  "sudo: unable to change to root gid: Operation not permitted /
  error initializing audit plugin sudoers_audit". The check
  surfaces that condition red with a one-line remediation
  pointing at the unit file. Catches the regression class
  before a sysop clicks Install.

---

# ANetBBS v1.0a2.54 — install-log rendering (block by default)

Changes since v1.0a2.53:

- **Install log uses `<div>` per line instead of `<span class="line">`.**
  The CSS rule `.upg-log .line { display: block; }` did the right
  thing in isolation but a downstream theme override on .51's box
  flattened the spans back to inline, making the log render as one
  paragraph. `<div>` is block by default — no CSS needed, no theme
  override can flatten it. Belt-and-suspenders on the .52 pretty-
  print work. Hard-refresh the upgrade page after installing this
  (`Ctrl+Shift+R`) so the browser picks up the new HTML.

---

# ANetBBS v1.0a2.53 — sysop QoL polish

Changes since v1.0a2.52:

- **`/admin/door-errors/`** — readable viewer for
  `logs/door-errors.log`. Each Synchronet-compat door crash gets
  collapsed into a single row showing time + slug + user; click to
  expand the stack trace. "Clear log" button at the top. Sysops
  notice door breakage without `ssh + tail`.
- **`/admin/backups/`** — list of every pre-update snapshot
  `update.sh` has stored at `/tmp/anetbbs-backup-*`. Shows version
  delta, age, size, which artefacts are present. Per-row actions:
  Delete, Restore `.env`, Restore DB. Restores go through a new
  `deploy/run_restore.sh` helper that's strictly arg-validated and
  sudoers-granted — same security shape as the upgrade wrapper.
- **Setup wizard: "Test hub connection" button.** Probes
  `REGISTRY_URL/anetbbs.lst` from this BBS before submit. Sysop
  sees "✓ reachable" or "✗ firewall blocked" inline before
  committing to federation registration. No more "did my install
  end up federated?" guesswork after the wizard closes.
- **Public homepage welcome card for anonymous visitors.** Big
  gradient card explaining what ANetBBS is + prominent Sign up /
  Log in buttons + sysop name & contact + a one-line stat strip.
  Logged-in users see the unchanged dashboard.

---

# ANetBBS v1.0a2.52 — readable install log

Changes since v1.0a2.51:

- **Pretty-print the install log on `/admin/upgrades/`.** Each line
  now gets a class based on its content: yellow for `── Step N/8 ──`
  banners with rule dividers, green for `✅` rows, amber for `⏭`,
  red for `❌`, blue for `[INFO]`, purple-italic for the wrapper's
  own `[upgrade]` lines, and a green outlined box around the final
  "Update Complete!" banner. ISO timestamps from the Python runner
  collapse to `HH:MM:SS` so the visual rhythm of the steps reads
  cleanly. ANSI escape sequences emitted by `update.sh`'s colour
  helpers are stripped server-side so the browser doesn't render
  them as glyphs.

---

# ANetBBS v1.0a2.51 — port-collision guard for auto-update

Changes since v1.0a2.50:

- **`update.sh` no longer hard-codes `:5000` when writing a missing
  `anetbbs-web.service`.** The old logic defaulted gunicorn's bind
  to `:5000` whenever no unit file was present. On installs where
  the sysop had previously been running gunicorn on a different
  port (`:8080`, behind nginx, etc.) and where the unit file wasn't
  named `anetbbs-web.service`, an auto-upgrade would write a fresh
  unit on `:5000` and leave their bookmarked URL pointing at
  whatever else was answering the old port — often the MRC bridge
  on `:8080`. v1.0a2.51's port-selection walks three steps:
  1. Use `WEB_PORT` from `.env` if set.
  2. Otherwise probe `ss -tlnp` for a running gunicorn from this
     install and inherit its bind port.
  3. Otherwise default to `:5000`, walking upward if it's bound by
     something else (mrc-bridge etc.), then write the chosen value
     back into `.env` so future upgrades stay consistent.

- **Preflight checklist: WEB_PORT consistency probe.** New check
  surfaces two regression shapes that auto-update used to ignore:
  - `.env`'s `WEB_PORT` disagrees with what the unit actually binds.
  - The MRC bridge wants the same port the BBS web uses, so visitors
    to the old URL land on chat.
  Both show up red in `/admin/preflight/` with a one-line remediation.

If you ran the v1.0a2.50 auto-upgrade on an install that wasn't on
the default `:5000`, this release will detect it on next launch and
preserve the original port. On the friend's specific BBS that hit
the regression, the immediate fix is either:
- visit `:5000` going forward (BBS lives there now), or
- edit `.env` to set `WEB_PORT=8080` plus move the MRC bridge to a
  free port (e.g., `MRC_BRIDGE_PORT=8081`), then restart.

---

# ANetBBS v1.0a2.50 — security update checker + survivable install log

Changes since v1.0a2.49:

- **Install-log poll survives the gunicorn restart.** The originating
  tab used to silently stop tailing the upgrade log the moment Step 3
  killed anetbbs-web. The poll loop now stays armed through every
  HTTP error, returning with a "web restarting…" badge during the
  outage window and resuming as soon as gunicorn comes back. It
  terminates only when the log shows the wrapper's final `exit N`
  line (or after a 30-minute ceiling). No more "did it work?" toggle
  to a second tab.

- **Daily security update check.** New event handler
  `security_check` scans `apt list --upgradable` and the install
  venv's `pip list --outdated`, tags Ubuntu `-security` packages as
  SECURITY, and writes a JSON report to `logs/security-report.json`.
  A new `/admin/security/` page renders the report with a red banner
  + count of pending security updates if any are pending, calm green
  otherwise. **Run scan now** button on the page fires the handler
  synchronously when you don't want to wait for the daily 04:00 UTC
  tick. Seeded as a default event so fresh installs start scanning
  immediately. Catches nginx / dosbox / openssl / Python and the
  rest of the system-level stack the sysop has to keep patched.

---

# ANetBBS v1.0a2.49 — recent events widget

Changes since v1.0a2.48:

- **"Recent scheduled events" widget on the admin dashboard.** Shows
  the last 5 firings from the `ScheduledEvent` table with name,
  handler, when it ran (absolute + relative), status (ok / fail),
  and duration. Surfaces TW2 maint, weekly VACUUM, log rotation,
  and any sysop-defined events at a glance — failures stand out
  without needing to navigate to `/admin/events/`. Quiet when no
  events have run yet.

This is also the first release shipped end-to-end through the
in-place auto-update path on bbs.a-net.fyi. If you're reading this
in the dashboard "What's new" panel after one-click upgrading from
.48, the plumbing is officially live.

---

# ANetBBS v1.0a2.48 — auto-update survives its own service stop

Changes since v1.0a2.47:

- **Auto-update wrapper now runs in its own systemd scope.** The .47
  attempt to upgrade through `/admin/upgrades/` halted at "Step 3/8:
  Stopping services" — because the upgrade wrapper inherited
  anetbbs-web's cgroup, `systemctl stop anetbbs-web` killed the
  wrapper itself before Step 4 (rsync) could run. The install
  endpoint now spawns the wrapper via
  `sudo systemd-run --scope --collect --quiet`, putting it in a
  transient scope under system.slice. The scope persists through
  the service stop/restart, so update.sh runs end-to-end. Sudoers
  template + install.sh / update.sh sudoers writer extended to
  grant both invocation forms (scope and direct).

- **update.sh Step 5 fast path.** Previous updates unconditionally
  uninstalled + recompiled bcrypt and force-reinstalled cryptography
  on every run. Both packages have Rust/C extensions; if pip can't
  find a prebuilt wheel for the running Python, compiling from source
  takes 5–10 minutes per package on a small VPS — with the web
  service down the whole time. .48 first probes whether the current
  install works (`import bcrypt; bcrypt.hashpw(...)`,
  `Fernet.generate_key()`); if it does, the rebuild is skipped.
  Typical upgrade time drops from minutes to ~30 seconds. Slow
  recovery path retained as a fallback for genuinely-broken
  installs.

---

# ANetBBS v1.0a2.47 — last-upgraded indicator

Changes since v1.0a2.46:

- **"Last upgraded" row in the System Info card on the dashboard.**
  Reads the mtime of the `VERSION` file in the install root (which
  `update.sh` rewrites on every patch) and shows both the absolute
  UTC timestamp + a relative age. Useful when you have several
  ANetBBS installs and need to know which one fell behind on
  updates.

This is the first release shipped via the in-place auto-update path
from /admin/upgrades/ — if you're reading this in the "What's new"
panel after a one-click upgrade from .46, the plumbing is officially
end-to-end functional.

---

# ANetBBS v1.0a2.46 — upgrade-wrapper bash regex fix

Changes since v1.0a2.45:

- **`deploy/run_upgrade.sh` URL validation fix.** The previous wrapper
  used `[[ "$URL" =~ ^https?://[A-Za-z0-9._%/\\:?&=+~\-]+\.tar\.gz$ ]]`
  to validate the download URL. Bash treats `&` inside `[[ =~ ... ]]`
  as a logical-operator token even when it's a literal inside a
  character class — so the wrapper would die at line 33 with:

      run_upgrade.sh: line 33: syntax error in conditional expression:
      unexpected token `&'

  …and exit non-zero before downloading anything. Auto-update via
  `/admin/upgrades/` was completely broken from .43 through .45 as a
  result. Replaced the regex with two `case` pattern checks: one for
  `https?://...tar.gz` scheme+suffix, one for shell-metacharacter
  rejection. Same defensive coverage, no bash quirks.

This release has to be installed manually one last time because the
broken wrapper on .43/.44/.45 can't deliver its own replacement. From
.46 onward, `/admin/upgrades/` should work end-to-end.

---

# ANetBBS v1.0a2.45 — update-available banner

Changes since v1.0a2.44:

- **"Update available" banner on the admin dashboard.** When the
  cached `/api/releases/latest` poll reports a version newer than
  what's running, the dashboard shows a green banner with the version
  delta and a one-click "Review & install" link to `/admin/upgrades/`.
  Reuses the same 30-second TTL cache that page already maintains, so
  the dashboard load doesn't add a network round-trip.
- **Peer Health "Probe now" CSRF fix.** Same root cause as the .44
  upgrades-page fix — the probe fetch was missing `X-CSRFToken`, so
  Flask-WTF replied with HTML and `JSON.parse` blew up with
  "Unexpected token '<', '<!doctype '…". Probe button now sends the
  token + degrades non-JSON responses into a readable error inline.

This is the first release ever shipped via the in-place auto-update
path on bbs.a-net.fyi. If you're reading this in the admin "What's
new" panel after one-click upgrading from .44, the plumbing works.

---

# ANetBBS v1.0a2.44 — scheduled events + bug fixes

Changes since v1.0a2.43:

- **Generic scheduled-events system.** New
  `/admin/events/` page + `ScheduledEvent` table + background
  scheduler thread + handler registry. Bundled handlers: `tw2_maint`,
  `db_vacuum`, `log_rotate`, `shell`, `noop`. Sysop can add/edit/
  enable/disable/run-now any event from the UI. Schedule kinds:
  daily HH:MM, hourly :MM, weekly DOW HH:MM, interval N minutes.
  Replaces the standalone TW2 maint thread from .43 — that handler is
  now a seeded default event row.
- **`/admin/upgrades/` CSRF fix.** Install / Rollback / Check fetch
  calls were missing `X-CSRFToken`, so Flask-WTF rejected them with
  HTML — which crashed `JSON.parse` on the browser side
  ("unexpected character at line 1 column 1"). Added a shared
  `fetchJson` helper that pulls the token from the meta tag and
  surfaces non-JSON responses with the HTTP status + body excerpt.
- **`update.sh` no longer prints "was not running" for legacy units.**
  The pre-merge `anetbbs-telnet` / `anetbbs-ssh` services were
  retired in v1.0a2.10. The stop-loop now skips units that don't
  have a `.service` file on disk, so screenshots from the upgrade
  flow are noise-free.

---

# ANetBBS v1.0a2.43 — Friday public alpha hardening

> A modern multi-node BBS for the FidoNet / Synchronet world.
> Web, telnet, SSH, rlogin, FTP front-ends sharing one user database.
> Federation hub for inter-ANetBBS peer discovery.

The big "public alpha" point release. Changes since v1.0a2.42:

- **`/healthz` JSON endpoint** — public, unauth, returns `{status,
  version, db_ok, uptime_sec, started_at, listeners, host}`. Used by
  `update.sh`'s post-restart probe and by external uptime monitors.
  Replaces an earlier stubby endpoint that hardcoded `version: "v100"`.

- **Pre-update DB snapshot uses `sqlite3 .backup`.** The previous
  `cp(1)` of the live SQLite file could produce a torn snapshot
  during concurrent writes — opens fine, scans broken. Switched to
  `sqlite3 ... .backup` which uses the WAL to capture a consistent
  point-in-time copy. Falls back to `cp` only if sqlite3(1) is
  missing. Also covers `anetbbs_dev.db` (previously only the prod DB
  was captured). Backup dir now includes a `MANIFEST` recording the
  version delta + service user, so rollbacks aren't a guessing game.

- **Post-update HTTP probe.** After the systemd restart, `update.sh`
  curls `/healthz` (then falls back to `/auth/login` on older
  installs without the new endpoint) for up to 30 seconds. If web
  fails to respond with a 200, the same critical-failure rollback
  path that already runs on `systemctl is-active` failures kicks
  in. Catches the "gunicorn is alive but every request 500s" case
  the old probe couldn't see.

- **Rollback drawer in the Updates page.** `/admin/upgrades/` now
  surfaces up to three previous tarballs in `data/releases/` with
  a "Roll back to vX" button. Reuses the same privileged wrapper
  that does forward upgrades — sha256 verified against an on-disk
  computation, not an upstream claim, since the trust anchor for
  rolling back is "the bytes already live on this box."

- **TW2 — Reset Universe button.** Games admin → "Reset TW2
  universe" wipes the per-install JSON DB (`data/sbbs_doors/tw2/db/`)
  plus the legacy in-source-tree path so a botched test or pre-launch
  cleanup is one click instead of a shell session.

- **Server-side door crash log.** `logs/door-errors.log` now collects
  the stack trace + door slug + username on any Synchronet-compat
  door crash. Sysops see breakage without having to wait for users
  to report it.

- **"What's new" panel on the admin dashboard.** Reads RELEASE.md,
  finds the section for the running VERSION, renders inline. So
  whenever you upgrade, the first time you load `/admin/`, you see
  exactly what landed.

---

# ANetBBS v1.0a2.24 — alpha 2

The **second public alpha**, point release v1.0a2.24. Changes since
v1.0a2.23:

- **Echomail import — PATH-loop misfire fix.** The previous loop check
  rejected any inbound message whose PATH kludge contained our address.
  But per FTS-0004 the sending tosser correctly appends the destination
  address to PATH right before shipping, so every legitimate inbound
  message had our address there and got dropped as a "loop." Removed
  the check entirely — real loop detection happens via `msg_id` dedup
  and SEEN-BY-at-forward-time, neither needing PATH inspection. Caps
  off the three-bug TQWnet receive arc (v1.0a2.22, .23, .24). After
  these fixes, %RESCAN on a populated hub successfully imports tens of
  thousands of messages into their proper TQW_* echoes.

Changes since v1.0a2.22:

- **Echomail import — per-message SAVEPOINT.** When the BinkP receive
  finally worked (v1.0a2.22), ~50,000 parsed messages from a TQW
  rescan all rolled back at outer-commit time:

  > "transaction has been rolled back due to a previous exception
  >  during flush. Can't reconnect until invalid transaction is rolled
  >  back."

  One malformed row in a 50k batch was poisoning the session and every
  subsequent insert was silently piling onto the broken transaction.
  Final commit rolled back the entire batch. Fixed by wrapping each
  insert in `with db.session.begin_nested():` so a bad row only rolls
  back its own savepoint; the outer transaction stays valid. Plus:
  length-truncate inbound fields to their column maxes (most common
  cause of `IntegrityError`) and force `db.session.flush()` inside
  the savepoint so constraint failures surface immediately.

Changes since v1.0a2.21:

- **BinkP CLIENT — real fix for ZIP-wrapped mail.** Root cause of the
  entire TQWnet-not-flowing saga. v1.0a2.21 fixed the regex in the
  BinkP LISTENER but the outbound POLLER uses a different file
  (`binkp.py`) which had a far worse bug: file completion was
  detected by looking for `\x00\x00` at the tail — the FTS-0001
  raw-packet end marker. Mystic ships echomail as ZIP-wrapped bundles
  which never end in `\x00\x00`. So 5+ files arrived per poll, none
  were marked complete, none were ACKed, none imported.

  Rewrote `_receive_messages` to parse the byte-count from CMD_FILE
  (`name size mtime offset`) and detect completion by byte-count, then
  dispatch by content: raw FTS-0001 → parse; ZIP → unzip and parse
  each packet member; anything else → stash for the TIC scanner.
  M_GOT ACK now goes back promptly so the hub stops re-queueing.

Changes since v1.0a2.20:

- **BinkP listener — day-of-week bundle extensions.** Mystic hubs
  deliver bundled mail to nodes using FTS-5003 day-of-week extensions
  (`.mo[0-z]` Monday through `.fr[0-z]` Friday through `.su[0-z]`
  Sunday). Our acceptor regex only covered Wednesday (`.we[0-9a-f]`).
  Every other day got silently filed to inbound and ignored.
- **Persistent inbound** — `BINKP_INBOUND_DIR` defaulted to
  `/tmp/binkp-inbound` (tmpfs on most distros), so unrecognized files
  vanished on every service restart. Default is now
  `data/binkp/inbound`.
- **Visible log line for unrecognised files** — INFO-level, so future
  "where did my mail go?" debugging is one `journalctl | grep` away.

Changes since v1.0a2.19:

- **Federation self-registration client.** Completes the federation
  loop. Set `REGISTRY_SELF_REGISTER=true` + `BBS_DOMAIN` + `SYSOP_EMAIL`
  in `.env` and the BBS auto-POSTs `/register` against the hub on
  startup, heartbeats daily, and persists the verify token to
  `data/registry_state.json`. New admin page `/admin/registry/self`
  shows the hub URL, our metadata, last hub response, the verify URL
  (so the sysop can click it without grepping logs), and a "Register
  / Heartbeat Now" button.

Changes since v1.0a2.18:

- **Dialout telnet — real IAC negotiation + raw key reads.** First
  user-filed bug against the pre-alpha. Outbound telnet to another BBS
  rendered as dumb terminal (no ANSI) and single keys (ESC, `*`,
  bot-defense challenges) didn't reach the remote — they got buffered
  until Enter. Rewrote `_proxy` with a minimal telnet IAC state
  machine that announces WILL TTYPE / NAWS / BINARY, responds to peer
  options, sends our terminal type ("ANSI") on demand, and uses
  `session.read_raw(1)` for byte-at-a-time user input. Ctrl+] escape
  (declared but never wired in the old code) now actually works.

Changes since v1.0a2.17:

- **Admin user delete cascade.** `Admin → Users → Delete` returned 500
  because `UserSession.user_id` is NOT NULL but the relationship had no
  cascade — SQLAlchemy tried `UPDATE user_sessions SET user_id=NULL` to
  detach the session before deleting the user, which the constraint
  rejected. Fixed by switching the backref to `db.backref('session',
  uselist=False, cascade='all, delete-orphan')`.

Changes since v1.0a2.16:

- **SYSTAT now sees web users.** Peer BBSes querying our SYSTAT
  (UDP/11) for "who's online" got `No users currently active.` even
  when the BBS was busy. Cause: SYSTAT read only `NodeActivity` (the
  multi-node terminal slot tracker); web users live in `UserSession`.
  Fixed by unioning both sources. Web sessions get synthetic slot names
  `web1`, `web2`, …, and their URL paths are sanitized through the
  same friendly-area-label map as `/who/`.

Changes since v1.0a2.15:

- **anetbbs.lst → BbsDirectoryEntry pull.** Bridges the federation
  registry into the existing inter-BBS IM directory. New module
  `anetbbs/msp/anetbbs_directory.py` pulls `REGISTRY_URL/anetbbs.lst`
  daily and upserts each peer into `BbsDirectoryEntry` with
  `source='anetbbs'`. Extended `bbs_directory` with `sysop`,
  `location`, `software`, `software_version`, `msp_port`,
  `systat_port`, `source`. `/imsg/directory/` shows a blue **ANetBBS**
  badge per row alongside the grey **Synchronet** rows from Vertrauen.

Changes since v1.0a2.14:

- **Federation registry — CSRF hotfix.** v1.0a2.14's
  `POST /registry/api/v1/*` endpoints required a CSRF token, fine for
  browser forms but not for peer ANetBBS hosts calling the API. First
  attempt to register against the live hub returned
  `400 The CSRF token is missing.`. Exempted the registry blueprint
  from CSRF protection at register-time. Admin-side
  `/admin/registry/*` still requires CSRF (admin blueprint).

Changes since v1.0a2.13:

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
