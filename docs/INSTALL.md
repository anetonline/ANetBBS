# Full Install Guide

Tested on Debian 12 / Ubuntu 22.04+. Should work on any Linux with Python ≥3.10.

## 1. System dependencies

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip python3-dev \
                    build-essential libffi-dev git rsync curl \
                    openssh-client lrzsz
```

(`rsync`/`curl`/`openssh-client` are used by the update flow;
`lrzsz` provides X/Y/Zmodem for terminal file transfers.)

Optional, but unlock features:

```bash
# DOS doors via DOSBox (any of these — auto-detected)
sudo apt install -y dosbox-staging       # preferred
# or: sudo apt install -y dosbox-x
# or: sudo apt install -y dosbox

# Synchronet .js doors (full compatibility)
# Install Synchronet itself: see https://wiki.synchro.net/install:nix
# Set SBBSEXEC to the dir containing jsexec, or symlink jsexec into PATH.

# Node.js fallback for Synchronet doors when jsexec is unavailable
sudo apt install -y nodejs

# Clamav for upload virus scanning
sudo apt install -y clamav clamav-daemon
sudo systemctl enable --now clamav-freshclam clamav-daemon

# LHA archive description extraction
sudo apt install -y lhasa

# Sixel images in terminal RSS reader (SyncTERM, IcyTERM, foot, mlterm, etc.)
# Provides img2sixel — without it the terminal RSS reader is text-only.
sudo apt install -y libsixel-bin
```

## 2. Get the code + Python deps

```bash
sudo mkdir -p /opt/anetbbs
sudo chown $USER /opt/anetbbs
cd /opt/anetbbs
git clone <your-repo-url> .            # or extract the release tarball here
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -e .
```

Optional Python extras (for full archive support in the FILE_ID.DIZ extractor):

```bash
pip install py7zr rarfile
```

## 3. Configure

Generate a real secret and set it (the app refuses to boot in production
mode without one):

```bash
export SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
```

Other useful env vars (defaults shown):

```bash
export BBS_NAME="My Cool BBS"
export WEB_PORT=5000
export TELNET_PORT=2233
export SSH_PORT=2234
export MSP_PORT=18              # privileged
export SYSTAT_PORT=11           # privileged (UDP)
export DATABASE_URL="sqlite:////opt/anetbbs/data/anetbbs.db"
```

## 4. First start

```bash
cd /opt/anetbbs
source venv/bin/activate
anetbbs-web
```

Watch the log for:

```
INITIAL ADMIN ACCOUNT CREATED
  username: admin
  password: <random>
  also written to: /opt/anetbbs/data/admin_password.txt
CHANGE THIS PASSWORD on first login!
```

Browse to `http://<host>:5000/`, log in as `admin`, change the password
under your profile, then configure echomail / file areas / doors.

## 5. Run as a systemd service

The unit templates in `deploy/` hardcode `User=anetbbs`/`/opt/anetbbs` —
create that dedicated system account and hand ownership over to it
before installing them (you built everything as your own login in
steps 2-4, which is fine, but the service itself shouldn't run as
your personal account):

```bash
sudo useradd -r -d /opt/anetbbs -s /usr/sbin/nologin anetbbs
sudo chown -R anetbbs:anetbbs /opt/anetbbs
```

Then install the unit templates — `anetbbs.service` is the unified
process for telnet/SSH/rlogin/FTP (which of those actually start is
driven by the `*_ENABLED` flags in `.env`, not by which unit you
install):

```bash
sudo cp deploy/anetbbs-web.service /etc/systemd/system/
sudo cp deploy/anetbbs.service /etc/systemd/system/

sudo systemctl daemon-reload
sudo systemctl enable --now anetbbs-web anetbbs
journalctl -u anetbbs-web -f
```

(Older versions of this project shipped split `anetbbs-telnet.service`/
`anetbbs-ssh.service` units — those templates still exist in `deploy/`
for reference, but are legacy: they fought each other for ports and
are replaced by the unified `anetbbs.service` above. Don't install
them on a fresh setup.)

If you're enabling Finger and/or BinkP (BinkP/FidoNet setup is covered
in §8 below; Finger is a simple RFC 1288 per-user info query service,
see `docs/PORTS.md`), they run as their own separate systemd units,
not part of `anetbbs.service`:

```bash
# Finger (RFC 1288) — only if you plan to enable it
sudo cp deploy/anetbbs-finger.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now anetbbs-finger

# BinkP (FidoNet inbound mail) — only if you're joining a FidoNet-style network
sudo cp deploy/anetbbs-binkp.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now anetbbs-binkp
```

Both templates already carry `AmbientCapabilities=CAP_NET_BIND_SERVICE`
where needed (Finger binds the privileged port 79) so they don't need
to run as root.

## 6. Privileged ports (MSP/SYSTAT)

Ports < 1024 require root or a capability. Pick **one**:

### A. setcap on the python binary (simplest)

```bash
sudo setcap 'cap_net_bind_service=+ep' \
    /opt/anetbbs/venv/bin/python3
sudo systemctl restart anetbbs-web
```

### B. systemd AmbientCapabilities (cleanest)

Edit `/etc/systemd/system/anetbbs-web.service`, add inside `[Service]`:

```
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
```

```bash
sudo systemctl daemon-reload
sudo systemctl restart anetbbs-web
```

### C. High port + iptables redirect

```bash
export MSP_PORT=1118
export SYSTAT_PORT=1011
sudo iptables -t nat -A PREROUTING -p tcp --dport 18 -j REDIRECT --to-port 1118
sudo iptables -t nat -A PREROUTING -p udp --dport 11 -j REDIRECT --to-port 1011
# Persist with `iptables-save | sudo tee /etc/iptables/rules.v4` (debian) or netfilter-persistent
```

Verify it's working:

```bash
sudo ss -ltnp | grep ':18 '       # TCP listener
sudo ss -lunp | grep ':11 '       # UDP listener
```

## 7. nginx reverse proxy (recommended for production)

`deploy/anetbbs-nginx.conf.template` is a starting point — copy to
`/etc/nginx/sites-available/anetbbs`, edit `server_name` and the cert
paths, symlink to `sites-enabled`, `nginx -t && systemctl reload nginx`.
This puts TLS in front of the Flask app and isolates the web socket from
the privileged ports.

**If your install directory isn't `/opt/anetbbs`**, also edit the
`location /static/ { alias ... }` line in the copied config — it's
hardcoded to `/opt/anetbbs/anetbbs/static/` in the template (`install.sh`
substitutes this automatically when it writes nginx's config for you;
only a manual template copy needs the edit).

## 8. Inbound BinkP / FTN

If you participate in FidoNet, install + enable `anetbbs-binkp.service`
(see §5 above), forward TCP `24554` (BinkP), and put your linked node
configs in `/admin/echomail/networks`. See `docs/06-echomail.md`.

## 9. FTP file-area access (optional)

FTP isn't a separate systemd unit — it runs as a background thread
inside the same unified `anetbbs.service` process as telnet/SSH/rlogin
(see `anetbbs/main.py` / `anetbbs/ftp/server.py`), gated by
`FTP_ENABLED`. To turn it on:

```bash
export FTP_ENABLED=true
export FTP_PORT=21               # privileged — see §6 for the setcap/
                                  # AmbientCapabilities/iptables options.
                                  # NOTE: unlike MSP/SYSTAT, FTP runs in
                                  # anetbbs.service, not anetbbs-web.service
                                  # — if you use the AmbientCapabilities
                                  # option (§6.B), edit anetbbs.service.
export FTP_PASV_PORTS=40000-40050  # passive-mode data channel range —
                                    # open this range on your firewall too
export FTP_ANON_ENABLED=true     # read-only anonymous access to public areas
export FTP_ROOT_DIR=data/ftp_root  # virtual FTP tree, rebuilt on every start
export FTP_BANNER="ANetBBS FTP — file areas"
```

Optional FTPS (`AUTH TLS`) — set both to enable, reusing the same cert
nginx uses:

```bash
export FTP_TLS_CERTFILE=/etc/letsencrypt/live/yourdomain/fullchain.pem
export FTP_TLS_KEYFILE=/etc/letsencrypt/live/yourdomain/privkey.pem
```

Put these in `.env` (not just your shell) so they survive a service
restart, then `sudo systemctl restart anetbbs`. `pyftpdlib>=2.0.0` is
already a normal dependency (installed by `pip install -e .` in step
2) — if it's missing, FTP logs a warning and stays disabled rather
than crashing the rest of the BBS. See `docs/PORTS.md` for the full
port table and firewall rules.

## 10. PETSCII (Commodore 64/128) terminal support (optional)

Dedicated telnet listener(s) for real C64/128 hardware and PETSCII
terminal emulators (SyncTERM's C64 mode, Novaterm, CCGMS, 64NIC+).
Runs as part of the same unified `anetbbs.service` process as
telnet/SSH/rlogin (see `anetbbs/main.py` / `anetbbs/core/petscii_server.py`),
not a separate systemd unit. Off by default — two independent toggles,
one per screen width, matching Synchronet's own "40 Column PETSCII
Support" / "80 Column PETSCII Support" convention:

```bash
export PETSCII40_ENABLED=true
export PETSCII40_PORT=6400
export PETSCII80_ENABLED=true
export PETSCII80_PORT=6401
```

Each port is fixed at one width, unconditionally — real C64 telnet
clients mostly don't announce themselves usefully via telnet TTYPE
negotiation, so there's no auto-detect; users just connect to
whichever port matches their screen. This is a hand-built plain-text
rendering path (no ANSI/cursor-addressing at all, since PETSCII isn't
ANSI) covering message boards, echomail, private messages, file
browsing + XMODEM download, who's-online, profile, and one built-in
game (Number Guessing). Sysops can also build fully custom PETSCII
menus at `/admin/petscii-menus/` — separate from the ANSI custom-menu
system, since most ANSI actions (art, sixel, most doors) have no
PETSCII equivalent. See `docs/25-petscii.md` for the full rundown.

Put these in `.env` so they survive a restart, then `sudo systemctl
restart anetbbs`.

## Troubleshooting

- **MRC (or other static assets) 404/fails to load through nginx, but
  works fine going straight to the Flask port** and the `/static/`
  `alias` path and file permissions all look correct → if your install
  directory is under a user's home directory (e.g.
  `/home/sysopname/anetbbs` instead of `/opt/anetbbs`), Ubuntu/Debian
  gives new home directories `750` permissions by default — that blocks
  nginx's worker user from even traversing into the home directory to
  reach the static files, regardless of what the files themselves are
  set to. Root-caused live against a real sysop's broken MRC web
  client. Fix:
  ```bash
  sudo chmod o+x /home/sysopname
  ```
  (Only adds "can traverse" for others, not "can list/read" — nginx
  doesn't need more than that to serve files under it.)
- **`MSP: cannot bind ... Permission denied`** in bbs.log → see step 6.
- **`SECRET_KEY is the dev default`** warning → set `SECRET_KEY` env var
  (or `RuntimeError` will hit you in production mode).
- **`/admin/echomail/...` returns 500** after upgrading → restart the web
  service so the auto-migration adds new columns.
- **Synchronet door fails silently** → check the log for the door child's
  stderr; if no jsexec is found, the Node.js shim runs but doesn't cover
  every Synchronet API. Install Synchronet for full compat.
- **BotWars / RDQ3 fails with `EACCES: permission denied` reading
  `sbbs_stubs/sbbsdefs.js`** or writing a save file → service user
  needs read/write group access. `install.sh` sets this on every run;
  if you used a manual rsync deploy that reset the perms, run:
  ```bash
  sudo chmod -R g+rX,o+rX /opt/anetbbs/anetbbs/games/sbbs_stubs
  sudo chmod -R g+rwX /opt/anetbbs/doors
  ```
- **`bbs.log` PermissionError** → service user needs write access to the
  install dir. `install.sh` `chown`s on every run; if you re-pointed the
  unit file at a different install dir, mirror the perms there.
- **Web service stuck in restart loop with `EADDRINUSE` on :5000** →
  an old gunicorn worker is leaking past the master. Our systemd unit
  ships with `KillMode=mixed` to prevent this, but if you adopted an
  older unit file, add `KillMode=mixed` and `RestartSec=10` to
  `[Service]` and `daemon-reload`.
- **MRC `<no name>` in Synchronet's IM display** → Synchronet IDENTs
  (RFC 1413) the sender to look up "real name". ANetBBS doesn't ship
  an identd; this is a known cosmetic-only limitation.
- **nginx returns a blank page / 502, its own error log shows
  `connect() to 127.0.0.1:5000 failed (13: Permission denied)`
  repeating for every request** even though `nginx -t` passes clean and
  the service is "active (running)" → SELinux enforcing mode (the
  default on Fedora/RHEL/CentOS) blocks nginx from making outbound
  connections to backend ports unless explicitly allowed. `install.sh`/
  `update.sh` set this automatically, but if you set up nginx manually,
  or `setsebool` wasn't installed at the time, fix it directly:
  ```bash
  sudo setsebool -P httpd_can_network_connect 1
  ```
  (needs `policycoreutils-python-utils` on Fedora/RHEL if `setsebool`
  itself is missing).
- **BBS info fields (telnet/ssh/website/description/sysop) never show
  up when other MRC clients look this BBS up** (`/bbses` + `/info <n>`
  on another client), even with a correctly-filled-in
  `mrc/bridge/config.json` and zero errors anywhere → check whether the
  MRC bridge's systemd unit sets a custom `MRC_BRIDGE_CONFIG`
  environment variable pointing at a different path than the one
  you're editing (`systemctl show anetbbs-mrc-bridge -p Environment`).
  If it does, edit that file instead — the running service never reads
  the default path once an override is in place. More generally: if a
  config file looks correct and nothing is logging an error, but the
  feature still doesn't reflect your changes, suspect an environment
  variable silently redirecting where that service actually reads its
  config from.
- **On a Raspberry Pi (or other low-RAM board), something reports
  "disk full" even though `df -h /` shows plenty of free space** →
  check `df -h /tmp` separately. On many Pi images `/tmp` is a small
  RAM-backed `tmpfs` (often under 500MB, sized off available memory),
  completely separate from the real disk `/` lives on — filling it up
  has nothing to do with how much storage the SD card/USB drive
  actually has left.
- **FTPS (`AUTH TLS`) worked when you set it up, then stopped working
  weeks later** with no config change on your end → certbot resets
  `/etc/letsencrypt/archive/` to `0700 root:root` on every certificate
  renewal by default, which revokes the service user's read access to
  the cert/key it was using. `install.sh` (when it obtains a cert via
  certbot) and `update.sh` (whenever `FTP_TLS_CERTFILE` in `.env`
  points at `/etc/letsencrypt/...`) both install a renewal hook
  (`/etc/letsencrypt/renewal-hooks/deploy/anetbbs-ssl-cert-perms.sh`)
  that restores the correct permissions after every renewal, so this
  should now be handled automatically whether or not FTPS was already
  turned on at install time. If you're on an install that predates
  this and hit the problem, re-run `sudo bash update.sh` to get the
  hook installed retroactively.
