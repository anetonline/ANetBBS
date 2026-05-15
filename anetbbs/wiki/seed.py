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
- [[Telnet]] — classic terminal access on TCP 23
- [[SSH]] — secure terminal access on TCP 22
- [[Rlogin]] — game-server style auto-login on TCP 513

## What's inside

- [[Message Boards]] — local discussion forums
- [[Echomail]] — FidoNet-style global mail (BinkP networking)
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
| Telnet | port 23 | Choose `(N)ew user` at the prompt |
| SSH | port 22 | Same `(N)ew user` flow over SSH |

After registering, the sysop may require you to answer a few
questions — these are configured per-BBS and seen on first login.
See [[New User Questionnaire]] for details.

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
- [[Telnet]] (TCP 23)
- [[SSH]] (TCP 22, password and key auth)
- [[Rlogin]] (TCP 513, used by game-server style auto-login)
- [[Gemini]] (TCP 1965, read-only mirror of public boards)
- [[Finger]] (TCP 79, user info)

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
- [[TIC Processor]] for FidoNet TIC echo distribution
- [[QWK]] mail packets for offline reading

## Doors

- [[Doors]] — door game subsystem driving:
  - Native Synchronet (over rlogin)
  - DOS doors via [[DOSBox-staging]] + TCP nullmodem bridge
  - Web doors (native HTML/JS games)

## Real-time

- [[Chat]] — multi-user chat with channels
- [[IRC Bridge]] — bridge to/from external IRC networks
- [[MRC]] — the Multi-Relay Chat protocol used by many BBSes
- [[Telegram Bridge]]
- [[Sysop Page]] — bell the sysop, see who's around

## Information

- [[RSS Reader]] — built-in feed aggregator (web + terminal)
- [[Bulletins]] — sysop-curated short notices
- [[Site Pages]] — pages the sysop hand-authors
- [[Personal Pages]] — user-edited homepages on the BBS
- [[Image Gallery]]
- [[Stats]] — site activity, top users, leaderboards
- [[Nodelist]] — FidoNet/network nodelist browser
- This wiki (`/wiki/`)

## Sysop tools

- [[Sysop Control Panel]] — services, NodeSpy, who's online
- [[NodeSpy]] — view and kick live terminal sessions
- [[Echomail Admin]] — networks, areas, BinkP polling
- [[Doors Admin]] — register doors and game servers
- [[RSS Admin]] — manage feeds
- [[Files Admin]] — areas, virus scanner, batch tools
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
the [[Login]] screen. Otherwise, ask the [[Sysop]] — they can
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
3. **Play doors.** High scores show up on the [[Leaderboard]].
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

Classic terminal access. Port **23/tcp**.

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
email, and answer the [[New User Questionnaire]].

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
Port **22/tcp**.

## Connect

```bash
ssh -p 22 username@bbs.a-net.fyi
```

Replace hostname / port to match your BBS.

## Why SSH over telnet?

- Encrypted in transit
- Auth handled by SSH before you ever see the BBS menu — username
  pre-fills, optional password is also accepted
- Public-key auth works for accounts that have one registered
- Some networks block port 23 outright; SSH usually gets through

## SSH key auth

Register your public key from `/profile/edit` → SSH Keys.
Multiple keys per user are fine. Key auth bypasses the password
prompt.

## What you can do over SSH

Everything the [[Telnet]] menus offer. Doors work over SSH too,
including DOS doors via the [[DosBridge]] — the bridge is
protocol-agnostic.

## Host key

The first connection prompts you to accept the BBS's host key
fingerprint. The sysop should publish the fingerprint somewhere
(usually on the [[Site Pages|info pages]]) so you can verify it.
"""),

    # ------------------------------------------------------------------
    ('rlogin', 'Rlogin', """
# Rlogin

Rlogin (RFC 1282-ish — see below) gives single-sign-on terminal
access. Port **513/tcp**. Mostly used by **inter-BBS game servers**
where one BBS launches a game on another and the player travels with
their identity intact.

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
convention. See [[Door Setup#rlogin doors]] for the details on the
remote-game flow.

## Locally

For local user logins, ANetBBS uses rlogin to auto-login from the
[[Web Terminal]] and from MRC user-roster joins. The user types
nothing — their identity rides the rlogin handshake and they land
on the menu.
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
See [[Reactions]].

## Subscriptions

Click **Subscribe** at the top of a thread to get notified of every
new reply.

## Polls

Reply to a thread with a `/poll` slash-command from the editor, or
use the **Poll** tab on the compose form. See [[Polls]].

## Attachments

Up to four files per post. Images render inline; other files become
download links. See [[Files]] for the global library.

## Threading

ANetBBS uses flat threads by default — replies are appended in time
order. Quoted replies (Reply quote) prefix the original text with
`>` like classic Usenet.

## Moderation

Sysops can lock or unlock threads, pin posts to the top of a board,
move threads between boards, and soft-delete content. See
[[Moderation]].
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
list under [[Echomail Admin]]. Common areas in the BBS scene:

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
- **MicroNet**, **AmigaNet**, **AgoraNet**, …

Each network has its own uplink, addressing scheme (`zone:net/node`),
and area policies. See [[BinkP Setup]] to join one.

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

`/admin/echomail/areas/` → **Add Area**. The tag must exactly match
the network's tag (`BBS_SCENE`, `fsx_gen`, etc.) — coordinator will
have a list.

## Step 3 — first poll

`/admin/echomail/poll/<network>` runs an immediate BinkP session.
Watch the log for handshake success and "received N packets".
On the first poll you'll often get a big initial dump as the uplink
catches your node up.

## Step 4 — automated polling

Sysop adds a cron entry (recommended every 30–60 minutes):

```cron
*/30 * * * * curl -fsS https://bbs.a-net.fyi/admin/echomail/poll/fsxnet
```

Or use the built-in scheduler under
`/admin/echomail/schedule/`.

## Areafix

To subscribe to a new area without bothering the coordinator,
send a netmail to the `AreaFix` user at the uplink's address with
the body:

```
%PASSWORD yourareafixpw
+BBS_SCENE
+SYSOP_HELP
```

ANetBBS has an [[AreaFix]] processor for inbound areafix requests
on your own node too — useful if other BBSes peer off you.

## TIC files

For BinkP-distributed file echoes, see [[TIC Processor]].
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
| `web` | Native HTML/JS — runs in the browser, no shell-out | 2048, Wordle |
| `door_rlogin` | Remote Synchronet game server | LORD, TradeWars on a partner BBS |
| `door_dos` | DOS executable via [[DOSBox-staging]] + TCP nullmodem bridge | LORD locally |
| `door_native` | Linux native binary speaking DOOR.SYS | various forks |

## Playing

- Web: `/games/` — game list. Pick one and play in the browser
  (web games) or in a [[Web Terminal]] popup (terminal games).
- Terminal: main menu — usually under a "Games" or "Doors" sub-menu.

## Dropfiles

Terminal doors expect a **dropfile** — a text file with the user's
name, time remaining, baud rate, etc. ANetBBS writes the major
formats automatically right before launch:

- `DOOR.SYS` — almost-universal
- `DORINFOx.DEF` — Synchronet-flavored
- `EXITINFO.BBS` — Renegade/Telegard

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

`/admin/games/` → **Add Game**. Type = `web`. Provide the HTML
template path (relative to `anetbbs/templates/games/web/`). That's it.

## 2. Remote rlogin doors (Synchronet game servers)

Most "play LORD on someone else's BBS" setups go through rlogin.

`/admin/games/` → **Add Game**. Type = `door_rlogin`. Fields:

- **Host** — the remote BBS hostname or IP
- **Port** — usually 513
- **Remote username** — the rlogin server-side login name. For
  Synchronet game-server doors this is typically `door:LORD` or
  similar; check with the remote sysop.
- **Send password** — the session password agreed with the remote
- **Local username pass-through** — if checked, your local
  user's name is sent as the rlogin client-username. Otherwise a
  generic guest name is used.

> ⚠ Synchronet-flavored rlogin uses **password first, username
> second** — opposite of RFC 1282. ANetBBS's outbound client
> already does this; you don't need to flip anything.

## 3. DOS doors

The full setup — DOS exe + DOSBox + TCP nullmodem bridge. Detailed
in [[DOS Door Recipe]]. Short version:

1. Install DOSBox-staging.
2. Drop the door's files under
   `/var/lib/anetbbs/doors/<door-name>/`.
3. Create a DOSBox config that wires `serial1=nullmodem
   server:<port>` and `serial2=nullmodem client:<host>:<port>`.
4. Register the game in the admin UI with type `door_dos`. Set the
   bridge port and the DOSBox command line.
5. Test by playing it. Watch
   `journalctl -u anetbbs-telnet -f` for bridge bytes-in/out.

[[DOS Door Recipe]] walks through LORD as a concrete example,
including the [[LORD Setup]] gotchas.

## 4. Native Linux doors

A bare `door_native` type — invoked with the dropfile path on
argv. Works for forks like `lord-linux` or any compliant
DOOR.SYS-aware binary.

## Per-door config tips

- **Time limits**: set on the Game row. The BBS enforces them with
  a watchdog that closes the user's writer when time runs out.
- **Max nodes**: how many users can be in the door simultaneously.
  Most classic DOS doors are single-user; set 1.
- **Idle timeout**: if the user's terminal goes silent for N
  minutes the watchdog kicks them. Useful for doors that don't
  detect carrier loss.

## Debugging

- `/admin/games/<id>/test` does a launch from the admin view (sysop
  account, time/idle limits respected).
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
   `anetbbs/games/sbbs_doors/lord/`. Needs the Synchronet `jsexec`
   runtime on the host.
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

- DOSBox-staging built with `--enable-nullmodem` (default in 0.81+)
- Xvfb if you want to run headless
- The LORD binaries (`LORD.EXE`, `LORDCFG.EXE`, `LORD.DAT`, etc.)
  in a directory you own — `/var/lib/anetbbs/doors/lord/`

## DOSBox config

`lord.conf` next to the binaries. Key sections:

```ini
[serial]
serial1 = nullmodem server:9001
serial2 = disabled

[autoexec]
mount c .
c:
LORDCFG -nodes 1
LORD /N1 /B19200
```

`COM1` is the bridge port (matched on the BBS side); LORDCFG runs
once to write `NODE1.DAT`; then LORD launches in "single-node, COM1
at 19200" mode.

## Bridge wiring

The BBS process opens a TCP connection to `localhost:9001` and
ferries bytes between the user's terminal writer and the socket.
That's the `DosBridge` class — see [[DosBridge]].

## LORDCFG gotchas

- `LORDCFG` writes `NODE1.DAT` (or `NODE<n>.DAT`) into the working
  dir. It's a binary config blob — leave it alone between runs.
- The dropfile path inside LORDCFG should be `.\\` (current dir),
  not absolute, because DOSBox-staging's path mounting confuses
  some doors otherwise.
- `DOOR.SYS COM0` means "carrier present"; set to **COM1** for the
  bridge to think it's on a real port. ANetBBS writes this
  correctly out of the box.

## Exit hangs

If LORD's exit doesn't release Xvfb cleanly, the watchdog needs
a `waitpid` on DOSBox plus an idle timeout. ANetBBS does both —
`xvfb-run` will hang past door exit if not babysat. The fallback
is **Ctrl+]q** which the bridge translates to DOSBox's "kill
emulator" sequence.

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
  — see [[Doors#don-t-touch-the-door-s-working-dir]].
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

Multi-user real-time chat. Three flavors live in one feature:

## 1. Local chat

Users on this BBS in the local channels. Web: `/chat/`. Terminal:
main menu → Chat.

Backed by Socket.IO on the web side and async on the terminal side.
History scrollback is 200 lines per channel.

## 2. IRC bridge

The BBS can join external IRC networks and bridge them into local
channels. Sysop configures servers/networks under
`/admin/irc/`. Once linked, anything users say in a bridged local
channel is forwarded to the IRC channel and vice versa.

Web users see their IRC nick on hover; user/channel modes (op,
voice) are preserved.

See [[IRC Bridge]].

## 3. MRC (Multi-Relay Chat)

A BBS-scene chat network — like IRC but specifically for BBS
sysops and users. The protocol is RFC-style line-oriented with
`!` commands. Default ANetBBS connects to the public Magicka MRC
hub on `mrc.bottomlesszoo.com:5000`.

See [[MRC]].

## Etiquette

- Local channels: keep on-topic; sysop sets the topic.
- Bridged channels: assume people on the other side don't know
  what BBS they're talking to. Mention it when relevant.
- Be patient — async typing means a lull doesn't mean nobody's
  there.

## Slash commands

`/me action`, `/topic ...`, `/who`, `/away [reason]`,
`/whois <nick>`, `/msg <user> <text>` (DM), `/help` lists them all.
"""),

    # ------------------------------------------------------------------
    ('mrc', 'MRC', """
# MRC

**Multi-Relay Chat** — a BBS-scene chat protocol that connects sysops
and users across many BBSes through a central hub.

## How it differs from IRC

- One central hub (no server federation)
- Authentication is BBS-name-based (no nickserv)
- Messages carry the user's BBS-name as part of the identity
- Mostly text-only; not many bots; a calmer, more BBS-y feel

## Connecting

Built in. The `mrc-bridge` systemd service runs alongside the BBS
and keeps a persistent connection to the configured MRC hub.

`/admin/mrc/` configures:

- Hub host (default `mrc.bottomlesszoo.com`)
- Hub port (default 5000)
- Local channel mapping — which MRC channels get bridged into which
  local chat channels

## Default channels

- `general` — everyone
- `sysops` — sysop-only (gated by the hub)
- `coding`, `linux`, `games` — topical
- channel-of-the-day rotates on Sundays

## Bridged into web/terminal

Once MRC is wired up, [[Chat]] users on the local BBS see MRC
traffic as if it were native. Your own messages get a `<sysop@anet>`
or `<user@anet>` prefix on the MRC side.

## Reference: uMRC

The `uMRC` reference client is bundled in `vendor/uMRC/` — useful
as a sanity check if the bridge is misbehaving.
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
- Polls each feed in the background — see [[RSS Poller]]
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

- Web: `/pm/send`
- Terminal: main menu *N* on systems with the v2 main menu

The compose form takes a recipient username, subject, and body.
Markdown is supported in the body.

## Inbox

`/pm/inbox` (web) or main menu *P* (terminal). Unread messages
have a bold/yellow row. The badge in the top nav shows the unread
count.

## Reply / forward

Standard `>` quoting for replies; forward includes a "Fwd:" prefix
on the subject.

## Block list

Bothered by a user? `/profile/blocks/` lets you block them. Blocked
users can't PM you; you can still see their public posts.

## Deletion

Soft-delete only — the message is hidden from your inbox but still
exists for the other party. Sysops can hard-delete from
`/admin/users/<id>/pm/`.
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

`/imsg/send` — pick a target from the [[BBS Directory]] or type
`username@host` manually. Up to ~500 chars per message.

## Receiving

Inbound MSP arrives on TCP 18. The listener stores it in the
`InstantMessage` table; the unread badge in the nav shows the
count. `/imsg/` is your inbox.

## Network reachability

You need TCP 18 reachable from the outside, OR your messages will
arrive but you can't be paged back. `/admin/peers/health` runs a
self-test against a list of MSP-speaking BBSes.

## Directory

`/imsg/directory/` shows the BBS directory the sysop has curated.
Add to it under `/admin/peers/`.

## Encoding

MSP is 8-bit clean; assume CP437 for safety on inbound. Outbound
encodes the textarea content as latin-1 (so CP437-style art going
out doesn't get mangled).
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

## Uploading

Logged-in users can upload to the **Uploads** area by default.
Sysops can change which areas accept user uploads.

Per-file metadata:

- Description (required)
- Tags (optional, comma-separated)
- Replaces (older file? link them so the system marks one as
  superseded)
- License (optional)

## Virus scanning

The sysop can wire ClamAV via `/admin/files/virus-scan/`. Uploads
are scanned on arrival; infected files are quarantined to a
sysop-only area.

## Descriptions

ANetBBS reads `FILE_ID.DIZ` from inside `.zip` / `.rar` / `.7z`
uploads and uses it as the description if you didn't provide one.
Common BBS-scene practice.

## TIC distribution

For files received via [[TIC Processor]], the file lands in the
configured TIC area, its `.tic` companion is parsed, and the
description is read from the `Desc:` line.

## Quotas

Sysop sets per-user upload quotas under `/admin/users/<id>/`. Default
is no quota.
"""),

    # ------------------------------------------------------------------
    ('themes', 'Themes', """
# Themes

The web UI's color palette is variable-driven. ANetBBS ships with
several themes plus a custom-theme editor.

## Default themes

| Slug | Look |
|------|------|
| `modern-dark` | High-contrast slate (recommended) |
| `amber-crt` | Amber-on-black 80s vibe |
| `green-crt` | Green-on-black classic |
| `solarized-dark` | Solarized palette |
| `dracula` | Popular dark scheme |
| `nord` | Cool blues |

## Per-user

`/profile/edit` → Theme. Stored on `User.theme_id`.
The selected theme's CSS variables are emitted in a `<style>` tag at
the top of every page (see `base.html`).

## Site-wide default

Sysop sets one theme as `is_default=True`. Anonymous visitors and
new accounts see this theme until they change it.

## Custom themes

`/admin/themes/` lets the sysop author a new theme. Pick variable
values from a color-picker; preview live; save. Every theme is just
a set of CSS variables — `--theme-bg`, `--theme-primary`,
`--theme-text`, etc.

## Light themes

ANetBBS is dark-by-default — every list-group, table-row tint, and
alert palette is hand-tuned for dark backgrounds. Light themes are
possible but require overriding the few "dark" overrides in
base.html. There's no shipped light theme today.
"""),

    # ------------------------------------------------------------------
    ('sysop-guide', 'Sysop Guide', """
# Sysop Guide

The long-form admin overview. Newer than the README, less prescriptive
than the [[Tutorial]].

## What runs

ANetBBS is a couple of services:

| Service | What it does |
|---------|--------------|
| `anetbbs-web` | gunicorn + eventlet. Serves the web UI, REST, sockets. |
| `anetbbs-telnet` | asyncio listener on 23/22/513 — telnet, SSH, rlogin |
| `anetbbs-mrc-bridge` | persistent MRC connection |

`systemctl status anetbbs-web anetbbs-telnet anetbbs-mrc-bridge`
shows the lot.

## Daily

- Glance at the [[Sysop Control Panel]] for service health and
  who's online.
- Check the [[NodeSpy]] panel for any stuck sessions; kick if needed.
- Process [[Echomail]] polls (cron handles this; verify
  `/admin/echomail/log/` for errors).
- Approve any [[Files|files]] flagged by the virus scanner.

## Weekly

- Run [[Backup]].
- Skim the [[Stats]] page — top users, new registrations, message
  counts.
- Read the latest fido news area, post a hello if you've been
  quiet.

## Monthly

- Rotate logs (`/var/log/anetbbs/` — `logrotate` config in
  `deploy/logrotate.anetbbs`).
- Review user registrations: ban obvious spam accounts.
- Update door binaries if their authors have patched.
- Check [[Peers]] for dead links (`/admin/peers/health`).

## On upgrade

1. `sudo systemctl stop anetbbs-web anetbbs-telnet anetbbs-mrc-bridge`
2. Run [[Backup]]
3. `sudo bash update.sh --install-dir /home/stingray/anetbbs`
4. `sudo systemctl start anetbbs-web anetbbs-telnet anetbbs-mrc-bridge`
5. Hard-refresh your browser to bust cached CSS.
6. Watch `journalctl -u anetbbs-web -f` for a minute to catch
   any column-add migration noise.

## Where things live

- Install: `/home/stingray/anetbbs/` (this site)
- Data: `/home/stingray/anetbbs/data/` (sqlite + uploads)
- Logs: `/home/stingray/anetbbs/logs/` (rotated)
- DOSBox doors: `/var/lib/anetbbs/doors/<name>/`
- Echomail spool: under data dir, `echomail/{in,out}box/`

## See also

- [[Sysop Control Panel]]
- [[Door Setup]]
- [[BinkP Setup]]
- [[Backup]]
- [[Tutorial]]
"""),

    # ------------------------------------------------------------------
    ('sysop-control-panel', 'Sysop Control Panel', """
# Sysop Control Panel

The launchpad for live operations. `/admin/control/`. Admin-only.

## Sections

### Services

systemctl-driven status for `anetbbs-web`, `anetbbs-telnet`,
`anetbbs-mrc-bridge`, etc. Start / Stop / Restart buttons need the
`anetbbs` sudoers entry — see `deploy/sudoers.anetbbs`.

### NodeSpy

Live terminal session list with view-screen and kick buttons. See
[[NodeSpy]].

### Online users

Web + terminal users combined, last 5 minutes. Refreshes every
4 seconds.

### Sysop tools

Quick links to:

- [[Echomail Admin]]
- [[Doors Admin]]
- [[RSS Admin]]
- [[Files Admin]]
- [[Themes|Theme manager]]
- [[Backup]]

## See also

- [[NodeSpy]]
- [[Sysop Guide]]
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

Different from a [[Ban]]: a kick just drops the current connection.
The user can reconnect immediately. To stop them coming back, add
an IP ban under `/admin/ip-bans/`.

## Audit trail

Every kick writes a `UserActivity` row with `activity_type='kick_node'`,
the slot, the target username, the reason, and the sysop's
username + IP — `/admin/audit/` shows the trail.
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
| `data/ansi/` | Custom ANSI screens |
| `data/echomail/` | Inbound/outbound BinkP packets |
| `data/secrets.env` | API keys (CHMOD 600!) |
| `/var/lib/anetbbs/doors/` | Door binaries & per-door state |

Source code itself isn't critical — restored from the install
tarball.

## Quick weekly tarball

```bash
sudo tar czf /backup/anetbbs-$(date +%F).tar.gz \\
    --exclude=data/logs --exclude=__pycache__ \\
    /home/stingray/anetbbs/data \\
    /var/lib/anetbbs/doors
```

Run that out of cron once a week, rotate keep-last-30.

## Hot DB copy

sqlite's online backup API gives a consistent dump even while the
BBS is writing. The bundled script:

```bash
sudo -u stingray /home/stingray/anetbbs/tools/db_backup.sh \\
    /backup/db/anetbbs-$(date +%F-%H%M).db
```

cron this every 6 hours during the day if you want point-in-time
recovery.

## Off-site

Worst case, your house burns down. Push the rotated tarballs to an
off-site target — rclone to B2/S3, or rsync over SSH to a friend's
box.

## Restore

1. Stop the services.
2. Untar the backup into a fresh tree.
3. `chown -R stingray:stingray /home/stingray/anetbbs/data`
4. Start the services. Auto-sweep migrations will fix any
   column-add diffs between the backup era and current code.
"""),

    # ------------------------------------------------------------------
    ('architecture', 'Architecture', """
# Architecture

A 10,000-ft overview of how ANetBBS is wired up.

## Processes

```
+---------------------+         +------------------------+
|  anetbbs-web        |         |  anetbbs-telnet        |
|  gunicorn+eventlet  |         |  asyncio listener      |
|  Flask app + sock.IO|         |  telnet/ssh/rlogin     |
+----------+----------+         +-----------+------------+
           |                                |
           |  SQLite (shared)               |
           +--------- data/anetbbs.db ------+
                                            |
                                  +---------+----------+
                                  |  anetbbs-mrc-bridge|
                                  |  MRC client (long  |
                                  |  poll)             |
                                  +--------------------+
```

Two main services (`web`, `telnet`), one secondary (`mrc-bridge`),
all sharing the same SQLite database. Cross-process signals (like
[[NodeSpy]] kick) go via DB flags polled by the other process.

## Tech stack

| Layer | Choice |
|-------|--------|
| Web framework | Flask 3 + Flask-Login + Flask-SocketIO |
| WSGI | gunicorn + eventlet workers |
| Realtime | Socket.IO (web) / asyncio (terminal) |
| DB | SQLite via SQLAlchemy 2 |
| Migrations | "Auto-sweep" — read model metadata, ALTER TABLE for missing cols |
| Frontend | Bootstrap 5, xterm.js, vanilla JS |
| Markdown | python-markdown + bleach |
| RSS | feedparser |
| ANSI | custom CP437 helpers in `anetbbs/core/ansi_ui.py` |

## Repo layout

```
anetbbs-rebuilt/
  anetbbs/
    core/           session, ANSI, terminal protocols
    features/       BBS sub-systems (menus, doors, multinode...)
    web/            Flask blueprints (one per feature)
    wiki/           wiki renderer + slug + seed
    models.py       SQLAlchemy models (one file, ~2000 lines)
    web_app.py      Flask app factory
    config.py       env-driven config
  templates/        Jinja templates
  static/           images, css overrides
  data/             SQLite + uploads (runtime; gitignored)
  docs/             markdown docs served at /docs/
  deploy/           systemd units, sudoers, logrotate
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
subscription requests. See [[AreaFix]].

**BinkP** — Modern FidoNet mail transport protocol (TCP 24554). See
[[BinkP Setup]].

**Bridge** — Glue between two protocols. The [[DosBridge]] copies
bytes between TCP and a terminal writer; the [[IRC Bridge]] copies
messages between local chat and IRC.

**CP437** — The "OEM-US" codepage with line-drawing chars and blocks
that ANSI art relies on. See [[CP437]].

**Dropfile** — A text file the BBS writes for a door so the door
knows the user's name, time remaining, etc. (`DOOR.SYS`,
`DORINFOx.DEF`, `EXITINFO.BBS`).

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
distributed weekly. See [[Nodelist]].

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
username is. Sysops can see IP through `/admin/audit/`.

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
- [[Orphan Pages]]
"""),

    # ------------------------------------------------------------------
    ('web-terminal', 'Web Terminal', """
# Web Terminal

A real terminal in your browser, talking to the BBS's local
[[Telnet]] listener via xterm.js over WebSockets.

## Where

`/term/` on the web UI. Logged-in users only by default; sysop can
allow guest access.

## What you get

A full CP437/ANSI terminal — the same UI you'd see in [[SyncTERM]]
or [[NetRunner]], rendered in the browser:

- 80×25 default; resizable to whatever your viewport allows
- ANSI color, bold, blink (animated)
- Mouse-driven copy/paste
- Doors run inline, including DOS doors via the [[DosBridge]]

## Auth

The web app generates a one-shot token tied to your session,
opens an internal rlogin to `localhost:513` with your username,
and pipes both ends. Your password isn't re-prompted.

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

Bridges between local [[Chat]] channels and external IRC networks.
One bridge per network, many channel mappings per bridge.

## Configure

`/admin/irc/` — sysop only. Fields:

- **Network name** (label only — e.g. `Libera`)
- **Server** (`irc.libera.chat`)
- **Port** + TLS toggle
- **Nick / username**
- **SASL** credentials if applicable
- **Auto-join channels** + which local channel each maps to

## How it works

1. The bridge connects on BBS startup and joins the configured
   channels.
2. Anything users say in a mapped local channel is forwarded to
   IRC tagged with the user's BBS nick.
3. Anything IRC users say is forwarded into the local channel.

## Identity

IRC sees a single bridge bot. The bot's NICK is set in admin; the
real BBS-user identity is prefixed onto the message text:
`<jerry@anet> hello world`.

Some networks like Libera prefer a "relay bot"-style namespace;
configure your nick accordingly.

## Limits

- One-way for the moment: bridge can't run /msg or /whois on
  behalf of BBS users. PMs from IRC go to the sysop.
- No DCC.
- TLS supported; SASL EXTERNAL not yet (sasl PLAIN works).

## See also

- [[Chat]]
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

`/netmail/inbox`. Unread netmail shows in the badge in the nav.

## AreaFix

`AreaFix@<your-node>` is a virtual recipient that lets *other*
sysops subscribe to your areas without bothering you. Configure
allowed areas in `/admin/echomail/areafix/`.

## Routing

If you peer with multiple uplinks, you can configure routing rules
(`zone 1 via 1:104/315`, `else via 21:1/1`). Most small installs use
a single uplink and don't need routing.

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

- **Title** (1–80 chars)
- **Body** (markdown supported)
- **Active** (uncheck to hide without deleting)
- **Order** (lower first)

## Pager view

On terminal, opening a long bulletin paginates with a `[Q]=quit`
prompt — see [[Pager]].

## Use cases

- Announcing maintenance / upgrades
- Highlighting a new feature (point users at the wiki page)
- Posting a "what's new" digest each month
- Sharing scene news that doesn't deserve a full board thread

## See also

- [[Site Pages]] — longer, more page-like sysop content
- [[Tour]] — multi-slide intro for new users
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

`/qwk/` — choose which areas and time range. The BBS bundles them
into a `.QWK` file you can download.

## Upload

A `.REP` (reply) packet uploads to the same `/qwk/` endpoint. The
BBS parses it and posts your replies to the right boards / echos.

## Readers

- [MultiMail](https://multimail.sourceforge.net/) — modern Linux
- [Bluewave](https://www.bbsdocumentary.com/) (DOS, classic)
- Various Windows tools

## Sysop tools

`/admin/qwk/` controls hub mode (let other BBSes pull QWK from
yours) and per-network packet limits.
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

`/admin/echomail/tic/` — list of TIC areas your node carries.

For each: which local file area to drop into, who's allowed to
upload (your uplink + maybe a few peers), and whether to forward
to downlinks.

## Forwarding

If you have nodes peering off you for the same TIC area, the
processor regenerates a fresh `.TIC` with you in the path and
queues it for outbound BinkP to each downlink.

## Checksums

`.TIC` carries a CRC32. If the file's CRC doesn't match, the
processor moves both into `bad/` and logs the mismatch. Manual
intervention required.

## See also

- [[Echomail]]
- [[BinkP Setup]]
- [[Files]]
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

- DOSBox-staging 0.81+ (`apt install dosbox-staging` on Debian/Ubuntu)
- Xvfb (`apt install xvfb`) for headless runs
- The DOS door's original files (preserve them as-is)
- TCP nullmodem patch baked into DOSBox-staging (default since 0.81)

## Steps

### 1. Drop the door into place

```bash
sudo mkdir -p /var/lib/anetbbs/doors/lord
sudo chown stingray:stingray /var/lib/anetbbs/doors/lord
sudo -u stingray cp -a /path/to/lord/* /var/lib/anetbbs/doors/lord/
```

### 2. Configure DOSBox

Make a `lord.conf` next to the binaries. The two essential
sections:

```ini
[serial]
serial1 = nullmodem server:9001
serial2 = disabled

[autoexec]
mount c .
c:
LORDCFG -nodes 1
LORD /N1 /B19200
```

Pick a free TCP port (9001 here) — used by the [[DosBridge]].

### 3. Register the door

`/admin/games/` → **Add Game**, type `door_dos`:

| Field | Value |
|-------|-------|
| Name | LORD |
| Description | Legend of the Red Dragon |
| Working dir | `/var/lib/anetbbs/doors/lord` |
| Command | `xvfb-run -a dosbox-staging -conf lord.conf` |
| Bridge port | `9001` |
| Max nodes | 1 |
| Time limit | 30 min |
| Idle timeout | 5 min |

### 4. First test

Play it from your sysop account first. Watch
`journalctl -u anetbbs-telnet -f` — you should see:

```
bridge connected to 127.0.0.1:9001
bridge: 215 bytes in, 4096 bytes out, ...
door exited code 0 — bridge closing
```

If you see "bridge: 0 bytes in, 0 bytes out", DOSBox isn't talking
back. Common causes:

- `serial1=stdio` instead of `nullmodem server:` (old config)
- Port already in use (`ss -tnlp | grep 9001`)
- Door config still pointing at COM1=stdio inside the door itself

### 5. Ship it

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

## The seven door types

ANetBBS runs door games via seven distinct backends:

1. **DOS doors** (DOSBox-staging) — TradeWars, LORD-DOS, Usurper
2. **Native Linux doors** — any executable, stdio piped
3. **Synchronet `.js` doors** — real `jsexec` if installed, otherwise
   our built-in Node + Synchronet API shim (~270 functions)
4. **Mystic Pascal `.mps`/`.mpx`** — bundled Mystic 1.12 A48 runtime
5. **Mystic Python `.py`** — fake `mystic_bbs` module
6. **rlogin out-dial** — DoorParty / A-Net Online / Synchronet xtrn
7. **Built-in web games** — Flask-routed mini-games

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
5. (Optional) Add a model class to `anetbbs/models.py` + run
   `flask db migrate`

The whole pipeline is ~50 lines of code for a working feature blueprint.

## Database

`anetbbs/models.py` is the single source of truth for SQLAlchemy
models. Flask-Migrate (alembic) handles schema changes — add a column,
run `flask db migrate -m "..."`, the service auto-runs `flask db
upgrade` on next start.

## Themes

Themes are DB rows with CSS variable values, edited visually at
`/admin/theme-builder/`. Add a new built-in theme by tweaking colors
in the builder, then add the resulting values to the theme seed block
in `anetbbs/seed_data.py`.

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
]


def seed_initial_pages(force=False):
    """Insert the initial wiki content if it isn't there yet.

    Idempotent — only adds a page if its slug doesn't already exist
    (or `force=True` to rewrite). Each page gets an initial `r1`
    revision so history isn't empty.
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
        if existing and not force:
            continue
        if existing and force:
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
                           edit_summary='Initial seed content',
                           author_id=None, author_ip=None)
        db.session.add(rev)
        added += 1
    if added:
        db.session.commit()
    return added
