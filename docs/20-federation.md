# ANetBBS federation directory (`anetbbs.lst`)

A small federation registry that lets ANetBBS instances find each other.
Modelled on Synchronet's `sbbsimsg.lst` but JSON-shaped and built into
the BBS itself (no separate service to run).

This page covers two related but distinct things:

1. The `anetbbs.lst` peer-directory/registry described below — a
   lightweight "who else is running ANetBBS" list.
2. **ANotherNetwork**, ANetBBS's bundled echomail/QWK network — see
   [ANotherNetwork](#anothernetwork) further down. Both roles are
   gated by the same `REGISTRY_MODE_ENABLED` flag; see
   [REGISTRY_MODE_ENABLED gates two roles](#registry_mode_enabled-gates-two-roles)
   before you flip it on.

## How it works

One BBS is designated the **central hub**. Other BBSes (the **peers**)
register against it on first start, heartbeat daily, and pull the
peer list down into their local inter-BBS directory.

```
                    ┌──────────────────────────┐
                    │   central hub            │
                    │   REGISTRY_MODE_ENABLED  │
                    │                          │
                    │  /registry/api/v1/*      │
                    │  /anetbbs.lst (JSON)     │
                    └────────────▲─────────────┘
                                 │
              ┌──────────────────┼──────────────────┐
              │                  │                  │
         ┌────┴─────┐        ┌───┴──────┐      ┌────┴─────┐
         │ ANetBBS  │        │ ANetBBS  │      │ ANetBBS  │
         │ peer 1   │        │ peer 2   │      │ peer 3   │
         └──────────┘        └──────────┘      └──────────┘
```

The current canonical hub is **`https://bbs.a-net.fyi`** — set as the
default `REGISTRY_URL` in `config.py`. Every fresh ANetBBS install
will pull `anetbbs.lst` from there on startup.

## For peer sysops — getting your BBS listed

Edit `/path/to/anetbbs/.env`:

```ini
# Required
BBS_DOMAIN=bbs.example.org
SYSOP_EMAIL=sysop@example.org
REGISTRY_SELF_REGISTER=true

# Friendly metadata (shown in the public list)
BBS_NAME=Example BBS
SYSOP_NAME=Alice
BBS_LOCATION=Seattle, WA

# Default already points at bbs.a-net.fyi; override only if running
# against a private federation hub
# REGISTRY_URL=https://bbs.a-net.fyi
```

Restart the web service:

```bash
sudo systemctl restart anetbbs-web.service
```

On the next service start, ANetBBS POSTs your metadata to the hub.
Two acceptance gates before you appear on the public list:

1. **Email verification.** The hub returns a `verify_url`. Open
   `https://yourbbs/admin/registry/self` to see it — click through.
   Future plan: the hub will email this URL to `SYSOP_EMAIL` directly
   once outbound mail support is added.
2. **Sysop approval.** After you verify, the hub sysop reviews the
   entry at `https://bbs.a-net.fyi/admin/registry/` and approves it.

Both gates passed → you appear in `https://bbs.a-net.fyi/anetbbs.lst`,
every other peer pulls you on their next daily refresh, and your BBS
shows up in their `/imsg/directory/` listing with a blue **ANetBBS**
badge.

## For hub sysops — running a federation registry

If you want to run your own federation hub (private network, parallel
registry, etc):

```ini
# In .env
REGISTRY_MODE_ENABLED=true
```

Restart the web service. New endpoints become live:

- `POST /registry/api/v1/register` — peers announce themselves
- `POST /registry/api/v1/heartbeat` — daily keep-alive
- `GET  /registry/verify/<token>` — peer sysop clicks to confirm
- `GET  /anetbbs.lst` — public JSON list of approved peers
- `GET  /registry/api/v1/list` — same content, machine-readable URL

A SYSTAT prober runs hourly against every listed peer (UDP/11). After
3 consecutive failures (`REGISTRY_PROBE_FAILURE_THRESHOLD`), the peer
is dropped from the public list — it re-lists automatically on
recovery. Threshold + interval are configurable in `.env`.

Approve / reject / edit / delete via the admin UI at
`/admin/registry/`. Entries flow through these states:

- `awaiting verify` — peer registered, hasn't clicked the email
  token yet
- `awaiting approval` — verified by peer, waiting for you to OK them
- `listed` — public on `anetbbs.lst`
- `delisted` — was listed, you rejected (row kept, can re-list)

## JSON format

```json
{
  "version": 1,
  "updated": "2026-05-15T14:00:00Z",
  "maintainer": "StingRay",
  "bbses": [
    {
      "host": "bbs.a-net.fyi",
      "msp_port": 18,
      "systat_port": 11,
      "name": "A-Net BBS",
      "sysop": "StingRay",
      "location": "Woodstock, GA",
      "software": "ANetBBS",
      "software_version": "v1.0a2.216",
      "notes": "",
      "since": "2026-05-15",
      "last_seen": "2026-05-15T14:00:00Z"
    }
  ]
}
```

The `bbses` array is empty until the hub has at least one verified +
approved + active peer. Stale entries (no heartbeat for
`REGISTRY_HEARTBEAT_STALE_HOURS`, default 48) are filtered out
automatically.

## Rate limits

The hub applies coarse rate limits to keep spam attempts cheap:

- **register**: 5s per host floor, 60 calls/hour per source IP
- **heartbeat**: 10s per host floor, 100 calls/hour per source IP

Hitting the floor returns HTTP 429. Daily heartbeat from a sane peer
never approaches the limit.

## Configuration reference

All env vars are read at service start. Defaults are sensible for the
"peer pulling from bbs.a-net.fyi" case — most sysops only need to set
`REGISTRY_SELF_REGISTER=true` + the BBS/sysop metadata.

| Variable | Default | Notes |
|---|---|---|
| `REGISTRY_URL` | `https://bbs.a-net.fyi` | where to register + pull |
| `REGISTRY_SELF_REGISTER` | `false` | opt in to publishing your BBS |
| `REGISTRY_MODE_ENABLED` | `false` | THIS install is a hub |
| `BBS_DOMAIN` | `(empty)` | your public hostname (required for self-reg) |
| `SYSOP_EMAIL` | `(empty)` | required for self-reg |
| `SYSOP_NAME` | `(empty)` | friendly display |
| `BBS_LOCATION` | `(empty)` | friendly display |
| `REGISTRY_HEARTBEAT_INTERVAL_SEC` | `86400` | client heartbeat cadence (1 day) |
| `REGISTRY_HEARTBEAT_STALE_HOURS` | `48` | hub: drop entries quiet this long |
| `REGISTRY_PROBE_INTERVAL_SEC` | `3600` | hub: SYSTAT probe cadence (1 hour) |
| `REGISTRY_PROBE_FAILURE_THRESHOLD` | `3` | hub: probe-fails before delist |
| `ANETBBS_DIRECTORY_REFRESH_SEC` | `86400` | peer pull cadence (1 day) |

## ANotherNetwork

Separate from the `anetbbs.lst` peer directory above, **ANotherNetwork**
is a real, pre-configured echomail/QWK network that ships with every
ANetBBS install — the same way Dove-Net ships with a fresh Synchronet
install. It's seeded automatically (`anetbbs/web_app.py`, the
"Seed ANotherNetwork" block) and needs no setup to exist, only to be
activated:

- **26 message echo areas** (tags `ANN.GENERAL`, `ANN.BBS`,
  `ANN.ANETBBS`, `ANN.DOORS`, `ANN.SYSOP`, etc.) spanning General,
  Technology, BBS Scene, Retro, Hobby, Trading, Data, SysOp, and Test
  categories.
- **9 file-echo areas** (tags `ANN.FILES.NODELIST`, `ANN.FILES.BBSSOFT`,
  `ANN.FILES.DOORS`, `ANN.FILES.EBOOKS`, `ANN.FILES.LINUX`,
  `ANN.FILES.RETRO`, `ANN.FILES.ANSIART`, `ANN.FILES.INFOPACK`,
  `ANN.FILES.TEST`) distributed via TIC, same as any other file echo.
- Two `EchomailNetwork` rows are seeded — one BinkP, one QWK/FTP —
  both **inactive and unsubscribed by default**. All 26 message areas
  and all 9 file areas are shared between the two transports via the
  hub tosser, so a sysop can join over whichever transport suits them.

To actually join, apply for a node at `bbs.a-net.fyi` and fill in the
node address/packet-id + password on the seeded network row(s) at
`/admin/echomail/`, then activate + subscribe the areas you want.

### `ftn_domain` — fixing the BinkP address suffix

`EchomailNetwork.ftn_domain` (`anetbbs/models.py`) is an optional,
≤8-character override for the BinkP qualified-address domain suffix
(the part after `@` in `1200:1/1@domain`). Per FSP-1028 that suffix
must be ≤8 chars and `[a-z0-9_~-]+`; without an override the poller
derives it from the network's `name` field instead, which is why
"ANotherNetwork" on its own would otherwise truncate to the awkward
`anothern`. The seeded ANotherNetwork BinkP row sets `ftn_domain=anet`
to sidestep this. It's editable per-network at `/admin/echomail/` —
the "Domain suffix override (addr@domain, max 8 chars, optional)"
field on the network edit form (`anetbbs/web/echomail_admin.py`).
Leave it blank on other networks to keep the old name-derived behavior.

## Hub Management panel

The install designated as the ANotherNetwork hub gets an extra admin
section at **`/admin/echomail/hub/`** (`anetbbs/web/hub_admin.py`) —
404s entirely on any install that isn't `REGISTRY_MODE_ENABLED`, so
peer installs never see it. It covers:

- **BinkP node management** — CRUD for downstream `BinkPNode` rows
  (FTN address, session password, sysop/system/location metadata for
  the nodelist, per-node subscribe/catch-up/flush-queue actions).
- **QWK node management** — CRUD for downstream `QWKNode` rows (packet
  ID, download password, subscribe/reset-high-water-mark, and a
  **Preview** action that builds a QWK packet on demand without
  marking anything as sent — useful for testing a node's setup without
  affecting their real next download).
- **Hold queue** — messages held back from distribution pending review.
- **Node-request approval** — the queue of sysops applying for a QWK
  node via the terminal wizard (`anetbbs/features/bbs_ui.py`'s
  `_apply_qwk_node()`, submitting `QWKNodeRequest` rows); approve/deny
  at `/admin/echomail/hub/qwk/requests`. There has never been a BinkP
  equivalent of this wizard — BinkP nodes are only ever created by hand
  at `/admin/echomail/` or via the public join form below. (This queue
  used to be visible on every install, which meant a sysop applying
  for a node from *any* ANetBBS install had their request land in that
  install's own local queue instead of the real hub's — fixed by
  gating this whole blueprint to the hub install only.)
- **Public join form** — a separate, newer way in: a public page at
  `/join/` (`anetbbs/web/network_join.py`), reachable with no login at
  all, where anyone can apply to join this hub's network. One generic
  form covers both transports — a BinkP address/crash-or-hold section
  and a QWK packet-ID section, either of which can be left blank as
  long as at least one is filled in. There are no password fields
  anywhere on the form; like the QWK terminal wizard above, real
  credentials are always hub-generated at approval time, never
  applicant-supplied. Above the form, the applicant reads the rules
  text pulled from the sysop's uploaded infopack (see the **Join
  Form** tab below) and must check a box confirming they read it
  before submitting. The route 404s until both `REGISTRY_MODE_ENABLED`
  and the Join Form tab's own "enabled" checkbox are on — two
  independent switches, not one. Applications land in their own review
  queue at `/admin/echomail/hub/join/requests` (separate from the
  QWK-only queue above) and every admin gets notified. Approving a
  request creates a `BinkPNode` and/or a `QWKNode` depending on which
  transport section(s) the applicant filled in — zero, one, or both —
  each with its own independently hub-generated password; if either
  address is already taken the whole approval is rejected rather than
  partially creating one node. If SMTP relay is configured on this
  install, the new credentials are emailed to the applicant
  automatically. This isn't ANotherNetwork-specific — any sysop
  running their own ANetBBS hub can turn it on for their own network.
- **Generation & Distribution panel**, with four tabs:
  - **Nodelist** — shows the `hub_generate_nodelist` scheduled event's
    schedule and last-run status, plus a **Generate Now** button that
    fires the same handler synchronously for testing. See
    [`21-scheduled-events.md`](21-scheduled-events.md).
  - **QWK Packets** — per-node **Preview** links (see above).
  - **TIC / File Distribution** — pending/failed counts from the
    `HatchQueue` table; files uploaded to any `ANN.FILES.*` area are
    queued automatically, no manual step needed, delivery happens on
    each peer's next BinkP poll.
  - **Join Form** — enable/configure the public `/join/` page above.
    A checkbox turns it on, with optional "Network Name" and short
    "Intro Text" fields displayed above the rules on the public page.
    Upload one infopack zip (rules text, area lists, ANSI art, whatever
    applicants should see or download) and the system automatically
    picks the largest `.txt` member inside it as the rules text shown
    inline on the public page — a dropdown lists every `.txt` member
    found in the zip so the sysop can override the pick if it grabbed
    the wrong file, without re-uploading. The full zip is also served
    as a public, no-login download link.

### Running more than one network from this install

Everything above assumes one hub identity — ANotherNetwork's own
zone:net, QWK hub ID, and node pool. If you want this same install to
also be the designated hub for a *second*, unrelated network (rather
than standing up a second physical install just to keep the two
networks' addressing and node pools from colliding), that's the
**Hub Identities** feature — CRUD at
`/admin/echomail/hub/identities/`, giving the second network its own
`/join/<slug>/` and `/admin/echomail/hub/nodelist/<slug>`. Almost no
install ever needs this — see the [[Sysop Guide]] wiki page's
"Running more than one hub identity" section for the full writeup.

## REGISTRY_MODE_ENABLED gates two roles

**One flag, two separate hub roles activate together.** Setting
`REGISTRY_MODE_ENABLED=true` in `.env` doesn't just turn this install
into the `anetbbs.lst` federation/peer-directory hub described earlier
on this page — it *also* turns it into the ANotherNetwork echomail hub:

- **Federation/directory role** (covered above): `/registry/api/v1/*`
  endpoints, `/anetbbs.lst`, the SYSTAT prober, `anetbbs/web/registry.py`,
  `anetbbs/msp/probe.py`, `anetbbs/msp/hub_self_register.py`,
  `anetbbs/web/peer_health.py`.
- **ANotherNetwork echomail hub role**: the `/admin/echomail/hub/`
  blueprint (`hub_admin.py` above), QWK node serving
  (`anetbbs/web/qwk_hub.py`), the hub's own self-registration
  (`hub_self_register.py`), and the weekly `hub_generate_nodelist`
  scheduled event that only gets seeded on these installs (see
  [`21-scheduled-events.md`](21-scheduled-events.md)).

Both checks read the exact same `REGISTRY_MODE_ENABLED` config value —
there's no way to enable one role without the other today. A sysop
standing up a private federation registry only (no interest in running
an ANotherNetwork-style echomail hub) should know that flipping this
flag on also exposes the Hub Management panel and starts seeding the
nodelist-generation event. Neither does anything harmful on its own —
the echomail hub role is inert until you actually add downstream
BinkP/QWK nodes — but it's worth understanding both switch on together,
not just the directory role this page is mostly about.

## Dialing yourself is handled safely

If your install is the ANotherNetwork hub, the same seeded "point at
the hub" network row also exists on your own install (since every
fresh install seeds it) — activating that row on the hub's own BBS
would otherwise mean the poller tries to dial itself. It doesn't fail
or spam errors: `anetbbs/echomail/poller.py`'s `_self_referential_reason()`
detects this automatically (BinkP: `our_address` matches `hub_address`;
QWK: `qwk_host` matches this BBS's own public host) and the poller
skips the dial-out cleanly.

As of v1.0b2.41 this check runs *before* `_do_poll()` creates an
`EchomailPollLog` row, so a detected self-reference leaves no poll-log
entry at all — not even a "skipped" status row — only a
`logger.debug(...)` application-log line, invisible at the default
INFO log level. It used to create a real `EchomailPollLog` row every
time, but since the poller loop re-checks every network once a
minute, that flooded the admin UI's poll log with dozens of identical
entries within about 20 minutes of uptime on a freshly-started hub
(live-caught on Jerry's own install), drowning out real poll activity.
The areas themselves stay visible either way — this only stops the
pointless outbound connection attempt and its logging noise, nothing
else is affected.

## Limitations + future work

- **No federation between multiple registries yet.** v1.0a3 ships one
  hub model. Mirror-sync between registries is post-alpha (v1.0b).
- **No public-key signing of heartbeats.** Today an entry is owned by
  whoever holds the verify token + has been approved. Cryptographic
  ownership is future work.
- **`anetbbs.lst` exposed at root URL doesn't include `contact_email`**
  — that's intentional, only the hub sysop sees it via the admin UI.

For the day-of-development arc that led to this design, see the
`v287.13` → `v287.20` entries in [`CHANGELOG.md`](CHANGELOG.md).
