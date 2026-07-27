"""Seed initial wiki content for a fresh ANetBBS install.

Idempotent — a page is only created if its slug doesn't already exist.
Each entry creates the page AND a `r1` revision so the history view
isn't empty on day 1.

To re-seed an existing install with brand-new content for any of these
pages, delete or rename the existing slug first.
"""
from datetime import datetime
from ..models import db, WikiPage, WikiRevision


SEED = [
    # ------------------------------------------------------------------
    ('home', 'Welcome to the ANetBBS Wiki', """
# Welcome to the {bbs_name} Wiki

This is the community documentation for **ANetBBS** — the modern,
multi-protocol Bulletin Board System that powers this site.

The wiki is a place where users, sysops, and curious passers-by can
read about how the BBS works, look up technical details, and (if
they're logged in) help edit and expand the docs.

## Start here

- [[Getting Started]] — connect to the BBS for the first time
- [[Features]] — what ANetBBS can do
- [[FAQ]] — frequently asked questions

## Connecting

ANetBBS speaks every classic BBS protocol plus a modern web UI:

- [[Web Access]] — the easy path, in your browser
- [[Telnet]] — classic terminal access on TCP 2233
- [[SSH]] — secure terminal access on TCP 2234
- [[Rlogin]] — game-server style auto-login on TCP 513 (off by
  default — sysop opts in)

## What's inside

- [[Message Boards]] — local discussion forums
- [[Echomail]] — FidoNet-style global mail (BinkP networking)
- [[ANotherNetwork]] — the echomail/QWK network this BBS ships
  pre-joined to
- [[Private Messages]] — one-on-one mail between users
- [[Instant Messages]] — Inter-BBS instant messaging via MSP
- [[Files]] — the file library
- [[Doors]] — door games (LORD, TradeWars, Synchronet game servers…)
- [[Chat]] — multi-user real-time chat (IRC, MRC)
- [[RSS Reader]] — the built-in news aggregator

## For sysops

- [[Sysop Guide]] — the long-form admin guide
- [[Door Setup]] — getting a new door online
- [[BinkP Setup]] — joining a FidoNet-style mail network
- [[NodeSpy]] — watching and kicking active sessions
- [[Backup]] — backing up your BBS
- [[Troubleshooting]] — fixes for common errors and gotchas

## About this wiki

- Anyone can read. Logged-in users can edit.
- Use `[[Page Name]]` to link to another wiki page.
- Every edit is kept in [[Revision History]] — see the page's
  *history* button.
- Missing pages show up on [[Wanted Pages]].
- Pages no other page links to are listed under
  *Orphans* in the wiki nav.

> _New users: when you spot something wrong or unclear, click the
>  **Edit** button at the top of the page and fix it. That's how a
>  wiki stays useful._
"""),

    # ------------------------------------------------------------------
    ('getting-started', 'Getting Started', """
# Getting Started

Welcome! Here's the short path from "I just discovered this BBS" to
"I'm reading messages and playing doors."

## 1. Create an account

Pick the access path that suits you and register:

| Path | URL | Notes |
|------|-----|-------|
| Web  | `/register` | Email + password — fastest |
| Telnet | port 2233 | Choose `(N)ew user` at the prompt |
| SSH | port 2234 | Same `(N)ew user` flow over SSH |

After registering, the sysop may require you to answer a few
questions — these are configured per-BBS and seen on first login.

## 2. Find your way around

- The **main menu** is the top-level launchpad. On terminal access
  it's the ANSI menu you see right after login. On the web it's the
  navbar at the top of every page.
- Most features are reachable from both UIs. The wiki tries to note
  where one is fuller than the other.

## 3. Try something low-stakes

A nice on-ramp:

1. Read a few [[Bulletins]] — short sysop-curated notices.
2. Browse the [[Message Boards]] and reply to a thread.
3. Check the [[RSS Reader]] for BBS-scene news.
4. Play a [[Doors|door game]] — LORD or TradeWars are classics.

## 4. Set your profile

Edit your profile from the user menu (web: top-right username
dropdown; terminal: *Y* on the main menu). Add a tagline, signature,
or avatar — these show up next to your posts.

## 5. Say hello

Drop into [[Chat]] and introduce yourself. The IRC and MRC bridges
mean someone is usually around.

## Need help?

- [[FAQ]]
- Ask in [[Chat]]
- Send the [[Sysop]] a [[Private Messages|private message]]
"""),

    # ------------------------------------------------------------------
    ('features', 'Features', """
# Features

ANetBBS aims to be a complete modern BBS — classic in spirit, modern
in plumbing. Below is the full feature inventory.

## Access

- [[Web Access]] (HTTP/HTTPS, real-time updates via WebSockets)
- [[Telnet]] (TCP 2233)
- [[SSH]] (TCP 2234, password and key auth)
- [[Rlogin]] (TCP 513, used by game-server style auto-login — off by
  default, sysop must enable it)
- [[PETSCII]] (TCP 6400/6401, Commodore 64/128 terminal support —
  off by default, sysop must enable it)
- Gemini — not a real Gemini-protocol (TLS+1965) listener; your
  gemtext capsule is exposed over plain HTTP at `/gemini/<username>`
- Finger (TCP 79, user info)

## Messaging

- [[Message Boards]] — local discussion forums with categories,
  pinning, locking, reactions, polls, mentions, and attachments
- [[Echomail]] — FidoNet-style global discussion areas with full
  BinkP networking
- [[Netmail]] — point-to-point FidoNet mail
- [[Private Messages]] — one-to-one mail between local users
- [[Instant Messages]] — Inter-BBS real-time IM via the MSP
  protocol (TCP 18)

## Files

- [[Files|File Library]] with file areas, categorization,
  uploads, downloads, descriptions, ratings, and virus scanning
- FTP access (off by default, `FTP_ENABLED`) — browse/download file
  areas with any plain FTP client; anonymous read-only + authenticated
  uploads where permitted, see [[Files]]
- [[TIC Processor]] for FidoNet TIC echo distribution
- [[QWK]] mail packets for offline reading

## Doors

- [[games|Game Center]] — 20 built-in browser games (puzzle, action,
  cards/casino, strategy, RPG) plus terminal doors, all in one lobby
- [[Doors]] — door game subsystem driving:
  - Native Synchronet (over rlogin)
  - DOS doors via [DOSBox-staging](https://dosbox-staging.github.io/) + TCP nullmodem bridge
  - Web doors (native HTML/JS games)
- **ANetCRAFT** — a bundled, pure-Python Minecraft-like survival door,
  active by default, no separate install needed. (Not the same thing
  as the standalone ANetCRAFT-Door C# project — same name, different
  codebase.)

## Real-time

- [[Chat]] — multi-user chat with channels
- [[IRC Bridge]] — relays one local MRC room to one IRC
  server/channel (sysop sets this up in the database; no admin UI)
- [[IRC Client]] — each logged-in user can connect to their own IRC
  server, nick, and channels from the web
- [[MRC]] — the Multi-Relay Chat protocol used by many BBSes
- Telegram Bridge
- Sysop Page — bell the sysop, see who's around

## Information

- [[RSS Reader]] — built-in feed aggregator (web + terminal)
- [[Bulletins]] — sysop-curated short notices
- Site Pages — pages the sysop hand-authors
- Personal Pages — user-edited homepages on the BBS
- Image Gallery
- Stats — site activity, top users, leaderboards
- Nodelist — FidoNet/network nodelist browser
- [[ebooks|Ebook Reader]] — search and read free public-domain books
  (Project Gutenberg, via the Gutendex API) in the web or terminal UI
- This wiki (`/wiki/`)

## Sysop tools

- [[Sysop Control Panel]] — the Service Control Center (per-service
  start/stop/restart, live CPU/RAM/thread graphs, listener health,
  journal viewer), NodeSpy, who's online
- [[Scheduled Events]] — cron-style maintenance jobs (log rotation,
  SQLite VACUUM, security-update checks, nodelist generation, InterBBS
  sync, arbitrary shell commands)
- [[NodeSpy]] — view and kick live terminal sessions
- [[BinkP Setup|Echomail Admin]] — networks, areas, BinkP polling
- [[Door Setup|Doors Admin]] — register doors and game servers
- [[RSS Reader|RSS Admin]] — manage feeds
- [[Files|Files Admin]] — areas, virus scanner, batch tools
- [[Themes]] — switch the dark theme palette per-user or globally
- [[Backup]] — sqlite dump + asset archive
- [[Tutorial]] — sysop onboarding walkthrough
"""),

    # ------------------------------------------------------------------
    ('faq', 'FAQ', """
# Frequently Asked Questions

## How do I sign up?

See [[Getting Started]]. Web is fastest; telnet/ssh both have an
interactive "new user" flow at the welcome prompt.

## I forgot my password

If your account had an email address, use **Forgot password** on
the [[Web Access#Sign-in|login]] screen. Otherwise, ask the [[Sysop]] — they can
reset it from the admin panel.

## Is this open source?

Yes — ANetBBS itself is open source. The license and source
repository are linked from the footer on every page.

## What protocols does it support?

See [[Features]] — short version: HTTP/HTTPS, Telnet, SSH, Rlogin,
Gemini, Finger, BinkP, MSP. Real-time chat travels over WebSockets,
IRC, MRC, or Telegram depending on which bridges the sysop enabled.

## How can I help?

A few easy ways:

1. **Edit the wiki.** Fix typos, expand thin pages, add screenshots.
2. **Post on the boards.** A BBS without messages is a quiet BBS.
3. **Play doors.** High scores show up on the leaderboard.
4. **Run a peer.** If you're a sysop, peer with us over BinkP — see
   [[BinkP Setup]].

## What's an "echomail area"?

A globally-distributed message area that you exchange with other
BBSes via BinkP. Think Usenet on a 1990s phone budget. Your post
gets sent up to your uplink overnight; theirs arrive in the same
poll. See [[Echomail]].

## Why is the wiki text white on dark?

Because every BBS worth visiting is dark-themed. You can switch
themes from your profile — see [[Themes]].

## What's a "door"?

A game (or other interactive program) that the BBS launches as a
sub-process for one user at a time. Originally DOS programs that
spoke to the BBS over a fake COM port; today most are either
modern web games, native Synchronet-style programs over rlogin,
or DOS originals running under DOSBox. See [[Doors]].

## Is this Y2K-compliant?

Yes. We have until **2038-01-19 03:14:07 UTC** to worry instead.
"""),

    # ------------------------------------------------------------------
    ('web-access', 'Web Access', """
# Web Access

The web interface is the most feature-complete way to use ANetBBS.

## URL

`https://bbs.a-net.fyi/` for this site. Replace with your BBS's URL.

## What works on the web

Almost everything: messages, echomail, files, doors (web doors and
rlogin-style remote), RSS reader, chat (Socket.IO-powered), profile,
themes, wiki, gallery.

Web-only features include:

- The [[Sysop Control Panel]]
- The [[Web Terminal]] — a real xterm.js terminal in your browser
  that talks to the local telnet listener, so you get the ANSI menus
  without installing a telnet client
- [[Themes|Theme selection]] preview

## What doesn't

A few things only exist on the terminal side, mostly because they
exercise CP437 / ANSI directly:

- The classic ANSI menu painter
- Raw [[Doors]] that need a fake serial port (these still work, but
  through the [[Web Terminal]] rather than as native web doors)

## Sign-in

`/login` for password sign-in. The session cookie is HTTPS-only and
SameSite=Lax. CSRF protection is enforced on every POST — visible
as a hidden `csrf_token` input on every form.

## Mobile

The default theme is mobile-responsive. Bootstrap 5 underneath; the
navbar collapses into a hamburger on small screens. Chat and the
web terminal both work on phones.

## Real-time

Several pages use Socket.IO for live updates:

- [[Chat]]
- [[Web Terminal]]
- Sysop broadcasts (toast on every page)
- New-message notifications (badge in the top-right)
"""),

    # ------------------------------------------------------------------
    ('telnet', 'Telnet', """
# Telnet

Classic terminal access. Port **2233/tcp** by default (not the
standard 23 — chosen so it doesn't need root/setcap to bind, and
doesn't conflict if you're already running something else on 23).

## Clients

Any standard telnet client. For the best experience, pick one that
speaks **CP437** and renders ANSI cleanly:

- [SyncTERM](http://syncterm.bbsdev.net/) — the BBS-scene gold
  standard
- [NetRunner](https://mysticbbs.com/downloads/) — popular alternative
- [mTelnet](https://github.com/RealHeavyDude/mTelnet) — Windows
- `tn5250` or plain `telnet` (limited; no CP437 font)

ANetBBS speaks the telnet binary-data option, so 8-bit clean transit
is on for ANSI escape sequences.

## First-time login

You'll see a welcome screen, then a prompt:

```
(G)uest, (L)ogin, (N)ew user — choice:
```

Pick **N** to register. You'll be asked for a username, password,
email, and (if the sysop requires it) a short new-user questionnaire.

## Encoding

The session is **CP437**. The base.html theme talks about codepages —
each user can override per-account from `/profile/edit`. Default is
fine for almost everyone; non-CP437 only matters if you're trying to
read echomail bodies authored in Latin-1 or UTF-8.

See also: [[SSH]] (same UI, encrypted), [[Web Terminal]] (same UI,
in a browser tab).
"""),

    # ------------------------------------------------------------------
    ('ssh', 'SSH', """
# SSH

Secure-shell access to the same ANSI menus you'd get via [[Telnet]].
Port **2234/tcp** by default (deliberately not 22, so it can't
conflict with the box's real system SSH daemon).

## Connect

```bash
ssh -p 2234 username@bbs.a-net.fyi
```

Replace hostname / port to match your BBS.

## Why SSH over telnet?

- Encrypted in transit
- The SSH client's own username/password prompt IS the BBS login —
  whatever you type there is captured and used to auto-login into the
  menu system, no second prompt
- Public-key auth is intentionally disabled server-side (the BBS needs
  a real password to auto-login with, and public-key auth would skip
  that step) — any client falls back to password auth automatically
- Some networks block port 23 outright; SSH usually gets through

## What you can do over SSH

Everything the [[Telnet]] menus offer. Doors work over SSH too,
including DOS doors via the [[DosBridge]] — the bridge is
protocol-agnostic.

## Host key

The first connection prompts you to accept the BBS's host key
fingerprint. The sysop should publish the fingerprint somewhere
(usually on one of their info pages) so you can verify it.
"""),

    # ------------------------------------------------------------------
    ('rlogin', 'Rlogin', """
# Rlogin

Rlogin (RFC 1282-ish — see below) gives single-sign-on terminal
access. Port **513/tcp**. Mostly used by **inter-BBS game servers**
where one BBS launches a game on another and the player travels with
their identity intact.

**Off by default.** Rlogin is unauthenticated and unencrypted — a
client's claimed username is trusted at face value — so
`RLOGIN_ENABLED` defaults to `false`. A sysop has to explicitly turn
it on (and should only do so on a trusted network, or firewalled to
known peers) before this port answers at all.

## How it works

The client opens a connection to port 513 and sends three null-
terminated strings:

```
\\0 client-username \\0 server-username \\0 terminal/baud \\0
```

The server reads them, validates against its allowlist, and drops the
user straight into the menu (or directly into a door if the rlogin
was for a specific game).

## Synchronet game-server caveat

Synchronet-flavored rlogin uses an **inverted** field order vs the
standard:

> _**password first, username second.**_

ANetBBS's outbound `door_rlogin` game type follows the Synchronet
convention. See [[Door Setup#2-remote-rlogin-doors-synchronet-game-servers|rlogin doors]]
for the details on the remote-game flow.

## Locally

Note the current [[Web Terminal]] does *not* go through rlogin — it
opens a plain socket straight to the local [[Telnet]] listener, so
you still see the normal login prompt inside the browser terminal.
Rlogin's local auto-login handshake (username pre-filled, no
password re-prompt) is for genuine rlogin clients — other BBS
software launching a door on this system, or a sysop's own rlogin
client — not the web UI.
"""),

    # ------------------------------------------------------------------
    ('petscii', 'PETSCII', """
# PETSCII (Commodore 64/128)

A dedicated connection option for real Commodore 64/128 hardware and
PETSCII terminal emulators (SyncTERM's C64 mode, Novaterm, CCGMS,
64NIC+). Completely separate from the [[Telnet]]/[[SSH]] ANSI menus —
PETSCII isn't ANSI, so there's no color, no cursor-addressed
selectors, just plain text, numbered menus, and single-key prompts.

**Off by default** — the sysop has to opt in. If you don't see a
PETSCII port mentioned anywhere for this BBS, it isn't enabled here;
use [[Telnet]] or [[SSH]] instead.

## Connecting

Two ports, one per screen width — connect to whichever matches your
setup:

| Width | Typical port |
|-------|--------------|
| 40 columns | `6400` |
| 80 columns | `6401` |

There's no auto-detect: every connection on a given port is treated
as that width, unconditionally, since most real C64 telnet clients
don't announce themselves usefully otherwise.

## What you can do

- Message boards — browse, read, post, reply
- Echomail — pick a network first, then its areas
- Private messages
- File areas — browse, view extended descriptions, download over
  XMODEM (start your terminal's receive when prompted)
- Who's online
- View/edit your profile
- Play **Number Guessing** — the one built-in game available here
  (guess a number, get higher/lower hints)

Door games, ANetCRAFT, [[MRC]]/[[IRC Client]], and ANSI art are not
offered over PETSCII — none of those have a plain-text-only path.

## Sysop note

Sysops can build fully custom PETSCII menus — see [[Sysop Guide]].
"""),

    # ------------------------------------------------------------------
    ('message-boards', 'Message Boards', """
# Message Boards

The local discussion forums. Each board lives in a category and has
its own list of threads.

## Reading

- Web: `/boards/` shows the board index. Click into a board to see
  threads; click a thread to read it.
- Terminal: main menu *M*.

## Posting

Logged-in users can start new threads and reply. Markdown is
supported in posts, including fenced code blocks, tables, and
images.

## Mentions

Type `@username` to mention another user. They'll get a notification
(see [[Notifications]]). The mention popup auto-completes from
nearby usernames.

## Reactions

Bottom of every post — thumbs-up / down / heart / fire / laugh.

## Subscriptions

Click **Subscribe** at the top of a thread to get notified of every
new reply.

## Polls

Reply to a thread with a `/poll` slash-command from the editor, or
use the **Poll** tab on the compose form.

## Attachments

Up to four files per post. Images render inline; other files become
download links. See [[Files]] for the global library.

## Threading

ANetBBS uses flat threads by default — replies are appended in time
order. Quoted replies (Reply quote) prefix the original text with
`>` like classic Usenet.

## Moderation

Sysops can lock or unlock threads, pin posts to the top of a board,
move threads between boards, and soft-delete content.
"""),

    # ------------------------------------------------------------------
    ('echomail', 'Echomail', """
# Echomail

FidoNet-style globally-distributed discussion areas. Like Usenet but
delivered over **BinkP** instead of NNTP, with each message tagged
to an *echo area* like `BBS_SCENE` or `LINUX_HELP`.

## How it works

1. A user posts in echo area `BBS_SCENE` on this BBS.
2. The local BinkP poller bundles the new message with other
   outbound mail and sends it to your **uplink** at the next poll.
3. The uplink redistributes it to every node that subscribes to the
   `BBS_SCENE` area, including back to BBSes that link off the
   uplink.
4. Their users see the message on their own next poll cycle.

A round-trip is typically minutes to hours depending on each node's
poll schedule.

## Reading

- Web: `/echomail/`
- Terminal: main menu *E*. Areas list paged 18 at a time; press *Q*
  to skip to the picker.

## Composing

- Web: from any echo area page, click **Compose**.
- Terminal: main menu *C*. The compose flow asks for area → To: →
  Subject: → body editor.

Outbound messages queue with `direction='outbound'` and `sent_at=NULL`
until the next BinkP poll picks them up.

## Areas

Each area is a topic-bound conversation. Sysop maintains the area
list under [[BinkP Setup|Echomail Admin]]. Common areas in the BBS scene:

- `BBS_SCENE` — talking about BBSes
- `BBS_ADS` — BBS advertisements
- `SYNCHRONET` — Synchronet-specific
- `FIDONEWS` — the FidoNet weekly newsletter
- `LINUX_HELP`, `WIN10_HELP`, … — distro-flavored support

## Networks

You can be in multiple networks at once. The most common BBS-scene
networks today are:

- **FidoNet** (`zone 1-6`) — the original
- **fsxNet** — modern, friendly, smaller
- **tqwNet**, **zer0net**, …

Each network has its own uplink, addressing scheme (`zone:net/node`),
and area policies. See [[BinkP Setup]] to join one.

## Pre-configured: ANotherNetwork

Every ANetBBS install ships pre-joined to **ANotherNetwork** (hub
`bbs.a-net.fyi`, zone **1200**) — 26 message echo areas across
General, Technology, BBS Scene, Retro, Hobby, Trading, Data, SysOp,
and Test categories, seeded automatically on first run but **inactive
and unsubscribed** until a sysop turns on the ones they want. See
[[ANotherNetwork]] for the full area list and how to get your own
node number.

## Encoding

Echomail bodies travel as raw bytes and can be in any codepage. ANetBBS
stores them as latin-1 mojibake (each byte 0xNN → codepoint U+00NN)
so the original CP437 bytes survive round-trips. The terminal
reader writes them back via `latin-1.encode()` so a CP437 client
renders ANSI art correctly.
"""),

    # ------------------------------------------------------------------
    ('binkp-setup', 'BinkP Setup', """
# BinkP Setup

How to join an [[Echomail]] network or netmail-peer with another BBS.

Every install already has one network pre-configured and waiting:
**ANotherNetwork**, hub `bbs.a-net.fyi`, zone 1200 — see
[[ANotherNetwork]]. Everything below also applies to activating that
one; you're just filling in a node number instead of registering a
whole new network from scratch.

## What you need

- Your **node address** assigned by the network coordinator
  (e.g. `21:1/199`)
- The uplink's **host**, **port**, and **session password**
- A short list of **echo areas** you want to subscribe to
- A few free hours on a weekend

## Step 1 — register the network

`/admin/echomail/` → **Add Network**. Fields:

| Field | Example | Notes |
|-------|---------|-------|
| Name | `fsxnet` | Short label used in menus |
| Our address | `21:1/199` | Your assigned node |
| Uplink host | `fsxnet.nz` | Or IP |
| Uplink port | `24554` | Standard BinkP port |
| Session password | `********` | From the coordinator |

Optional but useful:

- **CRAM-MD5** if your uplink supports it (most modern hubs do)
- **TLS** if the hub speaks BinkP-over-TLS

## Step 2 — register echo areas

`/admin/echomail/areas` → **Add Area**. The tag must exactly match
the network's tag (`BBS_SCENE`, `fsx_gen`, etc.) — coordinator will
have a list.

## Step 3 — first poll

On the network's row in `/admin/echomail/` (Networks list), click
**Poll Now** — it runs an immediate BinkP session in the background
and flashes a confirmation; watch `/admin/echomail/logs` for
handshake success and "received N packets". On the first poll you'll
often get a big initial dump as the uplink catches your node up.

## Step 4 — automated polling (nothing to set up)

Polling is **not** cron-driven — no cron entry, no external curl
call, no `/admin/echomail/schedule/` page. `anetbbs-web` runs a
background poller thread from the moment it starts, and it polls
every active network on its own **Poll Interval** (a field on the
network's edit form, in minutes; default 60). Change that field if
you want faster or slower polling for a given network — there's
nothing else to configure.

## Areafix

To subscribe to a new area without bothering the coordinator, send a
netmail to the `AreaFix` user at the uplink's address with your
AreaFix password **in the Subject line** (per FTS-0024 — not the
body) and one command per line in the body:

```
Subject: yourareafixpw

+BBS_SCENE
+SYSOP_HELP
```

ANetBBS has an [[Netmail#AreaFix|AreaFix]] processor for inbound areafix requests
on your own node too — useful if other BBSes peer off you.

## TIC files — file-echo distribution

BinkP doesn't only move messages — it moves *files* too, the same
way, via a **file echo**. Where a message-echo message carries the
text, a file-echo file is accompanied by a small `.TIC` control file
(area tag, filename, description, CRC32) that tells every hop along
the chain what the file is and where it's headed. A file dropped into
a network-attached file area (`network_id` set — [[ANotherNetwork]]'s
9 `ANN.FILES.*` areas are the built-in example) gets queued for
outbound TIC distribution automatically the moment it's uploaded, no
manual "send this out" step. See [[TIC Processor]] for the full flow,
both inbound and outbound.

## Hub Management vs. this page

Everything above (`/admin/echomail/` and `/admin/echomail/areas`) is
about *your own* node peering out to an uplink — the normal case for
almost every install. A separate admin surface,
`/admin/echomail/hub/`, only appears if this install is itself
designated the network hub (`REGISTRY_MODE_ENABLED=true`) and is
about managing *downstream* nodes that peer off **you** — see
[[Sysop Control Panel]] and [[QWK]] for what's in there.

If you're a hub for more than one real network from this one install,
the BinkP node form grows a **Hub Identity** picker once a second
identity exists — see [[Sysop Guide]], "Running more than one hub
identity." Almost no install needs this.
"""),

    # ------------------------------------------------------------------
    ('anothernetwork', 'ANotherNetwork', """
# ANotherNetwork

The [[Echomail]]/[[QWK]] network every ANetBBS install ships
pre-joined to. It exists so a fresh install isn't a ghost town —
there's somewhere to talk to other ANetBBS sysops and users from
day one, the same way classic Synchronet installs shipped pre-joined
to Dove-Net.

- **Hub:** `bbs.a-net.fyi`
- **Zone:** 1200
- **Transport:** BinkP (message + file echoes) or QWK/FTP, your
  choice — both reach the same areas via the hub's tosser

## What's seeded automatically

On first run, two network entries and 35 areas are created — all
**inactive / unsubscribed by default**. Nothing polls or shows up on
your boards until a sysop turns areas on.

**Two network rows** (`/admin/echomail/`, both `is_active=False`
until you fill in your node info):

- **ANotherNetwork** — BinkP transport, `bbs.a-net.fyi:24554`
- **ANotherNetwork (QWK)** — QWK-over-FTP transport, same hub

**26 message echo areas** (tag prefix `ANN.`), grouped by category:

| Category | Areas |
|----------|-------|
| General | General Discussion, Introductions, Humor & Jokes, Friendly Debate, Network Feedback |
| Technology | Technology, Linux & Open Source, Security & Privacy, Networking & Internet |
| BBS Scene | BBS News & Discussion, BBS Software Development, ANetBBS Support, Door Games, ANSI / ASCII Art |
| Retro | Retro Computing, Games & Gaming, Music |
| Hobby | Movies & TV, Books & Reading, Food & Cooking, Sports |
| Trading | For Sale / Wanted / Trades |
| Data | Data & File Discussion |
| SysOp | SysOp Discussion, SysOp Help & Tips *(sysop-only)* |
| Test | Test Messages |

**9 file echo areas** (tag prefix `ANN.FILES.`): Weekly Nodelists,
Infopacks, BBS Software, Door Games & Utilities, eBooks,
Linux/Open-Source Files, Retro Computing Files,
ANSI/ASCII Art Collections, and a Testing-only area. See [[Files]]
and [[TIC Processor]] for how file-echo distribution actually moves
bytes.

## Turning it on

1. Pick which of the 26 message areas and 9 file areas you actually
   want — subscribe to them under [[BinkP Setup|Echomail Admin]] /
   [[Files|Files Admin]]. Nothing forces you to run all of them.
2. Get a node number (see below) and fill in the address/password on
   whichever of the two network rows matches your transport.
3. Flip that network row's **Active** flag on.

## Getting your own node number

You need a node number before your BinkP/QWK sessions with the hub
will authenticate. Two ways to apply:

- **Terminal:** Echomail main menu → pick the Echomail Networks
  screen → **A = Apply for ANotherNetwork QWK node**. Fill in your
  BBS name, a proposed packet ID, sysop name, contact email, and BBS
  address. On the hub itself this writes straight into the request
  queue; on any other install it POSTs to the real hub's
  `/qwkhub/apply` endpoint instead (fixed in v1.0b2.29 — it used to
  wrongly write to whichever install you ran the wizard from).
- **Hub side:** the sysop running `bbs.a-net.fyi` reviews pending
  applications at `/admin/echomail/hub/qwk/requests` and
  approves or denies them; approving auto-creates the QWK node
  record with a generated password.
- **Public join form:** if the hub sysop has turned it on, `/join/`
  is a public, no-login application page — read the network rules,
  download the full infopack zip, tick a box confirming you read the
  rules, then submit one form covering both BinkP and QWK transport
  (fill in either section or both). There are no password fields;
  credentials are always hub-generated by the sysop on approval.
  Once enabled, it shows up for logged-in users under the Tools menu
  → **Join Our Network**.

Outside of the public join form above, there's no other self-service
flow for a **BinkP** node number specifically — if the hub sysop
hasn't enabled `/join/`, contact the hub sysop directly for BinkP
transport and they'll add your node under
`/admin/echomail/hub/binkp/`.

## See also

- [[Echomail]] — how message-echo distribution works
- [[BinkP Setup]] — joining any FTN-style network, including this one
- [[QWK]] — the offline-reading side, and Hub Management for QWK nodes
- [[TIC Processor]] — file-echo distribution mechanics
- [[Sysop Control Panel]] — where Hub Management lives if you run the hub
- [[Sysop Guide]] — "Running more than one hub identity," if bbs.a-net.fyi
  isn't the only network you hub from this install
"""),

    # ------------------------------------------------------------------
    ('doors', 'Doors', """
# Doors

A **door** is a single-user game (or utility) the BBS launches as a
sub-process. Originals were DOS programs talking to the BBS via a
fake COM port; today there's a wider menagerie.

## Door types ANetBBS supports

| Type | What it is | Typical example |
|------|------------|------------------|
| `builtin_web` | Native HTML/JS — runs in the browser, no shell-out | 2048, Tetris, Meadowlark Valley |
| `door_rlogin` | Outbound rlogin to a remote BBS's own game server | LORD, TradeWars on a partner BBS |
| `door_telnet` | Outbound telnet to a remote game server, no pre-auth handshake | TWGS (Trade Wars Game Server) |
| `door_dos` | DOS executable via [DOSBox-staging](https://dosbox-staging.github.io/) + TCP nullmodem bridge | LORD locally |
| `door_dosemu` | DOS executable via dosemu2, virtual COM1 (no FOSSIL) | DOS doors needing dosemu2 specifically |
| `door_native` | Linux native binary speaking DOOR.SYS | various forks |
| `door_synchronet` | Synchronet `.js` doors — real `jsexec` if present, otherwise Node + compat shim | LORD-JS, MajorMUD-JS |
| `door_mystic` | Mystic Python `.mpy` with our compat shim | Mystic-native Python doors |
| `door_mystic_mps` | Mystic Pascal `.mps`, auto-compiled via `mplc` | Mystic-native Pascal doors |
| `door_dos_browser` | DOS .ZIP bundle via EmulatorJS + dosbox_pure, runs entirely client-side | DOOM/Duke3D shareware, no telnet/SSH needed |
| `builtin_python` | Bundled pure-Python door, runs natively in the terminal — no external binary or runtime | ANetCRAFT (active by default) |

Note: **ANetCRAFT** (the bundled `builtin_python` door above) is a
separate codebase from the standalone **ANetCRAFT-Door** project (a
C# .NET Minecraft-replica door) — same name, different projects. Don't
assume a fix or feature in one applies to the other.

## Playing

- Web: `/games/` — game list. Pick one and play in the browser
  (web games) or in a [[Web Terminal]] popup (terminal games).
- Terminal: main menu — usually under a "Games" or "Doors" sub-menu.

## Dropfiles

Terminal doors expect a **dropfile** — a text file with the user's
name, time remaining, baud rate, etc. ANetBBS writes the major
formats automatically right before launch:

- `DOOR.SYS` — almost-universal
- `DORINFO1.DEF` — Synchronet/Telegard-flavored
- `DOOR32.SYS` — modern 32-bit format, used by newer FPC-based door kits

Stored in each door's working directory; cleared on launch.

## Don't touch the door's working dir

ANetBBS deliberately does **not** sweep files inside a door's
working directory between launches. Some doors keep persistent
node-specific state in files named like `NODE1.DAT` that look like
lockfiles but are actually config. Sysops who run an aggressive
cleanup will break the door.

## Adding a door

See [[Door Setup]] for the long version.
"""),

    # ------------------------------------------------------------------
    ('door-setup', 'Door Setup', """
# Door Setup

How to register a new door so users can run it from the menu.

## 1. Web doors

The simplest case — these live entirely in the browser.

`/admin/games/` → **Add Game**. Type = `builtin_web` ("Built-in Web
Game" in the dropdown). Provide the HTML template path (relative to
`anetbbs/templates/games/web/`). That's it.

## 2. Remote rlogin doors (Synchronet game servers)

Most "play LORD on someone else's BBS" setups go through rlogin.

`/admin/games/` → **Add Game**. Type = `door_rlogin`. Fields:

- **Server (host:port)** — the remote server, one field, e.g.
  `game.example.com:513`
- **User-template PASSWORD [TERMINAL]** — three space-separated
  values in one field: a template for what gets sent as the
  rlogin username (tokens: `@USER@`/`@ALIAS@`/`%U`/`%u`), the
  session password agreed with the remote, and an optional
  `[TERMINAL]` value (e.g. `xtrn=LORD408`) to drop straight into a
  specific door on servers that support it.
- **BBS Tag (optional)** — appends `-TAG` to your BBS's identifying
  string so the remote sysop can tell which BBS a caller came from.
  A separate field purely so you don't have to hand-assemble the
  combined string yourself.

> ⚠ Synchronet-flavored rlogin uses **password first, username
> second** — opposite of RFC 1282. ANetBBS's outbound client
> already does this; you don't need to flip anything, just fill in
> the fields above in the order they're labeled.

## 3. DOS doors

The full setup — DOS exe + DOSBox/dosemu2 + TCP nullmodem bridge.
Detailed in [[DOS Door Recipe]]. Short version:

1. Install DOSBox-staging (or dosemu2, for the `door_dosemu` type).
2. Drop the door's files under `<install>/doors/<door-name>/`.
3. Register the game in the admin UI with type `door_dos` (or
   `door_dosemu`): **Executable Path**, **Working Directory**,
   **Command Line Args**, **Drop File Type**, **Drop File Path**.
   That's it — there's no bridge-port or raw DOSBox/dosemu2
   command-line field to fill in by hand. The bridge picks a free
   TCP port itself at launch, and the emulator invocation (including
   the `serial1=nullmodem`/mount setup) is built automatically from
   the fields above.
4. Test by playing it. Watch
   `journalctl -u anetbbs -f` for bridge bytes-in/out (telnet, SSH,
   and rlogin are one unified systemd service, not separate units).

[[DOS Door Recipe]] walks through LORD as a concrete example,
including the [[LORD Setup]] gotchas.

## 4. Native Linux doors

A bare `door_native` type — invoked with the dropfile path on
argv. Works for forks like `lord-linux` or any compliant
DOOR.SYS-aware binary.

## 5. Bundled Python doors

Some doors ship as pure Python and need no setup at all — type
`builtin_python`, run natively in the terminal process, no external
binary, DOSBox, dosemu2, or jsexec required. **ANetCRAFT** (ANetBBS's
own Minecraft-inspired 2D survival game) is the current example and
is pre-seeded **active by default**. Nothing to register — it's
already in `/games/` on a fresh install.

This is a separate codebase from the standalone **ANetCRAFT-Door**
project (a C# .NET Minecraft-replica door with the same name) — don't
confuse the two when troubleshooting.

## Per-door config tips

- **Time limits**: set on the Game row. The BBS enforces them with
  a watchdog that closes the user's writer when time runs out.
- **Max nodes**: how many users can be in the door simultaneously.
  Most classic DOS doors are single-user; set 1.
- **Idle timeout**: if the user's terminal goes silent for N
  minutes the watchdog kicks them. Useful for doors that don't
  detect carrier loss.

## Debugging

- Launch the door yourself as a normal user (sysop account) from the
  Game Center — there's no separate admin-side test-launch button.
- The Game row has a **Last error** field that the bridge writes to
  on launch failure.
- For DOS doors, run DOSBox interactively to see what the bridge is
  feeding it — the [[DosBridge]] page has the recipe.
"""),

    # ------------------------------------------------------------------
    ('lord-setup', 'LORD Setup', """
# LORD Setup

LORD — *Legend of the Red Dragon* by Seth Robinson, 1989 — is the
canonical door game. ANetBBS ships **two ways to run it**:

1. **Bundled JS port** (recommended, no DOS required) — Synchronet's
   pure-JavaScript LORD rewrite, pre-installed at
   `anetbbs/games/sbbs_doors/lord/`. Runs under Node.js via ANetBBS's
   own compat shim — no Synchronet install required (see Path 1
   below).
2. **Original DOS** under DOSBox + TCP nullmodem bridge — for
   purists. Detailed below; see also [[DOS Door Recipe]].

## Path 1 — Bundled JavaScript LORD (works out of the box)

ANetBBS pre-seeds an **active** Game row for LORD on first install.
The game runs under Node.js via the Synchronet compat shim
(`anetbbs/games/synchronet_compat.py`) — no Synchronet install
required. The shim provides:

- `server`/`client`/`bbs`/`user`/`console` globals so dorkit picks
  its sbbs-mode console driver
- `Queue`, `strftime`, scope-form `require()`, `js.load_path_list`,
  `js.on_exit()`, `js.exec()`, etc.
- A Node-friendly `sbbs_input.js` replacement that drives stdin
  reads through `dk.console.input_queue_callback` instead of a
  forked input thread
- Full `console.*` API (clear, cleartoeol, gotoxy, right/left/up/down,
  ctrlkey_passthru, …) wired to ANSI escape writes on stdout
- Resolver priority that prefers `<stubs_dir>/dorkit/` over the flat
  `<stubs_dir>/` so the dorkit-internal `screen.js` / `graphic.js`
  win over older bare copies

Just play from `/games/`.

## Path 1 fallback — install real Synchronet `jsexec` (optional)

If you want bit-perfect upstream behaviour or you're running another
door the compat shim doesn't cover, install Synchronet's `jsexec`.
The door_runner auto-detects it at standard paths
(`/sbbs/exec/jsexec`, `/opt/synchronet/exec/jsexec`,
`/usr/local/sbbs/exec/jsexec`, `$PATH`) and prefers it over the
Node shim when present.

```bash
git clone --depth 1 https://gitlab.synchro.net/main/sbbs.git /tmp/sbbs
# Synchronet's build needs siblings — start at the top:
cd /tmp/sbbs/src/sbbs3
make USE_DOSEMU=0 jsexec  || make CFLAGS_EXTRA="-I../conio" jsexec
sudo cp jsexec /usr/local/bin/
```

Or set `SBBS_JSEXEC=/path/to/your/jsexec` in the systemd unit's
environment.

## Path 2 — Original DOS LORD under DOSBox

### Prerequisites

- DOSBox-staging (installed by `install.sh`/`update.sh` if you said
  yes to that prompt)
- `xvfb-run` (usually the `xvfb` package) if you want headless
  operation — ANetBBS auto-wraps the DOSBox launch with it when found
  on the system, no config needed
- The LORD binaries (`LORD.EXE`, `LORDCFG.EXE`, `LORD.DAT`, etc.) in
  a directory you own — `<install>/doors/lord/`

### Registering the game

There's no DOSBox config file to hand-write — ANetBBS generates it
per launch. `/admin/games/` → **Add Game**, type `door_dos`:

- **Executable Path**: `<install>/doors/lord/LORD.EXE`
- **Working Directory**: `<install>/doors/lord/`
- **Drop File Type**: `door.sys` (or `door32.sys`)
- **Drop File Path**: `E:\\` (see [[Door Setup]]'s drive-layout table
  — `E:` is always the per-node scratch drive on DOSBox)

At launch, ANetBBS mounts `C:` = your working directory, `D:` = its
bundled FOSSIL driver bundle, `E:` = a per-node scratch directory,
loads `BNU.COM` on COM1 automatically, copies the drop file from `E:`
into `C:` so LORD finds it in its own directory, then runs your
executable. See [[Door Setup]] for the full drive-letter table
(dosemu2 uses different letters than DOSBox for the same three
roles).

## Bridge wiring

The BBS process opens a TCP connection to a bridge port it allocates
itself at launch time (no port to configure by hand) and ferries
bytes between the user's terminal writer and the socket. That's the
`DosBridge` class — see [[DosBridge]].

## LORDCFG gotchas

- `LORDCFG` writes `NODE1.DAT` (or `NODE<n>.DAT`) into the working
  dir. It's a binary config blob — leave it alone between runs.
- `DOOR.SYS COM0` means "carrier present"; set to **COM1** for the
  bridge to think it's on a real port. ANetBBS writes this
  correctly out of the box.

## Exit hangs

If LORD's exit doesn't release Xvfb cleanly, the watchdog needs a
`waitpid` on DOSBox plus an idle timeout — Xvfb can keep the PTY's
slave fd open past the actual door exit if not babysat. ANetBBS does
both. The fallback is **Ctrl+]q** which the bridge translates to
DOSBox's "kill emulator" sequence.

## See also

- [[DOS Door Recipe]]
- [[DosBridge]]
- [[Door Setup]]
"""),

    # ------------------------------------------------------------------
    ('dosbridge', 'DosBridge', """
# DosBridge

The TCP-socket-to-terminal-writer bridge that lets DOS doors running
inside DOSBox talk to a user connected via [[Telnet]] / [[SSH]] /
[[Web Terminal]].

## Architecture

```
+-----------+  bytes  +-----------+  TCP  +-----------+
|  user     |  <--->  |  BBS      | <---> |  DOSBox-  |
| terminal  |         | session   |       |  staging  |
+-----------+         | (DosBridge)|       | (door exe)|
                      +-----------+       +-----------+
```

The BBS session opens a TCP connection to DOSBox's nullmodem
`serial1=nullmodem server:<port>`. Two async tasks copy bytes in
both directions:

- **terminal → socket**: user keystrokes go to the door
- **socket → terminal**: door output reaches the user

## Why TCP?

Earlier versions tried `serial1=stdio`. That stopped working when
DOSBox-staging dropped stdio passthrough; the bridge would see
zero bytes through the door. TCP nullmodem is the supported path
since v279.

## Lifecycle

1. BBS launches DOSBox as a subprocess.
2. BBS opens the bridge socket (or waits for DOSBox to connect,
   depending on `server:` vs `client:` mode).
3. Bridge runs until any of: door exit, user disconnect, idle
   timeout, or the watchdog fires.
4. Teardown closes the socket, then `waitpid` on DOSBox, then
   kills Xvfb if running headless.

## Cleanup contract

- Never delete files inside the door's working dir on shutdown
  — see [[Doors#dont-touch-the-doors-working-dir]].
- Do clean up tempfiles in `/tmp/dosbridge-<pid>-*`.
- Do reap the DOSBox process — orphan Xvfb processes are a known
  bug class.

## See also

- [[LORD Setup]] for a concrete example
- [[Door Setup]] for registering a door in the admin UI
"""),

    # ------------------------------------------------------------------
    ('chat', 'Chat', """
# Chat

Multi-user real-time chat. Three separate systems share the "chat"
label:

## 1. Local chat

Users on this BBS talking to each other in real time. **Terminal
only** — main menu → Chat → Local Chat/Multinode. There is currently
no web equivalent of local chat (the web Chat menu only has MRC Chat
and IRC — see below).

Multinode chat is a lightweight broadcast: `/list` to see who's
connected, `/w <slot> <msg>` to whisper a specific node, `/q` to quit
back to the menu.

## 2. IRC — three separate, unrelated things use this word here

- **ANetIRC (terminal door)** — a full personal IRC client built into
  the terminal Chat menu (SASL, bookmarks, scrollback). This is what
  most callers mean by "IRC chat" on this BBS. See [[IRC Client]] for
  the closest write-up of the connection flow (same underlying idea,
  different implementation).
- **IRC Client (web)** — a personal, per-user IRC connection at
  `/irc/` — you pick your own server, nick, and channels, same as
  running your own IRC client, but in the browser. See [[IRC Client]].
- **IRC Bridge** — a sysop-configured relay between one MRC room and
  one external IRC server/channel, so MRC and IRC users can talk to
  each other. There's no admin web UI for this; it's a database row
  plus its own systemd instance. See [[IRC Bridge]].

## 3. MRC (Multi-Relay Chat)

A BBS-scene chat network — connects sysops and users across many
BBSes through one central hub. Default ANetBBS connects to the public
hub at `mrc.bottomlessabyss.net:5001`. Both the web (`/mrc/`) and
terminal MRC clients talk through the same local bridge, so web and
terminal users share rooms and a single trust identity.

See [[MRC]] for the full command reference — it's grown a lot of
terminal-side features (nick sidebar, status bar, ticker, ignore
lists) that are easy to miss if you only skim this page.

## Etiquette

- Local channels: keep on-topic; sysop sets the topic.
- MRC/bridged channels: assume people on the other side don't know
  what BBS they're talking to. Mention it when relevant.
- Be patient — async typing means a lull doesn't mean nobody's
  there.

## Quick MRC command reference

`/me action`, `/topic [text]`, `/who`, `/afk [msg]` / `/back`,
`/msg <user> <text>` (DM). `/help` asks the *hub* for its own help
text; for the local command list inside the client, use `/helpserver`
(alias `/h` or `/?`). Full list, including `/set`/`/twit`/`/shield`,
is on the [[MRC]] page.
"""),

    # ------------------------------------------------------------------
    ('mrc', 'MRC', """
# MRC

**Multi-Relay Chat** — a BBS-scene chat protocol that connects sysops
and users across many BBSes through a central hub.

## How it differs from IRC

- One central hub (no server federation)
- Authentication is optional, per-handle "trust" (`/identify`), not a
  NickServ-style requirement to chat at all
- Messages carry the user's BBS as part of the identity, composed by
  the bridge — you don't need to type it yourself
- Mostly text-only; not many bots; a calmer, more BBS-y feel

## Connecting

Built in. The `anetbbs-mrc-bridge` systemd service runs alongside the BBS and
keeps one persistent, BBS-wide connection to the hub — every web and
terminal user shares it. Both `/mrc/` (web) and the terminal Chat
menu's MRC option talk through this same bridge.

There is no web admin page for the bridge — it's configured by editing
`mrc/bridge/config.json` (copy `mrc/bridge/config.example.json` on
first setup) and restarting the `anetbbs-mrc-bridge` service:

- `mrc_host` / `mrc_port` — the hub to connect to (default
  `mrc.bottomlessabyss.net` / `5001`)
- `identify_required_mode` — if `true`, callers must `/identify`
  before they can chat at all. **Defaults to `false`** — `/identify`
  is optional "MRC Trust," not a gate, unless a sysop deliberately
  turns this on.
- `bbs_description` / `bbs_telnet` / `bbs_ssh` / `bbs_website` /
  `bbs_sysop` — fill these in! They're what other MRC clients see
  when someone runs `/bbses` or `/info <n>` and looks up *your* BBS.
  Left blank (the default), your entry shows up with missing info to
  everyone else on the network.

## Rooms

There's no sysop-configured channel list — rooms are dynamic, created
on the hub side as people `/join` them. Everyone lands in `lobby` by
default on connect. Use `/list` to see what rooms currently have
people in them, `/join <room>` to switch.

## Command reference (terminal client)

**Session:** `/quit` (leave), `/help` (ask the *hub* for help),
`/helpserver` / `/h` / `/?` (local command list).

**Messaging:** `/msg <user> <text>` (DM, aliases `/t /tell /dm /pm
/whisper /w`), `/r <text>` (reply to last DM), `/me <action>`,
`/broadcast <text>` (sysop-wide).

**Rooms:** `/join <room>`, `/list`, `/topic [text]`, `/roompass
<pw>`, `/roomconfig [param value]`.

**People:** `/who`, `/chatters`, `/users`, `/bbses` (list connected
BBSes), `/info [n]` (look up one BBS's directory entry — see the
`bbs_description` etc. fields above), `/lastseen [user]`, `/afk
[msg]` / `/back`, `/status <param> [value]` (set a hub status field,
e.g. `/status AFK gone fishing`).

**Info:** `/motd` (ask the hub for its message of the day), `/banners`
(ask the hub for its banner rotation).

**Auth (optional):** `/identify [password]`, `/register <password>
[email]`, `/trust [sub]`.

**Preferences — `/set`:** `/set list` shows current values.
Configurable: `prefix`/`suffix` (nick decoration), `color <00-15>`
(nick color code), `entermsg`/`leavemsg`/`quitmsg` (supports
`{handle}`), `ticker on|off`, `clock on|off`, `tz <zone-or-offset>`,
`palette default|green|amber|cyan|mono`, `clockformat 12|24` (status-bar
clock/timestamp hour format), `twitfilter on|off` (master switch for
your `/twit` ignore list — turn it off to temporarily hear from
everyone again without clearing the list), `defaultroom <room>` (room
to auto-join on your *next* connect — changing it mid-session doesn't
move you immediately). All `/set` values save per-user.

**Ignore/shield:** `/twit add|del|list|clear [user]` — per-user
ignore list. `/shield [on|off]` — refuses to send/show sysop
broadcasts while active.

**Client-only:** `/scroll [n|up|down|bottom]`, `/clear`, `/mentions`,
`/termsize`, `/raw <text>`, `/dlchatlog` (download your scrollback).

## What the terminal screen shows

- **Status bar** (row 1): current room, topic, mention count, latency,
  AFK flag, optional clock.
- **Scrolling ticker** (row 2, toggle with `/set ticker`): hub
  banners/stats plus rotating tips.
- **Nick-list sidebar**: everyone in the current room, on terminals
  ≥100 columns wide.
- **Tab-complete**: usernames seen in chat or via `/who` autocomplete
  on Tab.

## Long messages

The MRC hub caps a single wire message at 140 characters. A message
you type that's longer than that is split into multiple `(1/n)`,
`(2/n)`, … chunks and sent with a small delay between each chunk (the
hub rate-limits chunks sent back-to-back). If you notice a brief pause
partway through someone's long message, that's this splitting at
work, not lag.

## Bridged into web/terminal

Once MRC is wired up, [[Chat]] users on the local BBS see MRC traffic
as if it were native — web and terminal share the same rooms and
identity through the one bridge connection.
"""),

    # ------------------------------------------------------------------
    ('rss-reader', 'RSS Reader', """
# RSS Reader

Built-in RSS / Atom aggregator for BBS-scene news and anything else
you want to read alongside your messages.

## Where

- Web: `/rss/`
- Terminal: main menu *R*

## What it does

- Subscribes to a set of feeds (sysop-managed; users can't add yet)
- Polls each feed in the background (default 30-minute cycle — see
  "Adding a feed" below)
- Renders the latest items in a unified "river" view or per-feed
- Marks items read/unread per user
- Limited HTML inside items is sanitized via bleach

## Default feed

Out of the box you'll see **X-News** (`https://x-bit.org/rss/rss.xml`),
a good BBS-scene news source.

## Adding a feed

`/admin/rss/` → **Add Feed**. URL is required; everything else is
optional. The poller will fetch on its next cycle (default 30
minutes; bump it down to test).

## Categories

Feeds can be tagged into categories (`scene`, `tech`, `news`, …).
The web reader can filter by category once you have a few feeds.

## Reading on terminal

The terminal reader doesn't render HTML — it falls back to the
plain `summary` field. Item bodies that are HTML-only get
collapsed by an `html2text` pass at fetch time.

## Per-user read state

Read state is per-user; multiple users can share the same feed list
but each has their own unread badge.
"""),

    # ------------------------------------------------------------------
    ('private-messages', 'Private Messages', """
# Private Messages

One-on-one mail between local users. Distinct from [[Echomail]]
(networked discussion areas) and [[Netmail]] (FidoNet point-to-point).

## Send

- Web: `/messages/compose`
- Terminal: main menu *N* on systems with the v2 main menu

The compose form takes a recipient username, subject, and body.
Markdown is supported in the body.

## Inbox

`/messages/` (web) or main menu *P* (terminal). Unread messages
have a bold/yellow row. The badge in the top nav shows the unread
count.

## Reply

Standard `>` quoting for replies.

## Block list

Bothered by a user? `/blocks/` lets you block them. Blocked
users can't PM you; you can still see their public posts.

## Deletion

Soft-delete only — the message is hidden from your inbox but still
exists for the other party.
"""),

    # ------------------------------------------------------------------
    ('notifications', 'Notifications', """
# Notifications

The bell icon (top nav) is a per-user in-app inbox — `/notifications/`
— that fires for things that need your attention, without needing to
go check every page yourself.

## For every user

- **Mentions** — someone typed `@yourusername` in a post, PM, or
  shout.
- **Replies** — someone replied to a thread you posted in.
- **Private messages** — a new [[Private Messages|PM]] arrived.
- **Subscriptions** — a new post landed in a board you've
  [[Message Boards|subscribed]] to.
- **Achievement unlocks** — a badge/achievement you earned.

## For sysops — admin-review notifications

Every `is_admin=True` user additionally gets notified about five
things that need a sysop's review, so nobody has to manually check
each admin page for new work:

- **MSP federation registry join requests** — once a registrant
  verifies their contact email (not the earlier, unverified
  registration step, which isn't yet actionable). See
  [[ANotherNetwork]].
- **QWK node applications** — a sysop applied for a QWK node number,
  via the API or the terminal wizard. See [[QWK]].
- **New users pending NUV approval** — a new signup needs manual
  sysop approval. Plain email-verification-only signups don't trigger
  this — those resolve themselves with no sysop action needed.
- **Unknown/bad echomail areas** — inbound mail arrived tagged for an
  area this BBS doesn't carry. Fires once per newly-discovered area,
  not once per message. See [[Echomail]].
- **Network join applications** — someone submitted the public
  "apply to join this network" form. See [[ANotherNetwork]].

## Preferences

`/notifications/settings` lets you turn any kind off individually —
default is on for everything. The five admin-review kinds only show
up on this page for admin accounts; a regular user's settings page
never shows toggles for notifications they could never receive.

## See also

- [[Sysop Guide]]
- [[Sysop Control Panel]]
"""),

    # ------------------------------------------------------------------
    ('instant-messages', 'Instant Messages', """
# Instant Messages

Inter-BBS real-time IM using the **Message Send Protocol** (MSP,
RFC 1312). Port **18/tcp**.

## What's the use?

Synchronet, Mystic, ANetBBS and a handful of others all speak MSP.
A sysop pings you live from another BBS; you see a toast in the
web UI (or a `>>> message <<<` line on terminal) and can reply.

It's a low-friction way to make sure your neighbour is alive without
sending netmail.

## Sending

`/imsg/send` — pick a target from the [[Instant Messages#Directory|BBS directory]] or type
`username@host` manually. Up to 8000 chars per message (the MSP wire
protocol itself allows up to 8192 bytes).

## Receiving

Inbound MSP arrives on TCP 18. The listener stores it in the
`InstantMessage` table; the unread badge in the nav shows the
count. `/imsg/` is your inbox.

## Network reachability

You need TCP 18 reachable from the outside, OR your messages will
arrive but you can't be paged back. `/admin/peers/` runs a SYSTAT
probe against federation-hub peers (ANotherNetwork registry entries)
as a general "is my BBS reachable" health check — it isn't MSP-specific,
but the same reachability it confirms is what MSP paging needs too.

## Directory

`/imsg/directory/` shows the BBS directory. It's populated by pulling
a `sbbsimsg.lst`/`anetbbs.lst` file from a master directory URL — the
"Directory refresh" button on that page (`/imsg/directory/refresh`) —
not by manually adding entries in admin.

## Encoding

MSP is 8-bit clean. Outbound messages are encoded as UTF-8. Inbound
messages are decoded as UTF-8 first, falling back to latin-1 only if
that fails — so a peer sending plain CP437/latin-1 text still comes
through readable, but UTF-8 is what ANetBBS actually sends and
prefers on the wire.
"""),

    # ------------------------------------------------------------------
    ('files', 'Files', """
# Files

The BBS file library — uploads, downloads, descriptions, areas.

## Layout

| Concept | What it is |
|---------|------------|
| **File** | A single uploaded file with description + uploader |
| **File area** | A bucket of files — e.g. "Software", "Music" |
| **TIC** | A FidoNet-distributed file echo (network-replicated) |

## Browsing

- Web: `/files/` shows a tree of areas. Click into one to see
  the file list with descriptions and download links.
- Terminal: main menu *F*. Terminal downloads happen via Zmodem
  if the client supports it; otherwise base64-into-a-pager
  fallback.
- FTP: if the sysop has turned it on (`FTP_ENABLED`, off by
  default), any plain FTP client can browse and download from the
  same file areas — anonymous access is read-only and limited to
  active, non-sysop-only areas; logged-in users can also upload
  where an area's upload permission allows it. Default port `21`.

## Daily download quota

Sysops can cap how much a user downloads per day, scaled by access
level, under `/admin/file-quotas` (Admin > File System > Download
Quotas) — a list of (minimum access level, daily quota in MB) tiers. A
user gets whichever tier has the highest access level they still
qualify for; a level with no tier configured at or below it has
unlimited downloads. Admins always bypass. The quota is enforced
everywhere a file can be downloaded — web, terminal (Zmodem/Ymodem/
Xmodem), PETSCII (Xmodem), and FTP — and resets at Eastern midnight.
If you hit your quota mid-download, the transfer is rejected outright
with a message showing how much you have left.

## Uploading

Logged-in users can upload to the **Uploads** area by default.
Sysops can change which areas accept user uploads, and set a minimum
access level per area (an area can also be sysop-only).

Per-file metadata is just a description — optional, since it can be
auto-extracted (see below). There's no tags/license/"replaces older
file" metadata today.

Two safety checks run automatically on every upload: an **archive
integrity test** (corrupt zip/tar/7z/rar files are rejected outright)
and a **content-hash dedup check** (warns if the same file — by
content, not filename — already exists somewhere in the file base).

## Virus scanning

The sysop can wire ClamAV via `/admin/virus-scan`. Uploads are
scanned on arrival; infected files are quarantined to a sysop-only
area.

## Descriptions

If you didn't type one, ANetBBS auto-extracts a description from
`FILE_ID.DIZ`, `README.md`/`README.txt`, or `DESCRIPT.ION` found
inside the upload — supported archive formats are `.zip`, `.rar`,
`.7z`, `.lha`/`.lzh`, and `.tar.gz`/`.tar.bz2`/`.tar.xz`. Common
BBS-scene practice, extended to modern archive formats too.

## TIC distribution

For files received via [[TIC Processor]], the file lands in the
configured TIC area, its `.tic` companion is parsed, and the
description is read from the `Desc:` line.

Every install ships with 9 such areas already configured (though
unsubscribed by default) under [[ANotherNetwork]] — tags
`ANN.FILES.NODELIST`, `ANN.FILES.INFOPACK`, `ANN.FILES.BBSSOFT`,
`ANN.FILES.DOORS`, `ANN.FILES.EBOOKS`, `ANN.FILES.LINUX`,
`ANN.FILES.RETRO`, `ANN.FILES.ANSIART`, and `ANN.FILES.TEST`.

This works in both directions: uploading a file into *any*
network-attached file area (not just ANotherNetwork's) automatically
queues it for outbound distribution to every subscribed peer — no
separate "publish" step. See the Outbound section of
[[TIC Processor]] for exactly which upload paths trigger this.
"""),

    # ------------------------------------------------------------------
    ('ansi-screens', 'Custom ANSI Screens', """
# Custom ANSI Screens

ANetBBS supports replacing every built-in menu header and session screen
with a sysop-supplied CP437 ANSI art file. If the file is present it is
displayed; if absent the built-in menu renders as normal. No restart needed —
the file is read fresh on every menu entry.

## How it works

Drop a standard CP437 `.ans` file (Moebius, PabloDraw, TheDraw, etc.) into
the correct directory with the slot name shown below. The BBS reads it as raw
bytes and sends it directly to the terminal, so every block graphic and color
code reaches the user exactly as drawn.

## Slot reference

### Session screens — `data/text/<slot>.ans`

These appear at connection time, not inside a menu loop.

| Filename | When it appears |
|----------|-----------------|
| `welcome.ans` | Login / welcome screen (shown before the main menu) |
| `newuser.ans` | Shown to a user who just created an account |
| `goodbye.ans` | Logoff screen |

### Menu headers — `data/text/menus/<slot>.ans`

These replace the banner at the top of the named menu. The menu items
(hotkeys, labels, prompt) are still rendered below the ANSI art.

| Filename | Menu |
|----------|------|
| `main.ans` | Main BBS menu |
| `game_center.ans` | Game Center (top-level games menu) |
| `door_games.ans` | Door Games list (shows installed doors) |
| `chat.ans` | Chat Systems menu |
| `irc_chat.ans` | IRC Chat menu |
| `dialout.ans` | Dial Out — Visit Another BBS |
| `sysop_users.ans` | Sysop -> Manage Users list header |
| `sysop_boards.ans` | Sysop -> Manage Boards list header |
| `sysop_status.ans` | Sysop -> Server Status header |

(The Sysop Tools top-level menu itself is a scrolling lightbar screen
with fixed row positions, so it can't take a custom art header the way
these can -- it always shows the standard banner.)

Any menu you create through **Admin → Menus** also supports a file override
using the menu's name as the filename (e.g. a menu named `utilities` →
`data/text/menus/utilities.ans`).

## Widescreen (132-column) support

Session screens (`welcome`, `goodbye`, `newuser`, and any custom `ansi`
slot) also support a widescreen variant for terminals that negotiate
132 columns: drop a `<slot>132.ans` file alongside the regular
`<slot>.ans` and ANetBBS serves it automatically to those sessions
instead of the 80-column art. There's also a `<slot>.asc` plain-ASCII
fallback for terminals that can't render ANSI at all. ANetBBS checks
these file-based drop-ins before falling back to the database-driven
screen, so they're the fastest way to swap in art without touching
Admin.

## Multiple screens in one login (classic multi-logon-screen style)

Drop in more than one numbered file for the same slot and ANetBBS shows
**all of them**, in order, every login — the same idea as Synchronet's
`logon1.ans`/`logon2.ans`/`logon3.ans` convention:

```
data/text/welcome132.ans      <- shown first (the plain file counts as #1)
data/text/welcome132_2.ans    <- shown second
data/text/welcome132_3.ans    <- shown third
```

Numbers don't need to be contiguous — whatever `_N` files exist are used
in ascending order. This works independently per screen/variant
(`welcome`, `welcome132`, `welcome.asc`, `goodbye`, etc. each get their
own sequence), and a single file behaves exactly as before — nothing
extra to set up unless you actually want more than one.

Each variant controls its own pause — ANetBBS doesn't insert one
automatically between screens in a sequence. Put `@PAUSE@` at the end of
a variant if you want the visitor to press a key before the next one
loads.

### Random variant: `_ran` naming

Want a different single screen picked at random each login instead of
the whole sequence? Use `_ran` in the filename in place of the plain
numbered naming:

```
data/text/welcome132_ran.ans      <- random variant #1
data/text/welcome132_2_ran.ans    <- random variant #2
data/text/welcome132_3_ran.ans    <- random variant #3
```

With `_ran` naming, each login shows **one** file from the group,
chosen at random, instead of the whole sequence. If both a `_ran` group
and a plain numbered group exist for the same slot, the `_ran` group
wins. Works for any screen — mix and match per-screen as you like (full
sequence for `welcome`, random-pick-one for `goodbye`, say).

## File format

Standard CP437 ANSI art. SAUCE records (metadata appended by editors) are
harmless — the file is sent as-is, so keep the file 23–25 lines tall to
avoid pushing the menu items off screen.

Recommended editors:
- **Moebius** (cross-platform, free)
- **PabloDraw** (Windows/Mac)
- **TheDraw** (DOS classic, runs in DOSBox)

## Permissions

Files must be readable by the service user (`anetbbs`). After copying via
SCP as your own login, set ownership:

```bash
sudo chown anetbbs:anetbbs /opt/anetbbs/data/text/menus/game_center.ans
```

Or fix the whole directory at once:

```bash
sudo chown -R anetbbs:anetbbs /opt/anetbbs/data/text/
```

## Removing an override

Delete (or rename) the `.ans` file. The built-in menu returns on the next
visit — no restart needed.

## See also

- [[Themes]] — web UI color palette
- [[Sysop Guide]]
"""),

    ('themes', 'Themes', """
# Themes

The web UI's color palette is variable-driven. ANetBBS ships with
several themes plus a custom-theme editor.

## Default themes

| Slug | Look |
|------|------|
| `modern-dark` | High-contrast Catppuccin-style slate (recommended, default) |
| `classic-green` | Green-on-black classic BBS terminal look |
| `amber-terminal` | Warm amber on dark brown |
| `blue-ice` | Cyan accent on deep navy |
| `matrix` | Bright neon green on pure black |
| `synthwave` | Hot pink + cyan on dark purple |
| `paper-white` | Light theme for daytime use |
| `enhanced` | "VOID SIGNAL" — triple neon (green/cyan/magenta) on black, scanlines, glitch effects |
| `hackers` | "HACKERS (1995)" — neon violet/lime/cyan cyberpunk homage |

## Per-user

`/profile/edit` → Theme. Stored on `User.theme_id`.
The selected theme's CSS variables are emitted in a `<style>` tag at
the top of every page (see `base.html`).

## Site-wide default

Sysop sets one theme as `is_default=True`. Anonymous visitors and
new accounts see this theme until they change it.

## Custom themes

`/admin/theme-builder` lets the sysop author a new theme. Pick
variable values from a color-picker; preview live; save. Every theme
is just a set of CSS variables — `--theme-bg`, `--theme-primary`,
`--theme-text`, etc.

## Light themes

ANetBBS is dark-by-default — every list-group, table-row tint, and
alert palette is hand-tuned for dark backgrounds. `paper-white` is the
one shipped light theme; building another light theme requires
overriding the same "dark" assumptions in base.html.

## Terminal menu art

The web theme system doesn't affect the telnet/SSH/rlogin menus —
those use CP437 ANSI art files instead. See [[ansi-screens|Custom ANSI Screens]]
for the full slot reference and how to drop in your own `.ans` files.
"""),

    # ------------------------------------------------------------------
    ('sysop-guide', 'Sysop Guide', """
# Sysop Guide

The long-form admin overview. Newer than the README, less prescriptive
than the [[Tutorial]]. Something broken? Check [[Troubleshooting]]
first — it collects the fixes for errors sysops actually hit in
production.

## What runs

ANetBBS is a handful of services:

| Service | What it does |
|---------|--------------|
| `anetbbs-web` | eventlet's native WSGI server (`deploy/serve.py`, `socketio.run()`), single process. Serves the web UI, REST, sockets. |
| `anetbbs` | asyncio listener on 2233/2234/513 — telnet, SSH, rlogin (one process, which protocols actually start is driven by `.env` flags) |
| `anetbbs-mrc-bridge` | persistent MRC connection |
| `anetbbs-finger` | RFC 1288 Finger daemon |
| `anetbbs-binkp` | FidoNet BinkP inbound listener |

`systemctl status anetbbs-web anetbbs anetbbs-mrc-bridge anetbbs-finger anetbbs-binkp`
shows the lot.

## Daily

- Glance at the [[Sysop Control Panel]] for service health and
  who's online.
- Check the [[NodeSpy]] panel for any stuck sessions; kick if needed.
- [[Echomail]] polling runs on its own — a background poller thread
  inside `anetbbs-web` polls each network on its configured interval
  (default every 60 min), no cron needed. Verify
  `/admin/echomail/logs` for errors.
- Approve any [[Files|files]] flagged by the virus scanner.
- If you run the network hub: peek at the hatch/hold queue under
  Hub Management (see below) — a backlog there means outbound files
  or messages aren't reaching peers.
- Check the notification bell: it flags five things that need a
  sysop's review — MSP federation join requests, QWK node
  applications, users pending NUV approval, unknown/bad echomail
  areas, and network join applications. Each admin can toggle these
  independently at `/notifications/settings` (admin-only toggles).

## Weekly

- Run [[Backup]].
- Skim site activity — top users, new registrations, message
  counts.
- Read the latest fido news area, post a hello if you've been
  quiet.

## Monthly

- Rotate logs — there's no external `logrotate` config; it's
  built in as the `log_rotate` scheduled event (any `<install>/logs/*.log`
  over a size threshold, default 50MB, gets renamed to `.1` and a
  fresh file starts). See **Scheduled events** below to check its
  schedule or run it manually.
- Review user registrations: ban obvious spam accounts.
- Update door binaries if their authors have patched.
- Check [[Instant Messages#Network reachability|peers]] for dead links (`/admin/peers/health`).

## Hub Management

If this install is the designated network hub
(`REGISTRY_MODE_ENABLED=true` — see [[ANotherNetwork]]), an extra
admin surface appears at `/admin/echomail/hub/`:

- **BinkP nodes** and **QWK nodes** — add/edit/delete downstream
  nodes, subscribe/unsubscribe them from areas, reset a QWK node's
  high-water mark.
- **Hold queue** — BinkP-only: outbound echomail queued per
  downstream node, flushed on its next poll. Purely a delivery queue
  (pending/sent status) — no moderation step involved.
- **Node requests** — the approve/deny queue for BBSes applying for
  a node number (terminal wizard or the peer-facing `/qwkhub/apply`
  endpoint both land here).
- **Join Form** (`/admin/echomail/hub/join/requests`) — a SEPARATE
  review queue from Node requests above, for the public self-service
  `/join/` application form (see [[ANotherNetwork]]) — a prospective
  member fills in their BBS name/location/software/telnet address, an
  optional BinkP address (crash/hold) and/or QWK packet ID, notes, and
  acknowledges the rules; no file upload or echo/file-echo picker is
  part of the form. Each pending application has a full detail/View
  page before you decide, with Approve/Deny/Archive actions right
  there. Approving auto-creates the BinkP and/or QWK node record(s)
  (BinkP node numbers auto-assign from the hub's own configured
  zone:net) and emails credentials; a rules-file upload and other
  config for the form itself live under **Join Form** too.
- **Generation & Distribution** — generate the nodelist right now
  (in addition to its weekly schedule), preview a QWK packet for a
  node without marking anything as sent, and see TIC/file
  distribution status.

A peer install (not the hub) doesn't see this section — it has
nothing of its own to manage there.

## PETSCII custom menus

If [[PETSCII]] (C64/128) support is enabled, build custom PETSCII
menus at `/admin/petscii-menus/` — a separate tree from the ANSI
custom-menu system, with a much smaller action set (boards, echomail,
PMs, files, who's-online, profile, Number Guessing, goto-another-menu,
logoff) since most ANSI actions have no PETSCII equivalent. No
default menu is required — without one, PETSCII sessions fall back
to the built-in Phase 1 menu unchanged.

## Scheduled events

`/admin/events/` lets a sysop automate routine maintenance instead
of doing it by hand, each on its own daily/hourly/weekly/interval
schedule (times are UTC). Ten built-in handlers ship: a `noop` test
handler, log rotation, a security-update check, SQLite `VACUUM`,
TradeWars 2002 daily maintenance, generating the ANotherNetwork
nodelist, an arbitrary shell command, and three InterBBS inbound-sync
handlers (Wall, Last Callers, Game Scores) that auto-appear when
those features are turned on. Worth a look if you're tired of
remembering to do the **Monthly** items above yourself. Full handler
table and worked examples: [[Scheduled Events]].

## On upgrade

1. Take your own backup if you want extra safety beyond what
   `update.sh` snapshots automatically (see [[Backup]]).
2. `sudo bash update.sh --install-dir /opt/anetbbs` — this stops
   services, backs up `.env`/the DB/systemd units, applies the new
   code, migrates the schema, and restarts everything itself. No
   manual stop/start needed.
3. Hard-refresh your browser to bust cached CSS.
4. Watch `journalctl -u anetbbs-web -f` for a minute to catch
   any column-add migration noise.

## Where things live

- Install: `/opt/anetbbs/` (this site)
- Data: `/opt/anetbbs/data/` (sqlite + uploads)
- Logs: `/opt/anetbbs/logs/` (rotated)
- Doors: `/opt/anetbbs/doors/<name>/`
- Echomail spool: `/opt/anetbbs/data/echomail/binkp/{inbound,outbound}/`

## Admin tools reference

Smaller admin pages that don't warrant their own wiki page, grouped by
what they manage. All are under **Admin** in the navbar.

**Users & access**

| Tool | Path | What it does |
|------|------|--------------|
| New User Questions | `/admin/newuser-questions` | Custom sign-up questions shown right after registration; answers are stored per user. |
| Inactive Users | `/admin/inactive-users?days=N` | Accounts with no login in N days (default 90) — mass PM, deactivate, or delete. |
| Registration Attempts | `/admin/registration-attempts` | Paginated log of every signup attempt, success and failure. |
| Chat Bans | `/admin/chat-bans` | Mute a user from MRC chat — one room or all, optional expiry. Expired bans auto-clear the next time you open the page. |
| Time Budgets | `/admin/time-budgets` | Per-user daily time-online allowance / time-bank balance. |

**Messages**

| Tool | Path | What it does |
|------|------|--------------|
| Default Echo Subs | `/admin/default-echos` | Toggles which echo areas are subscribed install-wide — a global default, not yet a per-new-user template. |

**Network**

| Tool | Path | What it does |
|------|------|--------------|
| IRC Server Presets | `/admin/irc-presets` | The sysop-curated server list shown in the terminal [[IRC Client]] menu. |

**System**

| Tool | Path | What it does |
|------|------|--------------|
| Setup Wizard | `/admin/setup-wizard` | First-run config — BBS name, sysop info, theme, seed MOTDs, default echoes. Idempotent, safe to re-run any time. |
| Preflight Checklist | `/admin/preflight/` | Green/red pre-launch readiness probes (disk space, DNS, firewall, email config, and more) with a one-line fix for anything that fails. Meant for right after install and again just before announcing the BBS publicly. |
| Security Updates | `/admin/security/` | Shows the daily 04:00 UTC OS-package security-patch report; "run scan now" runs it on demand instead of waiting. |
| Door Errors | `/admin/door-errors/` | Parses `logs/door-errors.log` (crash traces from the door wrapper) into a readable list; clear it once you've acknowledged the breakage. |
| Connection Test | `/admin/connection-test` | Probes telnet/SSH/rlogin/web/finger/IRC/BinkP ports against any host (defaults to your own) — latency plus whatever banner comes back. Handy for "is my firewall actually open" from the sysop seat itself. |
| Activity Log | `/admin/activity` | Paginated feed of user activity, filterable by type/user/IP. |
| Check for Updates | `/admin/upgrades/` | Compares your local version against the configured registry and offers one-click download + install — sha256-verified against the same API response before anything is extracted. |
| Logon/Logoff Modules | `/admin/login-modules/` | Attach an action — graffiti wall, an ANSI screen, a shell command, or a native/Python door — to run automatically at logon or logoff. |
| Pre-update Backups | `/admin/backups/` | Browse the snapshots `update.sh` takes before every upgrade (`.env`, both databases, systemd units, nginx config if present). Restore `.env` or the database from one, or delete old ones. Only the 3 most recent are kept. |

## See also

- [[Sysop Control Panel]]
- [[Door Setup]]
- [[BinkP Setup]]
- [[PETSCII]]
- [[Backup]]
- [[Tutorial]]
- [[Troubleshooting]]
"""),

    # ------------------------------------------------------------------
    ('troubleshooting', 'Troubleshooting', """
# Troubleshooting

Real symptom-to-fix entries pulled from actual sysop reports and
live-debugging sessions across every ANetBBS subsystem. If Ask Anet
help search (or this wiki's own search) sent you here, use your
browser's find-in-page to jump to the exact wording.

## Web / nginx

- **MRC (or other static assets) 404 / fails to load through nginx,
  but works fine going straight to the Flask port**, and the
  `/static/` `alias` path and file permissions all look correct → if
  your install directory is under a user's home directory (e.g.
  `/home/sysopname/anetbbs` instead of `/opt/anetbbs`), Ubuntu/Debian
  gives new home directories `750` permissions by default — that
  blocks nginx's worker user from even traversing into the home
  directory to reach the static files, regardless of what the files
  themselves are set to. Fix:
  ```
  sudo chmod o+x /home/sysopname
  ```
  (Only adds "can traverse" for others, not "can list/read" — nginx
  doesn't need more than that to serve files under it.)
- **nginx returns a blank page / 502**, its own error log shows
  `connect() to 127.0.0.1:5000 failed (13: Permission denied)`
  repeating for every request, even though `nginx -t` passes clean
  and the service is "active (running)" → SELinux enforcing mode
  (the default on Fedora/RHEL/CentOS) blocks nginx from making
  outbound connections to backend ports unless explicitly allowed.
  `install.sh`/`update.sh` set this automatically, but if you set up
  nginx manually, or `setsebool` wasn't installed at the time, fix it
  directly:
  ```
  sudo setsebool -P httpd_can_network_connect 1
  ```
  (needs `policycoreutils-python-utils` on Fedora/RHEL if
  `setsebool` itself is missing.)
- **"The CSRF session token is missing" on web login** → your site is
  running over plain HTTP but `SESSION_COOKIE_SECURE=true`. Fixed by
  default as of v1.0a2.72. On older installs:
  ```
  echo "SESSION_COOKIE_SECURE=false" | sudo tee -a ~/anetbbs/.env
  sudo systemctl restart anetbbs-web
  ```
- **"unable to open database file" on telnet/SSH** → fixed in
  v1.0a2.74. If you're on an older version, add `DATABASE_URL` to
  `.env`:
  ```
  INSTALL_DIR=~/anetbbs   # adjust if different
  echo "DATABASE_URL=sqlite:///${INSTALL_DIR}/data/anetbbs.db" | sudo tee -a "$INSTALL_DIR/.env"
  sudo systemctl restart anetbbs
  ```
- **"SECRET_KEY is the dev default" warning** → set a `SECRET_KEY`
  env var, or a `RuntimeError` will hit you the moment production
  mode notices.
- **`/admin/echomail/...` returns 500 after upgrading** → restart the
  web service so the auto-migration adds the new columns.
- **Web service stuck in a restart loop with `EADDRINUSE` on :5000**
  → a previous `anetbbs-web` process (eventlet's native WSGI server,
  `deploy/serve.py`) is leaking past its own shutdown. Our systemd
  unit ships with `KillMode=mixed` to prevent this, but if you
  adopted an older unit file, add `KillMode=mixed` and
  `RestartSec=10` to `[Service]` and `daemon-reload`.
- **Web interface loads fine but telnet/SSH won't connect** → check
  the firewall and that the terminal service is actually running:
  ```
  sudo ufw status
  sudo systemctl status anetbbs
  sudo journalctl -u anetbbs -n 50
  ```

See also [[Web Access]] and [[Sysop Guide]].

## FTPS / certificates

- **FTPS (`AUTH TLS`) worked when you set it up, then stopped working
  weeks later** with no config change on your end → certbot resets
  `/etc/letsencrypt/archive/` to `0700 root:root` on every
  certificate renewal by default, which revokes the service user's
  read access to the cert/key it was using. `install.sh` (when it
  obtains a cert via certbot) and `update.sh` (whenever
  `FTP_TLS_CERTFILE` in `.env` points at `/etc/letsencrypt/...`) both
  install a renewal hook
  (`/etc/letsencrypt/renewal-hooks/deploy/anetbbs-ssl-cert-perms.sh`)
  that restores the correct permissions after every renewal, so this
  should now be handled automatically whether or not FTPS was
  already turned on at install time. If you're on an install that
  predates this and hit the problem, re-run `sudo bash update.sh` to
  get the hook installed retroactively.

See also [[Files]] (FTP access section).

## MRC chat

- **MRC `<no name>` in Synchronet's IM display** → Synchronet IDENTs
  (RFC 1413) the sender to look up a "real name." ANetBBS doesn't
  ship an identd; this is a known cosmetic-only limitation.
- **BBS info fields (telnet/ssh/website/description/sysop) never
  show up when other MRC clients look this BBS up** (`/bbses` +
  `/info <n>` on another client), even with a correctly-filled-in
  `mrc/bridge/config.json` and zero errors anywhere → check whether
  the MRC bridge's systemd unit sets a custom `MRC_BRIDGE_CONFIG`
  environment variable pointing at a different path than the one
  you're editing:
  ```
  systemctl show anetbbs-mrc-bridge -p Environment
  ```
  If it does, edit *that* file instead — the running service never
  reads the default path once an override is in place. More
  generally: if a config file looks correct and nothing is logging
  an error, but the feature still doesn't reflect your changes,
  suspect an environment variable silently redirecting where that
  service actually reads its config from.

See also [[MRC]] and [[Chat]].

## BinkP / Echomail

- **`MSP: cannot bind ... Permission denied`** in `bbs.log` → apply
  one of the privilege fixes for binding low ports (see the
  [[Sysop Guide]] service table, or re-run `install.sh` which sets
  `AmbientCapabilities=CAP_NET_BIND_SERVICE` on the relevant unit).
- **A network's poller looks like it dials the BBS's own install
  instead of a real remote peer, and nothing ever seems to happen**
  → for BinkP, this happens when `our_address` equals `hub_address`;
  for QWK, when `qwk_host` equals this BBS's own public host. The
  poller detects this ("self-referential poll skip") and safely
  skips the dial-out instead of failing — this is expected and
  harmless on the hub's own install, since the seeded "point at the
  hub" network row needs to exist and be configured exactly that way
  for *other* sysops to reach the hub. Areas stay visible either way.
  As of v1.0b2.41 the skip produces **no poll log entry at all**
  (just an invisible-at-default-level debug line) — earlier versions
  logged a `status='skipped'` row every time the poller loop
  re-checked the network (once a minute), flooding **Poll Logs**
  with dozens of identical entries within about 20 minutes of
  uptime.
- **Don't poll too aggressively** — `poll_interval_minutes` defaults
  to 60 and is hard-floored at 5 (setting it lower gets silently
  clamped back up), but the floor existing doesn't mean polling
  every 5 minutes is a good idea for a real remote uplink. Some hubs
  rate-limit or temporarily block a peer that connects too
  frequently, which makes a genuinely failed poll harder to recover
  from, not easier. 30–60 minutes is normal; only go tighter if the
  hub's own docs say it's fine. Separately: as of v1.0b2.114, a poll
  that fails no longer retries on the very next scheduler tick — it
  correctly waits out the configured interval before trying again.
- **No poll transcript for an inbound session** (a peer connecting TO
  this BBS, not this BBS dialing out) → fixed in a later v1.0b2
  release. Before that fix, only outbound polls saved a frame-by-frame
  session transcript; inbound sessions created a poll log row but
  never captured one. If you're on an older version, the workaround is
  a full BinkP debug capture at the OS level (`tcpdump`/`socat` in
  front of the listener) since there's no in-app transcript to fall
  back on before that fix.

See also [[BinkP Setup]], [[Echomail]], and [[ANotherNetwork]].

## Door games / Synchronet / Mystic

- **Synchronet door fails silently** → check the log for the door
  child's stderr; if no `jsexec` is found, the Node.js shim runs but
  doesn't cover every Synchronet API. Install Synchronet itself for
  full compatibility.
- **BotWars / RDQ3 fails with `EACCES: permission denied` reading
  `sbbs_stubs/sbbsdefs.js`** or writing a save file → the service
  user needs read/write group access. `install.sh` sets this on
  every run; if you used a manual rsync deploy that reset the perms,
  run:
  ```
  sudo chmod -R g+rX,o+rX /opt/anetbbs/anetbbs/games/sbbs_stubs
  sudo chmod -R g+rwX /opt/anetbbs/doors
  ```
- **`bbs.log` `PermissionError`** → the service user needs write
  access to the install dir. `install.sh` `chown`s it on every run;
  if you re-pointed the unit file at a different install dir, mirror
  the perms there.
- **`snap-confine is packaged without necessary permissions` /
  `cap_dac_override not found`** → your `dosbox`/`dosbox-staging` is
  a snap symlink. Snap-packaged binaries can't run from systemd
  services that only grant `CAP_NET_BIND_SERVICE`. Fix:
  ```
  sudo snap remove dosbox dosbox-staging dosbox-x   # whichever is installed
  sudo apt install dosbox                            # apt vanilla works for BBS doors
  # OR install dosbox-staging from a GitHub release tarball into /opt/dosbox-staging
  # OR change the Game type to door_dosemu and install dosemu2:
  #   Debian/Ubuntu: sudo apt install dosemu2
  #   Fedora/RHEL:   enable RPM Fusion or a COPR providing dosemu2, then dnf install dosemu2
  #   Arch:          AUR-only — yay -S dosemu2 (or paru)
  #   openSUSE:      check the Packman repo, or build from source
  ```
  As of v278, the BBS auto-rejects snap-packaged binaries up front
  with this exact message instead of letting you discover it the
  hard way mid-launch.
- **Door 404 / "executable not found"** → `executable_path` doesn't
  point at a real file. Check the path with `ls`. Tokens are
  expanded before the file check, so `%P` only resolves to a real
  per-node dir if `BBS_NODES >= node_number`.
- **Drop file written but door reads garbage** → wrong drop file
  type. LORD wants `door32.sys`. Older doors want `dorinfo` or
  `door.sys`. Check the door's docs.
- **`mplc` failures** → set `MYSTIC_MPLC_PATH` to the absolute path
  of a known-good `mplc`, or re-run `install.sh` and accept the
  Mystic download step. Errors are logged to `journalctl -u
  anetbbs-web` (web launches) or `bbs.log` (terminal launches).
- **DOSBox doors freeze on launch** → likely the TCP nullmodem
  bridge isn't binding. Check the per-node DOSBox config the runner
  generates in `<install>/data/temp/nodeN/dosbox.conf` —
  `serial1=nullmodem port:NNNN` should match what the bridge is
  listening on.
- **dosemu2 commands fail with "TERM environment variable needs
  set"** → export `TERM` and pass `-td` in your script (relevant if
  you're driving dosemu2 from a scheduled event's shell handler, not
  just doors).
- **`door_dosemu` game crashes with `ERROR: MFS: failed to get
  xattrs for .../SOMEFILE.LOG, Numerical result out of range`** →
  the filesystem your DOS game data lives on (e.g. `/opt`) is
  mounted without the `user_xattr` option, which dosemu2's MFS host
  passthrough needs. Not an ANetBBS bug — add it in `/etc/fstab`:
  ```
  UUID=xxxx-xxxx /opt   ext4   defaults,user_xattr   0   2
  ```
  then `sudo mount -o remount /opt` (or reboot) and relaunch the door.

See also [[Doors]], [[Door Setup]], [[LORD Setup]], [[DosBridge]],
and [[DOS Door Recipe]].

## Docker / containers

- **`docker: permission denied`** → you're not in the `docker` group
  yet, or you added yourself but didn't open a new terminal.
- **Build fails on a specific pip package** → save the full output
  and report it; some dependency may need a from-source build on
  your specific CPU architecture, which can need an extra system
  package.
- **A service shows `FATAL`/`BACKOFF` in `supervisorctl status`** →
  run `supervisorctl ... tail <program>` (single-container) or
  `docker compose logs <service>` (compose) to see why it's
  crash-looping.
- **Port already in use** → something else on your machine (maybe a
  bare-metal ANetBBS install!) is already using that port. Either
  stop the other thing or change the *host* side of the `-p
  host:container` mapping (e.g. `-p 5001:5000` to use 5001 on your
  machine instead).
- **Can't reach the web UI / telnet from another device on your
  network** → confirm the port mappings are actually published
  (`docker ps` shows them in the "PORTS" column) and that your
  machine's firewall allows those ports.
- **MSP (18) and SYSTAT (11, UDP) refuse to bind inside the
  container** → those need `cap_add: [NET_BIND_SERVICE]` in compose
  (mirrors the bare-metal systemd unit's
  `AmbientCapabilities=CAP_NET_BIND_SERVICE`). If your platform
  doesn't allow `cap_add`, override `MSP_PORT`/`SYSTAT_PORT` in
  `.env` to unprivileged values and remap them the same way Finger
  is.

## Webhooks

- **"Last status" stays "—" forever** → the webhook has never
  actually been triggered. Confirm the underlying action has
  actually happened (posted, logged in, etc.) since the webhook was
  created; it won't retroactively fire for anything that already
  happened before it was added.
- **"Last status" shows a real HTTP code (200, 404, 500...) but
  nothing shows up on the receiving end** → that's not an ANetBBS
  problem; the request reached your server, and your server
  responded with that code. Check your receiver's own logs/config.
- **Red "err" badge** → a network-level failure (timeout, DNS,
  connection refused) on the last attempt. Check the URL is correct
  and reachable from the ANetBBS server specifically (not just from
  your own machine).
- **JSON looks malformed on the receiving end** → likely a custom
  body template getting broken by an unescaped `"` or `\\\\` in a
  free-text field. Switch to a blank template (default JSON
  encoding) if you don't need a specific non-default shape.
- **`{something}` shows up literally, unsubstituted, in the
  delivered body** → that key doesn't exist in this event's payload.
  There's no universal placeholder set — check the payload keys
  available for that specific event.
- Webhook delivery has an 8-second timeout and, by design, **no
  retry queue** — a failed delivery just shows the red "err" badge
  above and won't be automatically retried.

## Scheduled events

- **"Params (JSON object)" rejects what I typed** → it must parse as
  a JSON object. `/path/to/script.sh` is not valid JSON; wrap it as
  `{"command": "/path/to/script.sh"}`.
- **`shell` handler crashes with `UnicodeDecodeError`** → fixed as of
  v1.0a2.116+; older builds decoded captured output as strict UTF-8
  and crashed on non-UTF8 bytes (e.g. CP437 from DOS programs run
  via dosemu2/dosbox). Update if you still see this.
- **dosemu2 commands fail with "TERM environment variable needs
  set"** → export `TERM` and pass `-td` in your script.
- **An event always fails the same way** → click **Run now** and
  read the captured output; it's the same stdout/stderr you'd get
  running the command by hand over SSH, just minus your interactive
  shell's environment (PATH, TERM, etc. may differ from your login
  shell — set them explicitly in the script if needed).

## MSP / Instant Messages

- **"Connection refused" on port 18** → the listener never bound.
  Check `bbs.log` for `MSP: cannot bind ... Permission denied` and
  apply one of the privilege fixes above.
- **Sent OK but never received** → check the recipient name on the
  remote BBS. The MSP server falls back from `username` to
  `display_name`, but that's it — there's no fuzzy match.
- **SYSTAT reply garbled** → the responder is probably emitting
  CP437 box glyphs that need rendering. The SYSTAT query client
  decodes UTF-8 first, then falls back to latin-1, so high bytes
  survive but won't look pretty in a plain text view.

See also [[Instant Messages]].

## Raspberry Pi

- **Something reports "disk full" even though `df -h /` shows plenty
  of free space** → check `df -h /tmp` separately. On many Pi images
  `/tmp` is a small RAM-backed `tmpfs` (often under 500MB, sized off
  available memory), completely separate from the real disk `/`
  lives on — filling it up has nothing to do with how much storage
  the SD card/USB drive actually has left.
- **Out of disk space on the SD card generally** → move `data/` to a
  USB SSD if you have one attached. Check usage:
  ```
  df -h
  du -sh ~/anetbbs/data/*/
  ```
- **Pi runs hot / throttles** → add a heatsink and fan. Check
  throttling:
  ```
  vcgencmd get_throttled
  # 0x0 = no throttling, anything else = problem
  ```
- **Low memory warnings** → add swap (a Pi 5 shouldn't need this
  with 8GB, but a Pi 4 4GB might):
  ```
  sudo dphys-swapfile swapoff
  sudo sed -i 's/CONF_SWAPSIZE=100/CONF_SWAPSIZE=1024/' /etc/dphys-swapfile
  sudo dphys-swapfile setup
  sudo dphys-swapfile swapon
  ```
- **Door games on Pi** — in-browser DOS games (DOOM, Duke3D via
  EmulatorJS) run in the user's browser, no server-side binary
  needed, though on a Pi 3 they may not run well in a browser on the
  Pi itself (fine for users connecting from a PC browser). LORD
  (Synchronet JS via Node.js) works on Pi 3+ ARM. DOSBox doors: use
  DOSBox-X, which has ARM builds — install from the DOSBox-X
  releases page and set `DOSBOX_PATH` in `.env`. Wine + `door32.exe`
  doors are possible with box86/box64, but a project for advanced
  sysops.

## RSS reader

- **A feed shows a red badge in the admin feed list** → its last
  fetch failed; the poller sets `feed.last_error` on the feed row
  whenever `feedparser` can't retrieve or parse it. Hit the
  circular-arrow refresh icon next to the feed in `/admin/rss/` to
  retry manually, or check the feed's URL is still valid.

See also [[RSS Reader]].
"""),

    # ------------------------------------------------------------------
    ('sysop-control-panel', 'Sysop Control Panel', """
# Sysop Control Panel

The launchpad for live operations. `/admin/control/`. Admin-only.

## Sections

### Services — the Service Control Center

`/admin/control/` is a real-time dashboard over every systemd unit
ANetBBS runs, not just start/stop buttons. Five unit cards:

- **Web + Federation** (`anetbbs-web`)
- **Terminal Protocols** (`anetbbs`, unified telnet/SSH/rlogin)
- **MRC Chat Bridge** (`anetbbs-mrc-bridge`)
- **Finger (RFC 1288)** (`anetbbs-finger`)
- **BinkP Inbound** (`anetbbs-binkp`)

Each card shows:

- **Listener port health dots** — green/yellow/red per TCP (or UDP)
  port the unit owns, rolled up into an overall pill of `all_up`
  (green), `partial` (yellow, some but not all ports answering), or
  `all_down` (red). This is a real socket probe against each port, not
  just "is the systemd unit active."
- **Live per-service CPU% / RAM / thread-count sparkline** — a small
  Chart.js graph sampled continuously by a background thread, so you
  can see a service's resource trend without SSHing in and running
  `top`.
- **Start / Stop / Restart** buttons (Restart always shown; Stop shows
  only while running, Start only while stopped). The backend also
  accepts a `reload` action (systemd `reload`, for a config re-read
  without dropping connections) for units that support it. All three
  actions need sudoers permission for the account these run as — see
  `deploy/sudoers.anetbbs` (`update.sh` installs this automatically on
  every run; on a fresh install that hasn't been updated yet, it needs
  the `__SERVICE_USER__` placeholder substituted with `sed`, not just
  copied — see [[Sysop Guide]]).
- **Logs** button opens a journal-viewer modal — pick how many lines
  to pull (100 / 500 / 2,000 / 5,000), refresh on demand, or download
  the log as a `.txt` file. Reads via `journalctl`, no privilege
  elevation beyond systemd-journal/adm group membership.

Above the five cards, two **aggregate charts** roll up CPU% and memory
(MB) across all five services on one timeline, so you can spot which
service is driving a load spike at a glance.

Live **online-user counts** are also broken out by protocol (web,
telnet, SSH, rlogin) rather than just a single combined total.

### NodeSpy

Live terminal session list with view-screen and kick buttons. See
[[NodeSpy]].

### Online users

Web + terminal users combined, last 5 minutes. Refreshes every
5 seconds.

### Sysop tools

Quick links to:

- [[BinkP Setup|Echomail Admin]]
- [[Door Setup|Doors Admin]]
- [[RSS Reader|RSS Admin]]
- [[Files|Files Admin]]
- [[Themes|Theme manager]]
- [[Backup]]
- [[Notifications|Notification Settings]] — turn off any of the five
  admin-review notification kinds you don't want (they're all on by
  default)
- [[Scheduled Events]] — cron-style maintenance jobs (nodelist
  generation, DB vacuum, log rotation, InterBBS sync, and more)

### Hub Management

Only present when this install is the network hub
(`REGISTRY_MODE_ENABLED=true`) — links into `/admin/echomail/hub/`:

- BinkP node management and QWK node management (add/edit/delete,
  subscribe to areas — including a **Subscribe to All** button per
  QWK node — reset a QWK node's high-water mark)
- The hold queue — items withheld from distribution
- Two separate node-request review queues: the QWK-only one for
  applications submitted via a BBS's own terminal wizard, and a newer
  one covering the public "apply to join this network" form
  (`/join/`, both BinkP and QWK) — see [[ANotherNetwork]]
- Generation & Distribution, four tabs — generate/publish the
  nodelist on demand (plus its weekly schedule), preview a QWK packet
  per node without marking it sent, TIC/file-distribution status, and
  the Join Form tab for enabling/configuring the public application
  page above

New applications on either queue send you a [[Notifications|notification]]
— no need to keep checking these pages for new work.

See [[ANotherNetwork]] for what this network actually is, and
[[QWK]] / [[TIC Processor]] for the mechanics each panel is managing.

### Running more than one hub identity

Almost every install has exactly one **hub identity** — its own
zone:net, QWK hub ID, downstream node pool, nodelist, and join form —
and never needs to think about this section at all. It exists for a
sysop whose single install is designated the hub for more than one
real network at once (own zone:net, own QWK hub ID, separate
downstream node pools) rather than running a second physical install
per network.

- **Hub Identities** (`/admin/echomail/hub/identities/`) is the CRUD
  for these — name, URL slug, QWK Hub ID, BinkP zone/net/hub-node,
  nodelist metadata. Exactly one identity is flagged **default**; every
  existing single-hub install's one identity is that default, created
  automatically, so upgrading changes nothing until you deliberately
  add a second one.
- Once a second identity exists, BinkP/QWK node forms grow a **Hub
  Identity** picker, node/request lists grow an **Identity** column,
  and the join form gets a second URL: `/join/<slug>/` alongside the
  default `/join/`. Each identity's nodelist is served separately too
  (`/admin/echomail/hub/nodelist/<slug>`).
- BinkP auth resolves a downstream node's identity automatically once
  matched by address/password — it never rejects a connection over
  identity resolution (fails open to the default identity, logged, if
  something's misconfigured), and stamps outbound mail with that
  identity's own AKA so two identities' peers never get cross-stamped
  mail.
- **The terminal (tUI) only ever manages the default identity** —
  QWK Node Requests and the node-application wizard have no identity
  picker by design (same reasoning as BinkP/QWK peer node CRUD above:
  many fields, edit on web). Use the web admin for every identity
  beyond the first.

## See also

- [[NodeSpy]]
- [[Sysop Guide]]
- [[Notifications]]
"""),

    # ------------------------------------------------------------------
    ('nodespy', 'NodeSpy', """
# NodeSpy

Watch (and kick) live terminal sessions. Sysop-only.

## Where

- Card on `/admin/control/` (the [[Sysop Control Panel]])
- Direct link in the admin dropdown → "NodeSpy (kick users)"

## What it shows

| Column | Meaning |
|--------|---------|
| Slot | The node number (1..BBS_NODES) |
| User | Logged-in username |
| Proto | telnet / ssh / rlogin |
| Page | Their current page slug (`main`, `boards/123`, `door:lord`) |
| Last action | Last thing they did (`pressed E`, `loaded /boards/...`) |
| Idle | Seconds since their last keypress |

The eye icon shows a snapshot of their last terminal screen (handy
for "this user looks stuck — what are they looking at?").

## Kicking

The door-arrow icon disconnects the user. A prompt asks for a
reason — shown to them just before the connection drops:

```
*** Disconnected by sysop: <reason> ***
```

The kick is cross-process: the web app sets a flag on the user's
`NodeActivity` DB row, and a watchdog inside the terminal session
polls that flag every 5 seconds. Worst-case kick latency: ~5 s.

Different from a ban: a kick just drops the current connection.
The user can reconnect immediately. To stop them coming back, add
an IP ban under `/admin/ip-bans/`.

## Audit trail

Every kick writes a `UserActivity` row with `activity_type='kick_node'`,
the slot, the target username, the reason, and the sysop's
username + IP — `/admin/activity` shows the trail.

## The terminal has its own, separate Node Monitor — with messaging

Everything above is the **web** NodeSpy panel, and it's kick-only. The
terminal Sysop Tools menu has a distinct **Node Monitor** screen (same
scrollable live-list style as Synchronet's UNIX monitor) that shows
the same live nodes — slot, user, protocol, page, action, idle time —
but adds a second capability the web panel doesn't have:

- **K** — kick, same DB-flag mechanism as the web panel (sets
  `kick_requested`/`kick_reason` on the node's `NodeActivity` row,
  picked up by the session watchdog within ~5s), prompts for a reason
  first.
- **M** — **message** a specific connected node directly from the
  terminal. Prompts for the text, then delivers it immediately via
  the sysop-paging mechanism (the same one used for sysop chat
  replies) — the recipient sees it without you having to drop into
  chat or wait for them to check anything. Only works against an
  authenticated node (can't message an anonymous/unauthenticated
  session).

Both actions live in the same list — press the letter with a row
selected, same as any other Sysop Tools record list. This is a
terminal-only feature; there's no web-panel equivalent of the message
action.
"""),

    # ------------------------------------------------------------------
    ('backup', 'Backup', """
# Backup

Backing up an ANetBBS install.

## What to back up

| Path | What's there |
|------|--------------|
| `data/anetbbs.db` | The sqlite database (everything) |
| `data/uploads/` | User uploads + file library |
| `data/avatars/` | Profile pictures |
| `data/text/menus/` | Static per-menu ANSI/ASCII art overrides (welcome/goodbye/newuser/custom screens live in the DB — see `data/anetbbs.db` above, not here) |
| `data/echomail/` | Inbound/outbound BinkP packets |
| `doors/` | Door binaries & per-door state |

Source code itself isn't critical — restored from the install
tarball.

## Quick weekly tarball

```bash
sudo tar czf /backup/anetbbs-$(date +%F).tar.gz \\
    --exclude=logs --exclude=__pycache__ \\
    /opt/anetbbs/data \\
    /opt/anetbbs/doors
```

Run that out of cron once a week, rotate keep-last-30.

## Hot DB copy

sqlite's online backup API (`.backup`, not a raw file `cp`) gives a
consistent dump even while the BBS is writing — this is the same
mechanism `update.sh` uses for its own pre-update snapshot:

```bash
sudo -u anetbbs sqlite3 /opt/anetbbs/data/anetbbs.db \\
    ".backup '/backup/db/anetbbs-$(date +%F-%H%M).db'"
```

cron this every 6 hours during the day if you want point-in-time
recovery. Admin → Backups lets you restore `.env` or the DB from an
existing `update.sh`-generated snapshot (or delete one) through the
web UI — there's no on-demand "download the live DB right now" button
there, so for your own off-site copies use the `sqlite3 .backup`
command above directly.

## Off-site

Worst case, your house burns down. Push the rotated tarballs to an
off-site target — rclone to B2/S3, or rsync over SSH to a friend's
box.

## Restore

1. Stop the services.
2. Untar the backup into a fresh tree.
3. `chown -R anetbbs:anetbbs /opt/anetbbs/data`
4. Start the services. Auto-sweep migrations will fix any
   column-add diffs between the backup era and current code.
"""),

    # ------------------------------------------------------------------
    ('scheduled-events', 'Scheduled Events', """
# Scheduled Events

A built-in cron replacement. Sysop-only, **Admin → Scheduled Events**
(`/admin/events/`). Maintenance jobs run inside the same process as
the web app, on a one-minute tick, with results visible right in the
admin UI — no need to grep syslog to find out if last night's VACUUM
actually ran.

## How it works

A background thread wakes every 60 seconds, loads every row with
**Enabled** checked, and fires any whose schedule says it's due. Each
row records `last_run_at`, `last_status` (`ok`/`fail`),
`last_duration_ms`, and the first 4KB of captured output — all shown
in the admin list. Editing or disabling a row takes effect on the
next tick (within a minute); no restart needed.

There's no per-event timeout enforced by the scheduler itself — a
handler that hangs blocks the next tick's events. Handlers that shell
out to something that could run long (like `shell`, below) enforce
their own timeout instead.

## Creating an event

- **Name** — free text, shown in the admin list only.
- **Handler** — picks the built-in function that runs (see table
  below). Each option shows its description and expected params once
  selected.
- **Schedule** — one of four kinds:
  - **Daily** — pick a time (`HH:MM`, **UTC**).
  - **Hourly** — pick a minute-of-hour (0–59).
  - **Weekly** — pick a day (Mon–Sun) + time, UTC.
  - **Every N min** ("interval") — repeats N minutes after the
    *previous run finished*, not wall-clock-aligned.
- **Params (JSON object)** — handler-specific arguments. Must parse
  as a JSON object, even if empty (`{}`) — a bare string or path is
  rejected. A handler taking one argument called `command` wants
  `{"command": "/path/to/script.sh"}`, not just the path.
- **Enabled** — unchecked rows never fire but stay in the list.

There's also a **Run now** button per row for testing without waiting
on the schedule — same code path either way, so output shown after a
manual run matches exactly what a scheduled fire would produce.

All schedule times are **UTC**, not the server's local timezone or
any caller's timezone — keep that in mind when picking a "quiet
hours" slot.

## Built-in handlers

| Handler key | Label | Params | Notes |
|---|---|---|---|
| `noop` | No-op (test) | none | Does nothing; returns ok. Use to confirm the scheduler thread is alive. |
| `tw2_maint` | Trade Wars 2002 maint | none | Runs TW2002's headless daily maintenance (Cabal move, inactive-player sweep). |
| `db_vacuum` | SQLite VACUUM | none | Reclaims free pages, defragments, refreshes planner stats. Skips (returns ok) on non-SQLite backends — Postgres autovacuums itself. |
| `log_rotate` | Rotate large logs | `max_mb` (default 50) | Any `logs/*.log` over the threshold is renamed to `.1` and a fresh empty file takes its place. |
| `security_check` | Security update check | none | Scans `apt list --upgradable` and the venv's `pip list --outdated`, tags Ubuntu `-security` rows, writes a report consumed by **Admin → Security**. Always returns ok even on a non-Ubuntu box, so a missing `apt` doesn't permanently red-flag the row. |
| `hub_generate_nodelist` | ANotherNetwork: generate nodelist | none | Publishes the ANotherNetwork nodelist into the `ANN.FILES.NODELIST` file area, replacing the prior copy. Only meaningful on the install designated as the ANotherNetwork hub (`REGISTRY_MODE_ENABLED`) — elsewhere it still runs harmlessly, publishing a nodelist with just the hub entry. |
| `sync_wall_inbound` | InterBBS Wall: import inbound posts | none | Materializes new inbound InterBBS Wall echomail into local Wall posts. Auto-created when InterBBS Wall is enabled — you won't add this by hand. |
| `sync_lastcallers_inbound` | InterBBS Last Callers: import inbound entries | none | Materializes new inbound echomail into local Last Callers entries. Auto-created when InterBBS Last Callers sharing is enabled. |
| `sync_scores_inbound` | InterBBS Game Scores: import inbound scores | none | Materializes new inbound echomail into local game high scores. Auto-created when InterBBS Score Sharing is enabled. |
| `shell` | Shell command | `command` (required), `timeout` (default 60s) | Runs an arbitrary command as the service user. **No sudo** — anything needing root privileges silently fails at that step. Output is captured as raw bytes and decoded with `errors='replace'`, so non-UTF8 output (e.g. CP437 from a DOS program) doesn't crash the handler. |

The three `sync_*_inbound` handlers aren't something you'd pick from a
blank slate — they appear already-created and enabled the moment you
turn on the matching InterBBS feature (Wall, Last Callers, or Game
Score sharing) on a network. Listed here so they aren't a mystery if
you spot one.

### `shell` handler — worked example

Running a nightly TradeWars external-events script via dosemu2:

**Params (JSON object):**
```json
{"command": "/opt/anetbbs/doors/dos/tw/event.sh", "timeout": 120}
```

**`event.sh`:**
```bash
#!/bin/bash
export TERM=linux
cd /opt/anetbbs/doors/dos/tw
/usr/libexec/dosemu2/dosemu2.bin -td EXTERN.EXE
```

`export TERM=linux` and `-td` (dumb-terminal mode) are required
because the scheduler runs the command with no controlling tty — a
normal door launch gets a real PTY from `door_runner.py`, but a
scheduled shell command doesn't. Without these, dosemu2 fails with
`ERROR: TERM environment variable needs set.` before it ever reaches
your DOS program.

## Default events on a fresh install

Seeded once (idempotent — re-running the seeder skips handler keys
that already have a row):

| Name | Schedule | Handler |
|---|---|---|
| TW2 daily maintenance | Daily 03:30 UTC | `tw2_maint` |
| Weekly SQLite VACUUM | Weekly, Sunday 04:15 UTC | `db_vacuum` |
| Rotate oversize logs | Daily 04:45 UTC | `log_rotate` (`max_mb: 50`) |
| Daily security update check | Daily 04:00 UTC | `security_check` |

Any of these can be disabled, deleted, or rescheduled — they're
normal rows, not special-cased.

On installs with `REGISTRY_MODE_ENABLED=true` (the designated
ANotherNetwork hub), a fifth event is also seeded:

| Name | Schedule | Handler |
|---|---|---|
| ANotherNetwork: weekly nodelist | Weekly, Sunday 05:00 UTC | `hub_generate_nodelist` |

This one is skipped on regular (non-hub) installs — there's nothing
useful for it to publish there.

## Troubleshooting

- **"Params (JSON object)" rejects what I typed** — it must parse as
  a JSON object. `/path/to/script.sh` isn't valid JSON; wrap it as
  `{"command": "/path/to/script.sh"}`.
- **dosemu2 commands fail with `TERM environment variable needs
  set`** — see the worked example above; export `TERM` and pass
  `-td` in your script.
- **An event always fails the same way** — click **Run now** and
  read the captured output; it's the same stdout/stderr you'd get
  running the command by hand over SSH, just minus your interactive
  shell's environment (PATH, TERM, etc. may differ — set them
  explicitly in the script if needed).

## See also

- [[Sysop Control Panel]]
- [[Sysop Guide]]
- [[Backup]]
"""),

    # ------------------------------------------------------------------
    ('architecture', 'Architecture', """
# Architecture

A 10,000-ft overview of how ANetBBS is wired up.

## Processes

```
+---------------------+         +------------------------+
|  anetbbs-web        |         |  anetbbs               |
|  eventlet WSGI      |         |  asyncio listener      |
|  Flask app + sock.IO|         |  telnet/ssh/rlogin     |
|                     |         |  (one process, unified)|
+----------+----------+         +-----------+------------+
           |                                |
           |  SQLite (shared)               |
           +--------- data/anetbbs.db ------+-----------+
                                            |            |
                                  +---------+--+  +------+-----+
                                  |anetbbs-mrc-|  |anetbbs-    |
                                  |bridge      |  |finger/binkp|
                                  +------------+  +------------+
```

Two core services (`web`, the unified `anetbbs` terminal process),
plus three optional ones (`mrc-bridge`, `finger`, `binkp`) — all
sharing the same SQLite database. Cross-process signals (like
[[NodeSpy]] kick) go via DB flags polled by the other process.

## Tech stack

| Layer | Choice |
|-------|--------|
| Web framework | Flask 3 + Flask-Login + Flask-SocketIO |
| WSGI | eventlet's native WSGI server (`deploy/serve.py`, `socketio.run()`), single process — gunicorn's eventlet worker class is broken on Python 3.12, so it's deliberately not used |
| Realtime | Socket.IO (web) / asyncio (terminal) |
| DB | SQLite via SQLAlchemy 2 |
| Migrations | "Auto-sweep" — read model metadata, ALTER TABLE for missing cols |
| Frontend | Bootstrap 5, xterm.js, vanilla JS |
| Markdown | python-markdown + bleach |
| RSS | feedparser |
| ANSI | custom CP437 helpers in `anetbbs/features/ansi_ui.py` |

## Repo layout

```
anetbbs-rebuilt/
  anetbbs/
    core/           session, terminal protocols (telnet/ssh/rlogin/finger)
    features/       BBS sub-systems (menus, doors, multinode, ANSI helpers...)
    web/            Flask blueprints (one per feature)
    wiki/           wiki renderer + slug + seed
    models.py       SQLAlchemy models (one file, ~3000 lines)
    web_app.py      Flask app factory
    config.py       env-driven config
  templates/        Jinja templates
  static/           images, css overrides
  data/             SQLite + uploads (runtime; gitignored)
  docs/             markdown docs served at /docs/
  deploy/           systemd units, sudoers, gallery viewer script
  tools/            one-off scripts
  install.sh / update.sh
```

## Auto-sweep migrations

`web_app.py` walks `db.metadata.sorted_tables` on startup. For each
model column the live DB is missing, it issues `ALTER TABLE ADD
COLUMN`. New columns are added permissively (nullable, no default)
so the migration always succeeds on SQLite. ORM defaults apply for
new inserts.

This means: adding a column = bump the model, restart `anetbbs-web`,
done. No alembic incantations needed for the common case.
"""),

    # ------------------------------------------------------------------
    ('glossary', 'Glossary', """
# Glossary

Quick definitions for BBS / ANetBBS jargon.

**ANSI** — ECMA-48 escape sequences for cursor positioning and color.
Combined with [[CP437]] it's the visual language of BBSes.

**Areafix** — A FidoNet-style robot user that handles echo-area
subscription requests. See [[Netmail#AreaFix|AreaFix]].

**BinkP** — Modern FidoNet mail transport protocol (TCP 24554). See
[[BinkP Setup]].

**Bridge** — Glue between two protocols. The [[DosBridge]] copies
bytes between TCP and a terminal writer; the [[IRC Bridge]] relays
one MRC room to one IRC channel (a separate feature from the
per-user [[IRC Client]]).

**CP437** — The "OEM-US" codepage with line-drawing chars and blocks
that ANSI art relies on. See [[CP437]].

**Dropfile** — A text file the BBS writes for a door so the door
knows the user's name, time remaining, etc. (`DOOR.SYS`,
`DORINFO1.DEF`, `DOOR32.SYS`).

**Echo / echo area** — A globally-distributed message topic. See
[[Echomail]].

**FidoNet** — The original BBS-to-BBS message network, started 1984.
Still alive. Uses zone:net/node addressing like `1:104/315`.

**MSP** — Message Send Protocol (RFC 1312), the inter-BBS IM
protocol on TCP 18. See [[Instant Messages]].

**MRC** — Multi-Relay Chat, the BBS-scene chat network. See [[MRC]].

**Netmail** — FidoNet point-to-point mail (vs. [[Echomail]] which is
broadcast). See [[Netmail]].

**Node** — One simultaneously-connected user slot. ANetBBS supports
up to 100; default 8 (`BBS_NODES=8`).

**Nodelist** — The list of every node in a FidoNet-style network,
distributed weekly.

**QWK** — A mail packet format for offline reading; download QWK
once, read & reply offline, upload REP. See [[QWK]].

**Rlogin** — Auto-login terminal protocol (TCP 513). See [[Rlogin]].

**SyncTERM** — Reference BBS terminal client. CP437-native, supports
modern protocols, free.

**TIC** — FidoNet file echo control file. See [[TIC Processor]].

**Zmodem** — Classic file-transfer protocol used inside terminal
sessions. ANetBBS supports it for [[Files]] downloads.
"""),

    # ------------------------------------------------------------------
    ('cp437', 'CP437', """
# CP437

The "OEM-US" codepage IBM shipped with the original PC. 256
characters: ASCII 0–127 followed by accented letters, line-drawing
glyphs, block characters, and miscellaneous symbols.

## Why it matters

ANSI art relies on the CP437 line-drawing chars (`╔═╗`, `▓░`, etc.).
Without a CP437-aware font/terminal, BBS menus and art look like
random accented letters.

## Renderers that get CP437 right

- [SyncTERM](http://syncterm.bbsdev.net/) — gold standard
- [NetRunner](https://mysticbbs.com/downloads/)
- Most modern Windows terminals with an IBM-PC font
- xterm.js (via [[Web Terminal]]) — ANetBBS configures it for CP437

## Inside ANetBBS

Terminal sessions default `self.encoding = 'cp437'`. When the BBS
writes a string, it gets `.encode('cp437', errors='replace')`.

Stored ANSI screens and echomail bodies use a different trick:
each original byte 0xNN is stored as Python codepoint U+00NN
(latin-1 mojibake). On display, the BBS writes them back with
`.encode('latin-1')` so original bytes pass through untouched. This
way a CP437 client sees the original ANSI art exactly as the
author wrote it.

If you ever see `Ä` `─` mojibake instead of clean lines, the
encoding round-trip broke somewhere. Fix is usually to use the
latin-1 raw write path instead of `session.write()`.

## Per-user override

`/profile/edit` → Codepage. Falls back to UTF-8 or Latin-1 for users
on terminals that don't speak CP437. Doesn't change how the BBS
stores data — only how it encodes outgoing writes.
"""),

    # ------------------------------------------------------------------
    ('revision-history', 'Revision History', """
# Revision History

Every wiki edit is preserved.

## How it works

When someone saves a page, the wiki:

1. Writes a new `WikiRevision` row with the prior content snapshotted.
2. Updates the live `WikiPage` body/title to the new content.
3. Increments `rev_num` (1, 2, 3, …) per page.

The full revision body is stored — no diff compression. SQLite is
fine for this at typical wiki scale.

## Viewing

- Page → **History** button shows the revision list.
- Click any `r<N>` to see that exact version rendered.
- Pick two revisions and **Compare** to see a unified diff.

## Reverting

Logged-in users can revert any non-current revision. Revert writes
a *new* revision with the older body — the audit trail stays intact;
you can always re-revert.

Locked pages can only be reverted by sysops.

## Author tracking

Each revision stores `author_id` (NULL for logged-out edits, which
are disallowed by default) and the editor's IP. The IP is admin-
visible only.

## Privacy

Author IP is not exposed in the public history view; only the
username is. Sysops can see IP through `/admin/activity`.

## See also

- [[Edit Summary]]
- [[Wanted Pages]]
"""),

    # ------------------------------------------------------------------
    ('edit-summary', 'Edit Summary', """
# Edit Summary

The short message you can add to an edit, like a git commit message.

## Why bother

A wiki without edit summaries is a wiki where you can't tell *why*
something changed without reading the full diff. Future editors
(and your future self) appreciate even a one-line "Fixed typo" or
"Added BinkP TLS section".

## What goes there

Keep it short — under 300 chars. Examples that earn their keep:

- `Fixed typo in dosbox config example`
- `Added section on TLS for BinkP`
- `Reverted unhelpful change`
- `Restructured into 4 sections; no content changes`
- `Linked the [[DosBridge]] page from here`

## Special cases

- A revert auto-fills the summary as `Reverted to revision N`.
- A rename auto-fills `Renamed from old-slug to new-slug`.
- A new-page creation has an empty summary unless you fill one in.

## Shown where

- [[Revision History]] table
- Recent changes list
- The page itself shows the **latest** edit summary in the meta
  line under the title.
"""),

    # ------------------------------------------------------------------
    ('wanted-pages', 'Wanted Pages', """
# Wanted Pages

Pages that other pages link to via `[[wiki-link]]` but which haven't
been created yet.

## Where

`/wiki/wanted` — sorted by incoming-link count (most-wanted first).

## How to use it

When you're looking for something useful to write, this is your
queue. The page is already named, already has a known audience
(someone linked to it), and the link will go live the moment you
hit save.

## Red links

Pages that don't exist yet render as red dashed-underline links in
the wiki body. Clicking one takes you to the edit form for that
slug if you're logged in, or a friendly "this page doesn't exist
yet, log in to create it" placeholder otherwise.

## See also

- [[Revision History]]
"""),

    # ------------------------------------------------------------------
    ('web-terminal', 'Web Terminal', """
# Web Terminal

A real terminal in your browser, talking to the BBS's local
[[Telnet]] listener via xterm.js over WebSockets.

## Where

`/terminal/` on the web UI. Logged-in users only.

## What you get

A full CP437/ANSI terminal — the same UI you'd see in
[SyncTERM](http://syncterm.bbsdev.net/) or
[NetRunner](https://mysticbbs.com/downloads/), rendered in the browser:

- 80×25 default; resizable to whatever your viewport allows
- ANSI color, bold, blink (animated)
- Mouse-driven copy/paste
- Doors run inline, including DOS doors via the [[DosBridge]]

## Auth

You must already be logged into the web UI to open `/terminal/` at
all — but the emulated terminal itself is a plain socket straight to
the local [[Telnet]] listener (`localhost:2233` by default; a sysop
can point it at any host/port from the connect dialog, telnet or
SSH). It does **not** use rlogin and there's no auto-login token: you
still see the normal `(G)uest, (L)ogin, (N)ew user` prompt and type
your username/password inside the emulated session, same as any
telnet client. See [[Rlogin]] for what auto-login rlogin actually is.

## Why use it

- Zero install: no telnet client needed
- Mobile-friendly: works on phone browsers
- Useful as a sysop tool: jump into a user's experience to
  reproduce something they're seeing
- Sometimes the only thing that gets through restrictive
  firewalls (your gym, an airport, …)

## See also

- [[Telnet]]
- [[CP437]]
- [[Doors]]
"""),

    # ------------------------------------------------------------------
    ('irc-bridge', 'IRC Bridge', """
# IRC Bridge

Relays **one** [[MRC]] room to **one** external IRC server + channel.
Each bridge is its own row and its own running process — there's no
web admin page for this, and it's a completely different feature
from the per-user [[IRC Client]] at `/irc/`.

## Configure

There's no web UI. A sysop creates a row in the `mrc_irc_bridges`
table directly (a one-off script, `flask shell`, or a small admin
tool you write yourself). The fields on a bridge row:

| Field | Meaning |
|-------|---------|
| `name` | Label for the bridge (your own reference) |
| `mrc_room` | Which local MRC room to relay |
| `mrc_handle` | The name the bridge uses on the MRC side (default `ircbridge`) |
| `mrc_ws_url` | WebSocket URL of the local MRC bridge service |
| `irc_server` / `irc_port` | Target IRC server |
| `irc_use_ssl` | TLS toggle |
| `irc_nick` | Nick the bridge uses on IRC (default `ANETBridge`) |
| `irc_channel` / `irc_channel_key` | Target channel + key if it's locked |
| `sasl_user` / `sasl_pass` | SASL PLAIN credentials, if the network needs them |
| `is_active` | Whether the bridge should be running |

## Running it

Each bridge row runs as its own instance of the systemd template unit:

```
systemctl enable --now anetbbs-mrc-irc-bridge@<id>.service
```

where `<id>` is the bridge row's primary key. `%i` in the unit
expands to that ID, so `anetbbs.features.mrc_irc_bridge --bridge-id
<id>` knows which row's config to load. One bridge = one systemd
instance = one MRC room ↔ one IRC channel.

## How it works

1. The process opens a WebSocket to the local MRC bridge service and
   a plain TCP (or TLS) socket to the configured IRC server, joining
   the one configured channel.
2. Messages posted in the MRC room are relayed to IRC prefixed
   `[mrc:<user>]`.
3. Messages said in the IRC channel are relayed into the MRC room
   prefixed `[irc:<nick>]`.

## Limits

- One MRC room, one IRC channel, per bridge row — for more mappings,
  run more bridge instances (more rows, more `@<id>` units).
- The bridge appears as a single bot on both sides; it doesn't proxy
  per-user identity beyond the message-text prefix.

## See also

- [[Chat]]
- [[MRC]]
- [[IRC Client]] — the unrelated personal/per-user IRC feature
"""),

    # ------------------------------------------------------------------
    ('irc-client', 'IRC Client', """
# IRC Client

A personal IRC client built into the web UI — `/irc/`. Every
logged-in user can connect to whatever IRC server, nick, and
channels *they* want, independent of anything the sysop has set up.
This is a different feature from the sysop-run [[IRC Bridge]]; the
two aren't connected to each other.

## Using it

Open `/irc/`. Fill in:

- **Server** (default `irc.libera.chat`)
- **Port** (default `6667`) + a TLS toggle
- **Nick** (defaults to your BBS username)
- **Channels** to auto-join on connect
- Optional **SASL** username/password if the network requires
  authenticated connections

Click Connect. The page keeps a live Socket.IO session to the IRC
server for as long as your browser tab (or your saved session) stays
open, with the usual channel window, nick list, and `/commands`
you'd expect from any IRC client.

## Saved settings

Click **Save** and your server/port/SSL/nick/channel choices persist
to your account (`IrcServerConfig`), so the page reconnects with the
same setup next time you open it. Nothing here is shared with other
users or with the sysop's [[IRC Bridge]] configuration.

## Scrollback

If you get disconnected (closed tab, lost network) recent channel
activity replays when you reconnect, so you don't lose the last
stretch of conversation.

## See also

- [[Chat]]
- [[IRC Bridge]] — the separate, sysop-configured MRC↔IRC relay
- [[MRC]]
"""),

    # ------------------------------------------------------------------
    ('netmail', 'Netmail', """
# Netmail

FidoNet **point-to-point** mail — like email, but routed over BinkP
through the FidoNet/fsxNet/etc. network instead of SMTP.

## How it differs from [[Echomail]]

| Feature | Netmail | Echomail |
|---------|---------|----------|
| Audience | One specific node | An echo-area subscribers |
| Routing | Explicit `Zone:Net/Node` | Network-distributed |
| Privacy | Sealed envelope | Public area |
| Reply | Net-mail back to sender | Reply in the area |

## Composing

`/netmail/compose` (web) — picks recipient by node address + name.
The compose form supports markdown for the body and adds a kludge
line (`@MSGID:`, `@INTL`, `@TZUTC`) automatically.

## Inbox

`/netmail/`. Unread netmail shows in the badge in the nav.

## AreaFix

`AreaFix@<your-node>` is a virtual recipient that lets *other*
sysops subscribe to your areas without bothering you. Which areas a
downstream node can see is controlled by that node's echo-area
subscriptions on its network record (Admin → Echomail → Hub), not a
separate allow-list page. To see incoming AreaFix requests and how
they were handled, check the AreaFix Log at
`/admin/echomail/areafix_log`.

FileFix (`FileFix@<your-node>`, also `File Fix`/`FileMgr`) is the
file-echo counterpart — same idea, same password (Subject-line,
FTS-0024), same `+TAG`/`-TAG`/`+ALL`/`-ALL`/`%LIST`/`%HELP` grammar,
but for file-area subscriptions instead of message areas. It shares
the AreaFix Log page above (a **Bot** column and filter distinguish
AreaFix from FileFix rows), rather than having a separate log.

## Encryption

Bare FidoNet netmail is not encrypted. For secrets, encrypt the
body yourself (PGP, age) — the network treats it as opaque.
"""),

    # ------------------------------------------------------------------
    ('bulletins', 'Bulletins', """
# Bulletins

Sysop-curated short notices that appear on the home page and (on
terminals) as the "B" main-menu option.

## Adding

`/admin/bulletins/` → **Add**. Fields:

- **Title** (up to 200 chars)
- **Content** (markdown supported)
- **Pinned** (checkbox) — pinned bulletins always sort first
- **Expires At** (`YYYY-MM-DD HH:MM`, optional) — after this time the
  bulletin stops showing to users automatically; leave blank for no
  expiry

There's no manual sort-order field and no separate "active/hidden"
toggle — a bulletin is either pinned (shown first) or not, and either
current or expired. To hide one immediately without waiting for its
expiry, delete it or edit `Expires At` to a time in the past.

## Sort order

Bulletins list pinned-first, then newest-first: `is_pinned desc,
created_at desc`.

## Cleaning up

Expired bulletins stay in the database (for history/audit) until a
sysop clears them. `/admin/bulletins/purge-expired` permanently
deletes every bulletin whose `expires_at` has passed.

## Pager view

On terminal, opening a long bulletin paginates with a `[Q]=quit`
prompt.

## Use cases

- Announcing maintenance / upgrades
- Highlighting a new feature (point users at the wiki page)
- Posting a "what's new" digest each month
- Sharing scene news that doesn't deserve a full board thread
"""),

    # ------------------------------------------------------------------
    ('qwk', 'QWK', """
# QWK

A classic offline-reading packet format. Download a QWK file, read
and reply offline, upload a REP packet back.

## Why it exists

Phone calls used to cost money. Connect, download QWK packet,
disconnect. Read at leisure. Connect again only to upload replies.

In 2026 it's still useful for:

- Slow connections (cellular roaming, sat)
- Reading on a device that's not always online (a vintage laptop)
- Archiving a snapshot of the BBS at a point in time

## Download

`/qwk/` explains the feature; the actual packet comes from
`/qwk/download`, which bundles every active area's messages unread
since your last download automatically — there's no area or
date-range picker, it's always "everything new."

## Upload

A `.REP` (reply) packet posts to `/qwk/upload` (a separate endpoint
from download). The BBS parses it and posts your replies to the
right boards / echos.

## Readers

- [MultiMail](https://multimail.sourceforge.net/) — modern Linux
- [Bluewave](https://www.bbsdocumentary.com/) (DOS, classic)
- Various Windows tools

## Sysop tools — Hub Management

Hub-side QWK administration lives under
`/admin/echomail/hub/qwk/` (part of Hub Management — see
[[Sysop Control Panel]] — gated behind `REGISTRY_MODE_ENABLED`, only
meaningful on the one install that *is* the network hub):

- **Node list** (`/admin/echomail/hub/qwk/`) — every downstream QWK
  node, packet ID, and subscription state; add/edit/delete nodes,
  reset their high-water mark.
- **Node detail** (`/admin/echomail/hub/qwk/<node_id>`) — click into
  a node to manage which echo areas it actually receives.
  Subscribe/unsubscribe areas one at a time, or use the
  **Subscribe to All** button to bulk-subscribe. It first shows a
  checkbox list of the QWK-transport networks on the install — pick
  one or more and confirm; picking none just flashes an error and
  subscribes nothing. Once confirmed, the node is subscribed to
  every active area on the checked network(s) — deliberately scoped
  to QWK-type networks only, not every area on the whole BBS, so a
  QWK node doesn't end up subscribed to something that only exists
  on a BinkP-only network. If the node belongs to a specific hub
  identity, this is further filtered to that identity's own
  network(s), so a node never gets swept into another network's
  areas just because it shares the install.
- **Preview** — a **Preview** button per node builds and downloads a
  test `.QWK` packet for that node on demand *without* marking any
  messages as sent, so a sysop can sanity-check what a peer would
  receive before the real packet goes out.
- **Hold queue** (`/admin/echomail/hub/holdqueue`) — BinkP-only, not
  QWK: outbound echomail queued per downstream BinkP node, flushed the
  next time that node polls in (or is polled). No moderation/policy-
  check step involved — it's purely a delivery queue, filterable by
  status (pending/sent).
- **Node requests** (`/admin/echomail/hub/qwk/requests`) — the
  approve/deny queue for BBSes applying for a new QWK node number
  (see [[ANotherNetwork]] for how a sysop submits one of these).
  Approving auto-creates the QWK node record with a generated
  password.

If you're a hub for more than one real network from this one install,
the QWK node form grows a **Hub Identity** picker once a second
identity exists, and each identity gets its own QWK Hub ID (the
`<hub_id>.qwk` sysops actually download) — see [[Sysop Guide]],
"Running more than one hub identity." Almost no install needs this.
"""),

    # ------------------------------------------------------------------
    ('tic-processor', 'TIC Processor', """
# TIC Processor

Handles **FidoNet file echoes** — files distributed network-wide
the same way [[Echomail]] distributes messages, but with metadata
in a separate `.TIC` companion file.

## Flow

```
inbound BinkP
    ↓
.TIC + file land in inbox/<area>/
    ↓
TIC processor parses .TIC:
  Area: SCENE_FILES
  File: scene2026-01.zip
  Desc: BBS scene news, January
  Crc: 12345678
    ↓
File moves to data/files/<area>/  +  description set
    ↓
File appears in /files/<area>/
```

## Sysop config

File areas that carry a TIC file-echo are managed at
`/admin/file-areas` — each one is a `FileArea` row attached to an
`EchomailNetwork` (`network_id` set). For each: which local storage
path to drop into, and (under `/admin/file-echo-subs`) which peers
are subscribed to receive it. A downstream peer can also self-serve
their own subscriptions via netmail to the [[Netmail#AreaFix|FileFix]]
robot instead of the sysop managing it by hand.

## Forwarding

If you have nodes peering off you for the same TIC area, the
processor regenerates a fresh `.TIC` with you in the path and
queues it for outbound BinkP to each downlink.

## Outbound — auto-hatch on local upload

Inbound TIC processing (above) isn't the only way a file gets
distributed. **Any file that lands in a network-attached file area
gets queued for outbound distribution automatically** — no separate
"send this out" step:

1. A user or sysop uploads a file into a file area whose
   `network_id` is not null (for example one of
   [[ANotherNetwork]]'s `ANN.FILES.*` areas).
2. The upload route calls `hatch_local_file()`, which looks up every
   peer subscribed to that file echo and drops one `HatchQueue` row
   per peer — this is the *origin* hop, so there's no prior TIC to
   forward, just a fresh one built from scratch.
3. The next outbound BinkP session to each subscribed peer picks up
   its pending `HatchQueue` rows, builds the `.TIC` companion file
   (filling in `SEEN-BY`/`PATH` for that hop), and ships the file.

This runs from every upload path that can write into a network file
area — the regular `/files/` upload form, the sysop's
"manage upload" tool, the smart-upload flow, and approving a queued
file in the upload-review queue. A local-only file area
(`network_id` is null) never triggers this — there's nobody to hatch
to.

## Checksums

`.TIC` carries a CRC32. If the file's CRC doesn't match, the file is
left in place (not moved) and the TIC row is flagged `status='error'`
with an `error_message` describing the mismatch (expected vs. actual
CRC) — visible in the TIC admin log for manual review.

## Admin logs

Inbound and outbound get separate log pages: **TIC In Log**
(`/admin/tic-log`) for manifests received, **TIC Out Log**
(`/admin/hatch-log`) for the `HatchQueue` items described above —
pending/sent/failed, which peer, retry count, and the last delivery
error if one occurred, filterable by status. Both are linked from the
Hub Management TIC/File Distribution tab.

## See also

- [[Echomail]]
- [[BinkP Setup]]
- [[Files]]
- [[ANotherNetwork]]
"""),

    # ------------------------------------------------------------------
    ('tutorial', 'Tutorial', """
# Tutorial

The interactive sysop walkthrough. `/tutorial`.

A slide-deck-style intro that shows new sysops where each major
subsystem lives. Covers, roughly:

1. The [[Sysop Control Panel]] tour
2. Creating your first board
3. Bringing up an [[Echomail]] uplink
4. Registering a [[Doors|door]]
5. Setting up [[Backup]]
6. Where to look when something breaks

Each slide has next/prev nav and a "Mark complete" toggle. Once
complete, the slide is hidden by default (you can re-show all
slides anytime).

## When to take it

After a fresh install. ANetBBS spins up usable on day one, but
the tutorial walks you past the "I see something, what is it?"
questions a lot faster than reading the [[Sysop Guide]] front to
back.

## See also

- [[Sysop Guide]] — long-form reference
- [[Architecture]] — what runs where
"""),

    # ------------------------------------------------------------------
    ('dos-door-recipe', 'DOS Door Recipe', """
# DOS Door Recipe

Concrete recipe for getting a DOS door (e.g. [[LORD Setup|LORD]])
running under DOSBox-staging on Linux behind ANetBBS.

## Ingredients

- DOSBox-staging (`apt install dosbox-staging` on Debian/Ubuntu, or
  say yes to the DOSBox prompt in `install.sh`/`update.sh`)
- `xvfb-run` (`apt install xvfb`) if you want headless operation —
  ANetBBS auto-wraps the launch with it when found, no config needed
- The DOS door's original files (preserve them as-is)

There's no DOSBox config file, nullmodem port, or command line to
write by hand — ANetBBS generates all of that per launch from a few
admin-form fields. If you're expecting to hand-write a `.conf` file
like on a bare-metal DOSBox setup, that's not how this works; skip
straight to "Register the door" below.

## Steps

### 1. Drop the door into place

```bash
sudo mkdir -p /opt/anetbbs/doors/lord
sudo chown anetbbs:anetbbs /opt/anetbbs/doors/lord
sudo -u anetbbs cp -a /path/to/lord/* /opt/anetbbs/doors/lord/
```
(substitute your real service user / install dir if different from
the defaults above)

### 2. Register the door

`/admin/games/` → **Add Game**, type `door_dos`:

| Field | Value |
|-------|-------|
| Name | LORD |
| Description | Legend of the Red Dragon |
| Executable Path | `/opt/anetbbs/doors/lord/LORD.EXE` |
| Working Directory | `/opt/anetbbs/doors/lord/` |
| Drop File Type | `door.sys` (or `door32.sys`) |
| Drop File Path | `E:\\` |
| Max nodes | 1 |
| Time limit | 30 min |
| Idle timeout | 5 min |

At launch, ANetBBS picks a free bridge port itself, mounts `C:` =
your working directory, `D:` = its bundled FOSSIL driver bundle, `E:`
= a per-node scratch directory holding the drop file, loads
`BNU.COM` on COM1, then runs `LORD.EXE`. `LORDCFG` runs the first
time to write `NODE1.DAT` into the working directory — leave that
file alone once it exists.

### 3. First test

Play it from your sysop account first. Watch
`journalctl -u anetbbs -f` (telnet, SSH, and rlogin all run inside
this one unified service — there are no separate `anetbbs-telnet` /
`anetbbs-ssh` units) — you should see bridge connect/byte-count/exit
lines. If you see zero bytes in either direction, DOSBox isn't
talking back — check that the executable path is actually correct
and that `LORD.EXE` isn't itself configured to expect COM1=stdio
instead of a real port (`DOOR.SYS` line 1 should read `COM1:`, which
ANetBBS writes correctly by default).

### 4. Ship it

If the test works, make sure the user-side path works too — log
in as a regular user and play it.

## See also

- [[LORD Setup]] for LORD-specific tweaks
- [[DosBridge]] for the protocol details
- [[Door Setup]] for the higher-level admin flow
"""),

    # ------------------------------------------------------------------
    ('sysop', 'Sysop', """
# Sysop

The user who runs the BBS. Short for **sys**tem **op**erator.

On this BBS the sysop is **{sysop_name}** — pingable on [[Chat]] or
via [[Private Messages]].

The role is more "host" than "moderator". Day-to-day duties are in
the [[Sysop Guide]]; the long-form admin tools are at
`/admin/`.
"""),

    # ------------------------------------------------------------------
    ('development', 'Development', """
# Developing for ANetBBS

Want to write a door, a web feature, a theme, or hook into messaging?
You're in the right place.

## The ten door types

ANetBBS runs door games via ten distinct backends:

1. **DOS doors** (DOSBox-staging) — TradeWars, LORD-DOS, Usurper
2. **DOS doors** (dosemu2, virtual COM1, no FOSSIL) — an alternative to
   DOSBox-staging for DOS games that need it specifically
3. **Native Linux doors** — any executable, stdio piped
4. **Synchronet `.js` doors** — real `jsexec` if installed, otherwise
   our built-in Node + Synchronet API shim (~270 functions)
5. **Mystic Pascal `.mps`/`.mpx`** — bundled Mystic 1.12 A48 runtime
6. **Mystic Python `.py`** — fake `mystic_bbs` module
7. **rlogin out-dial** — DoorParty / A-Net Online / Synchronet xtrn
8. **Telnet out-dial** — TWGS and other telnet-only remotes, no
   pre-auth handshake
9. **Built-in web games** — Flask-routed mini-games
10. **In-browser DOS games** — EmulatorJS + dosbox_pure, runs entirely
    client-side, no telnet/SSH client needed

See [`docs/17-development.md`](/docs/17-development) for the
deep-dive: drop file formats, token substitution table, working code
examples for each type.

## Extending the web app

The web side is **Flask + SQLAlchemy + SocketIO**. Adding a new
feature is roughly:

1. Create a blueprint in `anetbbs/web/myfeature.py`
2. Register it in `anetbbs/web_app.py`
3. Add templates in `anetbbs/templates/myfeature/`
4. (Optional) Add a nav-bar link in `templates/base.html`
5. (Optional) Add a model class (or a new column on an existing one)
   to `anetbbs/models.py` — see **Database** below, no migration
   command needed for the common case

The whole pipeline is ~50 lines of code for a working feature blueprint.

## Database

`anetbbs/models.py` is the single source of truth for SQLAlchemy
models. There's no alembic/`flask db migrate` step in normal use —
`web_app.py` runs an auto-sweep (`_lightweight_migrate()`) on every
startup: it walks `db.metadata.sorted_tables`, and for any column a
model declares that the live table is missing, issues a permissive
`ALTER TABLE ADD COLUMN` (nullable, no default, so it always succeeds
on SQLite). Add a column, restart `anetbbs-web`, done. See
[[Architecture]] for the same mechanism described from the ops side.
A hand-written migration is still the right call for anything
destructive (renaming/dropping a column, backfilling data) — the
auto-sweep only ever adds.

## Themes

Themes are DB rows with CSS variable values, edited visually at
`/admin/theme-builder/`. Add a new built-in theme by tweaking colors
in the builder, then add the resulting values to the default-theme
seed block in `_create_default_data()` in `anetbbs/web_app.py`
(there's no separate `seed_data.py` — it's all in `web_app.py`).

## Real-time features

Flask-SocketIO is wired up in `web_app.py`. The Web Terminal and MRC
client are both reference implementations of a feature that spawns a
background eventlet greenthread, pumps bytes between a backend socket
and the browser, and handles reconnect.

## Echomail / FidoNet networks

Pollers live in `anetbbs/echomail/{binkp,qwk,tic}.py`. Adding a new
network type is one new module + one new `EchomailNetwork.protocol`
discriminator.

## Where to ask questions

- Email — `a-net-online@proton.me`
- Open a GitHub issue at `anetonline/anetbbs`
- This wiki — anyone with edit rights can expand the
  [[Development]] page and create child pages

If you ship something cool, PR it back and we'll bundle it with the
next alpha.

## Reference

The authoritative deep-dive is at [`docs/17-development.md`](/docs/17-development).
This wiki page is the lightweight community-editable companion.
"""),

    # ------------------------------------------------------------------
    ('games', 'Game Center', """
# Game Center

The Game Center (`/games/`) is every built-in browser game in one
place — no telnet client, no per-game setup, just log in and click
Play. Most games track your score on a shared leaderboard
(`/games/scoreboard`).

## Puzzle

- **Hangman** — guess the hidden word one letter at a time before the
  hangman is fully drawn.
- **Trivia Challenge** — multiple-choice questions across categories.
- **Number Guesser** — a number 1-100, guess it in as few tries as
  you can.
- **Memory Match** — flip cards, find the matching pairs.
- **Minesweeper** — clear the minefield without triggering a mine.
- **2048** — slide and merge tiles to reach the 2048 tile.
- **Tetris** — falling blocks with ghost piece, hard drop, and
  wall-kick rotation.

## Action

- **Snake** — eat, grow, don't hit the walls.
- **Galaga** — arcade shooter, waves of aliens, dive-bombing bosses.
- **Breakout** — Arkanoid-style brick breaker with power-ups (wide
  paddle, multi-ball, laser cannon).
- **ANetDarkForces** — a first-person raycasting shooter, ANetBBS's
  own take on the genre. Raid the Dark Forces' hideout across a
  10-sector campaign — 7 weapons, 8 enemy types, secrets, keycard
  vaults, explosive barrels. 3 save slots tied to your account.

## Strategy

- **Tic Tac Toe** — against a real AI opponent, not a coin flip.
- **[[Meadowlark Valley]]** — an original town/farm-builder sim, by
  far the deepest game in the Center. Has its own wiki page.

## Cards & Casino

- **Klondike Solitaire** — drag-and-drop, build all four suits Ace to
  King.
- **Video Poker** — Jacks or Better, with a full paytable.
- **Texas Hold'em** — no-limit, up to 4 CPU opponents (Easy, Medium,
  or Hard).
- **Blackjack** — hit, stand, double down, split.
- **Slot Machines** — three themed machines: Classic Bars, Lucky
  Fruits, Retro BBS.

Blackjack, Video Poker, Hold'em, and Slots share a play-money wallet
economy — a starting balance per game that resets every Monday if you
go broke. See [[Doors]] for how that's implemented.

## RPG & other

- **Text Adventure** — a classic parser-based dungeon crawl (`go
  north`, `take key`, `look`, ...).
- **[[ebooks|Ebook Reader]]** — search and read free public-domain
  classics from Project Gutenberg, with bookmarks and reading history.
  Has its own wiki page.
- **Typing Speed Test** — measure your WPM against a passage.

## Terminal doors (not in the browser Game Center)

Not everything is a web game. **ANetCRAFT** (a bundled
Minecraft-inspired survival game), **LORD** (Legend of the Red
Dragon), and shareware **DOOM**/**Duke Nukem 3D** (playable in-browser
via an emulator, no telnet/SSH needed) are classic doors, launched the
same way any other door is. Full rundown: [[Doors]].

## Something broken?

Message {sysop_name} — game bugs get fixed fast around here.
"""),

    # ------------------------------------------------------------------
    ('ebooks', 'Ebook Reader', """
# Ebook Reader

A built-in reader for free, public-domain books — no downloads, no
account on some other site, just search and start reading. Lives in
the [[games|Game Center]] alongside the rest of the built-in games,
in both the web UI and the terminal.

## What it does

- Searches Project Gutenberg's catalog (via the Gutendex API) by
  title or author.
- A curated shelf of well-known classics for instant browsing without
  searching first (Pride and Prejudice, Frankenstein, Dracula, Alice
  in Wonderland, Sherlock Holmes, Moby Dick, A Tale of Two Cities,
  and more).
- Clean, book-styled reading view with chapter navigation.
- Bookmarks and reading history — pick up where you left off.
- Download the cached text as a plain `.txt` file.

## Where

- Web: Game Center → Ebook Reader.
- Terminal: same feature, text-mode reading view, from the main menu.

Both share the same bookmarks/history — start a book on the web,
finish it in the terminal, or the other way around.
"""),

    # ------------------------------------------------------------------
    ('meadowlark-valley', 'Meadowlark Valley', """
# Meadowlark Valley

An original town + farm building sim, built entirely for {bbs_name} —
not affiliated with, based on, or copying any commercial game. Find
it in the [[games|Game Center]] under Strategy.

## The basics

You start with a Town Hall, 60x45 tiles of open land, and $1,500.
Zone houses, shops, and farms; keep the lights and water on; balance
taxes against happiness; and watch your villagers actually walk to
work and back on a daily schedule.

- **Roads** connect everything — houses/shops/farms need road access
  to grow or hire.
- **Power Plant** and **Water Tower** cover a radius around
  themselves (no wires/pipes to place — pure proximity). Town Hall
  gives a small free radius too, enough to bootstrap your first few
  houses before you can afford real utilities.
- **Tax rate** (0-25%, default 9%) funds services but a rate much
  above 10% starts denting happiness. A well-run town is sustainable
  at the default rate; running at 0% is a real (if harsh) austerity
  choice, not a trap.
- **Population milestones** unlock new buildings as your town grows —
  Factories at 20, Clinics/Police/Fire at 30, Schools/Parks at 50, and
  on up through Universities, Malls, Stadiums.

## Villagers

NPCs spawn based on population, each with a generated name, a home,
and (if a job is available) an assigned workplace — they path along
the real road network on a daily commute: to work, work in place,
back home, wander near home overnight. Click a villager to see their
name, mood, and employment status.

## Farmers

Farm Plots are a real workplace, same as a Shop or Factory. An NPC
assigned to a farm — a "farmer" — harvests the crop automatically the
day it ripens, no click needed, credited straight to your treasury
and named in the Town Log. An unstaffed farm (no population/villagers
assigned yet) still works exactly like it always did: click a ripe
farm to harvest it yourself.

## View modes

Beyond the normal map view, three data overlays help you scout where
to build next, SimCity-style:

- **Land Value** — green (desirable) to red (undesirable), boosted by
  parks/civic buildings, hurt by factories and heavy traffic.
- **Crime** — house-by-house crime risk, sharply reduced by nearby
  Police Station coverage (plus a smaller town-wide reduction for
  every station you've built).
- **Traffic** — road congestion near each tile.

## Disasters & events

Random fires (reduced by Fire Station coverage), storms (temporary
happiness dip), droughts (slower farm growth), and vandalism
(reduced by Police coverage, only starts once your town is big enough
to actually build a station) keep a long-running town from being
purely a spreadsheet.

## Saves

3 save slots, tied to your ANetBBS account — no import/export files,
your progress just lives on your account. Autosaves every ~30 seconds
to whichever slot you last used.

## Co-op — build with friends

Click the 🤝 button to host a room (you get a short share code) or
join a friend's room with their code. One player's town is the shared
one everyone builds on — real-time, no turns. Leaving the co-op panel
and building solo again is one click away (Leave Room).

## Reference

The authoritative deep-dive (every constant, every fix, every known
limitation) lives in the game's own `README.md` in its source
repository — this wiki page is the player-facing summary.
"""),
]


def _page_never_manually_edited(page):
    """True if every WikiRevision for this page was system-generated
    (initial seed or a prior auto-sync) -- never a real logged-in
    sysop/user edit. web/wiki.py's edit() route is @login_required and
    always calls _save_revision(..., current_user), so a genuine human
    edit always has a non-NULL author_id; seed-authored revisions
    always use author_id=None (see below). Used to gate sync_unedited
    so it can never clobber a sysop's own wiki customization."""
    return (WikiRevision.query
           .filter_by(page_id=page.id)
           .filter(WikiRevision.author_id.isnot(None))
           .first() is None)


def seed_initial_pages(force=False, sync_unedited=False):
    """Insert the initial wiki content if it isn't there yet.

    Idempotent by default -- only adds a page if its slug doesn't
    already exist. Each page gets an initial `r1` revision so history
    isn't empty.

    Real gap found live: because this was idempotent-only, once a
    page's slug existed, NO later content fix made to SEED here ever
    reached an already-seeded install -- across several past "docs/
    wiki accuracy pass" sessions, fixes landed in this file but never
    actually shipped to any install that had already seeded that page,
    only to brand-new ones. A live sysop found stale paths/service
    names/ports that had already been fixed in SEED long ago.

    force=True unconditionally overwrites EVERY matching page,
    including ones a sysop has hand-edited via the wiki UI -- kept
    for completeness but not safe for automated/unattended use.

    sync_unedited=True (the safe fix, used from the app startup path)
    refreshes only pages the sysop has never personally touched (see
    _page_never_manually_edited) and only when the content actually
    differs, so an update.sh-triggered restart keeps untouched pages
    current with the shipped SEED automatically, while never touching
    a page any real edit has ever landed on.
    """
    try:
        from flask import current_app
        cfg = getattr(current_app, 'config', {}) or {}
    except Exception:
        cfg = {}
    bbs_name = cfg.get('BBS_NAME') or 'ANetBBS'
    sysop_name = cfg.get('SYSOP_NAME') or 'the sysop'
    added = 0
    for slug, title, body_tpl in SEED:
        # Use explicit .replace() rather than .format() so braces in the
        # seed body (e.g. `echomail/{in,out}box/`) don't blow up.
        body = (body_tpl.strip()
                .replace('{bbs_name}', bbs_name)
                .replace('{sysop_name}', sysop_name))
        existing = WikiPage.query.filter_by(slug=slug).first()
        do_refresh = force
        if existing and not force and sync_unedited:
            if (_page_never_manually_edited(existing)
                    and (existing.title != title or existing.body != body)):
                do_refresh = True
        if existing and not do_refresh:
            continue
        if existing and do_refresh:
            existing.title = title
            existing.body = body
            existing.is_deleted = False
            existing.updated_at = datetime.utcnow()
            db.session.flush()
            page = existing
        else:
            page = WikiPage(slug=slug, title=title, body=body,
                            summary='Initial seed content',
                            is_deleted=False)
            db.session.add(page)
            db.session.flush()
        # Add a revision; rev_num = next per page.
        from sqlalchemy import func as _f
        next_rev = (db.session.query(_f.max(WikiRevision.rev_num))
                    .filter_by(page_id=page.id).scalar() or 0) + 1
        rev = WikiRevision(page_id=page.id, rev_num=next_rev,
                           title=title, body=body,
                           edit_summary=('Initial seed content' if next_rev == 1
                                         else 'Automatic sync from updated seed content'),
                           author_id=None, author_ip=None)
        db.session.add(rev)
        added += 1
    if added:
        db.session.commit()
    return added
