# RSS Reader

ANetBBS ships a built-in RSS / Atom feed reader. Users browse it from
the web (`Tools → RSS Reader`) or the BBS terminal (`Main BBS Menu → R`).
Sysops manage feeds at `Admin → Subsystems → RSS Feeds`.

A default feed (X-News at `https://x-bit.org/rss/rss.xml`) is seeded
on first run so a fresh install ships with at least one feed populated.

## How users see it

### Web
- **Tools → RSS Reader** lists every active feed with per-user unread
  counts.
- **All Feeds (River)** is a combined newest-first stream across every
  feed.
- Click a feed → paginated item list. New items show in **bold** with
  a **NEW** badge.
- Click an item → full content view. Marks as read.
- "Mark all read" button per-feed and global.

### Terminal (telnet/SSH)
- Press `R` from the Main BBS Menu.
- Feed list shows item count and unread count per feed.
- `A` = combined river of all feeds.
- Numeric choice = open that feed.
- Inside a feed: numeric = read item, `N`/`P` paginate, `Q` returns.
- `*` marker = unread; reading clears it.

## How the poller works

Background daemon thread (`anetbbs.rss.poller`) wakes on
`RSS_POLL_INTERVAL` (default 1800s = 30 min) and refreshes every active
feed via Python's `feedparser`. Items are deduped per-feed by GUID
(falls back to link or title if the feed doesn't supply a GUID).
Failed fetches set `feed.last_error` so the sysop sees red badges in
the admin list.

To refresh manually, hit the circular-arrow icon next to a feed in
`/admin/rss/`.

## How sysops add a feed

`Admin → Subsystems → RSS Feeds → Add Feed`:

| Field          | Notes |
|----------------|-------|
| **Name**       | Display name shown to users (e.g., `X-News`) |
| **Category**   | Free text — `news`, `tech`, `scene`, `general`. Feeds are grouped by this on the web feed picker. |
| **Feed URL**   | RSS 2.0 / Atom / RSS 1.0 — feedparser handles all of them. |
| **Site URL**   | Optional human-facing homepage of the feed source. |
| **Description**| One-liner shown on the feed picker. |
| **Sort order** | Lower = appears first. |
| **Active**     | Disable to hide a feed without deleting its history. |

Saving a new feed triggers an immediate fetch so users see items right
away (no waiting for the next 30-min poll tick).

Deleting a feed cascades — all its items and per-user read state are
removed.

## Tunables

- `RSS_POLL_INTERVAL` env var — seconds between polls (default 1800,
  minimum 60). Set tighter on a fast-moving feed; relax on stable
  feeds to be a polite citizen.

## Storage

| Table | What it holds |
|-------|---------------|
| `rss_feeds` | Sysop-configured feeds (URL, name, category, last fetch state). |
| `rss_items` | Article/post records, deduped per-feed by GUID. |
| `rss_read_status` | Per-user read markers (presence = read, absence = unread). |

Auto-created on app startup via the lightweight migration sweep.
No manual SQL needed.

## Future / open questions

- **Web autorefresh** — the user has to reload the page to see new
  items between poll ticks. A SocketIO push of "new item arrived"
  would be a nice touch.
- **Per-user feed subscriptions** — right now every user sees every
  active feed. A future version could let users hide/show specific
  feeds.
