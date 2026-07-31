# Quick Start

## Requirements

- Linux (Ubuntu 22.04+ or Debian 12+ recommended)
- Python 3.10+ (3.12 preferred — the installer looks for `python3.12`
  first, falling back to 3.11, 3.10, then plain `python3`)
- ~500 MB disk for app + venv, plus space for `data/`
- Open TCP ports for any protocols you want exposed
  (default: 5000 web, 2233 telnet, 2234 SSH, 513 rlogin,
  21 + 40000-40050 FTP if enabled)

## Fresh install

```bash
# extract the release tarball
tar xzf ANetBBS-v1.0.7.tar.gz
cd ANetBBS-v1.0.7

# run the installer
sudo bash install.sh
```

The wizard asks every question it needs, roughly in this order:

- **Install mode** (`production` / `behind` / `test`) — this is the
  *first* question and changes several other defaults (web port,
  whether nginx+SSL get offered, etc.). `production` = full public BBS
  with HTTPS via Let's Encrypt; `behind` = you already have your own
  reverse proxy/TLS in front; `test` = local/LAN, no domain needed.
- **BBS name + description**
- **System service user** + **install directory**
- **Sysop username** (your own login handle) + **sysop password**
  (leave blank to auto-generate one — it's written to
  `data/admin_password.txt` either way)
- **Domain / hostname** + **web / telnet / SSH ports**
- **Enable Telnet? Enable SSH? Enable nginx reverse proxy?**
- **Install the MRC bridge (inter-BBS chat)?**
- **Enable inter-BBS Instant Messaging (MSP/SYSTAT — privileged ports
  18/11)? Enable Finger (privileged port 79)? Enable BinkP (FidoNet
  inbound mail, port 24554)?**
- *(if nginx enabled)* **Enable SSL via Let's Encrypt?**
- **Optional extras**: DOSBox-staging (DOS door games), dosemu2
  (`door_dosemu` game type — TW2002-style doors needing a real
  FOSSIL/COM1), ClamAV (upload virus scanning), lhasa (.lzh archive
  descriptions), chafa + libsixel-bin (terminal image viewing — RSS
  reader and Image Gallery), Mystic BBS runtime (.mps door support) —
  each its own yes/no, all default **no**.
- **Configure UFW firewall** to open the ports you just enabled?
  (default **no**) — this prompt only appears in `production` mode;
  `test` and `behind` installs skip it (test mode is local-only, and
  `behind` mode assumes your own reverse proxy/firewall already
  handles this).

Number of concurrent terminal nodes (telnet/SSH/rlogin) and echomail
enable/disable are **not** wizard prompts — they default on
(`BBS_NODES=8`, `ECHOMAIL_ENABLED=true`) and are changed later via
`.env` or Admin → Settings, not during install. (Door games have
their own, separate concurrency cap, `GAMES_MAX_NODES=10`, also not a
wizard prompt.)

It then, without any further prompts:

1. Generates a strong random `SECRET_KEY` and writes `.env` (skips
   this if `.env` already exists — re-run with `--force` to
   regenerate it).
2. Creates `data/`, `logs/`, etc.
3. Builds a Python venv and runs `pip install -e .`.
4. Initializes the SQLite database and seeds your admin user.
5. Installs systemd unit files (paths and service user substituted for
   *this* install — no `/opt/anetbbs` hardcoding unless that's where
   you actually installed it) and **enables + starts every service you
   said yes to** — there's no separate manual `systemctl enable --now`
   step needed.
6. Runs a set of post-install health checks and prints a summary.

Visit `http://<your_host>:<web_port>/` (port 5000 by default in
`production` mode, 8080 in `test`/`behind` mode) and log in with the
sysop credentials from the wizard.

## Re-running the wizard

Safe to re-run. It won't touch an existing `.env` — it just skips
regenerating it and prints "already exists — skipping (use `--force`
to overwrite)". Pass `--force` if you actually want it to overwrite
your current config.

## Alternative: the Python installer toolchain

Separate from `install.sh`/`update.sh` above, four console-script
entry points ship with the package itself and become reachable the
moment you `pip install -e .` (e.g. via the manual-install path in
`docs/INSTALL.md`):

- **`anetbbs-install`** (`anetbbs/installer/wizard.py`) — a shorter,
  self-contained interactive Python installer. Asks install directory,
  BBS branding, ports, `BBS_NODES`, echomail on/off, and sysop
  credentials; writes `.env`, creates the venv, runs `pip install -e .`,
  initializes the database + seeds the sysop account, and optionally
  installs systemd units and `/usr/local/bin` command symlinks.
- **`anetbbs-upgrade`** (`anetbbs/installer/upgrade.py`) — point it at a
  release tarball or extracted directory. Backs up `data/`, `.env`, and
  `logs/` to a timestamped directory, rsyncs the new code in, reinstalls
  Python deps, runs schema migrations, restarts services, probes
  `/healthz`, and offers automatic rollback to the backup if the health
  check fails.
- **`anetbbs-symlinks`** / **`anetbbs-cleanup`** (`symlinks.py`,
  `cleanup.py`) — smaller helpers the two wizards above call
  internally; also runnable standalone.

`install.sh` and `update.sh` never call any of these — it's a
completely separate, lighter-weight toolchain. Reach for it for a
minimal or scripted install where the long interactive `install.sh`
prompt sequence (install-mode selection, nginx, optional-dependency
prompts, etc.) is more than you need.

**Known gaps vs. `install.sh`** — worth knowing before you pick this
path:

- No install-mode concept at all (no `production` / `behind` / `test`
  choice) — it always writes `FLASK_ENV=production`.
- No nginx or Let's Encrypt/SSL prompts — TLS termination is entirely
  on you afterward.
- No MSP/SYSTAT, Finger, or BinkP prompts — none of those `.env` vars
  get written and none of the matching systemd units
  (`anetbbs-finger.service`, `anetbbs-binkp.service`) get installed.
  Set those up by hand afterward — see `docs/INSTALL.md` §6, §8, §9.
- No UFW/firewall step.
- `wizard.py` writes `IDLE_TIMEOUT_SECONDS=0` into `.env` — that's
  different from the `Config` class default of `1800` (30 minutes)
  that an `install.sh`/manual install without an explicit override
  gets. `0` means idle terminal sessions are never force-disconnected;
  if you use this installer, that's worth changing on purpose rather
  than by surprise.

If you want everything `install.sh` sets up — nginx, SSL, UFW,
MSP/SYSTAT/Finger/BinkP — use `install.sh`. The Python installer
toolchain is a lighter, scriptable alternative, not a superset of it.

## Putting nginx in front

Copy `deploy/anetbbs-nginx.conf.template` to
`/etc/nginx/sites-available/anetbbs.conf`, edit the `server_name`,
symlink it to `sites-enabled/`, and reload nginx. The template handles:

- HTTPS via certbot
- WebSocket upgrade for SocketIO + the MRC bridge
- `auth_request` gating of the MRC WebSocket so only logged-in users
  can chat

(If you picked `production` mode in the wizard, this is already done
for you.)

## What the install lays down

| Path                         | Purpose                       |
| ---------------------------- | ----------------------------- |
| `/opt/anetbbs/`              | code (default)                |
| `data/`                      | uploads, avatars, SQLite DB   |
| `data/personal_pages/`       | sysop + per-user web pages    |
| `data/admin_password.txt`    | auto-generated sysop password (if you left it blank) |
| `logs/`                      | app logs                      |
| `venv/`                      | Python virtual env            |
| `.env`                       | runtime config (mode 0600)    |

## After install — see the [launch checklist](02-sysop-daily-ops.md)
