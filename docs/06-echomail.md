# Echomail (FidoNet-style networks)

Echomail = old-school message networks shared across BBSes. ANetBBS's
echomail subsystem has grown into several cooperating pieces: networks
and areas, netmail (private point-to-point mail), the AreaFix
subscription robot, TIC file-echo distribution, a QWK offline reader,
and — on the one install designated as the network hub — a separate
Hub Management admin panel. This doc covers all of them.

## Two transports

- **BinkP** — the FidoNet TCP/IP standard. ANetBBS speaks BinkP
  natively (poll out, accept inbound).
- **QWK** — packet-based, popular for Dove-Net. ANetBBS can
  download / upload QWK packets over HTTP/FTP.

## ANotherNetwork — bundled by default

Every fresh ANetBBS install seeds a real, working echomail/QWK network
called **ANotherNetwork** (hub `bbs.a-net.fyi`, Zone 1200) — the same
way Synchronet installs ship pre-configured for Dove-Net. This isn't a
placeholder; it's the project's own live hub, wired up out of the box
so a new sysop has somewhere to actually go once they're ready to join
a network, instead of starting from a totally blank echomail config.

Seeded automatically (in `_create_default_data()` in
`anetbbs/web_app.py`):

- **Two network rows**, both `is_active=False` until the sysop fills
  in a node number/password and flips them on:
  - `ANotherNetwork` — BinkP transport, host `bbs.a-net.fyi:24554`,
    hub address `1200:1/1`, `ftn_domain='anet'`.
  - `ANotherNetwork (QWK)` — QWK/FTP transport, same host, hub ID
    `ANET`.
- **26 message echo areas**, tag prefix `ANN.`, shared between both
  network rows (same tag exists under both the BinkP and QWK entries,
  since it's one hub distributing to both transports). All start
  `is_active=True` / `is_subscribed=False` — visible as "available"
  once the sysop activates a network, but nothing polls until
  subscribed. Areas span 8 categories:
  - **General** (5): `ANN.GENERAL`, `ANN.INTRO`, `ANN.HUMOR`,
    `ANN.DEBATE`, `ANN.FEEDBACK`
  - **Technology** (4): `ANN.TECH`, `ANN.LINUX`, `ANN.SECURITY`,
    `ANN.NET`
  - **BBS Scene** (5): `ANN.BBS`, `ANN.BBSDEV`, `ANN.ANETBBS`,
    `ANN.DOORS`, `ANN.ANSIART`
  - **Retro** (3): `ANN.RETRO`, `ANN.GAMES`, `ANN.MUSIC`
  - **Hobby** (4): `ANN.MOVIES`, `ANN.BOOKS`, `ANN.FOOD`, `ANN.SPORTS`
  - **Trading** (1): `ANN.ADS`
  - **Data** (1): `ANN.DATA`
  - **SysOp** (2, sysop-only): `ANN.SYSOP`, `ANN.SYSOP.HELP`
  - **Test** (1): `ANN.TEST`
- **9 file echo areas**, tag prefix `ANN.FILES.`, attached only to the
  BinkP network row (TIC is BinkP-native, so unlike message areas
  these aren't duplicated across the QWK entry — `FileArea.tag` also
  has a DB-level unique constraint, the TIC processor's lookup key):
  `ANN.FILES.NODELIST` (Weekly Nodelists — flagged
  `is_nodelist_source=True`, see Hub Management below),
  `ANN.FILES.INFOPACK` (Infopacks), `ANN.FILES.BBSSOFT` (BBS Software),
  `ANN.FILES.DOORS` (Door Games & Utilities), `ANN.FILES.EBOOKS`,
  `ANN.FILES.LINUX`, `ANN.FILES.RETRO`, `ANN.FILES.ANSIART`, and
  `ANN.FILES.TEST`. All start inactive-subscription same as the
  message areas.

To actually join: apply for a node number at `bbs.a-net.fyi`, then
fill in your assigned FTN address / packet ID and password on the
seeded network row(s) at **Admin → Echomail Networks**, subscribe to
whichever areas you want, and activate the network.

## Setting up a network

**Admin → Echomail Networks** (`/admin/echomail/`):

1. **Add Network** — pick BinkP or QWK, give it a name, set your FTN
   address (e.g. `1:142/0`), hub/uplink address, and password. BinkP
   networks also take a CRAM-MD5 toggle (recommended — Synchronet
   requires it), an optional TLS toggle, and an optional **default
   netmail recipient** (a local username that catches netmail
   addressed to a name the BBS doesn't recognize).
2. **Add Areas** to the network — each `EchoArea` has a tag like
   `FIDO.GENERAL`, a name, optional moderator address.
3. **Subscribe** to areas you want to read.

### Domain suffix override (`ftn_domain`)

Per FSP-1028, a qualified FTN address (`addr@domain`) needs a domain
suffix of 8 characters or fewer. If you leave this blank, the poller
derives one automatically from the network's display **name**
(truncated/lowercased) — which produces an awkward result for a long
name. `"ANotherNetwork"` truncates to `"anothern"`, for example (this
is exactly why the bundled ANotherNetwork network row ships with
`ftn_domain='anet'` set explicitly). Set the **"Domain suffix
override"** field on the network's edit page
(`templates/echomail/admin/network_form.html`) to override this with
something shorter and cleaner while keeping the full display name
intact everywhere else. Blank/`NULL` falls back to the old
name-derived behavior, so existing networks aren't affected until you
choose to set it.

## AreaFix — subscription requests from peers

AreaFix is the standard FTN robot that lets a downstream peer manage
their own area subscriptions by sending a netmail instead of the hub
sysop doing it by hand. It lives in `anetbbs/echomail/areafix.py`.

A peer addresses a netmail to **`AreaFix`** (also recognized:
`Area Fix`, `AreaMgr`) at your hub address, with the **AreaFix
password in the Subject line** (per FTS-0024 — NOT in the body) and
one command per line in the body:

```
+FIDO.GENERAL       subscribe to an area
-FIDO.GENERAL       unsubscribe from an area
+ALL / -ALL         subscribe / unsubscribe to everything available
%LIST (or %QUERY)   reply with current subscriptions
%HELP               reply with a help/command summary
```

The bot replies with a netmail confirming what changed. If the
requester's FTN address matches a `BinkPNode` this BBS hosts as a hub
peer (see Hub Management below), the change is scoped to that peer's
own `EchoAreaNode` subscription rows instead of the global
`EchoArea.is_subscribed` flag — so on a hub install, each downstream
node keeps an independent subscription list.

**Configuring the password**: each network has a `binkp_password`
(the BinkP session secret) and an optional separate
**`areafix_password`** field — many real-world hubs use a different
password for AreaFix requests than for the session handshake itself.
Leave it blank to fall back to the network's `binkp_password`. Set
both on the network's edit page.

The sysop side of outbound AreaFix requests (subscribe/unsubscribe
buttons on **Admin → Echomail Networks → Manage Areas**) queues the
same kind of netmail automatically — see `send_areafix_request()` in
`areafix.py`. All AreaFix traffic (in and out) is logged at
**Admin → Echomail Networks → AreaFix Log** (`/admin/echomail/areafix_log`).

## Netmail — private FTN mail

Netmail is FTN's point-to-point private mail, as distinct from
**echomail** (public, broadcast to every peer subscribed to an area)
and from local PMs (BBS-internal only, never leaves this system).
Model: `NetmailMessage` in `anetbbs/models.py`; user-facing blueprint:
`anetbbs/web/netmail.py`.

Each message has a single `from_address`/`to_address` FTN pair (not an
area), FTN attribute flags (`is_private`, `is_crash`, `is_hold`, etc.),
and the standard kludge lines (`MSGID`/`REPLY`/`INTL`/`FMPT`/`TOPT`/
`CHRS`/...) preserved verbatim as a JSON array so they round-trip
correctly if the message needs forwarding.

Routes (all under `/netmail/`): inbox, sent items, read, compose, and
reply. A user's inbox matches netmail addressed to any of their
`UserAka` FTN addresses, or to their username directly for first-time
arrivals not yet linked to an account. AreaFix replies and hub-related
netmail (see above) flow through this same table.

Note: netmail currently only has a **web** UI — there's no terminal
menu action for it yet (unlike echomail, which has both `echo`/
`echo_post` terminal actions and a web view).

## Per-board ↔ per-area

Boards and echomail areas are intentionally separate concepts:

- **Boards** — local discussion, threaded, with reactions.
- **Echomail areas** — networked message bases, FidoNet-style with
  origin/path lines, propagated to peers.

You can post to either independently.

## Per-user QWK offline reader

Anyone can grab their unread echomail as a `.QWK` packet, read it
offline in MultiMail / BlueWave / OLX, then upload a `.REP` packet
with replies.

URL: `/qwk/`. Download builds a fresh packet on demand. Upload
extracts MESSAGES.DAT and posts each reply as outbound echomail in
the matching area.

## Polling

The `anetbbs-web` process spins up a background poller that:

1. Reads `EchomailNetwork` rows.
2. For BinkP: polls each uplink at interval, exchanges packets.
3. For QWK: fetches new `.QWK` URLs on schedule.

Poll status/history is visible at **Admin → Echomail Networks →
Poll Logs** (`/admin/echomail/logs`) — filterable by network, showing
start/completion time, sent/received counts, and any error.

### Self-referential poll skip (informational, not an error)

If a network's poller looks like it's configured to dial the BBS's own
install instead of a genuine remote peer — for BinkP, `our_address`
equals `hub_address`; for QWK, `qwk_host` equals this BBS's own public
host — the poller detects this and safely skips the dial-out instead of
failing. This is expected and harmless on the hub's own install: the
seeded "point at the hub" network row needs to exist and be configured
exactly that way for *other* sysops to reach the hub, it's just never
meaningful for the hub to dial itself. Areas stay visible either way —
this only stops the pointless connection attempt, nothing else.

As of v1.0b2.41, the check happens first in `_do_poll()`
(`anetbbs/echomail/poller.py`), before any `EchomailPollLog` row gets
created — a self-referential network produces **no poll log entry at
all**, just a single `logger.debug(...)` line (invisible at the
default INFO log level) for anyone specifically chasing this down.
Earlier versions logged the skip as a `status='skipped'` poll-log row
every time the loop re-checked the network (once a minute), which
flooded the Poll Logs page with dozens of identical entries within
about 20 minutes of uptime and drowned out real poll activity. See
`_self_referential_reason()` in `anetbbs/echomail/poller.py`.

### Don't poll too aggressively

`poll_interval_minutes` defaults to 60 and is hard-floored at 5 (set
below that and it's silently clamped back up) — but the floor existing
doesn't mean polling every 5 minutes is a good idea for a real remote
uplink. Some hubs rate-limit or temporarily block a peer that connects
too frequently, which then makes the *original* problem (a genuinely
failed poll) harder to recover from, not easier. A standard interval
(30–60 minutes) is normal for most FTN/QWK links; only go tighter if
the hub's own docs say it's fine. Separately: a poll that fails no
longer retries on the very next scheduler tick (fixed in v1.0b2.114) —
it correctly waits out the configured interval before trying again,
same as a successful poll would.

## Hub Management — running ANetBBS as a network hub

If this install is the designated hub for a network (`.env`:
`REGISTRY_MODE_ENABLED=true`), a separate admin panel becomes
available at **Admin → Echomail Networks → Hub Management**
(`/admin/echomail/hub/`, `anetbbs/web/hub_admin.py`). On any install
where `REGISTRY_MODE_ENABLED` isn't set, every route in this blueprint
404s — the button on the Echomail dashboard is always visible, but
clicking it does nothing on a non-hub install. This is deliberate:
earlier, every install exposed this same UI, so a sysop applying for a
node number from their own terminal would have the request land in
their own local queue instead of the real hub's.

This panel is the **hub operator's** admin surface, separate from the
regular per-network echomail admin every install has:

- **BinkP node management** (`/admin/echomail/hub/binkp/`) — add,
  edit, and delete downstream `BinkPNode` peers (FTN address, session
  password, sysop/system/location metadata used for nodelist
  generation).
- **QWK node management** (`/admin/echomail/hub/qwk/`) — add, edit,
  and delete downstream `QWKNode` peers (packet ID, download
  password). Each node's detail page
  (`/admin/echomail/hub/qwk/<node_id>`, `qwk_node_detail()` in
  `hub_admin.py`) manages which echo areas that node actually
  receives — subscription is per-node, not global, tracked via
  `QWKNodeLastSent` rows (each carries the `conf_number` used in the
  QWK packet format). Areas can be subscribed/unsubscribed one at a
  time, or in bulk with the **"Subscribe to All"** button
  (`POST /admin/echomail/hub/qwk/<node_id>/subscribe-all`,
  `qwk_subscribe_all()`). This isn't a single click — the sysop first
  checks one or more networks from a checkbox list; submitting with
  none checked flashes an error and subscribes nothing. Requiring an
  explicit pick avoids sweeping in every QWK network on the install at
  once, which is what this used to do — a real problem for a sysop
  running more than one QWK network who wanted to add just one node's
  home network. The area query is further scoped two ways: to active
  areas on QWK-transport networks only (never BinkP-only networks),
  and, when the node belongs to a hub identity
  (`node.hub_identity_id`), to networks under that same hub identity —
  a node has no business receiving areas from a different hub
  identity's network. Networks with no hub identity set (a
  pre-migration edge case) are treated as identity-agnostic and always
  included.
- **Hold queue** (`/admin/echomail/hub/holdqueue`) — outbound BinkP
  items queued per node, filterable by status (pending/sent/failed).
- **QWK node-request approval queue**
  (`/admin/echomail/hub/qwk/requests`) — sysops can apply for a QWK
  node number from their own BBS's terminal; those applications land
  here for the hub operator to approve or deny. QWK-terminal-wizard-
  specific — see the **Public join form** section below for the
  newer, broader review queue that covers both transports.
- **Generation & Distribution** panel, four tabs:
  - **Nodelist** — shows the weekly nodelist-generation schedule (a
    `ScheduledEvent` seeded automatically when `REGISTRY_MODE_ENABLED`
    is on) plus last-run status/output, and a **Generate Now** button
    that runs the same handler on demand. Generated nodelists publish
    straight into the `ANN.FILES.NODELIST` file area so peers pick
    them up like any other file-echo item.
  - **QWK Packets** — lists registered QWK nodes with a **Preview**
    button per node. Preview builds and downloads a real packet on
    demand for testing, but deliberately does **not** call
    `mark_qwk_sent()`, so it never consumes the node's actual unsent
    message queue — the node's real next download is unaffected.
  - **TIC / File Distribution** — a status dashboard (pending/failed
    counts) for the outbound TIC hatch queue — see [doc 7 — File
    areas](07-file-areas.md#hatched-files-tic) for how files get
    queued in the first place; no manual step needed, files uploaded
    to any `ANN.FILES.*`-style network-attached area queue themselves
    automatically.
  - **Join Form** — enable/configure the public application page
    described below.

### Public join form (v1.0b2.43)

A public web page at `/join/` (`anetbbs/web/network_join.py`, blueprint
`network_join_bp`) lets anyone — no login required — apply to join this
hub's echomail network, instead of the sysop having to walk them
through it by email or a terminal wizard. It's gated by two independent
checks: `REGISTRY_MODE_ENABLED` (same as the rest of Hub Management)
**and** the sysop explicitly enabling it on the Join Form tab; if
either is off, `/join/` 404s just like the rest of this blueprint.

**Applicant flow**: read the rules text and (optionally) download the
full infopack zip, check a box confirming the rules were read (the
form won't submit without it), then fill in an application — name,
location, BBS name, BBS software, OS, telnet address, website URL,
email are always asked for, followed by an optional BinkP section (FTN
address + crash-or-hold) and an optional QWK section (packet ID).
Leave either transport section blank if it doesn't apply, but at least
one must be filled in. There are no password fields anywhere on the
form — real session/download credentials are always hub-generated at
approval time, never applicant-supplied, the same security rule the
QWK node-request wizard already follows. The endpoint is fully
unauthenticated, so it's rate-limited per IP: a 30-second floor between
submissions and a 10/hour cap.

**Setting it up**, on the **Join Form** tab:

1. Enable the checkbox, optionally set a **Network Name** (shown on
   the public page) and **Intro Text** (a blurb shown above the
   rules).
2. Upload a single infopack zip — a real-world bundle of whatever the
   sysop wants applicants to see: rules/info text, a readme, a node
   list, ANSI art, machine-readable area lists, etc.
3. The system automatically picks the **largest `.txt` member** in the
   zip as "the rules text" and displays it inline on the public page
   (not just offered as a download). If the auto-pick guesses wrong, a
   dropdown lists every other `.txt` member found in the zip so the
   sysop can manually re-pick one, without re-uploading.
4. The whole zip is also offered as a public download,
   `GET /join/infopack.zip`, no login required — same pattern as the
   existing public file-download route.

**Review queue**: applications land on a new **Join Requests** page
(`/admin/echomail/hub/join/requests`) — separate from, and in addition
to, the QWK-only node-request queue above; that older queue is
specific to the terminal AreaFix/QWK wizard, this one is for the
public web form and covers both transports. Every admin gets an
in-app notification when a new application arrives (see
[doc 2 — Sysop daily ops](02-sysop-daily-ops.md) for the notification
system itself).

**Approving a request** creates a `BinkPNode` and/or `QWKNode` — zero,
one, or both, driven entirely by which section(s) the applicant filled
in, not by an admin choice. Each new node gets its own independently
hub-generated random password. If either transport's address/packet ID
collides with an existing node, the whole approval is rejected — the
BBS won't partially create one side and silently drop the other — so
the sysop can resolve the conflict and retry. If outbound SMTP is
configured (see [doc 2 — Sysop daily ops](02-sysop-daily-ops.md)), the
applicant is emailed their new credentials automatically; otherwise the
sysop is told to relay them manually. Denials can include a reason,
also emailed if SMTP is working.

This feature isn't hardcoded to ANotherNetwork — it's meant to be
usable by any sysop running their own ANetBBS hub for their own
network. ANotherNetwork is simply the first real network to use it.

If your install is the hub for *more than one* real network at once —
own zone:net, own QWK hub ID, separate downstream node pools — see the
**Hub Identities** admin page (`/admin/echomail/hub/identities/`),
which gives each network its own `/join/<slug>/` and node forms grow
an identity picker once a second one exists. Almost no install needs
this; single-network installs never see it. Full writeup: the
[[Sysop Guide]] wiki page, "Running more than one hub identity."
