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

## Sandboxing and limits

Pages are served from your BBS's own domain (`/~username/`), but every
personal-page response is sent with a `Content-Security-Policy: sandbox`
header (without `allow-same-origin`). The browser treats each page as a
separate, unique origin. Scripts on the page can still run, but they
cannot read the visitor's BBS cookies or browser storage, and cannot read
responses from the rest of the BBS. This holds even when a logged-in sysop
is the one viewing the page.

Other rules:

- `.svg`, `.xml`, `.xhtml`, `.js` and `.mjs` files are always sent as
  downloads, never displayed inline.
- Uploads are limited to common page, image, sound, text and document
  types.
- Each user gets at most `PERSONAL_PAGES_MAX_FILES` files (default 100)
  and `PERSONAL_PAGES_MAX_BYTES` bytes in total (default 10 MB).
- With `PERSONAL_PAGES_ENABLED=false`, uploading, deleting and serving
  pages are all switched off.

If you want users' pages to keep full same-origin JavaScript, serve them
from a separate hostname instead. Prefer a different registrable domain,
not a subdomain of the BBS host, because a subdomain can set cookies for
its parent domain.

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
