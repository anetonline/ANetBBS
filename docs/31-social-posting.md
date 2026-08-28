# Auto-social-posting queue

Turns notable BBS activity into a ready-to-post Bluesky/Mastodon
draft automatically — a new #1 leaderboard score, a round-number BBS
milestone (every 100th registered user, every 1000th board post) —
plus, for anything the automatic triggers don't catch (a version bump,
a new feature, an event), a sysop can compose a post directly.
**It never posts on its own.** Every draft, automatic or manual, sits
in a review queue until a sysop approves it.

## Enabling it

Off by default. Two separate steps:

1. **Turn on the feature**: **Admin → System → Settings** (or set
   `SOCIAL_POSTING_ENABLED=true` in `.env`).
2. **Configure at least one platform's credentials** in `.env` (see
   `.env.example` for the exact keys) — you can configure just one of
   the two if you only use one:

   - **Bluesky**: `BLUESKY_HANDLE` (e.g. `yourbbs.bsky.social`) and
     `BLUESKY_APP_PASSWORD` — generate the app password at
     bsky.app → Settings → App Passwords. **Not your real account
     password** — an app password can be revoked independently
     without changing your login credentials.
   - **Mastodon**: `MASTODON_INSTANCE_URL` (your instance's base URL)
     and `MASTODON_ACCESS_TOKEN` — generate at your instance →
     Settings → Development → New Application, with the
     `write:statuses` and `write:media` scopes.

Credentials are `.env`-only, never stored in the database — unlike
some other integrations in this codebase (e.g. `Webhook.secret`),
this is one sysop-wide credential pair per platform, not a per-row
setting, so keeping it out of the database means it never shows up in
a database export or backup.

## Reviewing and approving

**Admin → Content & Presentation → Social Posting Queue**
(`/admin/social/`). Each pending item shows the auto-rendered image,
an editable caption, and two buttons:

- **Approve & Post** — posts immediately, for real, to every platform
  with credentials configured. Shows the resulting post URL(s), or a
  clear per-platform error if something failed (a platform failing
  never blocks the other one from posting).
- **Skip** — discards it, nothing is ever posted.

**+ New Post**, top right of the queue page, opens a compose form:
caption text (required) and an optional image (PNG/JPG, up to 5MB —
leave it blank for a text-only post). Use this for anything the
automatic triggers below don't catch: "ANetBBS v1.0.55 is live —
fixes X, adds Y", an upcoming event, a door game you just added. It
queues exactly like an automatic draft — nothing posts until you
approve it from the same page.

## Getting notified

Queuing a draft — automatic or manual — sends every admin a normal
ANetBBS notification (the same bell-icon/notification-page mechanism
used for pending user approvals, bad echomail areas, and everything
else that needs sysop attention): a persistent entry you'll see the
next time you're on the web admin, plus a live toast if you already
have a browser tab open when it happens. There's no email or
terminal-side alert for this specifically — checking the queue page
directly always shows the current, complete state either way.

## What triggers a draft

| Trigger | Where it's detected |
|---|---|
| New #1 high score on any web arcade game | `submit_score()` in `anetbbs/web/games.py`, right after a real (non-guest) score is saved |
| Every 100th registered user | Right after a registration completes, `anetbbs/web/auth.py` |
| Every 1000th board post (new threads and replies both count) | `anetbbs/web/boards.py` |
| Manual (sysop-composed) | **+ New Post** on the queue page, any time |

The three automatic triggers live entirely inside ANetBBS's own
database — this release does not wire up achievements from the
separate door games (RDQ3, ANetCHESS, LORD2), since those keep their
own state in their own programs, not this database.

The same automatic event never queues twice — enforced by a unique
dedupe key on the `SocialPost` row, not just by each trigger's own "is
this really new" check. A manual post has no natural event to dedupe
against, so each one you compose queues as its own separate draft —
nothing stops you from writing two if you want to.

## Related files

- `anetbbs/features/social_queue.py` — trigger detection, dedupe, and
  the admin notification (the only code that ever creates a
  `SocialPost` row).
- `anetbbs/features/social_card.py` — the highlight-card PNG renderer.
- `anetbbs/features/social_bluesky.py`, `social_mastodon.py` — the two
  platform API clients (the only code that ever calls Bluesky or
  Mastodon).
- `anetbbs/web/social_admin.py` — the review queue (the only code
  that ever calls the platform clients above).
