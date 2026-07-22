# ANetBBS -- Deployment Guide

## Quick Start (Ubuntu / Debian)

Run the automated installer from the repository root:

```bash
bash install.sh
```

This will install all dependencies, create a virtual environment, set up
systemd services, and generate an nginx config with optional SSL.

---

## Manual Deployment

### 1. System dependencies

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip nodejs npm nginx certbot python3-certbot-nginx dosbox
```

### 2. Create system user and directory

```bash
sudo useradd -r -d /opt/anetbbs -s /usr/sbin/nologin anetbbs
sudo mkdir -p /opt/anetbbs
sudo chown anetbbs:anetbbs /opt/anetbbs
```

### 3. Clone repository and install

```bash
cd /opt/anetbbs
git clone https://github.com/anetonline/anetbbs .
python3 -m venv venv
source venv/bin/activate
pip install -e .
```

### 4. Configure environment

```bash
cp .env.example .env
# Edit .env with your BBS name, domain, secret key, etc.
nano .env
```

**Generate a secure SECRET_KEY:**
```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

**Generate SSH host key** (or let the server auto-generate on first run):
```bash
ssh-keygen -t rsa -b 2048 -f data/ssh_host_key -N ""
```

### 5. Initialise database

```bash
source venv/bin/activate
flask db upgrade  # or: python -c "from anetbbs.web_app import create_app; app = create_app(); ..."
# The app auto-creates tables on first run via db.create_all()
```

### 6. Install systemd services

```bash
sudo cp deploy/anetbbs-web.service    /etc/systemd/system/
sudo cp deploy/anetbbs.service        /etc/systemd/system/   # telnet + SSH + rlogin + PETSCII, one process
sudo cp deploy/anetbbs-finger.service /etc/systemd/system/   # optional: RFC 1288 Finger
sudo systemctl daemon-reload
sudo systemctl enable  anetbbs-web anetbbs anetbbs-finger
sudo systemctl start   anetbbs-web anetbbs anetbbs-finger
```

**Note:** `anetbbs.service` is a single process that owns telnet, SSH,
rlogin, and PETSCII -- which of those actually start is controlled
entirely by `TELNET_ENABLED`/`SSH_ENABLED`/`RLOGIN_ENABLED`/
`PETSCII40_ENABLED`/`PETSCII80_ENABLED` in `.env`, not by separate
services. Older releases shipped `anetbbs-telnet.service`/
`anetbbs-ssh.service` as two independent units; those were merged into
`anetbbs.service` because Ubuntu systemd's `EnvironmentFile` directive
wins over per-unit `Environment=` overrides (the opposite of the
documented precedence), so the two split units couldn't reliably
override `.env` and ended up fighting each other for ports. If you're
upgrading an install that still has the old split units, stop/disable/
remove them and switch to `anetbbs.service` instead.

### 7. Configure nginx

```bash
sudo cp deploy/anetbbs-nginx.conf.template /etc/nginx/sites-available/anetbbs
sudo sed -i 's/DOMAIN_NAME/yourdomain.com/g' /etc/nginx/sites-available/anetbbs
sudo ln -sf /etc/nginx/sites-available/anetbbs /etc/nginx/sites-enabled/anetbbs
sudo nginx -t && sudo systemctl reload nginx
```

**Optional — obtain SSL certificate:**
```bash
sudo certbot --nginx -d yourdomain.com
```

---

## Production gunicorn command

The web service uses gunicorn with the eventlet worker for full WebSocket support:

```bash
gunicorn --worker-class eventlet -w 1 -b 0.0.0.0:5000 deploy.wsgi_wrapper:app
```

**Important:** Always use `-w 1` (single worker) with eventlet for correct Socket.IO behaviour.

---

## Service overview

| Service              | Port          | Description                                |
|----------------------|---------------|---------------------------------------------|
| anetbbs-web          | 5000          | Flask web app (via gunicorn+eventlet)        |
| anetbbs              | 2233/2234/513/6400/6401 | Telnet, SSH, rlogin, PETSCII (40/80-col) -- one process, each protocol individually enabled in `.env` |
| anetbbs-finger       | 79            | RFC 1288 Finger (privileged port)            |
| nginx                | 80/443        | Reverse proxy + SSL termination              |
| MRC bridge (optional)| 8080          | MRC chat bridge                              |

---

## Environment variables

See `.env.example` in the repository root for a full list of configurable
environment variables including SSH, rlogin, games paths, echomail, and MRC
bridge settings.

---

## Uninstall

```bash
sudo systemctl stop  anetbbs-web anetbbs anetbbs-finger
sudo systemctl disable anetbbs-web anetbbs anetbbs-finger
sudo rm /etc/systemd/system/anetbbs*.service
sudo systemctl daemon-reload
sudo rm -rf /opt/anetbbs
sudo userdel anetbbs
```
