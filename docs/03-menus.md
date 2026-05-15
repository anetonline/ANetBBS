# Menus and the data-driven engine

Every menu shown to telnet / SSH / rlogin users is stored in the
database and editable from the web. There's no source code change
needed to add, remove, rename, or relink anything.

## Where to edit

**Admin → BBS Menus** (`/admin/bbs-menus/`).

You'll see:

- **Menu tree** — entry-point menus and which submenus they goto
- **All menus** table with quick edit/delete

## Anatomy of a menu

A menu has:

| field         | meaning                                            |
| ------------- | -------------------------------------------------- |
| `name`        | unique key (e.g. `main`, `games`, `sysop_tools`)   |
| `title`       | shown in the auto-rendered banner                  |
| `prompt`      | text before the input cursor (`Choice: `)          |
| `ansi_screen` | raw ANSI/CP437 art shown above the menu (optional) |
| `is_default`  | this is the entry point after login                |
| `min_access`  | hide menu from users below this access level       |

## Menu items

Each item has:

- `hotkey` — single character or short sequence
- `label` — visible text
- `action_type` — what to do when picked
- `action_args` — passed to the action
- `min_access` — hide if user is below this
- `sort_order`, `is_visible`

## Action types

| action      | args                                              | what it does                                  |
| ----------- | ------------------------------------------------- | --------------------------------------------- |
| `goto`      | name of another menu                              | jump to another menu                          |
| `door`      | Game.id                                           | launch a registered door game                 |
| `exec`      | shell cmd or JSON                                 | run an external program (see [doc 5](05-external-programs.md)) |
| `ansi`      | slot name (welcome / weather_intro / ...)         | display a sysop-defined ANSI screen           |
| `boards`    | —                                                 | message boards UI                             |
| `pm`        | —                                                 | PM inbox                                      |
| `pm_send`   | —                                                 | compose new PM                                |
| `bulletins` | —                                                 | bulletins                                     |
| `echo`      | —                                                 | echomail areas                                |
| `echo_post` | —                                                 | compose echomail                              |
| `files`     | —                                                 | file library                                  |
| `who`       | —                                                 | who's online                                  |
| `multinode` | —                                                 | interactive multinode chat                    |
| `oneliners` | —                                                 | last 10 callers + recent one-liners           |
| `chat`      | —                                                 | chat menu (IRC / shoutbox)                    |
| `games`     | —                                                 | game center                                   |
| `dialout`   | —                                                 | dial out to other BBSes                       |
| `page`      | —                                                 | page sysop                                    |
| `profile`   | —                                                 | view profile                                  |
| `edit_prof` | —                                                 | edit profile                                  |
| `passwd`    | —                                                 | change password                               |
| `sysop`     | —                                                 | sysop tools (admin only)                      |
| `logoff`    | —                                                 | end session                                   |

## Adding a sub-menu

Click any menu's edit page → "**Add a sub-menu (one click)**" card.

Fill in:
- New menu key (`games_arcade`)
- Title (`Arcade Games`)
- Hotkey on this menu (`A`)
- Label (`Arcade`)

It creates the child menu, auto-adds a `Q` key on the child to come
back, and links from the parent. You can deep-nest as far as you
like.

## Tips

- The `min_access` field on items + menus implements
  Mystic/Synchronet-style tiers. Set a user's `access_level` in
  Admin → Users → Manage.
- Setting `is_default=True` on a menu other than `main` lets you
  override the entry point.
- The "Add sample items" button on the menu list adds a working
  weather door (curl wttr.in) + an ANSI replay so you can verify
  `exec` and `ansi` actions work without writing your own.

## Behavior at the prompt

- **Single-key hotkeys.** The prompt reads one keystroke and dispatches
  immediately — no Enter required. Bare Enter just redraws the menu.
  Backspace, arrow keys, and Ctrl-C are silently swallowed.
- **Screen is cleared between menus** so the new menu doesn't render
  on top of the previous one.
- **`Q` (logoff)** confirms with `Y/N` so a stray keystroke doesn't
  drop your session.

## Display codes inside `ansi_screen`

The menu's `ansi_screen` content runs through the same Synchronet
`@CODE@` and Mystic `|XX` substitution as standalone ANSI screens.
See [doc 4 — ANSI screens](04-ansi-screens.md) for the supported code
table. Color pipe-codes (`|07` etc.) are unaffected.
