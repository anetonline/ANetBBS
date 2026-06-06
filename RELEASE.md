# ANetBBS v1.0a2.83 — Feature: custom ANSI headers for all hard-coded menus/submenus

## What's new

### Custom ANSI art for all menus — not just the main menu

All hard-coded telnet/SSH/rlogin menus now support optional `.ans` file
overrides. If the file exists it is used; if not, the built-in menu renders
exactly as before.

Drop your file into `data/text/menus/` with the slot name shown below:

| Slot name       | Menu it replaces                          |
|-----------------|-------------------------------------------|
| `game_center`   | Game Center (top-level games menu)        |
| `door_games`    | Door Games list (shows installed doors)   |
| `chat`          | Chat Systems menu                         |
| `irc_chat`      | IRC Chat menu                             |
| `dialout`       | Dial Out — Visit Another BBS              |

Plus the previously supported slots from v1.0a2.82:

| Slot name       | Menu it replaces                          |
|-----------------|-------------------------------------------|
| `main`          | Main BBS menu (and any BbsMenu by name)   |
| `welcome`       | Login / welcome screen                    |
| `goodbye`       | Logoff screen                             |
| `newuser`       | New user welcome screen                   |

All files must be standard CP437 ANSI art (Moebius, PabloDraw, TheDraw, etc.).
Place them at `data/text/menus/<slot>.ans` (menus) or `data/text/<slot>.ans`
(welcome/goodbye/newuser). Files take priority over anything set in the web
admin — remove the file to revert to the built-in display.
