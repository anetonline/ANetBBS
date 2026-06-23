# ANetBBS v1.0a2.163 — RSS short-URL redirect + title word-wrap

## Changes

### `web/rss.py` — new `/r/<item_id>` redirect endpoint

Added `redirect_bp` (Blueprint, prefix `/r`) with a single unauthenticated
route `GET /r/<item_id>` that looks up the `RssItem` by ID and issues a
`302` redirect to its original `link` URL.  No new database model needed —
the existing `RssItem.id` primary key is the short code.

### `features/bbs_ui.py` — short URL + title word-wrap in RSS item view

- `_rss_view_item` now builds `{web_base}/r/{item.id}` from `BBS_DOMAIN` /
  `WEB_PORT` config and displays that instead of the truncated raw link.
  The short URL always fits within 80 columns.
- Title is now word-wrapped at 74 columns (using `_wrap_text`) instead of
  hard-truncated — long article headlines flow onto a second line.

### `web_app.py` — register `redirect_bp`

Imported `redirect_bp` from `web.rss` and registered it alongside `rss_bp`.
