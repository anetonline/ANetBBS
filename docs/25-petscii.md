# PETSCII (Commodore 64/128) terminal support

A dedicated rendering path for real C64/128 hardware and PETSCII
terminal emulators (SyncTERM's C64 mode, Novaterm, CCGMS, 64NIC+).
Completely separate from the ANSI telnet/SSH/rlogin path — PETSCII
isn't ANSI, so there's no shared cursor-addressing, no color codes,
no lightbar selectors. Everything here is plain text, numbered
menus, and single-key prompts, built by hand in
`anetbbs/features/petscii_ui.py`.

There is a matching player-facing wiki page (`petscii` slug) seeded
by `anetbbs/wiki/seed.py` — same content, aimed at players connecting
from a real C64 rather than sysops. Update both if you change how
this works.

## Connecting

Two dedicated ports, one per screen width — matching Synchronet's own
"40 Column PETSCII Support" / "80 Column PETSCII Support" convention.
Both are off by default; a sysop opts in per-width:

| Width | Port | Enable flag |
|-------|------|-------------|
| 40 columns | `PETSCII40_PORT` (default `6400`) | `PETSCII40_ENABLED` |
| 80 columns | `PETSCII80_PORT` (default `6401`) | `PETSCII80_ENABLED` |

Every connection on a PETSCII port *is* PETSCII, at that port's fixed
width, unconditionally — there's no telnet TTYPE auto-detection,
because real C64 clients mostly don't announce themselves usefully
that way. Users just pick the port matching their screen/emulator
setting. See `docs/INSTALL.md` §10 and `docs/PORTS.md` for the
enable/firewall steps.

Both listeners run inside the same unified `anetbbs.service` process
as telnet/SSH/rlogin (`anetbbs/main.py` starts one `PetsciiServer`
task per enabled width) — not a separate systemd unit.

## What's available

Reuses the same underlying data models (`Board`/`Post`,
`EchoArea`/`EchomailMessage`, `PrivateMessage`, `FileArea`/
`FileUpload`, `UserSession`, `User`) as the ANSI screens — never their
rendering code. Covered:

- **Message boards** — browse, read threads, post, reply
- **Echomail** — network-first picker (pick the network, then its
  areas — not one combined list across every joined network), read,
  compose/reply, XMODEM-free (message bodies only)
- **Private messages** — inbox, read, compose/reply
- **File areas** — browse, view extended description on demand (`E`),
  download over **XMODEM** (reuses `features/xfer.py`'s
  `send_file()` — same transfer code the ANSI side uses; PETSCII
  connections are plain telnet sockets, so no protocol difference)
- **Who's online**
- **Profile** — view and edit
- **Number Guessing** — the one built-in game offered here. Pure
  session I/O (guess a number, get higher/lower feedback), no cursor
  addressing at all, so it's the only game from the wider Game Center
  catalog that has a PETSCII-safe implementation
  (`features/games.py`'s `GameManager.play_number_guess()`, reused
  directly rather than reimplemented)
- **Logoff**

Deliberately absent (not shown, not marked "unavailable," just not
offered): door games, ANetCRAFT, DOOM/Duke3D, MRC chat, IRC — none of
those have a plain-text-only path, same reasoning that already
excludes Wall/ANSI-art screens from PETSCII sessions.

## Text rendering rules

- **ANSI-art bodies get stripped**, not garbled. File descriptions,
  echomail/PM/board bodies containing CP437/Unicode box-drawing art
  (common in `FILE_ID.DIZ`-style banners) collapse to spaces before
  display — the PETSCII encoder has no representation for those
  characters and would otherwise render walls of `?`.
- **Word-wrap, not hard-break.** All body text and status/prompt
  lines wrap at word boundaries to the session's actual width (40 or
  80), never mid-word.
- **Never fill a row to exactly the terminal width.** Real hardware
  auto-wraps a full-width line on its own, which eats a blank line if
  the software also tries to newline after it — every render budgets
  `width - 1`.
- **Pagination**: long lists/bodies page at a fixed line count with
  `M`=more, `B`=back (previous page), `Q`=quit-to-menu.

## Custom PETSCII menus (sysop-buildable)

Sysops can replace the hardcoded Phase 1 menu with fully custom
menus at **`/admin/petscii-menus/`** — a completely separate tree
from the ANSI custom-menu system (`BbsMenu`/`BbsMenuItem`), not a
shared one. Most ANSI action types (art, sixel, chat, most doors)
have no PETSCII equivalent, so a shared tree would mean items
silently missing on this side; a dedicated `PetsciiMenu`/
`PetsciiMenuItem` model pair gives full layout control instead.

- If no `PetsciiMenu` has `is_default=True`, PETSCII sessions fall
  back to the hardcoded Phase 1 menu unchanged — this feature is
  opt-in, not a replacement.
- Each menu item has a `hotkey`, `label`, `min_access`, `sort_order`,
  visibility flag, and an `action_type` — deliberately a much smaller
  set than the ANSI side, limited to what `petscii_ui.py` actually
  has a plain-text handler for:

  | action_type | Behavior |
  |---|---|
  | `goto` | Jump to another `PetsciiMenu` by name (`action_args` = target menu name) |
  | `boards` | Message boards |
  | `echo` | Echomail areas |
  | `pm` | Private messages |
  | `files` | File-area browsing |
  | `who` | Who's online |
  | `profile` | View own profile |
  | `games` | Number Guessing |
  | `logoff` | End the session |

- Admin routes: list (`/`), create (`/new`), edit (`/<id>/edit`),
  delete (`/<id>/delete`), add/edit/delete item, and spawn-submenu
  (`/<id>/spawn-submenu` — creates a new empty `PetsciiMenu` and a
  `goto` item pointing to it in one step, for building out a menu
  tree without leaving the editor).

## Real-hardware lessons baked into this code

Built and refined against real C64/128 hardware over several rounds
of live testing, not just terminal emulators. A few non-obvious
things this rendering path handles that a naive ANSI-style port
would get wrong:

- **Keyboard charset timing.** A real C64 keyboard's PETSCII byte
  code for a letter depends on which charset ROM is selected — a
  KERNAL keyboard-decode difference, not just display. The
  lowercase-charset switch must be sent *before* `session.start()`
  (before the login screen), or every password typed during login
  arrives silently case-flattened to uppercase and auth always fails
  — looks exactly like a broken user database if you don't know to
  look here.
- **Auto-wrap blank-line bug** — see "Text rendering rules" above.
- Column-width overflow in list rendering (echo area names, file
  listings) on both 40- and 80-column screens needed independent
  fixes — width and overhead-per-row don't scale linearly.

## See also

- `docs/PORTS.md` — port table
- `docs/INSTALL.md` §10 — enabling PETSCII40/PETSCII80
- `docs/03-menus.md` — the ANSI custom-menu system this deliberately
  does *not* share a tree with
- `docs/24-game-center.md` — the wider game catalog (Number Guessing
  is the only one also offered here)
