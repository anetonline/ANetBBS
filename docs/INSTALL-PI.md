# ANetBBS on Raspberry Pi — Full Install Guide

ANetBBS runs well on Raspberry Pi 3 and later. This guide covers everything
from hardware selection through a running BBS reachable from the internet.

---

## Hardware Recommendations

### Minimum
- **Raspberry Pi 3B/3B+** with **1 GB RAM** — BBS runs great; in-browser DOS games (Doom, Duke Nukem) not recommended (browser emulation is CPU-heavy)
- A2-rated microSD card (SanDisk Extreme Pro 32 GB+ or Samsung Pro Endurance)

### Recommended
- **Raspberry Pi 4** with **4 GB RAM** or better
- **Raspberry Pi 5** with **8 GB RAM** (best performance)
- **USB SSD** for the install dir (see [Moving data/ to a USB SSD](#moving-data-to-a-usb-ssd))
- A2-rated microSD for the OS; USB SSD for data

### Why SSD matters
SQLite does frequent small random writes. A good A2 SD card handles it fine for
a small BBS, but a USB SSD (Samsung T7, WD My Passport SSD) eliminates write
wear and latency spikes under load. The OS can stay on the SD card; only the
ANetBBS install dir needs to be on SSD.

---

## OS Recommendations

Use a **64-bit** OS. ANetBBS requires Python 3.10+ and 64-bit is needed for
full ARM64 performance and compatibility.

| OS | Notes |
|----|-------|
| **Raspberry Pi OS Lite 64-bit (Bookworm)** | Best supported. Python 3.11. Lean, no desktop. |
| **Ubuntu 24.04 Server for Pi** | Python 3.12. Same environment as bbs.a-net.fyi. |
| Raspberry Pi OS Full 64-bit | Works, but the desktop wastes RAM. |

### Flash the OS

Use **Raspberry Pi Imager** (https://www.raspberrypi.com/software/) to flash
the image. In the Imager settings (gear icon), set:
- Hostname (e.g. `noverdu`)
- Username + password
- Wi-Fi or leave blank for Ethernet
- Enable SSH

---

## Pre-Install System Setup

SSH into your Pi and run these before install.sh:

```bash
sudo apt update && sudo apt upgrade -y

# Required system packages
sudo apt install -y python3 python3-pip python3-venv \
    git curl wget nginx certbot python3-certbot-nginx \
    sqlite3 openssl ufw telnet

# Optional but useful
sudo apt install -y htop tmux fail2ban
```

### Enable ufw
```bash
sudo ufw allow ssh
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 2233/tcp   # telnet
sudo ufw allow 2234/tcp   # SSH (BBS)
sudo ufw enable
```

---

## Download ANetBBS

```bash
cd ~
wget https://github.com/anetonline/ANetBBS/releases/latest/download/ANetBBS-v1.0.29.tar.gz
# or check /downloads/ on bbs.a-net.fyi for the latest tarball

tar xzf ANetBBS-v1.0.29.tar.gz
cd ANetBBS-v1.0.29
```

---

## Run the Installer

```bash
sudo bash install.sh
```

### Pi-specific answers for the installer prompts

| Prompt | Pi recommendation |
|--------|-------------------|
| **Mode** | `test` if behind NAT without a domain; `production` if you have a domain + certbot |
| **Install directory** | `/home/<your-username>/anetbbs` (e.g. `/home/pi/anetbbs`) |
| **System service user** | Your username (e.g. `pi`) — avoids permission headaches |
| **Web port** | `5000` (or `80` if not using nginx) |
| **Enable SSL** | Only if you have a real domain pointed at this Pi |
| **Telnet port** | `2233` |
| **SSH port** | `2234` |

> **Install to your home directory, not `/opt/anetbbs`.**  
> `/opt` is owned by root and can cause permission edge cases on Raspbian.
> Using `/home/<user>/anetbbs` means the service user already owns the whole tree.
>
> If you later add nginx (§7) and static assets/MRC fail to load through
> it even though everything looks correctly configured, see the home-
> directory-permissions note in `docs/INSTALL.md`'s Troubleshooting
> section — Raspbian's default home directory permissions (`750`) block
> nginx from traversing in, same as on Ubuntu/Debian.

### Known Python 3.13 / eventlet issue on ARM

If `anetbbs-web` crashes on first boot with
`AttributeError: module 'eventlet.green.thread' has no attribute
'start_joinable_thread'`, it's because piwheels (the Raspberry Pi
prebuilt-wheel mirror) sometimes ships an `eventlet` wheel compiled
before this Python 3.13 compatibility fix landed, even at a version
number that should have it. `update.sh` detects and auto-fixes this on
every upgrade, but a **fresh** `install.sh` run has no equivalent
check yet. If you hit it, rebuild eventlet from source once:

```bash
sudo -u <service-user> /home/<user>/anetbbs/venv/bin/pip install \
    --force-reinstall --no-cache-dir --no-binary eventlet "eventlet>=0.38.0"
sudo systemctl restart anetbbs-web
```

---

## DDNS Setup (for Pis Behind a Home Router)

Most Pis are behind NAT — your router has the public IP, not the Pi. You need:

1. A **Dynamic DNS (DDNS)** hostname that follows your public IP
2. **Port forwarding** on your router

### Free DDNS services

| Service | URL | Notes |
|---------|-----|-------|
| **DuckDNS** | https://www.duckdns.org | Easiest, free, good updater |
| **No-IP** | https://www.noip.com | Free tier, must confirm monthly |
| **FreeDNS** | https://freedns.afraid.org | Many TLDs available |
| **Dynu** | https://www.dynu.com | Free, no nag emails |

### DuckDNS auto-update (recommended)

```bash
mkdir ~/duckdns && cd ~/duckdns

# Replace TOKEN and SUBDOMAIN with yours from duckdns.org
cat > duck.sh << 'EOF'
echo url="https://www.duckdns.org/update?domains=YOURSUBDOMAIN&token=YOURTOKEN&ip=" \
    | curl -k -o ~/duckdns/duck.log -K -
EOF
chmod +x duck.sh

# Run every 5 minutes
(crontab -l 2>/dev/null; echo "*/5 * * * * ~/duckdns/duck.sh >/dev/null 2>&1") | crontab -
```

### Router port forwarding

Log into your router (usually 192.168.1.1 or 192.168.0.1) and forward:

| External port | Internal IP | Internal port | Protocol |
|--------------|-------------|---------------|----------|
| 80 | Pi's IP | 80 | TCP |
| 443 | Pi's IP | 443 | TCP |
| 2233 | Pi's IP | 2233 | TCP |
| 2234 | Pi's IP | 2234 | TCP |

Find your Pi's local IP:
```bash
hostname -I
```

Set a static local IP on your Pi so the forwarding doesn't break:

```bash
# /etc/dhcpcd.conf (Raspbian) or netplan (Ubuntu)
# Raspbian: add to /etc/dhcpcd.conf
echo "
interface eth0
static ip_address=192.168.1.XXX/24
static routers=192.168.1.1
static domain_name_servers=1.1.1.1 8.8.8.8
" | sudo tee -a /etc/dhcpcd.conf
sudo systemctl restart dhcpcd
```

---

## SSL with Let's Encrypt (if you have a real domain)

Once your DDNS hostname resolves to your public IP and port 80 is forwarded:

```bash
sudo certbot --nginx -d yourname.duckdns.org
```

Certbot auto-renews via a systemd timer. Verify:
```bash
sudo certbot renew --dry-run
```

Then in your ANetBBS `.env`:
```bash
SESSION_COOKIE_SECURE=true
```

---

## SESSION_COOKIE_SECURE — Important for HTTP-only setups

If you are **not** using nginx+SSL (running directly on port 5000 over plain HTTP):

- `SESSION_COOKIE_SECURE` **must be `false`** (the default since v1.0a2.72)
- Check your `.env`:

```bash
grep SESSION_COOKIE_SECURE ~/anetbbs/.env
```

If the line is missing or set to `true`, fix it:

```bash
echo "SESSION_COOKIE_SECURE=false" | sudo tee -a ~/anetbbs/.env
sudo systemctl restart anetbbs-web
```

Setting it to `true` on a plain-HTTP site causes the browser to silently drop
the session cookie, which makes every login attempt fail with a CSRF error.

---

## Moving data/ to a USB SSD

The `data/` directory holds your database, uploads, and user files — the parts
that get written most. Moving it to an SSD extends SD card life and improves
performance.

### 1. Prepare the SSD

```bash
# List disks — your SSD is probably /dev/sda
lsblk

# Partition and format (WARNING: destroys existing data on /dev/sda)
sudo fdisk /dev/sda
# Press: n → p → 1 → Enter → Enter → w

sudo mkfs.ext4 /dev/sda1
```

### 2. Mount the SSD

```bash
sudo mkdir -p /mnt/bbsdata

# Get the UUID
sudo blkid /dev/sda1

# Add to /etc/fstab (replace UUID with yours)
echo "UUID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx  /mnt/bbsdata  ext4  defaults,noatime  0  2" \
    | sudo tee -a /etc/fstab

sudo mount -a
sudo chown $(whoami):$(whoami) /mnt/bbsdata
```

### 3. Move the data directory

Stop the BBS first:
```bash
sudo systemctl stop anetbbs-web anetbbs anetbbs-mrc-bridge 2>/dev/null; true
```

Copy and swap:
```bash
INSTALL_DIR=~/anetbbs   # adjust if different

cp -a "$INSTALL_DIR/data" /mnt/bbsdata/
mv "$INSTALL_DIR/data" "$INSTALL_DIR/data.bak"
ln -s /mnt/bbsdata/data "$INSTALL_DIR/data"
```

Verify the symlink:
```bash
ls -la "$INSTALL_DIR/data"
# should show: data -> /mnt/bbsdata/data
```

### 4. Update DATABASE_URL in .env

The symlink from step 3 is what actually relocates `data/` — there's
no `DATA_DIR` environment variable that does anything (it's hardcoded
relative to the install directory), so don't bother setting one.
`DATABASE_URL` **is** read from the environment and matters here,
since it's an absolute path baked in at install time:

```bash
cat >> "$INSTALL_DIR/.env" << EOF
DATABASE_URL=sqlite:////mnt/bbsdata/data/anetbbs.db
EOF
```

### 5. Restart and verify

```bash
sudo systemctl start anetbbs-web anetbbs
sudo systemctl status anetbbs-web anetbbs
```

Log in and confirm everything works, then remove the backup:
```bash
rm -rf "$INSTALL_DIR/data.bak"
```

---

## Updating ANetBBS on Pi

```bash
cd ~
wget https://github.com/anetonline/ANetBBS/releases/latest/download/ANetBBS-v1.0.29.tar.gz
tar xzf ANetBBS-v1.0.29.tar.gz
sudo bash ANetBBS-v1.0.29/update.sh
```

`update.sh` auto-detects the running install, backs up your data, syncs the
new code, runs DB migrations, and restarts services. Your `.env`, database,
uploads, and user files are preserved.

---

## Service Management

```bash
# Status of all ANetBBS services
sudo systemctl status anetbbs-web anetbbs

# Restart everything
sudo systemctl restart anetbbs-web anetbbs

# View live logs
sudo journalctl -u anetbbs-web -f
sudo journalctl -u anetbbs -f

# View last 100 lines
sudo journalctl -u anetbbs-web -n 100
sudo journalctl -u anetbbs -n 100
```

---

## Troubleshooting

### "unable to open database file" on telnet/SSH

Fixed in v1.0a2.74. If on an older version, add `DATABASE_URL` to `.env`:

```bash
INSTALL_DIR=~/anetbbs   # adjust if different
echo "DATABASE_URL=sqlite:///${INSTALL_DIR}/data/anetbbs.db" | sudo tee -a "$INSTALL_DIR/.env"
sudo systemctl restart anetbbs
```

### "The CSRF session token is missing" on web login

Your site is running over plain HTTP but `SESSION_COOKIE_SECURE=true`. Fixed by
default in v1.0a2.72. On older versions:

```bash
echo "SESSION_COOKIE_SECURE=false" | sudo tee -a ~/anetbbs/.env
sudo systemctl restart anetbbs-web
```

### Web interface loads but telnet/SSH won't connect

Check the firewall and that the terminal service is running:
```bash
sudo ufw status
sudo systemctl status anetbbs
sudo journalctl -u anetbbs -n 50
```

### Out of disk space on SD card

Move `data/` to the USB SSD (see above). Check usage:
```bash
df -h
du -sh ~/anetbbs/data/*/
```

### Pi runs hot / throttles

Add a heatsink and fan. Check throttling:
```bash
vcgencmd get_throttled
# 0x0 = no throttling, anything else = problem
```

### Low memory warnings

Add swap (Pi 5 shouldn't need this with 8 GB, but Pi 4 4 GB might):
```bash
sudo dphys-swapfile swapoff
sudo sed -i 's/CONF_SWAPSIZE=100/CONF_SWAPSIZE=1024/' /etc/dphys-swapfile
sudo dphys-swapfile setup
sudo dphys-swapfile swapon
```

---

## Door Games on Pi

- **In-browser DOS games** (DOOM, Duke3D via EmulatorJS): run in the user's browser —
  no server-side binary needed. On Pi 3, these may not run well in a browser on the Pi
  itself; users connecting from a PC browser will have no issues.
- **LORD** (Synchronet JS via Node.js): works on Pi 3+ ARM.
- **DOSBox doors**: DOSBox-X has ARM builds — install from the DOSBox-X releases
  page and set `DOSBOX_PATH` in `.env`.
- **Wine + door32 .exe doors**: possible with box86/box64, but a project for
  advanced sysops.

---

## Confirmed Working Configurations

| Hardware | OS | Python | Status |
|----------|-----|--------|--------|
| Pi 5 8 GB | Ubuntu 24.04 Server 64-bit | 3.12 | ✅ Fully confirmed |
| Pi 5 8 GB | Raspberry Pi OS Lite 64-bit (Bookworm) | 3.11 | ✅ Confirmed |
| Pi 4 8 GB | Ubuntu 22.04 Server 64-bit | 3.10 | ✅ Confirmed |
| Pi 4 4 GB | Raspberry Pi OS Lite 64-bit (Bookworm) | 3.11 | ✅ Works, tight on RAM |
| Pi 4 4 GB | Ubuntu 24.04 Server 64-bit | 3.12 | ✅ Confirmed |
| Pi 3B / 3B+ | Raspberry Pi OS Lite 32/64-bit | 3.11 | ✅ Works great — BBS runs fast; in-browser DOS games not confirmed |
| Pi 2 / earlier | any | — | ⚠ Not tested |
