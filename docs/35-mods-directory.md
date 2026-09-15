# The `data/mods/` directory — customization that survives an update

ANetBBS actually gives a sysop **two** separate ways to customize the
BBS, and it's easy to only ever discover one of them:

1. **The Admin web UI, database-driven.** The main menu tree — plus
   the Chat Systems, Game Center, and Sysop Tools picker screens,
   which live in this same `BbsMenu`/`BbsMenuItem` system — every
   hotkey/label/action, the `welcome`/`goodbye`/`newuser` ANSI
   screens, translations, themes: all of it lives in the database and
   is editable live from `/admin/` (or `anetbbs-cfg`), no file
   involved, no restart. This is the primary, recommended way to
   reshape *what the BBS says and does*, including adding a genuinely
   new option to one of those three pickers with no code at all. See
   [03 — Menus](03-menus.md) and [04 — ANSI screens](04-ansi-screens.md).
2. **The file-based `data/mods/` tree.** For the things that genuinely
   aren't database rows — bundled door-game scripts, the compat-shim
   library files those scripts load, a handful of remaining ANSI menu
   screens still rendered by *code* rather than the menu engine (Door
   Games list, Dial-Out — both always populated live from a database
   table, not a fixed option list), and actual Python screen logic for
   the four screens that support it. This page covers this second
   system end to end, since it's easy to miss entirely.

The one idea that ties all of `data/mods/` together: **`update.sh`
never touches it.** The update process rsyncs a fresh copy of the
`anetbbs/` package over your install but always runs with
`--exclude='/data/'` — and `data/mods/` lives inside `data/`. Anything
you put there survives every future `update.sh` run untouched,
forever, with zero merge conflicts to resolve by hand. This is
ANetBBS's version of real Synchronet's own well-known `/sbbs/mods/`
convention (wiki.synchro.net/dir:mods) — same idea, same name, same
guarantee.

`install.sh`/`update.sh` both create the full directory tree
(`data/mods/`, `data/mods/text/`, `data/mods/text/menus/`,
`data/mods/core/`) on every install/upgrade, empty. Nothing is seeded
into it — `data/mods/` only ever contains what you deliberately put
there, and every file is checked fresh from disk on every use, so
dropping a file in (or deleting one to revert to stock) takes effect
immediately with **no service restart**.

## The four things you can actually override

| Location | Overrides | Effect | Docs |
| -------- | --------- | ------ | ---- |
| `data/mods/text/<slot>.ans` | Lifecycle ANSI screens (`welcome`, `goodbye`, `newuser`, any custom slot) | Your art shown instead of the database screen | [04](04-ansi-screens.md) |
| `data/mods/text/menus/<slot>.ans` | The remaining built-in screens that aren't admin-editable menus (Door Games list, Dial-Out — full list below) | Your art shown above the live prompt, instead of the generated menu | [04](04-ansi-screens.md) |
| `data/mods/core/<name>.py` | The pre-login menu, or the Chat Systems / Game Center / Sysop Tools pickers' actual **logic** — add a genuinely new option, not just reorder/re-skin the existing ones | Your Python function runs instead of the admin-editable menu (or the built-in fallback if that menu row is missing) | This page, below |
| `data/mods/<name>.js` / `data/mods/<relative/path>.js` | A bundled Synchronet-compat door script, or any file it `load()`s at runtime | Your JS file runs instead of the bundled copy | [14](14-door-games.md) |

The first two rows are pure **art** replacement — drop a file, get
different pixels, the underlying behavior is unchanged. The last two
rows are **code** replacement — the file you drop actually runs,
which is real capability but also real trust: treat write access to
`data/mods/core/` or `data/mods/*.js` the same as you'd treat handing
someone a shell account on the BBS server. See "A note on trust"
below.

## 1. Lifecycle ANSI screens — `data/mods/text/`

Full detail in [doc 4](04-ansi-screens.md); short version here.

Checked *before* the database screen, in this order:

1. `data/mods/text/<slot>.ans` — preferred, survives updates
2. `data/text/<slot>.ans` — older location, still fully supported
3. the database `BbsAnsiScreen` row for that slot (Admin → BBS Menus →
   ANSI screens)

`<slot>` is `welcome`, `goodbye`, `newuser`, or any custom slot name
referenced by a menu item with `action_type = ansi`. Both locations
also support `<slot>132.ans` (widescreen), `<slot>.asc` (plain ASCII),
numbered sequences (`welcome132_2.ans`, `_3.ans`, ...) shown one after
another every login, and `_ran` groups (`welcome132_ran.ans`,
`_2_ran.ans`, ...) that pick one at random each login instead.

**Worked example** — a sysop wants a different welcome screen than
whatever's in the database, without touching Admin at all:

```
ssh youruser@yourbbs
mkdir -p ~/anetbbs/data/mods/text
nano ~/anetbbs/data/mods/text/welcome.ans   # paste your art
```

Next connection shows it. Delete the file and the next connection
falls straight back to the database screen — nothing was overwritten,
nothing to undo in Admin.

## 2. Built-in menu art — `data/mods/text/menus/`

Full detail in [doc 4](04-ansi-screens.md); short version here.

A handful of screens in ANetBBS still aren't part of the
database-driven `BbsMenu`/`BbsMenuItem` tree — they're always
populated live from a database table (the Door Games list from
`Game`, the Dial-Out directory from `PeerBbs`), not a fixed option
list, so there's nothing to add/reorder in a menu editor. This is
exactly the gap `data/mods/text/menus/` fills for these: drop
`<slot>.ans` and it's shown above the live prompt instead of the
generated plain-text header.

| Slot name | Menu | Notes |
| --------- | ---- | ----- |
| `door_games` | Door Games list | |
| `door_games_<category-slug>` | A game category's own submenu (only categories flagged "Show as a submenu section") | `<category-slug>` is the category's own Slug field, e.g. `door_games_synchronet-doors.ans` |
| `dialout` | Dial-Out Directory | |

**Worked example** — a sysop wants their own art on the Door Games
list:

```
mkdir -p ~/anetbbs/data/mods/text/menus
nano ~/anetbbs/data/mods/text/menus/door_games.ans
```

Save, reconnect, open Door Games from the Game Center — the custom art
shows, followed by the normal list. No restart, no Admin step.

**Chat Systems / Game Center / Sysop Tools headers work differently
now**: those three are real `BbsMenu` rows (see section 1 above and
[doc 3](03-menus.md)), each with its own `ansi_screen` field for
exactly this purpose — edit it from Admin → BBS Menus (or
`anetbbs-cfg`) instead of a file drop. A `chat`/`sysop_users`/
`sysop_boards`/`sysop_status`/`game_center` slot file under
`data/mods/text/menus/` from before this menu-editing capability
existed is no longer reached (the admin-editable menu renders first) —
move that art into the corresponding menu's `ansi_screen` field to
keep using it.

## 3. Core Python screen logic — `data/mods/core/`

Full detail on the underlying mechanism in
[doc 14](14-door-games.md#datamodscore--overriding-anetbbss-own-native-code);
short version here, since this is the deepest override point that
exists and the easiest one to underestimate.

Everything in sections 1 and 2 above swaps **art**; the code that
decides what happens next is untouched. `data/mods/core/` is
different: drop a complete replacement Python file defining an async
function, and *your code runs instead of ANetBBS's own*. This is real
Synchronet precedent, carried over on purpose: on real Synchronet,
`login.js`/`logon.js` are core system scripts (not doors) that the
engine loads by filename, and a sysop's modified copy in `mods/` is
used automatically instead — there's no doors-only carve-out there,
because on Synchronet *everything*, including the core login flow, is
just a script loaded by filename. ANetBBS's own core isn't
script-driven the same way — `login_screen()`, `ChatManager.show_menu()`,
etc. are compiled-in Python methods, not files loaded by name at
runtime — so getting the same capability needs an explicit override
point built per screen (`anetbbs/core/mods_override.py`'s
`call_core_override()`), rather than falling out for free. Four
screens are wired up to it today, checked in this order ahead of
everything else (including the admin-editable menu from section 1):
the pre-login menu (`login_menu`), and the Chat Systems (`chat_menu`),
Game Center (`game_center`), and Sysop Tools (`sysop_tools`) pickers.

For the three pickers, reach for this only when what you want genuinely
isn't expressible as a menu item — a real custom control flow, not
just a different action. **Most "add an option" requests are better
served by editing the menu directly** (section 1, [doc 3](03-menus.md))
since an `exec`/`ansi`/`door` item needs no Python at all; `data/mods/core/`
is the deeper escape hatch for when that's not enough.

### `data/mods/core/login_menu.py` — the pre-login menu

The interactive Up/Down-lightbar menu shown to telnet visitors before
they type a username:

```python
# data/mods/core/login_menu.py
async def render_login_menu(session, bbs_name):
    """Return '1' (Login), '2' (New User), or '3' (Exit)."""
    await session.write('\x1b[2J\x1b[H')
    # ... draw your own layout, read a key with
    # session.read_key_arrow(), return the chosen digit ...
    return '1'
```

`session.write(text)` sends raw ANSI/text; `session.read_key_arrow()`
reads one keystroke and returns `'UP'`/`'DOWN'`/`'LEFT'`/`'RIGHT'`/
`'ENTER'`/`'ESC'`/`'CTRL_C'`/`'PGUP'`/`'PGDN'`/`'HOME'`/`'END'` or an
uppercase printable character.

### `data/mods/core/chat_menu.py` — the Chat Systems menu

For genuinely custom control flow the menu-item system can't express
(section 1 covers simply adding/removing/reordering options). Drop a
complete replacement defining `show_chat_menu(session, chat_manager)`:

```python
# data/mods/core/chat_menu.py
async def show_chat_menu(session, chat_manager):
    while True:
        await session.write(
            '\x1b[2J\x1b[H1) Local Chat\r\n2) IRC Chat\r\n'
            '3) MRC Chat\r\n4) My Custom Shoutbox\r\nQ) Back\r\n')
        choice = (await session.read_line('Choice: ') or '').strip().upper()
        if choice == '1':
            await chat_manager.local_chat()
        elif choice == '2':
            from anetbbs.features.anetirc2 import launch_anetirc_telnet
            await launch_anetirc_telnet(session.user, session)
        elif choice == '3':
            await chat_manager.chat_systems['mrc'].show_menu()
        elif choice == '4':
            await session.write('\r\n... your own code here ...\r\n')
        elif choice == 'Q' or not choice:
            return
```

`chat_manager` is the *live* `ChatManager` instance for this session —
that's what makes options 1–3 above a couple of lines each instead of
reimplementing local chat, IRC, and MRC from scratch: reuse
`chat_manager.local_chat()` and `chat_manager.chat_systems['mrc']`
exactly like the stock menu does, and just add your own branch beside
them. `session.user`, `session.read_line()`, and `session.write()`
work exactly as in any other terminal code.

### `data/mods/core/game_center.py` and `data/mods/core/sysop_tools.py`

Same contract, same reasoning — `show_game_center_menu(session, game_manager)`
and `show_sysop_tools_menu(session, bbs_ui)` respectively.
`game_manager`/`bbs_ui` are the live instances for this session, so
(mirroring `chat_manager` above) an override can still call
`game_manager.show_door_menu()`/`play_number_guess()`, or
`bbs_ui.sysop_users()` and friends, instead of reimplementing them.

### All four screens share the same behavior

The file is read fresh from disk on every visit to that menu — edit
it, reconnect, see the change, no restart. A syntax error, a missing
function, or any exception raised while your override runs all
degrade gracefully back to the stock built-in menu instead of breaking
that part of the BBS for everyone — check the BBS log
(`journalctl -u anetbbs`) if an edit doesn't seem to be taking effect.

`call_core_override()` is a general mechanism any other core screen
could opt into the same way in the future — these four are just what's
wired up today, not the only ones it's designed for. If there's a
specific other core screen you'd want this level of control over,
that's worth raising as a feature request rather than assuming it's
already possible — see the bug-report template for how.

## 4. Synchronet-compat door script overrides — `data/mods/<name>.js`

Full detail in [doc 14](14-door-games.md#datamods--the-sysop-override-tree-v1036);
short version here.

ANetBBS bundles a set of classic Synchronet-compatible doors
(`anetbbs/games/sbbs_doors/`) and the JS compat shim's own
library/stub files. A hand-patch to fix a door-specific quirk would
normally get silently overwritten on the next update — `data/mods/`
is where that patch lives instead, checked first, always:

- **A door's own entry-point script**, matched by filename only:
  drop `data/mods/lord.js` to override
  `anetbbs/games/sbbs_doors/lord/lord.js` regardless of the bundled
  file's real location in the package tree.
- **Anything the door itself loads at runtime** via `load("...")`
  (a door's sibling file, or a compat-shim stub like `sbbsdefs.js`),
  matched against the exact string the door passed to `load()` — so
  overriding `load("dorkit/screen.js")` means dropping a file at
  `data/mods/dorkit/screen.js`, preserving that same subdirectory
  structure under `data/mods/`, not flattening it to
  `data/mods/screen.js`.

## A note on trust

Sections 1 and 2 above (`data/mods/text/`, `data/mods/text/menus/`)
are plain static art files — no more risky than editing a screen in
Admin. Sections 3 and 4 (`data/mods/core/`, `data/mods/*.js`) are
**real code that runs with the BBS service account's own OS
privileges** — no sandbox, no restricted view, exactly like real
Synchronet's own door model has always worked. Anyone who can write to
those two locations (shell access, SFTP, a compromised deploy key) can
run arbitrary code as the BBS. That's an inherent property of what
these two override points are *for* — not a bug — but it means "who
has write access to `data/mods/`" deserves the same care as "who has a
shell account on this box."

## Quick reference: where does a given customization actually go?

| You want to change... | Where |
| ---------------------- | ----- |
| Main menu hotkeys/labels, adding a sub-menu | Admin → BBS Menus ([doc 3](03-menus.md)) — not `mods/` at all |
| Chat Systems / Game Center / Sysop Tools options (add/remove/reorder), or their header art | Admin → BBS Menus (or `anetbbs-cfg`) — edit the `chat_systems`/`game_center`/`sysop_tools` menu directly, same as `main` |
| The `welcome`/`goodbye`/`newuser` screen, or a custom `ansi`-action slot | `data/mods/text/<slot>.ans`, or Admin → BBS Menus → ANSI screens |
| The Door Games list / Dial-Out directory header art | `data/mods/text/menus/<slot>.ans` (these two stay code-driven — no Admin equivalent) |
| The pre-login menu's, or Chat/Game Center/Sysop Tools' actual **logic**, beyond what a menu item can express | `data/mods/core/<name>.py` |
| A bundled Synchronet-compat door's behavior | `data/mods/<name>.js` |
| Site theme / colors | Admin → Themes ([doc 8](08-themes.md)) — not `mods/` |
| Translations / language packs | Admin → Translations — not `mods/` |
