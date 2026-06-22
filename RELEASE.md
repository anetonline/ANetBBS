# ANetBBS v1.0a2.160 — VT renderer for terminal; flat-art \n fix; borderless ANView

## Changes

### `features/ansi_html.py` — shared VT renderer + terminal line output

Refactored the virtual-terminal engine into a shared `_run_vt(text)` function
that builds the 2-D cell grid and returns `(cells, max_row)`.  Both the HTML
renderer and the new terminal renderer use this shared core:

- `_to_html_vt` now delegates to `_run_vt` (no behaviour change for web).
- `to_ansi_lines(text, width=80)` — new export.  Converts ANSI body text
  through the VT renderer and returns a list of terminal-ready 80-column
  strings with ANSI SGR colour codes.  Used by ANView for all message types.
- Added `_HEX_TO_FG` / `_HEX_TO_BG` reverse palette maps (hex→SGR code)
  needed to convert VT cell colours back to terminal escape sequences.

### `features/anedit.py` — borderless ANView; all messages scrollable via VT

**`_ViewerScreen`** (border removed):
- Frame reduced to a single dark header bar at row 1 (subject + "ANView" tag).
- Content area expanded: rows 2–23 (22 visible lines, up from 17).
- Status/hints bar moved to row 24 — shows `Ln:X/Y  R=Reply N=New Q=Back`.
- No box-drawing characters, no side borders.

**`ANView`** (viewport overrides):
- All scroll/page methods overridden to use the new 22-line viewport.

**`launch_aneview`** (now handles all art types):
- Does CP437 decode + pipe-code conversion internally.
- Detects cursor-pos or block-art sequences → strips `\n` before VT render.
- Calls `to_ansi_lines()` to convert the body through the VT renderer.
- Feeds pre-rendered 80-col ANSI lines to ANView for scrollable display.
- All messages (ANSI art, plain text, cursor-pos art) now appear in the
  scrollable ANView frame — no more raw full-screen display path.

### `features/bbs_ui.py` — simplified message display

Removed the cursor-pos raw-display path.  All messages now route through
`launch_aneview()` which handles art detection and VT rendering internally.

### `web/render_msg.py` — flat-art \n strip for web

`render_msg_body` and `render_msg_body_rich` now strip `\n` for flat block-art
(bodies with CP437 block chars but no cursor-pos sequences), in addition to
the existing strip for cursor-pos art.  This fixes the horizontal-bar scatter
pattern for flat ANSI art in the web message view.

### `echomail/qwk.py` — correct \xe3 handling for ANSI bodies

At QWK import time: if the raw body contains `\x1b` (ESC / ANSI art), the
`\xe3` record-boundary bytes are now stripped (removed) rather than converted
to `\n`.  Real line structure for ANSI art comes from the `\r\n` sequences
embedded by the originating BBS software.  For plain-text bodies (no ESC),
`\xe3` → `\n` conversion is unchanged.

This prevents new imports from storing spurious newlines in art messages.
Existing messages are handled by the display-time `\n` strip in
`launch_aneview` and `render_msg_body`.

## Files changed

`anetbbs/__init__.py`, `setup.py`, `VERSION`, `FILE_ID.DIZ`, `RELEASE.md`,
`docs/CHANGELOG.md`, `README.md`,
`anetbbs/features/ansi_html.py` (VT refactor, to_ansi_lines),
`anetbbs/features/anedit.py` (borderless ANView, launch_aneview VT),
`anetbbs/features/bbs_ui.py` (remove raw path),
`anetbbs/web/render_msg.py` (flat-art \n strip),
`anetbbs/echomail/qwk.py` (\xe3 handling for ANSI bodies)
