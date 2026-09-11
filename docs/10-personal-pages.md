# Personal web pages

Each user can publish their own static web content under your BBS
domain. Sysop can also publish folders directly at the top level.

## Enabling

```
PERSONAL_PAGES_ENABLED=true
```

in `.env`, or toggle from **Admin → Settings**, then restart
`anetbbs-web`.

## Sysop pages

Drop a folder under `data/personal_pages/`:

```
mkdir -p data/personal_pages/photography
echo '<h1>Photos</h1>' > data/personal_pages/photography/index.html
```

Visit `https://yourbbs.example.com/photography/` and you'll see it.

## Per-user pages

Users go to **Tools → My Web Pages** (`/_pages/`) — built-in file
manager for upload / list / delete. Their files are served at
`https://yourbbs.example.com/~username/`.

## Index file resolution

When the URL ends with `/`, the server tries (in order):

```
index.html  index.htm  default.html  default.htm  home.html  home.htm
```

First one found wins. If none exist, you get a 404.

## Reserved names

Real BBS routes (admin, auth, profile, boards, pm, files, mrc, irc,
shoutbox, etc.) can never be shadowed by a personal-pages folder.
The blueprint catches via the 404 handler so it only kicks in for
URLs that didn't match anything else.

## Path traversal

The serve code rejects any path that resolves outside its intended
base directory. Safe to expose to the internet.

## Same-origin content — a deliberate tradeoff, not a bug

This feature is deliberately GeoCities-style: any user can publish
arbitrary HTML/CSS/JS, and it's served same-origin under your BBS
domain (`/~username/`), not from a separate sandboxed subdomain. That
means a malicious user's page can run JavaScript in a visitor's
browser under the same origin as the rest of the BBS. This is a known,
accepted tradeoff of offering user-published static pages this way —
not something a future patch is expected to "fix" — so treat
`PERSONAL_PAGES_ENABLED` the same way you'd treat any other
user-generated-content feature with no sysop review step: fine for a
community that trusts its own userbase, worth thinking twice about on
a fully open-registration public install.

## Gemini capsules (related, separate feature)

**Tools → Gemini Capsules** (`/gemini/`) is a related but separate
per-user publishing feature: a gemtext (Gemini protocol) capsule
editor and browser-friendly viewer, served over plain HTTP at
`/gemini/<username>` rather than a real TLS Gemini listener. Gemtext
`=> target label` link lines are checked against a scheme allowlist
(`http`, `https`, `gemini`, `gopher`, `mailto`, or a bare relative
path) before being rendered as a clickable link — a link with any
other scheme (e.g. `javascript:`) has its `=> ` marker stripped so it
renders as inert plain text instead, closing a stored-XSS gap found in
a security audit where an unsafe scheme rendered as a real clickable
link with no check at all.
