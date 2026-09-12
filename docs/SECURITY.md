# Security defaults & production hardening

The defaults are designed so a fresh install isn't trivially
compromised, but production deployment requires a few extra steps.
Read this whole file before exposing the BBS to the internet.

## What's enabled by default

- **Random initial admin password.** First start generates a 16-byte
  URL-safe token, hashes it for the `admin` user, writes it plaintext to
  `data/admin_password.txt` (mode `0600`), and prints it once at startup.
  No more `admin/admin123`. **Change it on first login.**
- **CSRF on all WTForms POSTs.** Every `FlaskForm`-backed route gets a
  CSRF token automatically. AJAX endpoints (`/api/vote`, `/imsg/*`)
  read the token from a `<meta name="csrf-token">` tag injected into
  every page.
- **Open-redirect guard on `/auth/login`.** The `?next=` parameter is
  rejected unless it's a same-origin path (`urlparse().netloc == ''`,
  not protocol-relative, not Windows-path-tricks).
- **Rate limits** (sliding window, in-memory):
  - `/auth/login` — sysop-configurable via Admin → IP Bans (default 10
    attempts / 5 min / IP); exceeding it **auto-bans the source IP**
    for a sysop-configurable duration (default 1 hour, 0 = permanent)
  - **Telnet/SSH/rlogin/PETSCII login** — the same IP-ban list and
    auto-ban threshold above apply here too, not just the web login.
    Found missing entirely in a full auth-security audit; every
    terminal transport shares one `UserManager.authenticate()` call, so
    this closes it for all of them in one place, including SSH (whose
    own `validate_password()` always accepts by design, to capture the
    password for the "client sent credentials with the connection"
    convenience flow — it was never a real auth boundary on its own).
  - **FTP login** — same models, its own `ftp_login:<ip>` bucket. Found
    missing in the same audit's follow-up pass over the FTP server; the
    FTP login path also only checked `User.is_active`, never
    `is_locked`/`is_verified` (unlike every other login surface) — a
    locked-out or not-yet-approved account could still fully
    authenticate over FTP. Both fixed.
  - `/auth/forgot` and `/auth/forgot/verify` (10 / 5 min / IP each) —
    the security-question recovery flow. Also found and fixed in the
    same audit: a wrong guess no longer lets the same question be
    retried indefinitely (capped at 5 attempts per recovery session),
    and the flow no longer reveals whether an account exists via its
    redirect target — every submission lands on the same verify page,
    with a random, unanswerable decoy question for an account that
    doesn't exist (or has no security questions on file).
  - `/imsg/send` — 30 messages / hour / user
  - `/api/vote` — 60 votes / min / user
- **`/auth/register` rate limit** (3 attempts / hour / IP) — unlike the
  in-memory limits above, this one is backed by a DB table
  (`RegistrationAttempt`), not the shared `rate_limit` decorator. It
  persists across restarts — the in-memory-only caveat under Known
  limitations below does **not** apply to registration.
- **`X-Forwarded-For` is untrusted by default.** IP bans, country
  blocking, and every rate limiter above key off the real
  `request.remote_addr`, not a client-supplied header — found in a full
  auth-security audit that the header was previously trusted
  unconditionally, letting a direct connection spoof it to dodge a ban
  or (worse) make the login auto-ban land on an arbitrary victim IP
  instead of the attacker's own. If you run behind your own reverse
  proxy (e.g. the nginx config `install.sh` sets up), set
  `TRUST_PROXY_HEADERS=true` in `.env` so the real visitor IP is used
  instead of the proxy's own loopback address — **only enable this if
  Flask is never directly reachable from the internet**, since it tells
  the app to trust whatever the nearest hop claims.
- **Path traversal mitigated** — uploads stored under UUID filenames; the
  `download` route uses `send_from_directory` against the configured
  uploads dir.
- **Per-area file upload permission** — each `FileArea` carries a
  `upload_permission` of `users` / `sysop` / `none`. The upload route
  enforces it before writing. The FTP server now enforces the same
  permission on `DELE`/`RNFR`/`RNTO`/`MKD`/`RMD` too, not just `STOR` —
  found in a full FTP-server audit that regular FTP users had a flat
  read/write grant over their whole session, letting them delete/rename
  inside areas they had no upload permission to.
- **FTP uploads are virus-scanned too**, matching every web upload
  route — found in the same audit that this was the one upload path
  that skipped it entirely.
- **QWK node public-form path traversal closed.** The public,
  unauthenticated network-join application form's `qwk_packet_id` field
  used to accept any 8-character string with no charset check, and that
  value flows straight into the FTP server's per-node home directory
  path once a sysop approves the request — `"../../.."` would have
  escaped `data/qwk-hub/` outward with full read/write/delete
  permission. Now regex-validated at both the public form and the
  approval handler (defense in depth).
- **Message-board access control now enforced on writes and interactions,
  not just reads.** A full audit found `reply_post()` had no board-access
  check at all (any authenticated user could reply into a restricted
  board's thread by guessing a post_id), and the same missing-check
  pattern on `subscribe()`, `react()`, saved-message bookmarking, the
  `/api/vote` endpoints, `@mention` notifications, and `/sitemap.xml`.
  Terminal (telnet/SSH/rlogin/PETSCII) board posting also checked
  neither the board's configured posting level nor a moderator-locked
  thread at all. All now consistently gated the same way the read paths
  already were.
- **SECRET_KEY guard.** If `SECRET_KEY` is the dev default and the app
  is started in production mode (`FLASK_ENV=production` or
  `config_name='production'`), it raises `RuntimeError` and refuses to
  boot. In development it just logs a loud warning.
- **Session cookie `HttpOnly` + `SameSite=Lax`** by default. `Secure`
  (cookie only sent over HTTPS) is **not** set automatically by
  production mode — it defaults to off everywhere, HTTP-only installs
  included, and only turns on when you explicitly set
  `SESSION_COOKIE_SECURE=true` in `.env` (see "What you MUST do for
  production" below). Deploying behind HTTPS without setting this
  leaves the session cookie sendable over a plain HTTP connection too.
- **Optional virus scan** on uploads if `clamav-daemon` is installed —
  infected files are deleted before the DB row is created.
- **Anonymous visitors default to access_level 0.** Found and fixed in a
  full auth-security audit: the shared `evaluate_access()` gate (boards,
  echomail areas, QWK, RSS, file areas — everything with a configurable
  `min_access_level`) fell through to level 10 ("registered") for a
  logged-out visitor instead of 0, silently granting anonymous access to
  anything gated at the standard "registered users only" level.
- **Admin `lock_user()` now refuses to lock the acting admin's own
  account**, matching the self-lock guard `edit_user()`/`delete_user()`/
  `toggle_ban()` already had — found missing specifically from `lock_user()`
  in a security audit; an admin could otherwise lock themselves out with
  no separate account to undo it from.
- **Door-game session ownership is now checked, not just presence.**
  An IDOR found in a security audit let a user interact with another
  user's in-progress door-game session by guessing/supplying its id —
  every session-scoped action now confirms the session actually belongs
  to the requesting user first.
- **Private netmail no longer leaks via the public echomail RSS feed.**
  `/feed/echomail.xml` used to apply only an area's ordinary
  `min_access_level` check, which a NETMAIL-tagged area (QWK-routed
  1-on-1 private mail, not broadcast echomail) typically leaves
  permissive — any visitor meeting that level could read any user's
  private netmail subject/body/sender. NETMAIL-tagged areas are now
  excluded from that feed entirely. See
  [`16-rss-reader.md`](16-rss-reader.md#your-bbss-own-outbound-feeds-feedxml)
  for the full writeup of ANetBBS's outbound feeds.
- **Terminal-output escape-sequence sanitization, closed across ~8
  surfaces in one audit pass.** User-supplied text that ends up echoed
  into another user's or the sysop's terminal — oneliners, MRC chat
  identity fields, wall posts, echomail/netmail sender fields shown in
  the terminal UI, the RSS reader and ebook reader's CP437 rendering,
  ZMODEM upload filenames, PETSCII fields, and IRC nick/channel fields
  — is now stripped of raw ANSI/terminal control sequences before
  display, closing a recurring terminal-escape-injection bug class
  found independently in each of those surfaces (a malicious sender
  field could otherwise rewrite another user's screen, hide/spoof
  text, or manipulate their terminal's title/clipboard depending on the
  client). A CRLF-based real IRC command-injection variant of the same
  bug class was also closed in the MRC↔IRC bridge specifically (a
  crafted MRC-side message could otherwise inject a second raw IRC
  command via an embedded CRLF).
- **Inbound rlogin handshake is now bounded.** The rlogin listener used
  to read the handshake's variable-length fields with no upper bound —
  a connection that never sent the expected terminator could hold a
  slot indefinitely, an unauthenticated denial-of-service. Now capped
  at a sane maximum with a hard timeout.
- **Multinode chat's per-node message queue is now bounded.** Found in
  the same audit pass: a node that stopped reading its own chat output
  (a stalled/hung client) let its queue grow without bound, an
  in-process memory-exhaustion path. Now capped, with the oldest
  buffered lines dropped once a node falls behind rather than the
  queue growing forever.
- **BinkP echo-area subscriptions are isolated per downstream node**,
  as [doc 6 — Echomail](06-echomail.md#areafix--subscription-requests-from-peers)
  already describes — a cross-tenant leak found in a security audit
  let one hub-hosted node's AreaFix subscription change affect another
  node's subscriptions under some conditions. Fixed to match the
  per-node isolation the feature was always meant to have.
- **BinkP inbound file receiving is now bounded per session, not just
  per file.** A single anonymous, unauthenticated peer connection could
  previously offer an unlimited number of files each just under the
  existing per-file size cap, accumulating without any overall limit
  for that session — an in-process memory-exhaustion path reachable
  with zero authentication (an unrecognized peer is accepted as
  ordinary anonymous crash-mail, per real FTN convention, before this
  point is ever reached). Now capped by total session bytes as well.
- **FREQ (file request) rate-limiting is now keyed on the real
  connecting IP**, not a self-reported address field an anonymous peer
  controls — closing a gap where the per-IP throttle could be trivially
  bypassed by claiming a different address on every connection. FREQ
  request parsing is also now bounded by request-line count, matching
  every other peer-suppliable command parser in this codebase.
- **QWK network packet downloads are now size-capped**, matching the
  cap BinkP's own inbound file receiving already had — an admin-
  configured QWK hub's *response* at poll time is still remote,
  foreign data, and was previously buffered fully into memory with no
  limit.
- **Disconnecting a door-game session now works correctly even when it's
  running in a different ANetBBS process than the one you're viewing
  it from.** ANetBBS runs web, telnet, and SSH as separate systemd
  services, each tracking its own live sessions in memory; every admin
  surface that lists sessions to disconnect reads the shared database
  instead, so a sysop routinely disconnects a session actually owned by
  a *different* process. This used to silently fail to kill the real
  subprocess while still marking the session finished and freeing its
  node number for immediate reuse by a new session — found and fixed in
  a security/performance audit; disconnecting now signals the real
  process directly regardless of which service owns it.
- **Door-game per-node working-directory paths are now sanitized**
  against the same class of path-traversal issue found and fixed in
  ANetCraft's own save-path handling — a sysop-templated per-node path
  could otherwise be walked outside its intended directory via
  unsanitized characters in a player's display name.
- **A background network-health thread now has the same start/stop
  guard every sibling background thread in that package already had** —
  found missing from one specific module in a security/performance
  audit; without it, re-initializing the app in one process could spawn
  an extra, un-stoppable daemon thread with no way to tell the
  duplicates apart.
- **Background polling loops (MRC bridge, SYSTAT responder) are now
  protected against a misconfigured near-zero interval** turning them
  into a busy loop that hammers an upstream service, and a real N+1
  query pattern in the SYSTAT responder (reachable by any
  unauthenticated UDP peer) was closed so its per-request cost no
  longer scales with the number of online users.
- **A maintenance tool no longer prints a live door password to its own
  output**, and a user-migration tool's report file (which intentionally
  contains plaintext temporary passwords, by design, for the sysop to
  relay to migrated users) is now created already-locked-down rather
  than world-readable at birth — both found in a security/performance
  audit. `install.sh`/`update.sh` also close a race window where the
  generated sudoers fragment briefly sat world-readable before its
  final permission lockdown, the same class of gap already fixed
  elsewhere in those same scripts for `.env` and the database file.

## What you MUST do for production

1. **Set `SECRET_KEY`.** Generate once, set in your systemd unit's
   `Environment=` or `EnvironmentFile=`:
   ```
   python -c 'import secrets; print(secrets.token_urlsafe(48))'
   ```
2. **Change the admin password** from the random one — and
   `rm data/admin_password.txt` once you've memorized / vaulted it.
3. **Front the web app with TLS.** nginx + Let's Encrypt is the easy
   path. The `deploy/anetbbs-nginx.conf.template` is a starting point.
   Without a TLS terminator, every login posts plaintext over HTTP.
4. **Set `FLASK_ENV=production`** so the prod config kicks in
   (stricter SECRET_KEY check). **Separately, also set
   `SESSION_COOKIE_SECURE=true` in `.env` once you're behind HTTPS** —
   production mode does not turn this on for you.
5. **Pick one privileged-port option** for MSP/SYSTAT (see
   `docs/INSTALL.md` §6). Leaving the default ports unbound silently
   degrades inter-BBS messaging — peers will get `connection refused`.
6. **Run as a non-root user** (the systemd templates already do —
   `User=anetbbs`). Use `setcap` or `AmbientCapabilities` for the
   privileged ports rather than running the whole process as root.
7. **Back up `data/`** — that's where the SQLite DB, uploads,
   configuration, and admin-password-on-first-install all live.

## Known limitations

- **Rate limiter is in-memory and resets on restart.** ANetBBS's web
  app runs as a single eventlet process (`deploy/serve.py` —
  `socketio.run()`, not gunicorn or any multi-worker WSGI server; see
  `docs/00-overview.md`), so there's no `N × workers` bypass to worry
  about. The real caveat: every process restart (deploy, crash,
  `systemctl restart anetbbs-web`) zeroes all counters, and if you
  ever ran more than one instance behind a load balancer — not a
  supported or documented topology today — each instance would keep
  its own independent counters. For durability across restarts,
  replace with a Redis- or DB-backed store (the `rate_limit` decorator
  is the only call site to change). This does not affect
  `/auth/register`, which is already DB-backed and correctly shared
  across restarts (see above).
- **No 2FA.** Single password for both web and terminal logins.
- **No per-account lockout** — the auto-ban above works on the source
  IP, not the targeted username, so a distributed brute-force attempt
  from many IPs against one account isn't slowed by this mechanism.
- **Uploads are not sandboxed beyond ClamAV** if you have it installed.
  Don't run the BBS on a host where executable uploads matter.
- **Synchronet `.js` doors** without real `jsexec` get a Node.js
  shim that is best-effort — a malicious door script can do anything
  Node.js can. Same trust model as any door game: only install ones
  you trust.

## Reporting issues

Security reports: please email the sysop privately rather than open a
public issue.
