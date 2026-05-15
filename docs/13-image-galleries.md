# Image galleries

The BBS includes a browser-native image gallery system at `/gallery/`.
A sysop can publish any directory of images on the host as a labelled
collection; users browse a paginated thumbnail grid and click for a
full-screen modal viewer.

This replaces the older sixel-based DSR door for web users — `/gallery/`
displays images at full quality natively in the browser, with no
terminal-art middleman or DOSBox-style chain.

## How users see it

- Top-bar **Tools → Image Galleries** opens `/gallery/`.
- Each gallery card shows label, description, image count.
- Click a gallery → paginated thumbnail grid (60 per page).
- Click any thumbnail → full-screen modal at native resolution.
  Click anywhere or press Esc to close.

Login required (any authenticated user can browse).

## Where the data lives

Galleries are configured in `gallery-config.json` at the BBS install
root (`/path/to/anetbbs/gallery-config.json`). The file is auto-seeded
on first run with the legacy DSR collections (`gifs`, `swim`) if they
exist, but you can edit it freely or use the admin UI.

Schema:

```json
[
  {
    "slug": "gifs",
    "label": "90s GIFs Galore",
    "path": "/home/stingray/anetbbs/doors/sbbs/dsr/gifs",
    "description": "6,500+ classic 90s GIFs.",
    "is_active": true,
    "sort_order": 10
  }
]
```

- `slug` — URL-safe identifier (must be unique). Becomes part of the URL
  (`/gallery/<slug>/`). Cannot be changed after creation via admin UI.
- `label` — display name shown on cards and page titles.
- `path` — absolute directory containing the images. The directory is
  scanned at request time; no separate index is maintained.
- `description` — optional one-line caption shown on the gallery picker.
- `is_active` — `false` to hide the gallery without removing it.
- `sort_order` — lower numbers appear first; ties broken by label.

Files at the path are picked up if their extension matches:
`.jpg .jpeg .gif .png .bmp .webp` (case-insensitive).

The deploy rsync should `--exclude=gallery-config.json` so deploys
don't clobber a sysop's gallery list. The default deploy command in
the repo's release notes already does this.

## Admin: add a gallery

`Admin → Subsystems → Galleries` (`/admin/galleries/`).

1. Click **Add Gallery**.
2. Enter label (e.g. "Vacation 2024"), slug auto-derives or specify.
3. Enter the absolute path on the host (e.g. `/srv/photos/vacation-2024`).
   The directory will be created if it doesn't exist.
4. Optional description, sort order, active flag.
5. Save → gallery appears in the public listing.

## Admin: manage files

From the gallery list, click the **folder icon** next to any gallery
to open the file manager. There you can:

- See all files as a thumbnail grid (paginated)
- Click the **×** on any tile to delete that file (irreversible — files
  are removed from disk)
- Drag-and-drop or browse to upload new images. Multi-file uploads
  supported. Filenames are sanitized; collisions auto-rename to
  `name-1.ext`, `name-2.ext`, etc.

Upload is restricted to image extensions; everything else is silently
skipped.

## Permissions on disk

Whatever Linux user the gunicorn web service runs as needs read/write
access to the gallery path (read for browsing, write for upload/delete).
On the reference setup the web service runs as `anetbbs`, so:

```bash
sudo chown -R anetbbs:anetbbs /path/to/your/gallery
sudo chmod -R u+rwX /path/to/your/gallery
```

Galleries can point at directories owned by other users — but uploads
and deletes will fail with permission errors. Read-only galleries are
fine if you set `is_active: true` and only need browsing.

## Terminal access (legacy fallback)

For SSH/Telnet users a standalone bash script is included:
`/home/<user>/anet-gallery.sh`. It uses **chafa** for Unicode-block art
that renders in any terminal, with **img2sixel** as an alternative for
sixel-capable terminals (SyncTERM, foot, mlterm, modern xterm).

Quality is much lower than the web viewer (chafa converts pixels to
character cells). For real photographic quality, point users at the
web `/gallery/` URL.

The script lives outside the BBS install dir on purpose — keeps the
deploy `rsync --delete` from removing it.
