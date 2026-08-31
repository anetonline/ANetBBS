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

## File-based override (welcome / goodbye / newuser / custom slots)

Before checking the database, `welcome`/`goodbye`/`newuser` and any
custom `ansi` slot also look for a plain file drop-in — checked in
this order, first match wins:

1. `data/mods/text/<slot>.ans` — the **preferred** location (v1.0.36+),
   matching real Synchronet's own `mods/text/` convention
   (wiki.synchro.net/dir:mods): a sysop customization that's guaranteed
   to survive an ANetBBS update untouched, the same guarantee
   `data/mods/` already gives door scripts (see
   [doc 14 — Door games](14-door-games.md)).
2. `data/text/<slot>.ans` — the older location, still fully supported
   for anyone already using it; nothing here needs to move.

Both accept `<slot>132.ans` for widescreen terminals and `<slot>.asc`
for plain-ASCII terminals, same as each other. Either location takes
priority over the database screen, so dropping a file in is the
quickest way to swap in art without touching Admin. No restart
required.

### Multiple screens shown together (classic multi-logon-screen style)

Everything below works identically whichever of the two directories
above you use — the examples show `data/text/` for brevity, but
`data/mods/text/` behaves exactly the same way.

Drop in more than one numbered file and ANetBBS shows **all of them**,
in order, every single login — the same idea as Synchronet's
`logon1.ans`/`logon2.ans`/`logon3.ans` convention:

```
data/text/welcome132.ans      <- shown first (the plain file counts as #1)
data/text/welcome132_2.ans    <- shown second
data/text/welcome132_3.ans    <- shown third
```

Every visitor sees `welcome132.ans`, then `welcome132_2.ans`, then
`welcome132_3.ans`, all in the same login — not a different one each
time. Numbers don't need to be contiguous — whatever `_N` files exist
are used in ascending numeric order. This works independently per
screen (`welcome`, `welcome132`, `welcome.asc`, `goodbye`, etc. each get
their own sequence), and for a single file it behaves exactly as
before — no extra setup needed unless you actually want more than one.

**Each variant controls its own pause.** ANetBBS doesn't automatically
insert a pause between screens in the sequence — put `@PAUSE@` at the
end of a variant's content if you want the visitor to press a key
before the next one loads (see "Display codes" below). Leave it off and
that screen flows straight into the next with no wait.

### Random: pick just ONE instead of showing the whole sequence

Want variety — a different single screen each login — rather than the
full sequence every time? Use `_ran` in the filename instead of the
plain numbered naming:

```
data/text/welcome132_ran.ans      <- random variant #1
data/text/welcome132_2_ran.ans    <- random variant #2
data/text/welcome132_3_ran.ans    <- random variant #3
```

With `_ran` naming, each login shows **one** of the group, chosen at
random — not the whole sequence. If both a `_ran` group and a plain
numbered group exist for the same screen, the `_ran` group wins. Works
for any screen (`welcome`, `goodbye`, `newuser`, custom slots) — mix and
match per-screen as you like: the full sequence for one, random-pick-one
for another.

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
Place a `.ans` file with the correct slot name in either of these
(checked in order, first match wins — same precedence as the
lifecycle screens above):

1. `data/mods/text/menus/` — the **preferred** location (v1.0.36+),
   matching real Synchronet's own `mods/text/menu/` convention.
2. `data/text/menus/` — the older location, still fully supported.

### How it works

1. Drop `<slot>.ans` into `data/mods/text/menus/` (or `data/text/menus/`
   — create the directory if needed).
2. No restart required — the file is read on every menu visit.
3. The screen is cleared, your ANSI is displayed, then the normal
   prompt appears below it (the sysop is still responsible for the art;
   ANetBBS always appends the live prompt so the user can make a choice).
4. If the file is absent or unreadable in both locations, the stock
   colored menu is shown.

### File naming requirements

- Extension **must** be `.ans` (lowercase)
- Encoding: CP437 + standard ANSI escape codes (same as any BBS art)
- File name is the slot name + `.ans`, e.g. `chat.ans`

### Slot names reference

| Slot name      | Menu shown                                    |
| -------------- | --------------------------------------------- |
| `chat`         | Chat Systems top menu (IRC / MRC / Local) — skipped entirely when MRC is disabled (`MRC_BRIDGE_ENABLED`), falling back to the generated menu instead; a custom `chat.ans` hardcoding an MRC line needs updating by hand if you disable MRC |
| `sysop_users`  | Sysop → Manage Users list header             |
| `sysop_boards` | Sysop → Manage Boards list header            |
| `sysop_status` | Sysop → Server Status header                 |
| `game_center`  | Game Center                                   |
| `door_games`   | Door Games list                               |
| `door_games_<category-slug>` | A category's own submenu screen (see below) |
| `dialout`      | Dial-Out Directory                            |

### Door menu category submenus

A game category flagged **"Show as a submenu section"** (Admin →
Games → Categories) collapses to one selectable line on the main Door
Games list; picking it opens a second screen listing just that
category's own games. That second screen supports the exact same
file-based art override as any other slot above — drop
`door_games_<category-slug>.ans` into `data/text/menus/`, where
`<category-slug>` is the category's own **Slug (URL-safe)** field
(e.g. a category with slug `synchronet-doors` looks for
`door_games_synchronet-doors.ans`). Falls back to the stock generated
layout if no matching file exists, same as every other slot.

Note: the Sysop Tools top-level menu itself (the U/B/E/G/W/... category
picker) is a scrolling lightbar screen with fixed row positions, so it
can't take a variable-height custom art header the way the other slots
above can — it always shows the standard banner.

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

When you import an `.ans` file into the **ANSI Editor** (`/admin/ansi/`),
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
| `@TIMELEFT@`                      | always blank — not modelled yet |
| `@CLS@`                           | clear screen + home cursor      |
| `@BPS:NNNN@`                      | simulate an NNNN-baud modem for the rest of the screen (300–56000, clamped); throttles the remaining output with real delays instead of just decorating text |

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

## Pagination and inline animation (ANetBBS-specific markers)

These aren't Synchronet or Mystic at-codes — they're ANetBBS's own,
recognized in any screen shown through the same shell path as the
tables above (welcome, goodbye, newuser, `action_type = ansi` custom
slots).

**`@PAUSE@`** splits a screen into pages. Put it anywhere in the file
and everything up to that point is shown, then the visitor sees
`[Press any key to continue]` before the rest loads. Use as many as
you like for a multi-page screen; see "Each variant controls its own
pause" above for how this interacts with multi-screen sequences.

**`@ANIMSTART@<frame>@FRAME@<frame>@FRAME@...@ANIMEND@`** plays a short
looping animation in place, right where the marker sits in the file —
useful for a waving character, a blinking light, anything that should
visibly move without scrolling the rest of the screen. Each `<frame>`
is one or more full lines of the same content redrawn with small
differences (a robot's arm in a different position, a different shade
character, etc.); every frame must be exactly the same number of
lines, since that line count is what tells the player how far to move
the cursor back up before redrawing the next frame — get this wrong
(frames with different line counts) and the animation will drift down
the screen instead of redrawing in place. There's no way to set the
frame delay or loop count from inside the screen file itself; both are
fixed constants in the code (`_ANIM_FRAME_DELAY` in `session.py`) — if
a screen needs different timing, that's a code change, not a per-file
setting. A screen can mix `@PAUSE@` and `@ANIMSTART@...@ANIMEND@`
freely; each is handled independently wherever it appears.

Building the actual frame content by hand is tedious for anything more
than a couple of frames — the easiest path is a small one-off Python
script that generates the CP437 art programmatically and writes out
the finished `@ANIMSTART@...@ANIMEND@` block, rather than hand-editing
raw escape sequences frame by frame.
