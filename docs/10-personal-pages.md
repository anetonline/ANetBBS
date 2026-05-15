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
