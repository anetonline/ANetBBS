# Security defaults & production hardening

This is the alpha. The defaults are designed so a fresh install isn't
trivially compromised, but production deployment requires a few extra
steps. Read this whole file before exposing the BBS to the internet.

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
  - `/auth/register` — 3 attempts / hour / IP
  - `/imsg/send` — 30 messages / hour / user
  - `/api/vote` — 60 votes / min / user
- **Path traversal mitigated** — uploads stored under UUID filenames; the
  `download` route uses `send_from_directory` against the configured
  uploads dir.
- **Per-area file upload permission** — each `FileArea` carries a
  `upload_permission` of `users` / `sysop` / `none`. The upload route
  enforces it before writing.
- **SECRET_KEY guard.** If `SECRET_KEY` is the dev default and the app
  is started in production mode (`FLASK_ENV=production` or
  `config_name='production'`), it raises `RuntimeError` and refuses to
  boot. In development it just logs a loud warning.
- **Session cookie `HttpOnly` + `SameSite=Lax`** by default. Production
  config also sets `Secure` (cookie only sent over HTTPS).
- **Optional virus scan** on uploads if `clamav-daemon` is installed —
  infected files are deleted before the DB row is created.

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
   (`Secure` cookies, stricter SECRET_KEY check).
5. **Pick one privileged-port option** for MSP/SYSTAT (see
   `docs/INSTALL.md` §6). Leaving the default ports unbound silently
   degrades inter-BBS messaging — peers will get `connection refused`.
6. **Run as a non-root user** (the systemd templates already do —
   `User=anetbbs`). Use `setcap` or `AmbientCapabilities` for the
   privileged ports rather than running the whole process as root.
7. **Back up `data/`** — that's where the SQLite DB, uploads,
   configuration, and admin-password-on-first-install all live.

## Known limitations

- **Rate limiter is in-memory and per-process.** Fine for a single
  gunicorn worker. With multiple workers or a multi-host deployment,
  the limiter is effectively per-worker — the user gets `N × workers`
  attempts. For a real multi-worker setup, replace with a Redis-backed
  store (the `rate_limit` decorator is the only call site to change).
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
