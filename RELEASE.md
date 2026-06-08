# ANetBBS v1.0a2.96 — Web file area: ANSI art renders correctly

## What's new

### Fix: FILE_ID.DIZ ANSI escape codes show as literal text in web UI

The expanded description block in the file area page was displaying raw
ANSI escape sequences as visible text (e.g. `□[0;40;37m□[7C□[1;46;36m…`).

Root cause: descriptions decoded from CP437 by `_scan_area` are already
proper Unicode strings. Putting them in a `<pre>` block HTML-escapes them
but does not interpret the `\x1b[…m` SGR sequences.

Fix: added two Jinja2 filters to `web_app.py`:
- `ansi_art` — runs the existing `_ansi_to_html` + `_pipe_to_ansi` pipeline
  from `render_msg.py` directly on the already-Unicode string (no latin-1
  round-trip, which would corrupt block-drawing codepoints above U+00FF).
  Used for the expanded `<pre>` description block.
- `strip_ansi` — strips escape sequences, returns plain escaped text.
  Used for the short one-line description shown inline in the table.

The `<pre>` block style is updated to black background, CGA gray default
foreground, and `Courier New` monospace so block art columns align.
