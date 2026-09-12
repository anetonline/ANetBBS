# Wiki

A collaborative, markdown-based wiki at `/wiki/` — reference material,
guides, and anything else the community wants a shared, editable page
for. 52 reference pages ship pre-seeded (connecting, every messaging
subsystem, door games, echomail/BinkP setup, NodeSpy, backups, full
architecture, sysop guide, glossary, FAQ).

Web-only today — there's no terminal wiki reader/editor.

## Reading and browsing

- `/wiki/` — the wiki home page.
- `/wiki/all` — every page, alphabetically.
- `/wiki/recent` — recently changed pages.
- `/wiki/search` — full-text search across page bodies.
- `/wiki/wanted` — pages that are linked to (via `[[Page Name]]`) but
  don't exist yet — a to-do list for what to write next.
- `/wiki/orphans` — pages nothing else links to, useful for finding
  content that's hard to discover by browsing.

Anyone, including a logged-out visitor, can read any page.

## Editing

Logged-in users can edit, gated by two configurable thresholds (both
must be met): a minimum number of posts (`WIKI_MIN_POSTS` in `.env`,
default 5) and a minimum account age in days (`WIKI_MIN_DAYS`, default
3). This keeps a brand-new throwaway account from immediately editing
pages, without requiring a separate wiki-specific permission to grant.
A page an admin has locked (see below) can't be edited regardless of
who's asking.

Page bodies are markdown, rendered through the same sanitization
pipeline (markdown → bleach) as boards and echomail, capped at 500,000
characters. `[[Page Name]]` creates a link to another wiki page —
linking to a page that doesn't exist yet is exactly how it shows up on
`/wiki/wanted`.

## Revision history

Every save creates a new revision — nothing is overwritten. Per page:

- `/wiki/<slug>/history` — every past revision, with author and edit
  summary.
- `/wiki/<slug>/rev/<n>` — view one specific past revision.
- `/wiki/<slug>/diff/<a>/<b>` — a unified diff between two revisions.
- `/wiki/<slug>/revert/<n>` (admin) — restore an old revision as the
  new current one (itself recorded as a new revision, so nothing from
  the history is lost).

## Admin actions

Admins can lock a page (blocks further edits regardless of the normal
edit gate), delete a page, or restore a deleted one. A locked page is
the right tool for reference material that shouldn't drift — the
glossary or the architecture overview, say — while still leaving the
rest of the wiki open for community contributions.

## Seeded pages don't clobber real edits

The 52 pages ANetBBS ships with are periodically re-synced from their
built-in source text on startup, but only while a page has never been
edited by a real person (`anetbbs/wiki/seed.py`'s sync logic checks
whether a page's latest revision has a real `author_id` before
touching it). Once anyone edits a seeded page, it's permanently theirs
— a future ANetBBS update to the stock wiki content will never
overwrite what a sysop or user actually wrote.
