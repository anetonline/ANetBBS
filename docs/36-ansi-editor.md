# Web ANSI Editor — file browser, extra formats, TheDraw fonts

The web ANSI art editor (Admin → ANSI Art, `anetbbs/web/ansi_editor.py`)
is a browser-based CP437 grid editor: pencil, eraser, line, rectangle,
ellipse, fill, a shading brush, colorize, half-block pixel mode, mirror
mode, select/copy/paste/flip, swap colors, replace a color, center, and
insert/delete rows/columns — undo/redo throughout. Art is normally
saved to a database-backed library, but two things below reach real
files on disk instead: a file browser, and a TheDraw font picker.
Both are additive to the library, not a replacement for it.

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

`TDF_FONTS_DIR` (env var, defaults to `{DATA_DIR}/tdf-fonts`) is where
the editor looks for `.tdf` font files. **No font pack ships with
ANetBBS** — [ANetDRAW](14-door-games.md#anetdraw--bundled-plays-out-of-the-box)'s
own binary is bundled (see the door games doc), but its 3,716-font
pack, which this editor's TDF support was built to be compatible with,
is still a separate download even from ANetDRAW itself. Point
`TDF_FONTS_DIR` at that pack (or any other `.tdf` collection, or an
existing Synchronet/ANetDRAW install's own font directory) and the
picker in the editor's tools panel finds every font across every
`.tdf` file there automatically — no per-font registration needed.

Using it: type in the search box to filter by name, click a font,
type banner text — a live preview renders below (server-rendered per
keystroke, debounced), then **Insert at cursor** pastes the result
into the grid at the current cursor position. Transparent cells (parts
of the font's own bounding box no glyph reaches) leave whatever was
already in the grid untouched, matching TheDraw's own behavior.

All three real TDF font types are supported: outline (with TheDraw's
full 19 outline styles), block, and color (each glyph cell carries its
own color, independent of the editor's current foreground/background
picker).
