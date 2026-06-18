# ANSI screens

ANSI screens are raw CP437 + ANSI escape code art shown at lifecycle
events: pre-login welcome, post-logoff goodbye, after registration,
or on demand from any menu item.

## Where they live

**Admin → BBS Menus → ANSI screens** (`/admin/bbs-menus/screens`).

Each screen has:

| field         | meaning                                                   |
| ------------- | --------------------------------------------------------- |
| `slot`        | unique key — `welcome`, `goodbye`, `newuser`, custom name |
| `title`       | sysop reference only                                      |
| `body`        | raw ANSI bytes                                            |
| `pause_after` | wait for Enter before continuing                          |
| `is_active`   | toggle without deleting                                   |

## Built-in slots used by the BBS lifecycle

| slot      | when shown                                                                  |
| --------- | --------------------------------------------------------------------------- |
| `welcome` | telnet visitors, before the login menu. SSH/rlogin auto-login skips it.     |
| `goodbye` | every protocol on logoff                                                    |
| `newuser` | shown right after a successful new-user registration                        |

## Custom slots

Create any slot name and reference it from a menu item:

- `action_type = ansi`
- `action_args = my_slot_name`

So you can make a "view the rules" item, a per-game intro splash,
a sysop-of-the-day plug, etc.

## Editing

The editor has a live preview pane that handles SGR colors + clear
screen + cursor home, so you see roughly what the user will see.

You can paste:

- The text content of an existing `.ans` file
- Raw escape codes typed as `\x1b[1;36m` etc.
- Output from `ansiedit`, Pablo, Moebius, ACiDDraw

## ANSI menu overrides (file-based)

Beyond the database-driven ANSI screens above, built-in terminal menus
(IRC chat, sysop tools, etc.) support **file-based** ANSI overrides.
Place a `.ans` file in `data/text/menus/` with the correct slot name
and ANetBBS will show your ANSI instead of the stock colored menu.

### How it works

1. Drop `<slot>.ans` into `data/text/menus/` (create the directory if needed).
2. No restart required — the file is read on every menu visit.
3. The screen is cleared, your ANSI is displayed, then the normal
   prompt appears below it (the sysop is still responsible for the art;
   ANetBBS always appends the live prompt so the user can make a choice).
4. If the file is absent or unreadable, the stock colored menu is shown.

### File naming requirements

- Extension **must** be `.ans` (lowercase)
- Encoding: CP437 + standard ANSI escape codes (same as any BBS art)
- File name is the slot name + `.ans`, e.g. `chat.ans`

### Slot names reference

| Slot name      | Menu shown                                    |
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

### Tips

- Keep art at ≤ 23 rows so the prompt fits on a 24-line terminal.
- If the art is taller than the terminal, the user can scroll up to see
  the full art after the prompt renders — but the prompt always appears
  at the current cursor position, not at the bottom of the art.
- Color pipe-codes (`|07` etc.) inside the art are **not** translated here;
  use raw ANSI escape sequences from your art tool instead.
- The menu slot system is separate from the database `ansi_screen` field
  on menus — those are for user-facing menu header art and support
  `@CODE@` substitution; these file overrides do not.

## SAUCE

When you import an `.ans` file into the **ANSI Editor** (`/ansi/`),
the import wizard auto-detects the SAUCE trailer (the 128-byte
metadata block at end), strips it cleanly so you don't get garbage
rows, and shows the title/author/group as a flash message.

## Display codes (Synchronet `@CODE@` and Mystic `|XX`)

ANetBBS substitutes a useful subset of Synchronet's at-codes
(<https://wiki.synchro.net/custom:atcodes>) and Mystic's named
display codes (<https://wiki.mysticbbs.com/doku.php?id=displaycodes>)
in any ANSI screen rendered through the BBS shell — pre-login
welcome, post-logoff goodbye, post-registration newuser, and any
custom slot referenced by a menu item with `action_type = ansi`.

The same codes also work inside Synchronet door files served via
`console.printfile(...)`, which uses the same `BBS_*` env vars that
`door_runner.py` exports to the door process.

| Synchronet code | Resolves to                                       |
| --------------- | ------------------------------------------------- |
| `@USER@` / `@ALIAS@` / `@HANDLE@` | login username                  |
| `@NAME@` / `@REAL@`               | display name (or username)      |
| `@FIRST@`                         | first word of display name      |
| `@EMAIL@`                         | user email                      |
| `@LOCATION@`                      | user-set location               |
| `@BBS@`                           | `BBS_NAME` config               |
| `@SYSOP@`                         | `SYSOP_NAME` config             |
| `@NODE@`                          | active node slot number         |
| `@SECURITY@`                      | 100 (admin) / 50 (user)         |
| `@CALLS@`                         | the user's login_count          |
| `@TIME@`                          | local `HH:MM`                   |
| `@DATE@`                          | local `YYYY-MM-DD`              |
| `@DAY@`                           | weekday, e.g. `Tuesday`         |
| `@VER@` / `@VERSION@`             | ANetBBS build version           |
| `@TIMELEFT@`                      | minutes left in this session    |

| Mystic code | Resolves to                                           |
| ----------- | ----------------------------------------------------- |
| `\|UN`      | login username                                        |
| `\|UA`      | alias / handle                                        |
| `\|UR`      | real name (display_name)                              |
| `\|BN`      | BBS name                                              |
| `\|SN`      | sysop name                                            |
| `\|DT`      | date `YYYY-MM-DD`                                     |
| `\|TM`      | time `HH:MM`                                          |
| `\|VL`      | ANetBBS build version                                 |
| `\|ND`      | active node slot number                               |
| `\|LF`      | CRLF                                                  |

Color pipe-codes like `|07` (Synchronet/Mystic light-grey) continue
to be handled by the existing pipe-to-ANSI translator and are
**unaffected** by the named-code substitution above.

Unknown codes pass through visibly so you can spot what's missing
and ask for it to be wired up.
