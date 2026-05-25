# ANetBBS v1.0a2.75 — Raspberry Pi full install guide

Added `docs/INSTALL-PI.md`, a comprehensive guide for Raspberry Pi 4/5 installs.

## Covered in the guide

- **Hardware**: Pi 4 4GB minimum, Pi 5 8GB recommended; A2-rated SD cards;
  USB SSD for write-heavy `data/` directory
- **OS**: 64-bit Raspberry Pi OS Lite (Bookworm) or Ubuntu 24.04 Server
- **Pre-install**: system packages, ufw rules, static IP
- **Installer prompts**: Pi-specific recommended answers (install to home dir,
  use your own username as service user, etc.)
- **DDNS**: DuckDNS auto-update cron, No-IP, FreeDNS, Dynu; router port
  forwarding table for all BBS ports
- **SSL**: Let's Encrypt via certbot + nginx with DDNS hostname
- **SESSION_COOKIE_SECURE**: explains the default-false behavior and when to
  set it true (only with nginx+SSL)
- **Moving data/ to USB SSD**: full procedure — partition, format, fstab,
  copy, symlink, update .env
- **Service management**: systemctl commands, journalctl log viewing
- **Troubleshooting**: SQLite path error, CSRF cookie error, port connectivity,
  disk space, thermal throttling, low memory / swap
- **Door games on Pi**: what works out of the box vs what needs extra setup
- **Confirmed working configurations** table (Pi 4/5, Raspbian/Ubuntu)
