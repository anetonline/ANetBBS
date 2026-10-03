# Web ANSI Editor — file browser, extra formats, TheDraw fonts, image tools

The web ANSI art editor (Admin → ANSI Art, `anetbbs/web/ansi_editor.py`)
is a browser-based CP437 grid editor: pencil, eraser, line, rectangle,
ellipse, fill, a shading brush, colorize, half-block pixel mode, mirror
mode, select/copy/paste/flip, swap colors, replace a color, center, and
insert/delete rows/columns — undo/redo throughout. Art is normally
saved to a database-backed library, but a few things below reach real
files (or URLs) instead: a file browser, a TheDraw font picker, image
import, and a reference-image trace mode. All are additive to the
library, not a replacement for it.

## File browser (Open from disk / Save to disk)

`ANSI_EDITOR_BROWSE_DIRS` (env var) is a `label:path;label:path...`
list of directories the editor's "Open from disk" / "Save to disk"
buttons can read and write real `.ans` files in. Defaults to:

- `text` → `{DATA_DIR}/text`
- `mods/txt` → `{DATA_DIR}/mods/txt` (the sysop override tree, see
  [35 — mods directory](35-mods-directory.md))

Point it somewhere else, or add a third location, by setting the env
var yourself — each directory is created automatically the first time
it's needed, same as `DOWNLOADS_DIR`/`FILE_BULLETINS_DIR` elsewhere in
this codebase.

## Import/export formats

Beyond the database library's own `.ans` (with a real SAUCE trailer —
title, author, `IBM VGA` font name) and PNG export:

| Format | Route | Notes |
|---|---|---|
| `.bin` | download / **Import .BIN/.XBin** | raw (char, attribute) pairs, no header — width isn't stored in the file, so import asks for it (80 is the near-universal default) |
| `.xb` (XBin) | download / **Import .BIN/.XBin** | real width/height/compression come from the file's own header; import handles both compressed (RLE) and uncompressed files, export always writes uncompressed |
| `.pcb` | download only | PCBoard `@X` color codes |
| `.ren` | download only | Renegade/Mystic/SBBSecho-style `\|NN` pipe color codes |
| `.syn` | download only | Synchronet's native `\x01`-prefixed Ctrl-A color codes |

The three color-code text formats are export-only for now — reimporting
one would mean parsing embedded color codes out of otherwise-plain
text, a separate, lower-priority piece of work; `.ans`/`.bin`/`.xb`
import already covers the common case of bringing outside art in.

## TheDraw (.TDF) font picker

`TDF_FONTS_DIR` (env var, defaults to `{DATA_DIR}/tdf-fonts`, empty on
a fresh install) is where the editor looks for `.tdf` font files.

**One-click install**: the font picker panel shows a **Download &
Install Fonts** button whenever it comes back empty. Clicking it hits
`POST /admin/ansi/fonts/install`, which downloads a zip from
`TDF_FONTS_PACK_URL` (env var, empty by default) and extracts every
`.TDF` file it finds — flattened into `TDF_FONTS_DIR` directly,
regardless of whatever folder structure the zip itself used (so a zip
shaped like ANetDRAW's own real pack — a flat top level plus a `SETS/`
subfolder of mega-pack files — installs correctly in one click; the
font picker only ever scans `TDF_FONTS_DIR` itself, not subfolders).
Confirmed live against the real ~1,241-file ANetDRAW pack: installs
cleanly, and the picker then finds **5,098 real font entries** — well
past the "almost 4k" figure once the `SETS/` mega-pack files are
included alongside the flat ones.

The font pack itself is **not** bundled in ANetBBS's own repo or
release tarball — same reasoning as `tools/download_jsdos.sh` fetching
the ~5MB js-dos runtime from a CDN at use time rather than committing
it to git: a ~29MB binary font pack doesn't belong in a source
checkout every sysop pulls down regardless of whether they use this
feature. Set `TDF_FONTS_PACK_URL` to a direct-download URL (a GitHub
Release asset works well, same as js-dos's own CDN link — a plain
HTTPS GET, no auth needed for a public asset) before the button will
do anything; it says so plainly if the setting is still empty.

`TDF_FONTS_DIR` can still be pointed at a font pack dropped in by hand,
or an existing Synchronet/ANetDRAW install's own font directory,
exactly as before — the download button is a convenience for the
common case, not the only way in. The same install action is also
listed on Admin -> Add-ons alongside other optional installable
pieces — see [38 — Add-ons](38-addons.md).

Using the picker: type in the search box to filter by name, click a
font, type banner text — a live preview renders below (server-rendered
per keystroke, debounced), then **Insert at cursor** pastes the result
into the grid at the current cursor position. Transparent cells (parts
of the font's own bounding box no glyph reaches) leave whatever was
already in the grid untouched, matching TheDraw's own behavior.

All three real TDF font types are supported: outline (with TheDraw's
full 19 outline styles), block, and color (each glyph cell carries its
own color, independent of the editor's current foreground/background
picker).

## Image → ANSI import

The **Import Image** button (next to Clear, above the canvas) converts
an uploaded `.jpg`/`.png`/`.gif`/etc into CP437 art, filling the
*entire current canvas* — a confirmation dialog warns it's destructive
before anything happens (Undo restores the previous art afterward,
same as Clear).

Entirely client-side: the image is stretch-fit to exactly the grid's
own width and height × 2 (two source pixel rows per cell row), each
cell becoming a half-block (`▀`, independent top/bottom color) or a
solid block (`█`) when both halves are close enough to match. Colors
are nearest-matched against this editor's own real 16-entry palette —
foreground can be any of the 16, background is restricted to the first
8 to match the BACKGROUND swatch panel's own range (every export
format masks background to 3 bits, so producing anything outside 0-7
there would just be silently altered on save anyway).

Stretching to the grid's exact dimensions, rather than preserving the
source image's own aspect ratio, is the correct choice here, not a
shortcut: this editor's own cell geometry already makes each resulting
"pixel" roughly square once halved vertically, the same compensation a
classic image-to-ANSI converter's own width/height formula exists to
provide — so a stretch-fit import doesn't look stretched.

## Reference image (trace mode)

The **REFERENCE IMAGE** panel (sidebar, below the TheDraw font picker)
loads a photo or sketch as a visual underlay to trace over by hand —
unlike Import Image above, this never touches a single cell. Load an
image, then:

- **Show image** toggles the underlay on/off without discarding it.
- **Dim canvas while tracing** lowers the *entire grid's* opacity (not
  just blank cells) so the image shows through art you've already
  drawn, not only empty space — the same "lower this layer's opacity"
  idea any paint program's own reference-layer feature already uses.
- The **opacity slider** controls how strong the underlying image
  itself looks, independent of the dim-canvas toggle.
- **Remove image** clears it.

The image is positioned to exactly match the grid's own on-screen
size and tracks zoom automatically (no extra setup needed after
zooming in/out). It is purely an editing aid: never serialized into
the saved grid, never part of any export, and safe to leave loaded —
reloading the page or navigating away simply drops it.
