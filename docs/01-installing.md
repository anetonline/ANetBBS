# Installing ANetBBS

## Requirements

- Linux (Ubuntu 22.04+ or Debian 12+ recommended)
- Python 3.10+
- ~500 MB disk for app + venv, plus space for `data/`
- Open TCP ports for any protocols you want exposed
  (default: 5000 web, 2233 telnet, 2234 SSH, 513 rlogin,
  21 + 40000-40050 FTP if enabled)

## Fresh install

```
# extract the release tarball somewhere temporary
tar xzf anetbbs-rebuilt-v120.tar.gz
cd anetbbs-rebuilt

# kick off the wizard
sudo python3 -m anetbbs.installer.wizard
```

The wizard asks every question it needs:

- **BBS name + description + sysop name + email**
- **Public domain** (or leave blank if just a LAN box)
- **Web / telnet / SSH / rlogin ports + which to enable**
- **Number of concurrent terminal nodes** (1–100)
- **Echomail enable/disable**
- **Admin username + password**

It then:

1. Generates a strong random `SECRET_KEY` and writes `.env`.
2. Creates `data/`, `logs/`, etc.
3. Builds a Python venv and runs `pip install -e`.
4. Initializes the SQLite database and seeds your admin user.
5. Optionally installs systemd unit files (with paths and user
   replaced for *this* install — no `/opt/anetbbs` hardcoding).

When it's done you start the services:

```
sudo systemctl daemon-reload
sudo systemctl enable --now anetbbs-web anetbbs-telnet anetbbs-ssh
```

Visit `http://<your_host>:5000/` and log in.

## Re-running the wizard

Safe to re-run — it won't clobber an existing `.env` without an
explicit "yes" prompt. Use it to redo specific steps after manual
poking.

## Putting nginx in front

Copy `deploy/anetbbs-nginx.conf.template` to
`/etc/nginx/sites-available/anetbbs.conf`, edit the `server_name`,
symlink it to `sites-enabled/`, and reload nginx. The template handles:

- HTTPS via certbot
- WebSocket upgrade for SocketIO + the MRC bridge
- `auth_request` gating of the MRC WebSocket so only logged-in users
  can chat

## What the install lays down

| Path                         | Purpose                       |
| ---------------------------- | ----------------------------- |
| `/home/stingray/anetbbs/`    | code (default)                |
| `data/`                      | uploads, avatars, SQLite DB   |
| `data/personal_pages/`       | sysop + per-user web pages    |
| `data/file-queue/`           | pending file uploads          |
| `logs/`                      | gunicorn + app logs           |
| `venv/`                      | Python virtual env            |
| `.env`                       | runtime config (mode 0600)    |

## After install — see the [launch checklist](02-sysop-daily-ops.md)
