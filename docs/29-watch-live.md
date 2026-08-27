# Watch It Live — public activity page

A public, no-login web page at `/watch` showing real-time BBS
activity — who's online right now and roughly what they're doing,
across every protocol (telnet/SSH/rlogin/web) — styled as a retro
terminal display. Meant to be shared or embedded off-site (a social
media post, your own landing page) as a live demonstration of what
ANetBBS actually looks like in use, rather than a static screenshot.

## Enabling it

Off by default. Turn it on in **Admin → System → Settings** (or set
`PUBLIC_WATCH_ENABLED=true` in `.env`) — it's a real, deliberate
broadening of exposure, not just a UI toggle: `/who` already shows
this same presence data, but only to your own logged-in users;
`/watch` shows it to anyone on the internet. That's the reason it
defaults off rather than on.

## What it shows — and deliberately does not

- Username, connection protocol, and a coarse "what they're doing"
  label (e.g. "MRC Chat", "Games", "Echomail") — built from the same
  privacy-conscious labeling `/who` already uses, so a web session's
  raw URL path is never exposed, only a general area name.
- Live counts by protocol, refreshed every ~5 seconds.
- It does **not** show message content, room/board names, or a
  mirrored terminal screen. That last one is a real gap in the
  underlying data model right now — see "What it doesn't do yet"
  below — not a design choice made for its own sake.

## Letting a user opt out

Any user can hide themselves from this specific page — without
affecting their normal Who's Online visibility to other logged-in
users — via **Profile → Edit → Privacy → "Hide me from the public
Watch It Live page"**.

A locked (`is_locked`) account is always excluded automatically.

## Embedding

No extra configuration needed — ANetBBS doesn't set `X-Frame-Options`
or a Content-Security-Policy anywhere today, so `/watch` embeds in an
`<iframe>` on another site with no special handling. If you later add
site-wide clickjacking protection, this route is the one place that
needs an explicit carve-out to keep working as an embed.

## What it doesn't do yet

The original idea for this page was to mirror an actual live terminal
screen, not just a "what area are they in" label. That turned out to
need real new work that's deliberately **not** part of this release:
the underlying `NodeActivity.last_screen` column exists but nothing
in the codebase populates it yet, and there's no privacy allowlist
built for which screens would ever be safe to show a stranger (never
a PM/netmail compose screen, never a password prompt, never an admin
menu). Building that safely is its own project for a future release,
not bundled into this page.

## Related files

- `anetbbs/web/watch.py` — the blueprint.
- `anetbbs/core/presence_labels.py` — the shared, privacy-conscious
  "what is this session doing" labeling logic, also used by `/who`
  (`anetbbs/web/who.py`).
- `anetbbs/templates/watch/index.html` — the public page itself
  (standalone, does not extend the normal site chrome).
