# Menus and the data-driven engine

Every menu shown to telnet / SSH / rlogin users is stored in the
database and editable from the web. There's no source code change
needed to add, remove, rename, or relink anything.

This covers the **ANSI** menu tree (`BbsMenu`/`BbsMenuItem`). PETSCII
(C64/128) sessions use a completely separate, sysop-buildable menu
tree at `/admin/petscii-menus/` with a much smaller action-type set —
most ANSI actions (art, sixel, chat, most doors) have no PETSCII
equivalent, so the two trees are deliberately not shared. See
[25 — PETSCII](25-petscii.md).

Three built-in picker screens — **Chat Systems**, **Game Center**, and
**Sysop Tools** — are *also* real, pre-seeded menus in this same tree
(`chat_systems`, `game_center`, `sysop_tools`), editable exactly like
`main`: add/remove/reorder/relabel items from here or from
`anetbbs-cfg`'s BBS Menus section, with no code required. See "Editing
the Chat/Game Center/Sysop Tools pickers" below. Every menu's header
art — `main` included — is separately file-overridable via
`data/mods/text/menus/<menu-name>.ans` (plus a `132.ans` widescreen
variant), the same as any other menu here. The Door Games list and
Dial-Out directory are different — they're always populated live from
the `Game`/`PeerBbs` tables (Admin → Games / Admin → Dial-out), not a
fixed option list, so there's nothing to reorder in a menu editor, but
their art is file-overridable the same way. See
[35 — The mods directory](35-mods-directory.md) for the full picture
of what's Admin-editable versus file-based versus `data/mods/core/`
Python-logic-overridable.

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
| `imsg`      | —                                                 | Inter-BBS Instant Message (MSP) inbox         |
| `imsg_send` | —                                                 | compose new Inter-BBS Instant Message         |
| `bulletins` | —                                                 | bulletins                                     |
| `echo`      | —                                                 | echomail areas                                |
| `echo_post` | —                                                 | compose echomail                              |
| `files`     | —                                                 | file library                                  |
| `who`       | —                                                 | who's online                                  |
| `multinode` | —                                                 | interactive multinode chat                    |
| `oneliners` | —                                                 | last 10 callers + recent one-liners           |
| `lastcallers` | —                                               | full, paginated Last Callers list             |
| `chat`      | —                                                 | Chat Systems picker (goes to the `chat_systems` menu if it exists, else the built-in picker) |
| `chat_local`| —                                                 | local multinode chat directly (for a `chat_systems` item) |
| `chat_irc`  | —                                                 | IRC (A-Net IRC door) directly (for a `chat_systems` item) |
| `chat_mrc`  | —                                                 | MRC (Inter-BBS chat) directly (for a `chat_systems` item) |
| `rss`       | —                                                 | RSS reader                                    |
| `guru`      | —                                                 | Ask Anet — searches the wiki (FTS5, not an LLM) |
| `ebooks`    | —                                                 | ebook reader (Gutenberg)                      |
| `wall`      | —                                                 | graffiti wall                                 |
| `games`     | —                                                 | Game Center picker (goes to the `game_center` menu if it exists, else the built-in picker) |
| `game_door_list` | —                                            | Door Games list directly (for a `game_center` item) |
| `game_number_guess` | —                                         | built-in Number Guessing directly (for a `game_center` item) |
| `dialout`   | —                                                 | dial out to other BBSes                       |
| `page`      | —                                                 | page sysop                                    |
| `profile`   | —                                                 | view profile                                  |
| `edit_prof` | —                                                 | edit profile                                  |
| `passwd`    | —                                                 | change password                               |
| `sysop`     | —                                                 | Sysop Tools picker, admin only (goes to the `sysop_tools` menu if it exists, else the built-in picker) |
| `sysop_users`, `sysop_boards`, `sysop_echomail`, `sysop_games`, `sysop_wall`, `sysop_file_queue`, `sysop_events`, `sysop_rss_admin`, `sysop_login_modules`, `sysop_notifications`, `sysop_registry`, `sysop_callers`, `sysop_node_monitor`, `sysop_status`, `sysop_cfg_tool` | — | one specific Sysop Tools category directly, admin only (for a `sysop_tools` item — see below) |
| `logoff`    | —                                                 | end session                                   |

## A couple of action types in more detail

Most action types are self-explanatory from the table above, but two
are easy to underestimate from a one-line description:

- **`wall`** — the graffiti wall (`anetbbs/features/wall.py`). A
  shared, paginated bulletin board of short (1-2 line) messages any
  user can post to, rendered as an ANSI box that adapts its width to
  the caller's terminal (79 cols on standard 80-col, up to 131 wide).
  Posts support Synchronet/Mystic-style `|XX` pipe color codes, with
  an ASCII-only fallback for terminals that can't render CP437
  box-drawing characters. Sysops get an extra `[D]el` option to remove
  a post by ID. Not networked or moderated beyond that — it's a purely
  local, lightweight "who was here" wall, distinct from boards,
  echomail, and the shoutbox.
- **`ebooks`** — the ebook reader (slug `ebooks`, `anetbbs/web/ebooks.py`
  for the web version, `show_ebooks()` in `anetbbs/features/bbs_ui.py`
  for the terminal version — both share the same backend). Lets users
  search and read free public-domain books from Project Gutenberg via
  Gutendex (gutendex.com, a community JSON search API over Gutenberg's
  catalog), plus a curated "classics" shelf for browsing without
  searching first. Book text is fetched once per title and cached
  forever (`EbookCache`); per-user bookmarks and reading history
  persist across sessions. The terminal reader offers the same
  classics/search/history/bookmarks flow as the web version, paginated
  for the terminal. This action is gated per-`Game` row by a
  `terminal_enabled` toggle (Admin → Games) independent of the web
  version's own `web_enabled` toggle, so a sysop can offer one without
  the other.

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

## Editing the Chat/Game Center/Sysop Tools pickers

`chat_systems`, `game_center`, and `sysop_tools` are seeded on every
install (fresh or upgraded) with items matching today's exact stock
behavior — they show up in **Admin → BBS Menus** and `anetbbs-cfg`'s
BBS Menus section like any other menu, right away, no setup step.
Concretely, this means a sysop can:

- **Add a genuinely new option** — e.g. a 4th Chat Systems item
  pointing at a local shoutbox door (`action_type = door`), an `exec`
  script, or a custom `ansi` screen. This is the case that used to
  require editing Python source; now it's the same one-click "Add
  item" flow as any other menu.
- **Remove or reorder existing options** — drop IRC from the Chat menu
  entirely, or put MRC before Local Chat, by deleting/reordering
  items, same as any other menu.
- **Relabel or rebind hotkeys** — call the Users category something
  else, or move it off `U`, on the `sysop_tools` menu.
- **Delete the whole menu** to fall back to the original hardcoded
  picker (mostly useful for troubleshooting — there's rarely a reason
  to do this over just editing items back to their defaults).

Each of the leaf action types listed in the table above
(`chat_local`/`chat_irc`/`chat_mrc`, `game_door_list`/
`game_number_guess`, the fifteen `sysop_*` ones) is exactly what the
corresponding stock option already did — they exist so a sysop's
*edited* version of these three menus can still reach the same
underlying functionality piece by piece, not just the bundled
all-three/all-fifteen defaults.

`sysop_tools`'s items (and the menu itself) are seeded at
`min_access = 100` and every `sysop_*` action independently re-checks
`is_admin` at dispatch time, regardless of what a menu item's own
`min_access` says — the same defense-in-depth precedent as the
Config Tool item's existing SSH-only check (see
[28 — anetbbs-cfg](28-anetbbs-cfg.md)), so a misconfigured `min_access`
on one item can't accidentally expose sysop functionality.

If a `data/mods/core/` override exists for one of these three screens
(`chat_menu.py`, `game_center.py`, `sysop_tools.py`), it takes priority
over the admin-editable menu entirely — see
[35 — The mods directory](35-mods-directory.md) for when you'd reach
for that instead of editing the menu here.

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
