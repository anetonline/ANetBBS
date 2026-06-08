# ANetBBS v1.0a2.85 — Fix: ANSI menu overlays double-rendering stock menus

## What's fixed

### Stock menu rendered on top of custom ANSI when a `.ans` file was present

When `load_menu_ansi()` found a custom `.ans` file the code correctly wrote the
ANSI bytes, but the stock menu items (for-loop rows, footer box) were outside
the `if/else` block and executed unconditionally — so the stock menu appeared
beneath the ANSI art on every render.

Affected menus: `door_games`, `game_center`, `chat`, `dialout`.
(`irc_chat` was already structured correctly in v1.0a2.83.)

Fixed: the items loop, footer, and any other stock-only output are now inside
the `else` block so they only render when no ANSI file is present.

### Screen not cleared before ANSI write

ANSI art was written over whatever was already on the terminal without first
clearing the screen, causing leftover content to show through where the art
didn't cover every cell.

Fixed: `\x1b[2J\x1b[H` (erase display + cursor home) is now prepended to the
ANSI bytes before they are sent to the terminal, for all five ANSI-override
menu slots (`door_games`, `game_center`, `chat`, `irc_chat`, `dialout`).

No configuration change needed.
