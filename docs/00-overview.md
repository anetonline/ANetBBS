# ANetBBS Overview

ANetBBS is a modern reimagining of a 1990s bulletin board system.
Web-modern in the parts that should be (HTTPS, real-time chat, mobile
layout), and old-school in the parts that should be (telnet/SSH access,
ANSI art, FidoNet echomail, door games).

## Architecture at a glance

```
                         ┌───────────────────────┐
                         │   nginx (HTTPS / 80)  │
                         └──────────┬────────────┘
                                    │
            ┌─────────┬───────┼───────┬───────┬──────┬────────────┐
            ▼         ▼       ▼       ▼       ▼      ▼            ▼
     anetbbs-web   telnet   ssh    rlogin   ftp   binkp   anetbbs-mrc-bridge
     (Flask +      (asyncio (asyncssh) (asyncio (pyftpdlib (asyncio  (aiohttp /
      eventlet,     telnet)            rlogin)  thread)    listener)  WebSocket)
      gunicorn)                  └── all in anetbbs.service ─┘
            │           │           │            │             │
            └───────────┴─────┬─────┴────────────┴─────────────┘
                              │
                       ┌──────▼──────┐
                       │   SQLite     │   ← single shared DB
                       │  /data/      │     all services agree on
                       │  anetbbs.db  │     users, posts, sessions,
                       └──────────────┘     menus, ratios, etc.
```

Each protocol runs as a separate systemd service. They all read the
same `.env`, use the same SQLite DB, and the BBS terminal services
sync their live presence into the same `UserSession` table the web
app uses, so the "who's online" widget shows everyone at once.

## What it can do

- **Message boards** with categories / sub-conferences, threading,
  reactions, drafts, last-read tracking.
- **Echomail** (FidoNet-style) over BinkP and QWK packet networks.
  Per-user QWK offline-reader workflow.
- **File areas** with optional sysop moderation queue, ratios,
  TIC processor for hatched files, shareable expiring links.
- **Door games** — DOS via DOSBox bridge, native scripts, Synchronet
  JS, Mystic Pascal Script, plus generic `exec` for anything else.
- **Image galleries** — browser-native image viewer at `/gallery/`
  with admin CRUD for adding collections and managing files.
- **Chat** — multinode terminal chat between connected sessions,
  IRC bridge, MRC bridge for inter-BBS chat, in-page shoutbox.
- **Real-time sysop control panel** — service start/stop/restart
  from the web, NodeSpy showing what every terminal user is doing.
- **Themes** — pick from 7 built-in or sysop builds custom.
- **Customizable everything** — every menu, hotkey, ANSI screen,
  external program, board category is data-driven.

## Where to go next

- [01 — Installing](01-installing.md)
- [02 — Sysop daily ops](02-sysop-daily-ops.md)
- [03 — Menus and the data-driven engine](03-menus.md)
- [04 — ANSI screens (welcome / goodbye / custom)](04-ansi-screens.md)
- [05 — External programs (doors, weather, AI)](05-external-programs.md)
- [06 — Echomail / FidoNet](06-echomail.md)
- [07 — File areas](07-file-areas.md)
- [08 — Themes + theming](08-themes.md)
- [09 — Multinode + NodeSpy](09-multinode-nodespy.md)
- [10 — Personal web pages](10-personal-pages.md)
- [11 — Spam control (NUV, bot gate, IP bans, word filter)](11-spam-control.md)
- [12 — Upgrading](12-upgrading.md)
- [13 — Image galleries](13-image-galleries.md)
- [14 — Door games](14-door-games.md)
- [15 — Synchronet door compat](15-synchronet-compat.md)
- [16 — RSS reader](16-rss-reader.md)
