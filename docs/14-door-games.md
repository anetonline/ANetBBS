# Door games

ANetBBS runs several flavours of "door" — the BBS term for an
external program a caller can launch from the menu and interact with
through their terminal:

| Type                 | Source                  | Runtime                             |
|----------------------|-------------------------|-------------------------------------|
| `door_dos`           | DOS .EXE / .COM         | DOSBox (auto-detect staging/x/vanilla) + TCP nullmodem bridge to BNU FOSSIL. Snap-packaged DOSBox is auto-rejected. |
| `door_dosemu`        | DOS .EXE / .COM         | dosemu2, virtual COM1 (no FOSSIL) bridged to the caller's PTY. |
| `door_native`        | any Linux executable    | Direct fork inside a PTY. |
| `door_synchronet`    | Synchronet `.js`        | Real `jsexec` if installed, otherwise Node + our compat shim. See [`15-synchronet-compat.md`](15-synchronet-compat.md). |
| `door_mystic_mps`    | Mystic Pascal `.mps`    | `mplc` auto-compiles to `.mpx`, `mystic -y` runs it under a real Mystic account. |
| `door_mystic`        | Mystic Python `.mpy`    | Python with our `mystic_bbs` compat shim. |
| `door_rlogin`        | remote BBS              | Outbound rlogin TCP bridge to a Synchronet xtrn server / DoorParty / etc. The "door" lives on someone else's BBS. |
| `door_telnet`        | remote telnet server    | Outbound telnet TCP bridge (e.g. TWGS — Trade Wars Game Server). Same idea as `door_rlogin` but no pre-auth handshake; the remote handles login interactively. |
| `builtin_web`        | in-process              | Flask templates — for the built-in web mini-games. |
| `builtin_python`     | in-process               | Bundled Python game, no drop file or external process. Currently just ANetCRAFT — see below. |
| `door_dos_browser`   | DOS .ZIP bundle         | EmulatorJS + dosbox_pure core, runs entirely in the browser. No telnet/SSH — web only. |

All of them get added at **Admin → Subsystems → Door Games → Add Game**.

## Categories and submenu sections

Every game/door belongs to a category (**Admin → Games → Categories**),
shown as a header on the Door Games list (terminal) and lobby (web).
By default a category's games are listed inline under its header —
fine for a handful of doors, but once a category grows large it can
run off the bottom of a real terminal screen.

Flip **"Show as a submenu section"** on a category to collapse it
instead: the top-level door menu shows one selectable line for that
whole category (e.g. "Synchronet Doors → (17 doors)"), and picking it
opens a second screen listing just that category's own games, with
`B`/`Q` (terminal) or the browser back button (web) returning to the
parent menu. One level deep only — a submenu category's own games are
always shown flat there, no further nesting. Off by default; existing
categories/installs render exactly as before until a sysop opts one
in.

Ordering is flat and global across the *entire* menu — a game's own
**Sort Order** field controls its absolute position in the whole list
(lower numbers first), not just its position within its own category.
Category headers/sections are a side effect of walking that flat
order and noticing where the category changes, not the primary sort
key — so a sysop numbering every door 1-N as one list gets exactly
that ordering, regardless of which categories those doors happen to
be in.

The submenu screen supports the same file-based ANSI art override as
the top-level Door Games list — see
[`04-ansi-screens.md`](04-ansi-screens.md#slot-names-reference) for
the full slot-naming reference (`door_games_<category-slug>.ans`).

## ANetCRAFT — bundled Minecraft-inspired terminal game

`anetbbs/features/anetcraft.py` is ANetBBS's own 2D survival game —
mine blocks, craft tools, and explore a procedurally generated world
with ores, caves, trees, and water. It's a `builtin_python` door: no
external binary, drop file, or emulator involved, and it's seeded
`Active=True` by default (`anetbbs/web_app.py`), so it's playable at
`/games/` out of the box on every install. Each player gets their own
persistent world. It runs natively over SSH, telnet, or the built-in
web terminal.

**Not to be confused with ANetCRAFT-Door** — a completely separate,
standalone project (a C# .NET 8 door game) that happens to share a
similar name. The two are unrelated codebases; ANetCRAFT-Door is not
bundled with ANetBBS and isn't the game described in this section.

## Built-in web games — casino wallet economy

The `builtin_web` type covers several in-process casino-style mini-games
(blackjack, slots, video poker, hold'em). Instead of real money, each
of these shares a common **persistent wallet** system
(`WebGameWallet` in `anetbbs/models.py`, API in `anetbbs/web/games.py`):

- Each user gets one wallet row **per game** (`game_slug` +
  `user_id`), not one shared pool across all four games.
- **Starting balance** per game is configurable via `.env` / Admin →
  Settings: `CASINO_BLACKJACK_START` (default 500),
  `CASINO_SLOTS_START` (default 200), `CASINO_VIDEOPOKER_START`
  (default 200), `CASINO_HOLDEM_START` (default 1000).
- **Weekly reset**: the wallet tracks a `week_start` (the ISO week's
  Monday). Every time the wallet is loaded (`GET /games/<slug>/wallet`)
  or updated (`POST /games/<slug>/wallet`), if the stored `week_start`
  doesn't match the current week, the balance resets to that game's
  starting balance. This is a **lazy** reset — it happens the next
  time the user opens/plays the game after a Monday rolls over, not on
  a background schedule of its own.
- Going broke (`balance <= 0`) doesn't lock the user out — the wallet
  API reports a `resets` date (next Monday) alongside `broke: true` so
  the UI can tell the player when they'll get a fresh stake.
- `peak_balance` is tracked alongside the live balance (a high-water
  mark), separate from `starting_balance`.

## Achievements system

A lightweight badge/achievement engine lives in
`anetbbs/features/achievements.py` (models: `Achievement`,
`UserAchievement`) — not games-specific, but documented here for lack
of a more dedicated doc. Ten badges are seeded by default:

| code            | name              | earned for                          |
|-----------------|-------------------|--------------------------------------|
| `first_login`   | First Login       | logged in once                       |
| `login_30`      | 30-Day Caller     | 30 total logins                      |
| `first_post`    | First Post        | first board post                     |
| `post_10`       | Frequent Poster   | 10 board posts                       |
| `post_100`      | Centurion Poster  | 100 board posts                      |
| `first_pm`      | PM Sent           | first private message sent           |
| `first_echomail`| Echomail Hatchling| first outbound echomail message      |
| `first_netmail` | Netmail Pioneer   | first outbound netmail message       |
| `shouter`       | Town Crier        | 25 shoutbox posts                    |
| `veteran`       | Veteran           | account older than 1 year            |

`check_for_user()` re-runs **every** rule for a user and awards any
newly-qualifying badges, but it's only called from a handful of
trigger points: login (`anetbbs/web/auth.py`), board post
(`anetbbs/web/boards.py`), PM send (`anetbbs/web/pm.py`), and shoutbox
post (`anetbbs/web/shoutbox.py`). Sending echomail or netmail doesn't
itself trigger a check — the `first_echomail`/`first_netmail` badges
only actually get awarded the next time the user hits one of those
four trigger points afterward, not the instant they send the message.
Newly-earned badges are flashed to the user at login; earned badges
are listed on the user's own profile page
(`anetbbs/web/profile.py`/`templates/profile/view.html`). There's no
dedicated admin view for achievements — only the per-user profile
listing and an aggregate count on the stats page
(`anetbbs/web/stats.py`).

## Per-node scratch directories

Every active terminal node gets its own scratch directory under
`<install>/data/temp/nodeN/` (where N is 1..`BBS_NODES`). They're
auto-created on app boot. This is where the BBS writes the drop file
for each door launch, so two callers running the same door on
different nodes don't fight over `DOOR.SYS` or `DOOR32.SYS`.

You don't have to think about this — the per-node dir just exists. You
reference it via the `%P` token (Mystic style) or the runner's working
directory (Synchronet style) in the door's command line.

## Drop files

| Drop type     | What gets written           | Used by                                      |
|---------------|-----------------------------|----------------------------------------------|
| `door.sys`    | 52-line `DOOR.SYS`          | LORD, TradeWars, classic DOS doors           |
| `dorinfo`     | `DORINFO1.DEF`              | Some 90s DOS doors                           |
| `door32.sys`  | `DOOR32.SYS`                | Modern doors that want comm/sock handles     |
| `none`        | (skipped)                   | Self-contained doors that read no drop file  |

Set `Game.drop_file_path` to where the door expects to read it. The
path is %-token expanded (see below) — so the canonical pattern is
`%Pdoor32.sys` for "the door32.sys file in this caller's per-node
scratch dir".

## Token vocabulary

You can use these in `Game.executable_path`, `Game.working_directory`,
`Game.command_line_args`, and `Game.drop_file_path`. They're expanded
at door-launch time. Both Synchronet (lowercase) and Mystic (uppercase)
conventions are accepted — pick whichever matches the door you're
configuring.

### Synchronet `%`-codes

Reference: `wiki.synchro.net/ref:xtrn` and `xtrn.cnf` REPLACEMENT KEYWORDS.

| Token | Resolves to                                                        |
|-------|--------------------------------------------------------------------|
| `%a`  | User account / user ID number                                      |
| `%c`  | Connection description (`TELNET`)                                  |
| `%d`  | Baud rate (always `0` — local connection)                          |
| `%e`  | Node number (synonym of `%n`)                                      |
| `%f`  | Full path to the drop file the BBS just wrote                      |
| `%g`  | SSH session active flag (always `0` — not modelled, same as `%h`/`%y`/`%z` below) |
| `%h`  | Socket handle (always `0` — we use a PTY, not a real socket)       |
| `%i`  | Caller's IP address                                                |
| `%j`  | Data dir (`<install>/data/`)                                       |
| `%k`  | Ctrl dir                                                           |
| `%l`  | Lines per screen                                                   |
| `%m`  | Minutes left in this session                                       |
| `%n`  | Node number (`1`..`BBS_NODES`)                                     |
| `%o`  | Sysop name (`SYSOP_NAME` env var)                                  |
| `%p`  | User phone (always blank)                                          |
| `%r`  | User real name                                                     |
| `%s`  | BBS name (`BBS_NAME`)                                              |
| `%t`  | Time left in seconds                                               |
| `%u`  | Username / alias                                                   |
| `%w`  | Working directory                                                  |
| `%y`  | Current date (blank — fill in if a door needs it)                  |
| `%z`  | Time of day (blank)                                                |
| `%!`  | Synchronet exec dir                                                |
| `%%`  | A literal `%`                                                      |

### Mystic `%`-codes

Reference: Mystic's `MENU.MAC` and `MUTIL.PDF`.

| Token | Resolves to                                                        |
|-------|--------------------------------------------------------------------|
| `%P`  | Path to this node's temp dir, **with trailing `/`** (so `%Pfoo` = `<temp>/nodeN/foo`) |
| `%N`  | Node number                                                        |
| `%M`  | Minutes left                                                       |
| `%U`  | Username                                                           |
| `%T`  | Temp dir base (`<install>/data/temp/`)                             |
| `%H`  | User handle                                                        |
| `%R`  | User real name                                                     |
| `%E`  | User email                                                         |
| `%S`  | Security level (255 for sysops, 50 otherwise)                      |
| `%L`  | Location (from user profile)                                       |

Tokens are case-sensitive. `%P` (Mystic) and `%p` (Synchronet phone)
are different. Unknown tokens are passed through untouched, so a
literal `%X` in a path stays as `%X`.

## DOS emulator drive layout

`door_dos` (DOSBox) and `door_dosemu` (dosemu2) mount the same three
roles — game dir, FOSSIL bundle, per-node scratch — but at
**different drive letters**, because dosemu2's FreeDOS boot layer
(FDPP) reserves `C:`–`F:` for itself before any of your mounts land,
while DOSBox's `mount` command puts your drives exactly where you
tell it to.

| Role | `door_dos` (DOSBox) | `door_dosemu` (dosemu2) | Host path |
|------|----------------------|---------------------------|-----------|
| Game working dir  | `C:` | `H:` | `Game.working_directory` (or dir of `executable_path` if blank) |
| FOSSIL bundle      | `D:` | `G:` | `anetbbs/games/dos_runtime/` (read-only) |
| Per-node scratch   | `E:` | `I:` | `<install>/data/temp/nodeN/` — drop file goes here, isolated per node |

**Drop file path differs by emulator.** In **LORDCFG**, **TWCFG**,
etc., point the dropfile path at `E:\` for a DOSBox (`door_dos`) game,
or `I:\` for a dosemu2 (`door_dosemu`) game. The BBS writes the drop
file to the right host path automatically
(`<install>/data/temp/nodeN/DOOR.SYS`); each emulator's generated
autoexec/batch step copies it from the scratch drive into the game
drive before launch, so the door finds it in its own working
directory either way. Multi-node Just Works.

**FOSSIL loading also differs.** DOSBox always loads `BNU.COM` on
COM1 via its autoexec, unconditionally. dosemu2 only loads a FOSSIL
driver if the game's **"Requires FOSSIL Driver"** checkbox is on —
this is deliberate, not a bug: dosemu2's own virtual COM1 (what
TW2002-style doors expect) conflicts with a FOSSIL driver, so leave
that checkbox off unless your specific door genuinely needs one.

## LORD (Legend of the Red Dragon) — bundled, plays out of the box

As of v1.0b, **LORD ships pre-installed** as a `door_synchronet` game
(using Synchronet's JS port of the original DOS game). The game files
live at `anetbbs/games/sbbs_doors/lord/`, a `Game` row gets seeded
on first start, and the door is `Active=True` by default. Just open
`/games/` → **Legend of the Red Dragon** to play.

Under the hood: LORD runs on Node.js + our Synchronet compat shim
(`anetbbs/games/synchronet_compat.py`). No external `jsexec` binary
required for LORD specifically. If real Synchronet `jsexec` is present
on the host, `door_runner.py` auto-prefers it.

The worked example below is the **DOS** flavour of LORD (Borland-era
binary under DOSBox + TCP nullmodem). It's no longer the recommended
path — the bundled JS port renders identically, plays the same, and
needs no DOSBox — but the writeup is preserved for purists and as a
template for other DOS doors.

## Worked example: adding LORD (DOS, manual)

Legend of the Red Dragon — a DOS door that reads `DOOR.SYS` from a
sysop-configured directory.

1. Drop the LORD distribution somewhere on disk:

   ```
   /opt/anetbbs/doors/lord/
       LORD.EXE
       LORDCFG.EXE
       LORD.CFG
       *.DAT
       ...
   ```

2. Run `LORDCFG.EXE` once (under DOSBox manually) and set:

   - **(3) BBS Software** → `DOORSYS`
   - **(4) Path to the dropfile** → `E:\`  ← trailing backslash, points at our per-node mount
   - **(5) Fossil / Internal** → `Fossil`

   Save and quit LORDCFG. This writes the choices into `LORD.CFG`.

3. **Admin → Subsystems → Door Games → Add Game**:

   - Name: `Legend of the Red Dragon`
   - Slug: `lord`
   - Game type: `door_dos`
   - Executable: `/opt/anetbbs/doors/lord/LORD.EXE`
   - Working directory: `/opt/anetbbs/doors/lord` (so C: = the LORD dir)
   - Drop file type: `door.sys`
   - Drop file path: `%PDOOR.SYS` (writes to `<install>/data/temp/nodeN/DOOR.SYS`,
     visible inside DOSBox as `E:\DOOR.SYS`)
   - Max nodes: 4 (or however many simultaneous LORD games you want)

4. When a caller launches LORD, the BBS:

   - Allocates them a node (say node 3).
   - Writes `DOOR.SYS` to `<install>/data/temp/node3/DOOR.SYS`.
   - Boots DOSBox with C: = the LORD dir, D: = our FOSSIL dir,
     E: = `<install>/data/temp/node3/`.
   - autoexec loads BNU on COM1 and runs `LORD.EXE`.
   - LORD reads `E:\DOOR.SYS` (per its LORDCFG setting), takes the
     caller's name, plays.
   - When LORD exits, the node is released.

A second caller's launch on node 4 gets E: = `node4/`. Their DOOR.SYS
is in their node4 dir; they don't see node3's. Save state and lock
files (LORD.NOD) live there too — clean separation.

## Worked example: a Synchronet `.js` door

For a stock Synchronet door pack (e.g., a clone of Synchronet's
`oneliner.js`):

- Game type: `door_synchronet`
- Synchronet script path: `/opt/anetbbs/doors/oneliner/oneliner.js`
- Synchronet exec dir: `/opt/anetbbs/doors/oneliner` (where `*.cfg` and any
  `load()`-ed helpers live)

If you have real Synchronet installed at `/sbbs/`, the BBS auto-detects
`/sbbs/exec/jsexec` and uses it directly — full Synchronet API. If not,
it falls back to Node + our compat shim, which covers the most common
Synchronet APIs but is not 100% complete (see
[Synchronet door compat](#synchronet-door-compat) below).

## Worked example: a Mystic `.mps` door

- Game type: `door_mystic_mps`
- Mystic script path: `/opt/anetbbs/doors/yourdoor/yourdoor.mps`
- Working directory: `/opt/mystic` (or wherever your Mystic install
  lives — `mystic` needs to find its own `mystic.dat` here)
- Username / Password: `USERNAME_OR_@USER@ PASSWORD` — Mystic has no
  anonymous/no-login mode for scripts, so this is required, not
  optional. `@USER@` substitutes the real ANetBBS caller's username;
  a single shared account (a literal username, no `@USER@`) is the
  realistic default unless every caller already has a matching Mystic
  account.

When the door launches, the runner:

1. Looks for `mplc` (Mystic Pascal compiler). Comes from the optional
   Mystic install step in `install.sh` (or set `MYSTIC_MPLC_PATH`).
2. If the `.mps` is newer than its `.mpx` bytecode, recompiles.
3. Runs `mystic -u<username> -p<password> -y<yourdoor.mpx>` — `-y`
   (not `-x`, which isn't a real Mystic flag; it silently falls
   through to a full interactive local-login session instead of
   running your script) launches a compiled MPL script standalone
   under the given account.

If `mplc` isn't installed, the runner falls back to running an existing
`.mpx` next to the source. If neither is present, you get a clear
`Mystic .mps script not found` error in the logs.

## Synchronet door compat

We ship a Node.js compat shim that emulates ~270 functions/objects from
Synchronet's JS runtime — enough to run many stock doors that don't
need full BBS internals. Highlights:

- `bbs.sys_*` properties (sys_name, sys_op, etc.)
- `console.write/print/getstr/getkey/getkeys`, color codes, `home/cls`
- `system.exec`, `system.spawn`, dropfile env vars
- `js.exec_dir`, `js.global`, `load()` cache
- `File` class with most read/write/seek/iniGet*/iniSet* methods
- `dd_lightbar_menu` (for menu-driven doors)
- `mouse_getkey` (with arrow key parsing)

What's not covered (and where stock doors will fail):

- `msg_area`, `file_area` (door needs to query message bases)
- `xbase` / `recordfile` (Synchronet record-file primitives)
- `client.socket` direct access
- Most `bbs.*` actions that mutate BBS state (start/end conf,
  goto_xtrn, etc.)

When in doubt: if the door pack is a self-contained JS file with its
own data directory, it'll likely work. If it's something Synchronet
ships *with* Synchronet (like the message reader), it won't.

## In-browser DOS games (`door_dos_browser`)

`door_dos_browser` runs classic DOS games directly in the user's web
browser via **EmulatorJS** (the dosbox_pure libretro core). No DOSBox
install on the server, no telnet session — the game plays inside a
dedicated browser tab with sound, pointer-lock mouse, and fullscreen.

> These games are **web-only**. They do NOT appear in the telnet/SSH
> door menu.

### How it works

1. The game's DOS files are packaged into a ZIP bundle with a generated
   `dosbox.conf` and `launch.bat`.
2. The ZIP lives at `<install>/data/dos-games/<slug>.zip`.
3. When a web user clicks Play, the BBS serves the ZIP from
   `/games/dos-data/<slug>.zip` and opens a full-page frame at
   `/games/dos-frame/<slug>`.
4. The frame loads EmulatorJS from CDN
   (`https://cdn.emulatorjs.org/stable/data/`) with `EJS_core =
   'dosbox_pure'`, fetches the ZIP, and runs the game.
5. The frame needs `Cross-Origin-Opener-Policy: same-origin` and
   `Cross-Origin-Embedder-Policy: require-corp` headers for
   `SharedArrayBuffer` (multi-threaded dosbox_pure). The BBS sets
   these on the `/games/dos-frame/` route automatically.

### Exit / replay loop

dosbox_pure intentionally blocks the `exit` command from the top-level
DOS shell (the shell that reads the [autoexec] block). Games are
therefore wrapped in a `launch.bat` loop:

```bat
@echo off
:restart
GAME.EXE
echo.
echo  ==========================================
echo       G A M E   O V E R
echo  ==========================================
echo    Press any key to play again
echo    -- or --
echo    Close this tab to exit
echo  ==========================================
echo.
pause > nul
goto restart
```

When the game exits, the GAME OVER banner appears and the user can
press any key to restart or close the tab to quit. A pointer-lock
overlay in the browser also shows a "Close Tab" button when the
emulator releases the mouse cursor.

### Pre-installed games

Two classic shareware titles ship bundled with ANetBBS as of v1.0a2.60:

| Game | Slug | Notes |
|------|------|-------|
| DOOM (Shareware) | `doom` | id Software shareware — freely distributable |
| Duke Nukem 3D (Shareware) | `duke3d` | 3D Realms shareware — freely distributable |

Both are seeded as `door_dos_browser` Game rows on first install. If
the rows are missing (e.g. you upgraded from an earlier release),
add them manually via **Admin → Door Games → Add Game**:

| Field | DOOM | Duke Nukem 3D |
|-------|------|--------------|
| Name | `DOOM (Shareware)` | `Duke Nukem 3D (Shareware)` |
| Slug | `doom` | `duke3d` |
| Game Type | `door_dos_browser` | `door_dos_browser` |
| Web Game URL | `/games/dos-data/doom.zip` | `/games/dos-data/duke3d.zip` |

### Adding a new browser DOS game

**Step 1 — Bundle the game files**

Use `tools/prepare_dos_games.py` from the source tree:

```bash
python3 tools/prepare_dos_games.py \
    --source-dir "/path/to/GameDir" \
    --exe GAME.EXE \
    --output myslug \
    --name "My Game (Shareware)" \
    --exclude SETUP.EXE        # optional: strip out extra EXEs dosbox_pure would pick up
```

The tool packages all files plus a generated `dosbox.conf` +
`launch.bat` into `data/dos-games/myslug.zip`.

Key flags:

| Flag | Purpose |
|------|---------|
| `--source-dir` | Root directory of the DOS game |
| `--exe` | Primary EXE (used for validation only — must appear in the bundle) |
| `--output` | Output slug (also the ZIP filename without extension) |
| `--exclude` | One or more filenames to omit (case-insensitive), e.g. `SETUP.EXE INSTALL.EXE` |
| `--gus` | Add GUS/UltraSound emulation section to dosbox.conf — required for Duke Nukem 3D and other Build Engine games |
| `--dry-run` | List files that would be packaged without writing anything |
| `--out-dir` | Override output directory (default: `<install>/data/dos-games/`) |

> **Multiple EXEs in the bundle root?** dosbox_pure will show a
> selection menu instead of auto-starting the game. Use `--exclude`
> to remove extras (SETUP.EXE, INSTALL.EXE, etc.) so only one EXE
> remains at the root.

**Step 2 — Deploy the ZIP**

The web service runs as `anetbbs` (uid 998). SCP to `/tmp/` first,
then move with sudo:

```bash
scp data/dos-games/myslug.zip user@your-bbs-host:/tmp/
ssh user@your-bbs-host \
  "sudo mv /tmp/myslug.zip /opt/anetbbs/data/dos-games/ && \
   sudo chown anetbbs:anetbbs /opt/anetbbs/data/dos-games/myslug.zip"
```

**Step 3 — Add the game in admin**

**Admin → Door Games → Add Game**:

- Game Type: `In-Browser DOS Game (js-dos)`
- Web Game URL: `/games/dos-data/myslug.zip`

### Duke Nukem 3D — GUS / UltraSound requirement

Duke Nukem 3D v1.3D (and other Build Engine games from that era)
compiled their audiolib with a specific set of sound devices. The
only FX device that works under dosbox_pure is **FXDevice = 9**
(UltraSound/GUS). Devices 3, 5, and 13 are compiled out or silent.

GUS emulation in dosbox_pure requires `gusirq=7` (Duke3D refuses IRQ
values > 7) and the `ULTRASND` environment variable. The `--gus` flag
on `prepare_dos_games.py` adds both automatically:

```ini
[gus]
gus=true
gusrate=22050
gusbase=240
gusirq=7
gusdma=3
```

```bat
SET ULTRASND=240,3,3,7,7
```

The game's `DUKE3D.CFG` must also have `FXDevice = 9` and matching
IRQ/DMA values. The pre-bundled Duke3D ZIP ships with a pre-configured
`DUKE3D.CFG`; if you're bundling a fresh install, run `SETUP.EXE`
under real DOSBox first, set FXDevice to 9, then bundle with
`--exclude SETUP.EXE --gus`.

### Rebuild commands (reference)

```bash
# DOOM (Shareware)
python3 tools/prepare_dos_games.py \
  --source-dir "/path/to/Doom" \
  --exe DOOM.EXE \
  --output doom \
  --name "DOOM (Shareware)" \
  --exclude SETUP.EXE DWANGO.EXE IPXSETUP.EXE SERSETUP.EXE DM.EXE

# Duke Nukem 3D (Shareware)
python3 tools/prepare_dos_games.py \
  --source-dir "/path/to/DUKE3D" \
  --exe DUKE3D.EXE \
  --output duke3d \
  --name "Duke Nukem 3D (Shareware)" \
  --exclude SETUP.EXE SETMAIN.EXE COMMIT.EXE DN3DHELP.EXE \
  --gus
```

## Troubleshooting

- **`snap-confine is packaged without necessary permissions / cap_dac_override not found`** —
  your `dosbox` / `dosbox-staging` is a snap symlink. Snap-packaged
  binaries can't run from systemd services that grant only
  CAP_NET_BIND_SERVICE. Fix:
  ```
  sudo snap remove dosbox dosbox-staging dosbox-x   # whichever is installed
  sudo apt install dosbox                            # apt vanilla works for BBS doors
  # OR install dosbox-staging from a GitHub release tarball into /opt/dosbox-staging
  # OR change the Game type to door_dosemu and install dosemu2:
  #   Debian/Ubuntu: sudo apt install dosemu2
  #   Fedora/RHEL:   enable RPM Fusion or a COPR providing dosemu2, then dnf install dosemu2
  #   Arch:          AUR-only — yay -S dosemu2 (or paru)
  #   openSUSE:      check the Packman repo, or build from source
  #                  (https://github.com/dosemu2/dosemu2)
  ```
  As of v278, the BBS auto-rejects snap-packaged binaries up front
  with this exact message instead of letting you discover it the hard
  way mid-launch.

- **Door 404 / "executable not found"** — `executable_path` doesn't
  point at a real file. Check the path with `ls`. Tokens are expanded
  before the file check, so `%P` resolves to a real per-node dir only
  if `BBS_NODES >= node_number` (it always is at runtime, but try the
  command manually with `node1`).

- **Drop file written but door reads garbage** — wrong drop file type.
  LORD wants `door32.sys`. Older doors want `dorinfo` or `door.sys`.
  Check the door's docs.

- **`mplc` failures** — set `MYSTIC_MPLC_PATH` to the absolute path of
  a known-good `mplc`, or re-run `install.sh` and accept the Mystic
  download step. Errors are logged to `journalctl -u anetbbs-web` (web
  launches) or `bbs.log` (terminal launches).

- **DOSBox doors freeze on launch** — likely the TCP nullmodem bridge
  isn't binding. Check the per-node DOSBox config the runner generates
  in `<install>/data/temp/nodeN/dosbox.conf` — `serial1=nullmodem
  port:NNNN` should match what the bridge is listening on.

- **`door_dosemu` game crashes with `ERROR: MFS: failed to get xattrs
  for /opt/anetbbs/data/games/.../SOMEFILE.LOG, Numerical result out
  of range`** — dosemu2's MFS (host filesystem passthrough) layer
  needs the `user_xattr` mount option on whatever filesystem your DOS
  game data actually lives on. Most distros don't enable it by
  default. Fix is an `fstab` mount-option change, not an ANetBBS
  setting — find the mount backing your install dir (e.g. `/opt`) and
  add `user_xattr`:
  ```
  # /opt was on /dev/nvme1n1p1 in this example
  UUID=f99cf1b0-b2c7-4bdf-9d75-4e19b30bf558 /opt   ext4   defaults,user_xattr   0   2
  ```
  then `sudo mount -o remount /opt` (or reboot) and relaunch the door.

See also: [`05-external-programs.md`](05-external-programs.md) for
ad-hoc menu `exec` items (which use a different, simpler substitution
vocabulary — `{user}`, `{userid}`, `{dropdir}`).

## Worked example: A-Net Game Server (`door_rlogin`)

`door_rlogin` is different from the other types — there's no local
subprocess. The "door" is actually another BBS reachable via rlogin
that hosts a pile of games (Synchronet xtrn server, DoorParty,
A-Net Online's game server, etc.). Your BBS opens a TCP connection,
sends the rlogin handshake, and bridges bytes between the socket and
your user's terminal — they appear to teleport into the remote BBS's
door menu, no second login prompt.

Configuration uses three fields:

- **Server (host:port)** → `executable_path`, e.g.
  `game.a-net-online.lol:513`
- **User template + password [+ optional terminal]** →
  `command_line_args`, space-separated.
- **BBS Tag** (optional) → `rlogin_bbs_tag`, e.g. `ANET`. Kept as its
  own field rather than folded into `command_line_args` — see "Wire
  format quirk" below for why.

### Bundled by default

As of v1.0b2.28+, A-Net Online's game server ships pre-installed and
active, the same way LORD does — a `Game` row (slug
`a-net-game-server`) gets seeded automatically on first boot, with a
**randomly generated password and a randomly generated 4-character BBS
tag**. Each install gets its own — both are generated once, locally,
the first time your BBS boots, and never touched again afterward, so
they're private to your install and not something a stranger could
guess from reading the ANetBBS source.

The remote server doesn't validate the password against anything in
particular — it just needs to be present and hard to guess, since
every user on your BBS shares it to connect. The tag just needs to be
short and reasonably unique so the remote server can tell your BBS's
users apart from everyone else's. You're free to change either at
**Admin → Door Games → A-Net Game Server**.

### Adding A-Net Online's game server

**Admin → Door Games → Add Game**:

| Field | Value |
|-------|-------|
| Name | `A-Net Online Game Server` |
| Slug | `anet-games` |
| Game Type | `A-Net Game Server (rlogin)` |
| Server (host:port) | `game.a-net-online.lol:513` |
| User template + PASSWORD [+ TERMINAL] | `@USER@ YOUR_PASSWORD` |
| BBS Tag | `ANET` (pick your own short tag) |
| Max nodes | 4 |

Coordinate `YOUR_PASSWORD` and your BBS Tag with the remote sysop. The
tag namespaces inbound users from each visitor BBS.

### Direct-to-door

For a per-game menu entry that drops the user straight into a
specific door instead of the remote BBS's door menu, append a third
field to `command_line_args` (BBS Tag stays in its own field either way):

| Door | command_line_args |
|------|-------------------|
| LORD 4.08 | `@USER@ YOUR_PASSWORD xtrn=LORD408` |
| Assassin | `@USER@ YOUR_PASSWORD xtrn=ASSASSIN` |
| RPG menu | `@USER@ YOUR_PASSWORD xtrn_sec=RPG` |

The `xtrn=NAME` and `xtrn_sec=NAME` codes match Synchronet's xtrn.cnf
internal codes — get the list from the remote sysop's website.

### Token vocabulary in user template

- `@USER@` → BBS user's username
- `@ALIAS@` → display_name (falls back to username)
- `%U` / `%u` → username (Synchronet/Mystic convention)

### Wire format quirk

Synchronet's BBS-mode rlogin server uses INVERTED field order vs
RFC 1282 — password goes in the client-user-name slot (1st), the BBS
username goes in server-user-name (2nd). Our `RloginConnection`
already sends in this order; you don't need to think about it.

If a **BBS Tag** is set, it's appended to the client-user-name value as
`username-TAG` (hyphen-joined, no space) — the Mystic-style convention
ANetBBS presents its rlogin connections as. This is different from
Synchronet's own native `?rlogin -s-TAG` client convention (a space
plus `-s-` prefix), which only applies when a real Synchronet BBS
calls another real Synchronet BBS. `rlogin_bbs_tag` is a separate
field from `command_line_args` purely so you don't have to hand-type a
combined string — not because the wire format itself needs it.

If you're connecting to a non-Synchronet rlogin daemon and auth fails,
that's the most likely culprit (since standard Unix rlogind expects
client-name first). The fix is in `anetbbs/games/rlogin_bridge.py:RloginConnection.connect()`
— swap `self.password` and `self.client_user`.

### Abort + idle timeout

**Ctrl+]q** aborts the session immediately and returns to the BBS
menu, on every path. Idle-timeout behavior, however, is **not** the
same everywhere:

- **DOS doors** (`door_dos`/`door_dosemu`): 60-second idle timeout
  (configurable via `DOOR_IDLE_TIMEOUT`), auto-closes if no traffic
  flows in either direction.
- **Web rlogin play**: 300-second idle timeout, same `DOOR_IDLE_TIMEOUT`
  variable, different default for this path.
- **Terminal (telnet/SSH) rlogin play** — the path used for A-Net
  Online's own game server above: **no idle timeout at all**. A
  session that goes silent (remote end hangs without closing the
  socket) will sit there until the caller sends Ctrl+]q or the remote
  socket actually closes/EOFs. If you hit a door that can go quiet
  mid-session, budget for this — nothing will auto-recover it.
