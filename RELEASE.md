# ANetBBS v1.0a2.124 — eventlet Python 3.13 fix; ANetIRC F2/PgUp/PgDn fix

## Changes

### Fix: `/scroll` view drifting as new messages arrived

When the user scrolled up (`/scroll` or Up arrow), new messages kept arriving and
`_redraw_chat_area` recalculated the visible window as `all_lines[end-rows:end]`
where `end = total - offset`. As `total` grew with each new message, `end` grew
too — the view silently drifted forward even though the offset was fixed.

**Fix**: `_emit` now increments `_scroll_offset` by the number of display lines
the new message produces (accounting for word-wrap). The view is locked to a
fixed historical position. New messages accumulate in the background; the
`PAUSED+N` indicator in the status bar shows how many lines behind the live view
the user is.

### New: Up/Down arrow keys scroll the chat

Up arrow (`ESC[A` / `ESCOA`) scrolls the chat up by 1 line.
Down arrow (`ESC[B` / `ESCOB`) scrolls it down by 1 line.
Left/Right arrows still cycle the outgoing text color as before.

### Fix: `/scroll up N` argument parsing

`n_str.isdigit()` returned False for `' 10'` (a leading space from `'up 10'.replace('up', '')`).
Fixed by calling `.strip().isdigit()` so `/scroll up 10` correctly scrolls 10 lines.

### Fix: Multi-byte escape sequences polluting input buffer

PgUp/PgDn keys send `ESC [ 5 ~` / `ESC [ 6 ~` (4 bytes after ESC). The old
`_read_escape_seq` only consumed `[ 5` and left the trailing `~` in the reader
buffer. On the next `reader.read(1)` call, `~` would be appended to the input
buffer and echoed as a literal character. Fixed: if the third byte is a digit,
`_read_escape_seq` drains bytes until it hits a non-digit/non-semicolon (the
terminating letter or `~`), discarding them.

### Quality of life: scroll auto-snaps to live view on send

When the user is scrolled up and sends a message (not a slash command), the
scroll is automatically reset to 0 (live / bottom) so they can see their
own message echo back from the server.

### New: `/scroll live` alias

`/scroll 0`, `/scroll bottom`, `/scroll end`, `/scroll latest`, and
`/scroll live` all return to the live (bottom) view.

## Files changed

- `anetbbs/features/mrc_chat.py` — scrollback stability; arrow scroll; escape seq drain; auto-snap on send
- `anetbbs/__init__.py`, `setup.py`, `VERSION`, `FILE_ID.DIZ`, `RELEASE.md`, `docs/CHANGELOG.md` — version bump

## ANSI menu slot names (for sysop-created ANSI overrides)

Place `.ans` files in `data/text/menus/` to override any stock terminal menu.
See `docs/04-ansi-screens.md` for full details.

| Slot name      | Menu                                          |
| -------------- | --------------------------------------------- |
| `chat`         | Chat Systems top menu (IRC / MRC / Local)     |
| `irc_chat`     | IRC Chat — server connection options          |
| `sysop_menu`   | Sysop Tools top-level menu                   |
| `sysop_users`  | Sysop → Manage Users list header             |
| `sysop_boards` | Sysop → Manage Boards list header            |
| `sysop_status` | Sysop → Server Status header                 |
| `game_center`  | Game Center                                   |
| `door_games`   | Door Games list                               |
| `dialout`      | Dial-Out Directory                            |
