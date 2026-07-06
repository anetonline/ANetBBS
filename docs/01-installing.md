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
tar xzf ANetBBS-v1.0b2.NNN.tar.gz
cd ANetBBS-v1.0b2.NNN

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
- **Optional extras**: DOSBox-staging (DOS door games), ClamAV (upload
  virus scanning), lhasa (.lzh archive descriptions), libsixel-bin
  (sixel images in the terminal RSS reader), Mystic BBS runtime (.mps
  door support) — each its own yes/no, all default **no**.
- **Configure UFW firewall** to open the ports you just enabled?
  (default **no**)

Number of concurrent terminal nodes and echomail enable/disable are
**not** wizard prompts — they default on (`GAMES_MAX_NODES=10`,
`ECHOMAIL_ENABLED=true`) and are changed later via `.env` or
Admin → Settings, not during install.

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
| `logs/`                      | gunicorn + app logs           |
| `venv/`                      | Python virtual env            |
| `.env`                       | runtime config (mode 0600)    |

## After install — see the [launch checklist](02-sysop-daily-ops.md)
