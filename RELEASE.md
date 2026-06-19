# ANetBBS v1.0a2.128 — bug fixes: bulletins, docs, chat.ans, read/write security

## Changes

### Bug fix: bulletins HTML line-feed conversion

Bulletins written in the telnet/SSH client use `\r\n` line endings. The web view
previously rendered them inside `<pre>`, which showed raw `\r\n` as blank lines on
some browsers. Swapped the default view to the markdown renderer (which has the
`nl2br` extension enabled), converting `\r\n` → `<br>`. The raw view is now a
toggleable secondary view.

### Bug fix: ANSI display codes not processed for chat.ans

`chat.py` was writing the raw bytes returned by `load_menu_ansi('chat')` directly
to the session. Synchronet @-codes and Mystic |XX color codes embedded in
`chat.ans` were sent as literal bytes instead of being rendered. Now runs
`display_codes.apply()` on the decoded string before writing, matching the pattern
used by `_show_ansi_screen()` and `menu_engine.py`.

### Bug fix: docs sidebar too compact

`templates/docs/view.html` sidebar list items had `py-1` padding,
`font-size: 0.85rem`, and `line-height: 1.3` — too tight for longer page names.
Increased to `py-2`, `0.875rem`, `line-height: 1.5`.

### Feature: separate read/write access levels for boards and file areas

`Board` and `FileArea` models now have a `min_write_level` column (nullable
integer; NULL = same as `min_access_level`). This lets sysops set one level to
read posts/files and a higher level to post/upload, matching QuickBBS/RA-style
security.

- Admin UI: board form shows side-by-side "Min Read Level" / "Min Write Level"
  fields; file area table adds a "Write Level" column.
- `web/boards.py` enforces write level on new posts; `bbs_ui.py` enforces it on
  file uploads.
- `_lightweight_migrate()` auto-adds the column on first startup — no manual
  schema change needed.

### New: ANetIRC v7 — pure-Python asyncio IRC client

Completely replaces the C binary door + PTY bridge with a pure-Python asyncio
implementation that runs directly inside the BBS session.

**What broke in the old client:**
- TLS/SSL connections (e.g., Libera.chat port 6697) locked up entirely. The C
  binary's blocking TLS handshake deadlocked against the PTY bridge.
- The PTY bridge added timing/encoding indirection that caused escape sequence
  truncation, F2 key misfires, and CP437 roundtrip issues.
- The binary only ran on x86-64; Pi sysops had to compile from source.

**What the new client does:**
- Runs as native asyncio code — `await asyncio.open_connection(ssl=ssl_ctx)`.
  TLS uses Python's `ssl.create_default_context()`. No blocking, no lockups.
- Works on every arch (x86, ARM, anything Python supports) — no binary at all.
- SASL PLAIN authentication via full CAP negotiation state machine.
- Word-wrap with configurable column width (adapts to terminal size).
- Scrollback (PgUp/PgDn, up to 800 rows visible / 2000 stored).
- Tab-completion for nicks from the users panel.
- Command history (Up/Down arrows).
- Startup bookmark manager with inline field editing — same pipe-delimited
  config format as v1 (backward-compatible: `label|server|port|nick|channel|tls|password`).
- Three color themes (Cyan/Green/Amber), cycle with T in startup screen.
- Graceful disconnect on ESC, shows "Disconnected" if server drops.
- mIRC color codes stripped from incoming messages.
- CTCP ACTION rendered as `* nick action`.

**Backward compatibility:**
- Entry point remains `launch_anetirc_telnet(user, session)` — `chat.py` only
  changes the import line (`.anetirc_door` → `.anetirc2`).
- Bookmark config file location and format unchanged.
- C binary (`doors/anetirc/anetirc`) retained in tarball for sysops who prefer
  the old client; to revert, change `chat.py` import back to `.anetirc_door`.
