# ANetBBS federation directory (`anetbbs.lst`)

A small federation registry that lets ANetBBS instances find each other.
Modelled on Synchronet's `sbbsimsg.lst` but JSON-shaped and built into
the BBS itself (no separate service to run).

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
   once mail is wired up next week.
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
      "software_version": "v1.0a2.24",
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
