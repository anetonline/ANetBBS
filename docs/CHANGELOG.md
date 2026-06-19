# ANetBBS Changelog

Versions are internal build numbers. Public releases are tagged
separately. Current release: **`v1.0a2.124`** (June 2026). Previous: `v1.0a2.123`.

## v1.0a2.124 — eventlet Python 3.13 piwheels fix; ANetIRC F2/PgUp/PgDn (June 2026)

Fix eventlet crash on Python 3.13 systems where piwheels ships eventlet 0.37.0
without the `start_joinable_thread` attribute (the wheel was compiled before the
patch merged). update.sh now greps the installed `eventlet/green/thread.py`
source file directly and force-rebuilds from source (`--no-binary eventlet`) if
the fix is absent. The rollback block no longer wipes application files for this
class of failure — it instead auto-fixes eventlet, retries the web service, and
prints a manual fix command if the retry still fails.

Fix ANetIRC F2 key exiting the IRC client instead of toggling the user list:
`ui_read_key()` only handled `\x1b[*` (CSI) sequences; F2 on xterm/SSH sends
`\x1bOQ` (SS3), which fell through to `return KEY_ESC` → exiting in startup mode.
Added `else if (s[0] == 'O')` branch: F1=`\x1bOP`, F2=`\x1bOQ`, F3=`\x1bOR`,
F4=`\x1bOS`. F2 now correctly maps to KEY_F2 → toggles the users panel.

Fix ANetIRC freeze/timing: PTY bridge `_input_pump` previously read one byte at
a time from the SSH session and wrote one byte at a time to the PTY. The asyncio
scheduler delay between bytes could exceed the C code's 100 ms VTIME, truncating
escape sequences mid-read. Changed to `session.read_raw(64)` (chunk reads) so
multi-byte sequences (`\x1bOQ`, `\x1b[5~`) arrive at the PTY in a single write.
Also replaced deprecated `asyncio.get_event_loop()` with `get_running_loop()`.

## v1.0a2.123 — MRC scrollback stability; arrow-key scroll; escape-seq drain (June 2026)

Fix `/scroll` scrollback drifting: when scrolled up, `_emit` now increments
`_scroll_offset` by the number of new lines added so the historical view stays
locked even as new messages arrive. Up/Down arrow keys now scroll the chat view
1 line (previously swallowed). Fix `/scroll up N` parsing (strip before isdigit).
Fix PgUp/PgDn escape sequences polluting the input buffer (drain trailing bytes).
Sending a message while scrolled auto-snaps to the live (bottom) view. Added
`_scroll_chat(delta)` helper; `/scroll 0` / `/scroll bottom` / `/scroll live`
all return to the live view. Updated docstring.

## v1.0a2.122 — MRC input visibility; /scroll; wall color; fast logon fixes (June 2026)

Fix MRC terminal input not showing while typing: `_redraw_chat_area` was clearing the
input row on every incoming message, erasing the SSH client's local echo. Fixed by not
touching the input row during chat area redraws — only `_draw_input_line()` (called per
keystroke) updates that row. Added `/scroll [n]` / `/scroll down [n]` / `/scroll 0`
command for scrolling through chat history; status bar shows `PAUSED+N` when scrolled.
Removed `/split on|off` command (not needed). Fixed graffiti wall color scheme not
updating after admin change: now reads `.env` directly at render time instead of using
a stale config class. Fixed fast logon prompt never appearing: same root cause — now
reads `.env` directly at login time.

## v1.0a2.121 — Terminal MRC client rewrite (June 2026)

Complete rewrite of `mrc_chat.py`. Fixed core scroll bug: word-wrapped messages
were writing past the DECSTBM scroll region into the input row, corrupting it.
Now each wrapped line emits as its own scroll event so the cursor never escapes
the scroll region. Added 60-second WebSocket keepalive ping (latency shown in
status bar). Added IAMHERE AWAY tracking after 10 min idle (matches ANetMRC
behaviour). New commands: /afk, /back, /broadcast, /ctcp, /roomconfig, /status.
Documented ANSI menu override slots in `docs/04-ansi-screens.md`.

## v1.0a2.120 — IRC presets admin; who's online fix; colored sysop/chat menus; screen-clear (June 2026)

Remove MRC-IRC bridge admin; add IRC Server Presets (`/admin/irc-presets`) with per-preset
name/server/port/SSL/nick/channels. Fix who's online: background heartbeat task every 2 min.
Colored IRC chat menu with Q-to-quit and screen-clear. Chat Systems menu screen-clear fixed.
All sysop terminal sub-menus now colorized and support ANSI overrides (sysop_menu/sysop_users/
sysop_boards/sysop_status.ans slots). New `IrcPreset` model (`irc_presets` table, auto-created).

## v1.0a2.119 — Security levels, graffiti wall, logon/logoff modules, fast logon (June 2026)

Security levels (0–255) on all content areas (boards, file areas, echomail, RSS).
New graffiti wall feature with pipe colors, pagination, and admin panel.
Logon/logoff module system — run wall/ANSI/shell/door at login or logout.
Fast logon option lets users skip intro modules. Menu `action_type = wall` added.

## v1.0a2.118 — Add: @BPS:NNNN@ throttled ANSI output; add @CLS@ clear-screen (June 2026)

`@BPS:NNNN@` in any ANSI screen file now throttles output to simulate the
specified modem baud rate (300–56000). 9600 bps = 960 bytes/sec; output is
sent in 48-byte chunks every 50 ms for smooth rendering. The goodbye, welcome,
newuser, and custom ANSI slots all support it. Also added `@CLS@` which
outputs `ESC[2J ESC[H` (clear screen + cursor home).

## v1.0a2.117 — Fix: @-code / display code substitution in ANSI screens (June 2026)

All Synchronet `@-codes` that resolve user identity (`@ALIAS@`, `@USER@`, `@NAME@`,
`@HANDLE@`, `@REAL@`, `@FIRST@`, `@EMAIL@`, `@LOCATION@`, `@CALLS@`, `@SECURITY@`)
were returning blank because `session.user` is a dict but the resolver used
`getattr()` instead of `.get()`. Fixed. Also: `display_name` and `location` added
to the user dict; `@SECURITY@` bool comparison fixed; `@VER@`/`@VERSION@` now
returns the real build version. Parametric codes like `@BPS:19200@` are now stripped
instead of printing as literal text.

## v1.0a2.116 — Add per-game FOSSIL driver checkbox; fix TW2002 black screen (June 2026)

Replaced FOSSIL driver auto-detection (introduced in v1.0a2.113) with an explicit
per-game checkbox in the Game admin: **Requires FOSSIL Driver (BNU.COM / X00.COM)**.
Check it for games like Zombie Slots / Mega Slots that ship a FOSSIL driver and
need it loaded. Leave it unchecked for TW2002 — dosemu2 virtual COM1 conflicts
with FOSSIL drivers and caused the black screen regression. The `needs_fossil_driver`
column is auto-migrated by `update.sh` Step 7.

## v1.0a2.115 — Fix: TW2002 black screen; fix game terminal right-side overflow (June 2026)

FOSSIL auto-detection (v1.0a2.113) loaded BNU.COM/FOSSIL.COM if present in the
game dir. TW2002 uses dosemu2 virtual COM1 which conflicts with any FOSSIL driver
— loading one caused a black screen. FOSSIL auto-load now skipped for TW2002.

Game terminal CSS: Bootstrap `.container` + `padding: 24px` limited content area
to ~700px on laptop viewports. 80-col terminal needs ~784px → rightmost ~20
columns overflowed outside the green border, showing game sidebars/stat panels
floating beside the terminal box. Fix: `max-width: 100%` override + `overflow:
hidden` clip on `#terminal-wrap`.

## v1.0a2.114 — Fix: web door-game terminal animation ghosting (June 2026)

Full-screen animation door games (slot machines, etc.) showed ghost artifacts
in the web game terminal — previous animation frames persisting visually while
new frames drew on top. SSH/telnet sessions were unaffected.

Root cause: xterm.js retains a 1000-line scrollback buffer by default. When a
door game fills the 25-row screen, the terminal scrolls, pushing the current
frame into the scrollback. The game then homes the cursor and redraws, but
the scrollback content remains visible above the viewport, creating doubled
headers/footers. Fix: `scrollback: 0` — DOS door games are 80×25 full-screen
apps that reuse the same grid via cursor addressing and need no scrollback.

## v1.0a2.113 — Fix: terminal DATABASE_URL; clean release tarball (June 2026)

Terminal service (`anetbbs/main.py`) hardcoded `anetbbs.db` instead of
respecting `DATABASE_URL` from `.env`, splitting terminal and web onto
different databases. Menu changes, password resets, and door configs made
via the web had no effect in terminal sessions. Fix: resolution order is now
`ANETBBS_DB_URL` → `DATABASE_URL` (from `.env`) → absolute-path fallback.

Release tarball now excludes `doors/dos/` (x86 DOS game binaries, sysop-
installed) and `data/ftp_root/` (created by installer). Tarball returns to
~29 MB. Pi/ARM installs no longer fail on oversized tarball with wrong-arch
binaries.

dosemu2 door launch now auto-creates the game working directory if missing.
`%P` in command_line_args now correctly maps to `I:\` (per-node scratch
drive) instead of a Linux path in the generated bat file. FOSSIL drivers
(BNU.COM etc.) are auto-loaded if present in the game directory.

## v1.0a2.112 — Fix: dosemu2 bat generic for non-TW2002 games; TW2002 ANSI via DORINFO1.DEF (June 2026)

**dosemu2 `_ANET.BAT` was TW2002-only for all door games.** `_build_dosemu_command()` was
injecting `SET TWNODE=1`, `MKDIR I:\NODE2`, and `COPY I:\DOOR.SYS I:\NODE2\DOOR.SYS` into
the generated batch file for **every** `door_dosemu` game. These lines are TW2002-specific
(node config environment variable + TW2002's per-node drop-file path). Fixed: the three
lines are now conditional on the game slug containing `"tw2002"`. Any other DOS game
launched via dosemu2 now gets a clean generic bat. Also added `DOOR32.SYS` and
`DORINFO1.DEF` copies to the generic path so games using those drop file types find them
in their working directory.

**TW2002 ANSI colors require DORINFO1.DEF, not DOOR.SYS.** When using DOOR.SYS, TW2002
detects ANSI by sending an `ESC[6n` cursor-position probe via COM1 and waiting for the
terminal reply. This round-trip through dosemu2's pts COM1 is timing-sensitive and
unreliable — TW2002 times out and falls back to ASCII. DORINFO1.DEF carries an explicit
ANSI flag (line 10 = `1`) with no probe needed. Set *Drop File Type* = `DORINFO1.DEF`
and *Drop File Path* = `%P` in the ANetBBS game admin. In TEDIT.EXE set *BBS Drop file
type* = `RBBS` (TW2002's name for the DORINFO1.DEF format), *I/O Type* = `Standard`,
*Comport* = `1`. Confirmed working with full ANSI color output.

## v1.0a2.111 — Fix: SQLite path on new installs; MRC defaults SSL+5001; security questions for password recovery (June 2026)

**Terminal "unable to open database file" on new installs fixed.** `main.py` now always
derives `DATABASE_URL` from its own `__file__` location (same as `serve.py` has done since
v1.0a2.70), rather than trusting whatever the EnvironmentFile supplies. An `.env` with a
stale or relative path can no longer cause every telnet/SSH session to fail at DB open time.
Custom Postgres or alternate SQLite paths still use `ANETBBS_DB_URL`.

**MRC bridge default connection changed to SSL port 5001.** Fresh `install.sh` runs now
generate `mrc/bridge/config.json` with `"mrc_port": 5001` and `"use_ssl": true`.
Bottomless Abyss MRC supports unencrypted (5000) and SSL (5001); SSL is the better default.
Existing installs are unaffected (their `config.json` is preserved on update).

**Password recovery via security questions.** Users can now set up to 3 security
question/answer pairs on their profile (`/profile/security-questions`). On the Forgot
Password page, accounts with security questions configured are offered a self-service
"Answer security question" path that issues a reset token directly — no sysop required.
Answers are stored hashed (werkzeug); matching is case- and whitespace-insensitive.
Accounts without security questions continue to use the existing sysop-copy-link flow.
New DB table: `user_security_answers` (auto-created by `db.create_all()`).

**Install wizard simplified.** The wizard no longer asks for a separate admin username —
the sysop display name chosen at the start is used as the account login, removing a
redundant prompt that confused fresh installs.

## v1.0a2.108 — Door categories + security levels (June 2026)

**Game categories (dynamic):** New `GameCategory` DB table replaces the hardcoded
category dropdown. Sysop manages categories at `/admin/games/categories` — add, rename,
reorder, delete. Default categories seeded on first start: Action, Classic DOS, Puzzle,
RPG, Space, Strategy, Other. Terminal door menu now groups games by category with
separator headers. Web lobby filter dropdown loads from DB.

**Min access level per game:** New `min_access_level` field on `Game` (0=all users,
10=regular, 50=power, 100=sysop). Set it in the game add/edit form. Terminal door menu
and web lobby both hide games the user's access level doesn't reach.

## v1.0a2.105 — Fix: upgrade runner eventlet RuntimeError (June 2026)

**upgrade runner:** "Second simultaneous read" RuntimeError from eventlet
during `proc.wait()` is now caught (non-fatal; upgrade runs in its own scope).

## v1.0a2.98 — Fix: DOS door games never connect on headless servers (June 2026)

**"DOSBox never connected; closing bridge" on headless servers fixed.** Three changes:
(1) Added `output=surface` to the generated dosbox-x `[sdl]` config when running
headless (`SDL_VIDEODRIVER=dummy`, no DISPLAY). Without this, dosbox-x may fail SDL
init silently and exit before running the autoexec — so it never loads BNU.COM or
connects to the TCP nullmodem bridge.
(2) DOSBox stdout/stderr now go to `logs/dosbox_<slug>_nodeN.log` instead of being
silently discarded via the PTY no-op lambda. Sysops can inspect this file to see any
SDL errors, missing-file messages, or BNU output from a failing door launch.
(3) The exact dosbox command and config file path are now logged at INFO level, visible
in `journalctl -u anetbbs-telnet` and the web admin log viewer.

## v1.0a2.97 — Fix: native Linux door games (RMDoor/DDPlus/FPC) carrier drop (June 2026)

**Instant carrier drop on `door_native` launch fixed.** Doors built with the
RMDoor / DDPlus Free Pascal toolkit use `fpSend`/`fpRecv` (socket syscalls) for
all I/O on Linux. ANetBBS was writing DOOR32.SYS with `CommType=1` (FOSSIL),
`CommNum=0` — `fpSend(0, …)` on a PTY fd returns `ENOTSOCK`, which set
`FCarrier=false` and triggered the hangup handler immediately on startup.
Fix: `door_native` games now get `CommType=2, CommNum=-1` (Mystic Linux STDIO
convention). RMDoor checks `ComNum==-1` on UNIX and switches to
`Write(StdOut)/ReadKey` (PTY-safe). Applies automatically; DOS doors unaffected.
Config for RMDoor doors: Drop File Type=`door32.sys`, Drop File Path=`%P`,
Command Line Args=`-D%f`.

## v1.0a2.96 — Web file area: ANSI/CP437 art renders correctly (June 2026)

**FILE_ID.DIZ ANSI art now renders as colored HTML** in the web file area.
Previously the expanded description showed raw escape codes (`□[0;40;37m…`).
Added `ansi_art` Jinja2 filter that calls the existing `_ansi_to_html` pipeline
directly on already-decoded Unicode strings (no latin-1 round-trip that would
corrupt block-drawing characters above U+00FF). Added `strip_ansi` filter for
the inline short description. `<pre>` block uses black background + `Courier New`
so block art columns align correctly.

## v1.0a2.95 — File browser page size fix for 80×25 terminals (June 2026)

**File browser page size reduced from 20 to 9.** Files with descriptions take
2 lines each; 20 items = 40+ lines, causing items 1-9 to scroll off-screen in a
standard 80×25 terminal. New page size: 9 items worst-case = 24 lines total.

## v1.0a2.94 — ZMODEM definitive fix, file desc view, batch downloads (June 2026)

**ZMODEM root cause fixed.** `asyncio.wait_for` with a 150ms timeout polled
`StreamReader.read()` dozens of times per second during transfers. Each cancelled
read left `StreamReader._waiter` in a pending state; after the transfer the stale
waiter caused `RuntimeError` in `read_line()`, which became "Menu action failed".
Replaced with `asyncio.ensure_future + asyncio.wait` — one persistent read task,
cancelled only when the transfer ends and awaited to completion so `_waiter` is
guaranteed clean. Fix applied to both `send_file` and `recv_file`.

**V# — view extended file description.** Type `V3` in the file browser to see the
full description for file 3. ANSI art (FILE_ID.DIZ with ESC sequences) renders
in CP437; plain-text descriptions word-wrap at 76 characters.

**Batch downloads: `1,3,5` or `1-5`.** Enter a comma list or range in the browser
to queue multiple downloads. Protocol selected once; files transfer sequentially.

**Area list count-column alignment fixed.** "0. General / Top-level" now pads
its name to 38 chars, matching the numbered rows.

**Web UI: expandable full descriptions.** File area page shows the first line
inline; files with multi-line or ANSI descriptions get a collapsible "full
description" `<pre>` block.

## v1.0a2.93 — File area: clear pages, ZMODEM fix attempt, FidoNet name cleanup (June 2026)

**ZMODEM/YMODEM/XMODEM transfers fixed.** `-Z` was an invalid lrzsz flag;
`sz` crashed immediately, the terminal sent binary handshake bytes, and they
corrupted the following `read_line()` call causing "Menu action failed". Fixed
flags: `--escape --binary` for send, `--escape` for receive. Added post-transfer
input drain to flush stale binary bytes.

**Clear screen on every page.** Area list and file browser now clear the terminal
before each draw so content from previous pages doesn't remain visible.

**FidoNet "0 !" prefix stripped from area names.** Pattern `^\d+\s*!\s*` removed
at display time.

## v1.0a2.92 — Terminal file areas read from disk (storage_path) (June 2026)

**Root cause found for 0 file counts.** Terminal queried only `FileUpload` +
`TicFile` DB tables. Most file areas store files on disk under `storage_path`
with no DB records — exactly how the web works. Terminal now uses the same
`_scan_area()` function the web uses. File counts and browser listings now
match the web view. Web URL for disk-based files now uses `/file-areas/` path.

## v1.0a2.91 — CP437 encoding fixes and file area 0-count bug (June 2026)

**No more ? characters in the terminal.** Em dashes, ellipsis, and checkmarks
throughout the terminal UI were not in CP437 and silently became `?`. All replaced
with ASCII equivalents (`-`, `>`, `[OK]`). Affects `bbs_ui.py`, `menu_engine.py`,
`ansi_ui.py`, `mrc_chat.py`, `dialout.py`, `xfer.py`, `archive_meta.py`.

**File areas showing 0 files fixed.** Files uploaded before `is_public` column
was added have `NULL` in that column; `filter_by(is_public=True)` missed them.
Terminal queries now use `isnot(False)`. Startup migration sets existing NULLs to 1.

## v1.0a2.90 — File area: ANSI colors, sysop visibility, FidoNet TicFile display (June 2026)

**ANSI colors throughout file area.** Area list and file browser now use full color
output: cyan headers, yellow item numbers, white filenames, gray counts, red/magenta
flags for inactive/sysop-only areas.

**Sysop sees all file areas.** Previously sysop was filtered to `is_active=True`
same as regular users. Sysop now sees all areas with status flags shown.

**TicFile records shown.** FidoNet hatched files (`TicFile`, `status='filed'`) are
now counted in area totals and shown in the file browser merged with `FileUpload`
records, sorted by date. Downloads use `stored_path`; falls back to web URL.

**lrzsz status notice** shown at bottom of area list.

## v1.0a2.89 — Full terminal file area: area browsing + ZMODEM/YMODEM/XMODEM (June 2026)

**Terminal file library rewritten.** `F` in the main menu now shows a proper
area browser (numbered list of all file areas with file counts), paginated file
listing (20/page with N/P navigation), and full protocol download/upload.

**ZMODEM / YMODEM / XMODEM download.** Selecting a file offers Z/Y/X protocol
choice. Server runs `sz`/`sb`/`sx` from `lrzsz`, piping raw binary through the
terminal connection with `--escape` for 8-bit-safe telnet transfer. Falls back
to web URL if lrzsz not installed.

**ZMODEM / YMODEM / XMODEM upload.** `U` in any area that allows user uploads
runs `rz`/`rb`/`rx`, saves the file, and registers it in the DB. FILE_ID.DIZ
auto-extracted for description if user leaves it blank.

**New `anetbbs/features/xfer.py`** — transfer engine. `available_protocols()`,
`send_file(session, path, proto)`, `recv_file(session, proto)`.

**Requirement:** `apt install lrzsz` on the server.

## v1.0a2.88 — Terminal: profile editing + file library URL lookup (June 2026)

**Terminal profile view wired up.** `Y. Your Profile` now offers inline E=Edit
Profile and W=Change Password options instead of "telnet edit next iteration."
`change_password` now uses `read_password()` (masked input) instead of plaintext.

**Terminal file library: real filenames + download URL.** Files now display their
`original_filename` instead of the storage UUID hash. Entering a file number
shows the full web download URL. "telnet download next iteration" placeholder removed.

## v1.0a2.87 — Remove internet email; calendar user submissions; password-reset admin panel (June 2026)

**Internet email / LMTP removed.** `anetbbs/mail/` module, `MAIL_ENABLED` config
key, LMTP thread in `main.py`, `email_enabled`/`email_local_part` user columns,
`InternetMail` DB model, and `aiosmtpd`/`aiosmtplib` deps are all removed.
No user-visible features were removed.

**Admin > Password Resets panel.** New admin page at `/admin/password-resets`
lists all pending (unused, unexpired) reset tokens with a Copy button for the
URL and a Revoke button. Sysop can now pass the link to the user out-of-band
without grepping the journal. The `forgot_password` flash message updated to
remove "email integration pending" wording.

**Calendar user event submissions.** Any logged-in user can now submit a
calendar event. Non-admin submissions land in a pending queue (`is_published=False`).
New `/calendar/pending` sysop review page with Approve/Reject buttons.
Admin submissions still go live immediately.

## v1.0a2.86 — Fix: door_dos path resolution + headless dosbox-x (June 2026)

**`door_dos` relative `executable_path` resolved against wrong directory.**
`_resolve_path()` was hardcoded to `/home/stingray/anetbbs` as the base for
relative paths, so any install at a different path got "executable_path does
not exist: /home/stingray/anetbbs/GAME.EXE". Fixed: base now derived from
`__file__` at runtime. Additionally, when `working_directory` is set, a
relative `executable_path` is now resolved against it — so sysops can use
`executable_path=TW2002.EXE` + `working_directory=/path/to/TW2002` as expected.

**`door_dos` dosbox-x failed with EPERM/SDL errors on headless servers.**
The child process now sets `SDL_VIDEODRIVER=dummy` and `SDL_AUDIODRIVER=dummy`
before exec'ing dosbox-x when no `DISPLAY` is set. Prevents SDL from trying to
open a display or audio device in containers and headless VPS environments.

## v1.0a2.85 — Fix: ANSI menu overlays double-rendering stock menus + missing screen clear (June 2026)

**Stock menu appeared beneath custom ANSI art.** When `load_menu_ansi()` found
a `.ans` file the ANSI bytes were written correctly, but the stock menu items
(items for-loop, footer box) were outside the `if/else` block and ran
unconditionally every time. Fixed by moving all stock-only output inside the
`else` branch. Affected menus: `door_games`, `game_center`, `chat`, `dialout`.

**Screen not cleared before ANSI write.** ANSI art was written over existing
terminal content without first sending an erase sequence, so leftover text
showed through wherever the art didn't fill every cell. Fixed by prepending
`\x1b[2J\x1b[H` (erase display + cursor home) to the ANSI bytes across all
five ANSI-override slots.

## v1.0a2.84 — Fix: custom ANSI menu files not loading (DATA_DIR path bug) (June 2026)

**`load_menu_ansi()` always returned None.** It used `os.environ.get('DATA_DIR')`
which is never written to `.env` — `DATA_DIR` is derived from `__file__` at
runtime in `config.py`, not stored in the environment. Fixed by computing the
data directory path from `__file__` directly (same logic as `config.py`).

## v1.0a2.83 — Feature: custom ANSI headers for all hard-coded menus/submenus (June 2026)

All hard-coded telnet/SSH/rlogin menus now support optional `.ans` file
overrides via `data/text/menus/<slot>.ans`. Supported slots: `game_center`,
`door_games`, `chat`, `irc_chat`, `dialout` (plus `main` and any BbsMenu name
already supported by the menu engine). If the file is absent the built-in
menu renders unchanged.

## v1.0a2.82 — Fix: custom ANSI menu screens showing garbled CP437 block characters (June 2026)

**Custom ANSI menu `.ans` files displayed wrong characters (Ü, ß, etc. instead
of block graphics).** The menu engine decoded the file as latin-1 (correct and
lossless), but then passed the string to `session.write()` which re-encodes as
CP437 — a non-symmetric transformation that mapped e.g. `▄` (0xDC) → Ü (0x9A)
and `▀` (0xDF) → ß/β (0xE1). Fixed by writing the ANSI content as raw
`latin-1`-encoded bytes via `session.writer.write()` directly, same as
`_show_ansi_screen()` already did.

## v1.0a2.81 — Fix: DOSBox-X detection (revised) + web auto-update tar failure on VPS hosts (June 2026)

**DOSBox-X detection fix revised.** The v1.0a2.80 fix (`SDL_VIDEODRIVER=dummy`)
was insufficient — dosbox-x on headless Debian/Ubuntu still hangs during the
subprocess version probe (audio init, not just display). Detection now simply
checks file existence and executable bit. If apt installed the binary, it's
the right arch; exec-format errors surface clearly at door launch time.

**Web auto-update "tar extract failed" on VPS hosts fixed.** On LXC containers
and some cloud VPS hosts, root cannot `chown` files to the uid baked into the
tarball (uid 1000 = developer's build machine). `run_upgrade.sh` now passes
`--no-same-owner` to tar, letting extracted files take the ownership of the
extracting process instead.

## v1.0a2.80 — Fix: duplicate Admin Caller Log entry + DOSBox-X detection on headless servers (June 2026)

**Duplicate "Caller Log" entry in Admin menu removed.** The Admin dropdown
showed two Caller Log links — a plain-text duplicate and the correct icon
version. The duplicate has been removed.

**DOSBox-X detection fixed on headless servers.** Running `dosbox-x --version`
without a display could hang on SDL display init, timing out the 5-second
runability check and making dosbox-x appear absent even when installed via
`apt install dosbox-x`. The detection subprocess now sets
`SDL_VIDEODRIVER=dummy` so SDL skips display init. The "No usable DOSBox"
error message now lists the paths that were actually tried, and `dosbox-x`
is now the first install recommendation.

## v1.0a2.79 — Fix: nginx immutable caching + MRC-optional update + web update completion (May 2026)

**nginx `Cache-Control: immutable` caused permanently-cached 404s.** The
`/static/` nginx block shipped with `public, immutable` which tells browsers
the URL will never change. When `anetbbs/static/mrc/client.js` didn't exist
(pre-v1.0a2.76), nginx 404'd the file *with* the immutable header — browsers
cached that 404 forever and never re-fetched the real file after upgrading.
Fixed: `immutable` removed, `expires 7d` removed, replaced with
`max-age=86400`. `update.sh` now auto-patches the running nginx config and
reloads nginx.

**`install.sh` nginx block fixed** — MRC bridge `proxy_pass` corrected from
`/mrcws` to `/ws`; MRC `auth_request` block added (prevents unauthenticated
WebSocket connections); `client_max_body_size 110m` added (nginx's 1m default
was rejecting avatar uploads and large file posts).

**`update.sh` no longer force-starts MRC bridge on servers that don't use
it.** Optional services (`anetbbs-mrc-bridge`, `anetbbs-finger`) are now only
restarted if they were running *before* the update. Servers where MRC is
stopped or failed skip MRC bridge restart entirely. `systemctl reset-failed`
added before each restart to clear start-limit-hit state.

**Web update UI completion detection fixed.** The "Check for Updates" web
upgrade was polling forever after a successful update: the `exit N` marker
was written by a Python thread that dies when gunicorn stops mid-update
(Step 3). After the new gunicorn restarted, the UI saw `running=false` but
no `exit N`, then polled for 30 minutes and gave up. The UI now also accepts
`[upgrade] upgrade to X complete` (written by `run_upgrade.sh`) as a
terminal condition.

## v1.0a2.78 — Fix: web MRC full UI (real index.html + client.js wired to Flask) (May 2026)

The web MRC chat UI was rendering from a 406-line stub template rather than
the real 2300-line `mrc/web/index.html` that runs on the live server. The stub
was missing themes, the user sidebar, macros, mentions panel, reconnection, pipe
color rendering, server selection, mobile layout, and the full slash-command UI.

- Replaced `anetbbs/templates/mrc/index.html` with the real `mrc/web/index.html`
- Three minimal Jinja2 injections added: `url_for` for client.js static path,
  `window.RETURN_TO_BBS_URL` for the back-to-BBS link, `{{ suggested_handle }}`
  pre-fills the handle input with the logged-in username
- `anetbbs/static/mrc/client.js` updated to the production version from
  `mrc/web/client.js` (reconnection, pipe colors, auto-rejoin)

## v1.0a2.77 — Fix: web MRC WebSocket connection error on HTTPS installs (May 2026)

Web MRC chat connected for the sysop (direct HTTP on port 5000) but failed for
all users on HTTPS installs with a WebSocket connection error. Two bugs:

**Bug 1 — wrong protocol (`ws://` vs `wss://`):** Flask sits behind nginx, so
`request.is_secure` is always False — Flask only sees plain HTTP from the
proxy. The template was generating `ws://` WebSocket URLs on HTTPS sites, which
browsers block as mixed content. Fixed by checking `X-Forwarded-Proto: https`
header in `mrc_web.py`.

**Bug 2 — nginx proxied to wrong bridge path:** The nginx template forwarded
`/mrcws` to `http://127.0.0.1:8080/mrcws`, but the bridge only registered its
WebSocket handler at `/ws`. Added `/mrcws` as an alias route in
`mrc/bridge/main.py` and corrected the nginx template to proxy to `/ws`.

## v1.0a2.76 — Fix: web MRC chat "MRCClient is not defined" (May 2026)

`anetbbs/static/mrc/client.js` was never created, so every web user opening
`/mrc/` got a JS `ReferenceError: MRCClient is not defined` and the chat page
was non-functional.

Added `anetbbs/static/mrc/client.js` with the full `MRCClient` WebSocket
class:
- `connect()` — opens the WebSocket to the bridge, resolves on open
- `joinRoom(handle, room)` — sends `join_room` to the bridge
- `sendMessage(text)` — sends `send_message` to the current room
- `sendServerCommand(cmd)` — sends `server_cmd` (slash commands)
- `leaveRoom()` — sends `leave_room`
- `disconnect()` — tears down the WebSocket cleanly
- 30-second keepalive ping so the bridge doesn't drop idle connections
- Tracks current room/handle from `joined` / `room_changed` / `handle_changed`
  events so callers don't need to pass the room on every message

## v1.0a2.75 — Raspberry Pi full install guide (May 2026)

Added `docs/INSTALL-PI.md` — a comprehensive guide for running ANetBBS on
Raspberry Pi 4/5, covering hardware selection, OS recommendations, DDNS setup,
port forwarding, Let's Encrypt SSL, moving `data/` to a USB SSD, service
management, door game compatibility, and a troubleshooting section covering
all known Pi-specific issues (SQLite path bug, CSRF cookie, disk space).
Linked from README.md documentation section.

## v1.0a2.74 — Fix: telnet/SSH "unable to open database file" on Pi / wizard installs (May 2026)

`main.py` (the `anetbbs` telnet/SSH entry point) was not pinning `DATABASE_URL`
before importing `anetbbs.config`. At import time, if `DATABASE_URL` was absent
from the environment — wizard installs wrote `SQLALCHEMY_DATABASE_URI` instead,
a key the config never reads — the fallback resolved `DATA_DIR` from
`config.py`'s location in the venv site-packages, which doesn't exist, causing
"sqlite3.OperationalError: unable to open database file" on every DB operation
in the terminal session. The web server was unaffected because `deploy/serve.py`
always pins `DATABASE_URL` from its own `__file__` location before any imports.

Fix: `main.py` now applies the same self-pinning logic at startup, before any
anetbbs imports. Also corrected `wizard.py` to write `DATABASE_URL` (not
`SQLALCHEMY_DATABASE_URI`) to `.env` so wizard-installed systems have the right
key going forward.

## v1.0a2.73 — ANSI file drop-in, bulletin word-wrap, screen-slot editor link, sysop name fix (May 2026)

**ANSI file drop-in system** (`data/text/`): sysops can now drop `.ans` files
into the install to override screens without touching the admin UI. Resolution
order: file first, then DB, then built-in fallback.
- `data/text/welcome.ans` — pre-login / logon screen
- `data/text/goodbye.ans` — logoff screen
- `data/text/newuser.ans` — new-user registration screen
- `data/text/menus/<name>.ans` — menu header ANSI (e.g. `main.ans`, `games.ans`)
Display codes (`@USER@`, `|BN`, etc.) are applied to file-based screens too.

**ANSI editor → screen slot link**: the ANSI editor now has an "Apply to Screen
Slot" card alongside "Apply to Menu". One click pushes the art directly into
the welcome/goodbye/newuser DB slot — no copy-paste required.

**Bulletin word-wrap**: bulletin text in the terminal pager now word-wraps to
the user's negotiated terminal width (NAWS) instead of silently truncating every
line at 78 characters.

**Sysop name as admin username default**: the installer now defaults the admin
login username to the sysop display name entered earlier, so sysops who type
"Firehawke" as their sysop name get "Firehawke" as the default admin account
name instead of "admin". `install.sh` also now writes `SYSOP_NAME` to `.env`
and asks for a sysop display name separately.

**Logged-out intro screen uses BBS_NAME**: the fallback login menu shown when no
custom welcome ANSI is configured now reads `BBS_NAME` from config instead of
always printing "Welcome to AnetBBS".

## v1.0a2.72 — Fix: login fails with CSRF error on HTTP-only installs (May 2026)

`ProductionConfig` hardcoded `SESSION_COOKIE_SECURE = True`. Browsers silently
discard Secure-flagged cookies on plain HTTP connections, so the CSRF token stored
in the session on the GET of `/auth/login` was never returned on the POST — Flask-WTF
saw no token and returned 400 Bad Request. Affected any install without nginx+TLS
(direct port 5000 access).

Fix: `SESSION_COOKIE_SECURE` is now an env-var flag (default `false`). `install.sh`
sets it to `true` only when SSL was enabled during setup. Upgrading installs that
lack the key in `.env` automatically get `false` via the env-var default.

## v1.0a2.71 — Fix: Trade Wars 2 player database silently wiped on player deletion (May 2026)

`json-client.js` treated `db.write(scope, key)` with no value as setting the key to
`undefined`. `JSON.stringify` omits undefined-valued keys, so the `players` array was
deleted from `tw2.json` the first time any player was removed from the game. Two call sites
triggered this: `DeletePlayer` (which flushes the modified players array) and the kill
handler (which flushes a modified team record). Universe data (`sectors`, `ports`,
`planets`, etc.) was unaffected because those writes always supply an explicit value.

Fix: `json-client.js` now treats a no-value `write(scope, key)` as "mark dirty and save",
preserving the in-place mutation already made to the cached reference. Added null guards in
`LoadPlayer`, `RankPlayers`, and `MatchPlayer` for resilience, and a warning log when a db
file exists but fails to parse.

## v1.0a2.70 — Fix: DATABASE_URL from EnvironmentFile not overridden correctly (May 2026)

`serve.py` guarded the DATABASE_URL override with `if not os.environ.get('DATABASE_URL')`.
Systemd's `EnvironmentFile` loads `.env` before the process starts, so DATABASE_URL is
always set (even to a stale/relative value from a preserved `.env`), and the conditional
never triggered. Fix: always set DATABASE_URL from `serve.py`'s own install dir path.
Operators who need Postgres or a non-default path can set `ANETBBS_DB_URL` instead.
Added diagnostic log lines (INSTALL_DIR, DATABASE_URL, DB file accessibility).

## v1.0a2.69 — Fix: web server database path wrong after non-editable pip install (May 2026)

`anetbbs-web` crashed with `sqlite3.OperationalError: unable to open database file`.
`config.py` computes `BASE_DIR = Path(__file__).parent.parent` at import time. With an
editable install (`pip install -e .`), `__file__` points to the source tree and `BASE_DIR`
is the install root. When pip falls back to a non-editable install, `__file__` is in
site-packages and `BASE_DIR` is wrong. The admin setup heredoc always worked because it
explicitly sets `sys.path` and `DATABASE_URL`. `deploy/serve.py` did not.

Fix: `deploy/serve.py` now inserts `INSTALL_DIR` (from its own `__file__`) at the front
of `sys.path` and sets `DATABASE_URL` to the absolute path if not already set — mirroring
what the install.sh admin setup has always done.

## v1.0a2.68 — Python 3.12 fix: patch threading.get_ident for greenlet safety (May 2026)

`eventlet.monkey_patch()` replaces `threading.get_ident` with a greenlet-based call to
`greenlet.getcurrent()`. During Python 3.12's import-time GC/logging cleanup, the hub
greenlet isn't fully initialised yet, so `getcurrent()` raises
`RuntimeError: greenlet is being finalized`. Python 3.12 changed the threading/GC startup
interaction, triggering a race that Python 3.10/3.11 didn't have.

Fix (in `deploy/serve.py`): after `eventlet.monkey_patch()`, wrap both
`threading.get_ident` and `eventlet.green.thread.get_ident` to catch `RuntimeError` and
fall back to `_thread.get_ident()` (real C-level thread ID). Safe because the crash only
occurs in finalizer paths, not during request handling. Also added `try/except` around
`create_app()` and `socketio.run()` to surface any other startup errors via the journal.

## v1.0a2.67 — Web server: drop gunicorn+eventlet, use eventlet native WSGI (May 2026)

gunicorn `--worker-class=eventlet` is broken on Python 3.12: fork()+greenlet
causes `RuntimeError: greenlet is being finalized` at worker boot (exit code 3).
Flask-SocketIO's own docs say not to use gunicorn+eventlet; use eventlet's
built-in WSGI server instead (no fork = no crash).

- Added `deploy/serve.py` — calls `socketio.run()` which uses eventlet.wsgi.server
- Updated `deploy/anetbbs-web.service`, `install.sh`, `update.sh` to use
  `python deploy/serve.py` instead of gunicorn
- Logs now go to journalctl instead of a separate gunicorn-access.log

## v1.0a2.66 — Install fix: Python 3.12 gunicorn/eventlet crash (May 2026)

`anetbbs-web` failed to start on Python 3.12 with gunicorn `WORKER_BOOT_ERROR`
(exit code 3). The eventlet worker crashed during init:

```
RuntimeError: greenlet is being finalized — eventlet/green/thread.py get_ident()
```

Python 3.12 changed threading/GC finalization in a way that breaks greenlet < 3.0.0.
`eventlet>=0.33.0` did not constrain greenlet's version, so pip could resolve
greenlet 2.x, which crashes at startup. Fix: explicit `greenlet>=3.0.0` +
`eventlet>=0.35.2` in setup.py.

## v1.0a2.65 — Install fix: PEP 440 version format (May 2026)

Fresh installs failed on Python 3.12+ with newer setuptools:
`packaging.version.InvalidVersion: Invalid version: '1.0a2.64'`.
The `1.0a2.NN` format is not valid PEP 440. This was a long-standing bug hidden by
`--quiet 2>/dev/null` suppressing pip output until v1.0a2.62 made errors visible.

- `setup.py` now uses `1.0a2.postNN` (PEP 440 post-release notation).
- Display version in admin panel / filenames stays `v1.0a2.NN` (from `__init__.py`).

## v1.0a2.64 — TIC file-echo ZIP binary + duplicate error spam (May 2026)

Fixed two BinkP/TIC processor bugs that caused `tqwinfo.zip` and similar
hatched file-echo ZIPs to never land in the inbound directory:

- `binkp.py`: inbound ZIPs were always treated as mail-bundle ZIPs and
  unpacked looking for FTS-0001 packets. If none found, the file was
  discarded rather than written to inbound for the TIC scanner. Fix: fall
  through to write as TIC binary when a ZIP contains no mail packets.

- `tic.py` `scan_inbound`: duplicate-skip check compared the `.tic` filename
  against `TicFile.filename` (which stores the binary name). They never
  matched, so every echomail poll spawned a fresh error row for the same TIC.
  Fix: peek at the TIC content to get the binary filename before the DB check.

## v1.0a2.63 — File gallery 500 fix (May 2026)

Fixed 500 error on `/files/` when any files had been uploaded. The template
used `f.user` / `f.user_id` but `FileUpload`'s relationship and FK column are
named `uploader` / `uploader_id`. Gallery was completely broken for anyone
who had uploaded files.

## v1.0a2.62 — Installer fix: pip failure on Python 3.13 / ARM64 (May 2026)

Fixed a cascade of misleading install errors on Python 3.13 / ARM64 (Pi 5,
Debian trixie). The installer was suppressing all `pip install` output with
`--quiet 2>/dev/null`, then blindly continuing into admin setup and service
starts against an empty venv — producing "ModuleNotFoundError: dotenv",
"gunicorn not found", "sqlalchemy not found" etc., all of which were
consequences of pip never running.

- Added `--prefer-binary` to `pip install` to use pre-built wheels on ARM64.
- Actual pip error output is now displayed when pip fails, so sysops can
  diagnose the root cause.
- Admin account setup is now skipped when pip failed (with a clear message).
- Service starts are now skipped when pip failed (with a clear message).

## v1.0a2.61 — Newsletter bugfix (May 2026)

Fixed 500 error when sending a newsletter. The admin newsletter route was
passing `content=body` when constructing `PrivateMessage` objects, but the
model field is `body`. No PMs were delivered; the newsletter record was
rolled back cleanly. Corrected to `body=body`.

## v1.0a2.60 — In-browser DOS games: DOOM + Duke Nukem 3D (May 2026)

New game type `door_dos_browser` — play classic DOS games directly in
the browser via EmulatorJS (dosbox_pure libretro core). No DOSBox
install on the server required; games run fully client-side.

**Two shareware titles now ship pre-bundled:**

- **DOOM (Shareware)** — id Software shareware, freely distributable.
  SoundBlaster audio via dosbox_pure's SB16 emulation (IRQ 7, DMA 1).
- **Duke Nukem 3D (Shareware)** — 3D Realms shareware, freely
  distributable. Uses GUS/UltraSound emulation (`--gus` flag) because
  the v1.3D binary only accepts FXDevice=9 (Build Engine audiolib
  devices 3, 5, 13 are compiled out or silent in this release).

**New files / changes:**

- `anetbbs/web/games.py` — `door_dos_browser` play route,
  `/games/dos-data/<file>` ZIP server, `/games/dos-frame/<slug>` with
  COOP/COEP headers for SharedArrayBuffer.
- `anetbbs/templates/games/play_jsdos.html` — landing page.
- `anetbbs/templates/games/play_jsdos_frame.html` — EmulatorJS frame
  with pointer-lock exit overlay and GAME OVER replay loop.
- `anetbbs/features/games.py` — terminal door menu now excludes
  `door_dos_browser` (and `builtin_web`) so browser-only games don't
  appear in the telnet/SSH game list.
- `tools/prepare_dos_games.py` — packages a DOS game directory into a
  dosbox_pure ZIP bundle. New flags: `--exclude` (remove extra EXEs so
  dosbox_pure auto-starts), `--gus` (GUS/UltraSound emulation for
  Build Engine games), `--dry-run`.
- `data/dos-games/doom.zip` and `data/dos-games/duke3d.zip` — the
  pre-built game bundles (included in the release tarball).
- `docs/14-door-games.md` — new `door_dos_browser` section with setup
  guide, flags reference, GUS notes, and rebuild commands.

**Deploy note for upgrades from v1.0.x:**

The `data/dos-games/` directory must be writable by the `anetbbs`
service user. SCP new ZIPs to `/tmp/` on the server and move with
`sudo`:

```bash
sudo mv /tmp/*.zip /opt/anetbbs/data/dos-games/
sudo chown anetbbs:anetbbs /opt/anetbbs/data/dos-games/*.zip
```

Add the two Game rows manually if they're missing (see
`docs/14-door-games.md` → Pre-installed games).

---

Versions are internal build numbers. Public releases are tagged
separately. Previous internal series: `v1.0a2.36` — alpha 2 (internal v287.35,
May 2026). `v1.0a` — alpha 1 (internal v196).

## v287.35 — FTPS: pyOpenSSL dep + soft-fail import (May 2026)

After v1.0a2.34 wired up the cert-perms and supplementary group, FTP
started crashing on the actual `TLS_FTPHandler` import:

```
ImportError: cannot import name 'TLS_FTPHandler' from 'pyftpdlib.handlers'
```

pyftpdlib only exposes `TLS_FTPHandler` when `pyOpenSSL` is importable
— it's a soft optional dep, but FTPS doesn't work without it. We
never declared it, so every install was a coin-flip depending on
what else happened to pull pyOpenSSL into the venv.

Two fixes:

1. **`pyopenssl>=24.0.0` added to `requirements.txt` + `setup.py`** —
   `update.sh`'s `pip install -e .` step now pulls it on every run.
2. **`build_server()` catches the ImportError** and falls back to
   plain FTP with a clear, actionable warning that names the package
   + the venv pip command, so a future drift can't silently disable
   FTPS again.

After deploying this release the FTP listener finally comes up on
:21 with TLS enabled — `AUTH TLS` upgrades succeed and the journal
shows `FTP: TLS enabled (/etc/letsencrypt/…)`.

## v287.34 — install.sh em-dash banner alignment (May 2026)

Sysop has been hand-patching `install.sh` for every release: two
boxed banner lines contain an em-dash (`—`, U+2014, 3 bytes UTF-8
but 1 terminal column) and the byte-counted whitespace layout was
1 column short of the right-edge `║`. Fixed in the source tree so
future releases don't need the manual fix-up:

- Line ~53: `║         ANetBBS — UNINSTALL                  ║`
- Line ~190: `║              ANetBBS — Installation Wizard                   ║`

Each one gets one extra trailing space before the closing `║`. The
sysop's notes are now stored in auto-memory so future agent
sessions remember the em-dash gotcha — anytime banner whitespace
gets regenerated.

## v287.33 — FTPS auto-enable + renewal-hook (May 2026)

`update.sh` now wires up FTPS end-to-end whenever
`FTP_TLS_CERTFILE` in `.env` points at `/etc/letsencrypt/…`:

1. **`ssl-cert` group**: created (system group) if missing.
2. **Service user → ssl-cert**: added via `usermod -aG` so the
   group membership exists on disk.
3. **letsencrypt perms normalised**: `chgrp -R ssl-cert
   /etc/letsencrypt/{live,archive}`, `chmod g+rX` on the dirs,
   `0640` on `privkey*.pem`, `0644` on the other `*.pem` files.
4. **`anetbbs.service` patched**: `SupplementaryGroups=ssl-cert`
   injected via `sed` (idempotent). Without this systemd's
   `User=stingray` doesn't pick up the new group at process start —
   systemd doesn't call `initgroups()` itself, supplementary groups
   have to be declared explicitly.
5. **Certbot renewal hook**:
   `/etc/letsencrypt/renewal-hooks/deploy/anetbbs-ssl-cert-perms.sh`
   gets installed. Certbot otherwise resets `archive/` to
   `0700 root:root` on every renewal, silently breaking FTPS
   overnight. The hook restores the ssl-cert group + perms
   after every successful cert renewal.

After deploying this release, restart `anetbbs` once so it picks
up the new supplementary group, then FTPS should work — `AUTH TLS`
upgrade on port 21 succeeds and the listener logs
`FTP: TLS enabled (/etc/letsencrypt/…)` instead of falling back to
plain.

The whole block is conditional on `FTP_TLS_CERTFILE` pointing at
`/etc/letsencrypt/…`. Sysops using other cert paths (self-signed
in `data/ssl/`, ACME from a different client) see no change.
Sysops with `FTP_TLS_CERTFILE` blank also see no change — FTP
keeps starting in plain mode.

## v287.32 — CLI logger crash fix (May 2026)

Hotfix on v1.0a2.32: `anetbbs --version` (and any CLI invocation
from a non-install CWD) was crashing at module-import time with
`PermissionError: '/tmp/bbs.log'`. `main.py` ran
`logging.basicConfig(... FileHandler('bbs.log'))` at the top of the
module, with `'bbs.log'` as a relative path — so running from /tmp
opened `/tmp/bbs.log`, which existed left over from an earlier
root-run and refused write to the service user.

Two changes:

- `bbs.log` is now resolved to an absolute path next to the
  installed package (or honours `ANETBBS_LOG_FILE=` if you want to
  point it elsewhere — journald-only setups can `=/dev/null`).
- The `FileHandler` is best-effort: if open fails (read-only fs,
  permission denied, noexec mount), we log only to stdout and write
  a one-line warning to stderr. systemd captures stdout regardless,
  so journald-side logging is unaffected.

This unblocks `anetbbs --version` / `anetbbs-web --version` from
every CWD.

## v287.31 — Quality-of-life follow-ups (May 2026)

Five field-driven fixes from one diagnostic session on bbs.a-net.fyi.

**FTP listener now survives a leftover root-owned tree.**
`_build_symlink_tree` was crashing the whole FTP thread on a single
un-`unlink()`-able symlink (left over from a previous run as a
different user — typical when migrating from root → service user).
Now caught with a warning that names the file + the exact `chown`
command to fix it, and the rest of the tree builds. Belt-and-braces:
`update.sh` `chown -R`s `data/ftp_root` to the service user on every
run, so the situation can't drift.

**SCC Restart buttons work again.** Past releases shipped
`anetbbs-web.service` with
`CapabilityBoundingSet=CAP_NET_BIND_SERVICE`. That bounding set
prevents the gunicorn worker's `sudo -n systemctl restart …` child
from re-acquiring `CAP_SETUID` / `CAP_SETGID` / `CAP_AUDIT_WRITE`
from its setuid-root bit, so every Restart click failed with
"sudo: unable to change to root gid" + "error initializing audit
plugin sudoers_audit". Removed the bounding set; `AmbientCapabilities`
alone is enough to bind MSP/SYSTAT on privileged ports. `update.sh`
strips the line from any pre-existing unit and `daemon-reload`s
before Step 8 picks up the change.

**BBS journal is quiet during SCC probes.** v1.0a2.31 killed the big
session-loop tracebacks but the per-call `print("Error sending
telnet command: …")` / `print("Write error: …")` one-liners were
still hitting stderr → systemd journal on every probe. Now caught as
expected `BrokenPipeError` / `ConnectionResetError` /
`ConnectionAbortedError` and swallowed; anything else is logged at
`DEBUG`. Also turned `asyncssh`'s logger down to `ERROR` so its own
"socket.send() raised exception" warnings stop multiplying.

**Username validation now matches the web form.** Terminal
registration was using `str.isalnum()` so "Dr Test" came back
invalid and the user had to type "drtest" — but the web form has
never been that strict. Aligned the two: 3–80 chars, must start
with a letter or digit, allow spaces / `.` / `_` / `-` / `'` in the
rest. Whitespace runs collapse, leading/trailing whitespace strips.

**Version is now visible everywhere.**

- New `anetbbs/version.py` reads `VERSION` once at import time.
- `anetbbs --version` and `anetbbs-web --version` print and exit.
- Every web page footer now shows the running version next to the
  copyright. (`{{ anetbbs_version }}` is exposed via a context
  processor, so any template can use it.)

## v287.30 — FTP TLS soft-fail + SCC probe cleanup (May 2026)

Three follow-ups from the v1.0a2.30 field test on bbs.a-net.fyi.

**FTP TLS now soft-fails instead of crashing the listener.** The unit
referenced `/etc/letsencrypt/live/bbs.a-net.fyi/{fullchain,privkey}.pem`
but the `archive/` directory was 0700 root-only, so the service user
couldn't actually read the cert. The previous check used
`os.path.exists()` — which returns True for an unreadable file — so
`TLS_FTPHandler` initialized, then crashed during SSL-context build,
killing the whole FTP thread silently. New code probes both files
with `os.access(R_OK)` + an `open()` round-trip, and if either fails
logs a clear warning ("add this user to the ssl-cert group OR copy
the cert to a readable path") and starts plain FTP instead.

**FTP thread crashes now hit the journal.** `anetbbs/main.py` only
caught exceptions from `thread.start()`, so any error inside the
thread's `run()` was lost. The wrapper now `logger.exception`s with
the most-likely causes inline — sysop won't have to ask why FTP is
silent again.

**BBS sessions no longer dump BrokenPipeError stacks during SCC port
probes.** The `/admin/control/` page probes telnet/SSH/rlogin every
few seconds. Each probe greets the protocol then closes, so the
BBS's first `writer.drain()` raised `BrokenPipeError` and the
session loop dumped a 40-line traceback for every probe. `start()`
now treats `BrokenPipeError` / `ConnectionResetError` /
`ConnectionAbortedError` the same way it treats `CarrierLost` —
silent unwind. Also removed a redundant `writer.drain()` from
`clear_screen()` (it ran *after* `self.write()` had already drained
and swallowed the error — so the second one bubbled).

**SCC probe results are cached for 4 s.** Opening N admin tabs (or
multi-poll dashboards) no longer multiplies probe load on the BBS.
First call per (host, port, proto) actually probes; subsequent calls
within 4 s read from an in-memory dict. With the BBS-side noise
suppression above, even uncached probes are now harmless — the cache
is just hygiene.

## v287.29 — SCC: surface failing listener + FTP cap fix (May 2026)

Field report on v1.0a2.29: SCC banner said "1 Listener problems" but
the sysop couldn't tell *which* port was failing — the only signal
was a colored dot on the chip, easy to miss especially in a text
dump or screen reader.

**Root cause (FTP).** `anetbbs.service` is the unit that owns
telnet / SSH / rlogin / FTP / LMTP. Telnet 2233, SSH 2234, rlogin
5132 are all unprivileged ports — they bind fine as the `stingray`
user. FTP **21 is privileged** and the unit didn't grant
`CAP_NET_BIND_SERVICE`, so the FTP listener silently failed to bind
on every startup. The other listeners with privileged ports
(anetbbs-web's MSP:18, anetbbs-finger's :79) already had the cap on
their respective units; this one didn't.

**Update.sh patches the live unit in place.** Adding the cap to the
template alone wouldn't help any existing install — the template
only runs on first install. The new logic detects an existing
`anetbbs.service` missing the cap, surgically `sed`-injects the
two lines after `EnvironmentFile=`, keeps a `.bak` backup, runs
`systemctl daemon-reload`, and lets Step 8 pick up the new caps on
restart. Sysop customizations elsewhere in the unit survive.

**UI: named listener-status line per card.** Every service card now
shows an explicit text row above the port chips:

- All listeners up → green check `All listeners up (4/4)`.
- One or more down → amber/red triangle `3/4 listening — down: FTP:21`.

So the sysop sees the failing listener by name without hovering for
a tooltip, and the colored-dot port chips remain as the visual quick
reference.

## v287.28 — Release Downloads: rename + move to Files dropdown (May 2026)

Sysop polish on the new /downloads/ section:

- Renamed everywhere from **"Downloads"** to **"Release Downloads"**:
  page title, hero H1, and the nav-bar item. Makes intent clearer
  alongside the existing File Gallery / File Areas / File Shares.
- Moved the nav entry from the **Tools** / More dropdown into the
  **Files** dropdown, where it sits with the other file-serving
  features. Added a divider above it so it visually groups separately
  from the user-uploaded-file features.

The on-disk directory path (`{{ base_dir }}`) shown in the hero is
already gated by `current_user.is_admin` — non-sysop visitors never
see it. No change needed there.

## v287.27 — SCC: sudoers path mismatch + sudo-free reads (May 2026)

Field report from v1.0a2.27: every service card on `/admin/control/`
showed **PID `—`** and **state `unknown`**, even though every service
was actually running and the live CPU / RAM / threads numbers were
populated. Banner said "0/4 Services up".

Root cause was a path mismatch between sudo's `secure_path` and the
sudoers rule. On Ubuntu 18.04+ sudo resolves `systemctl` to
`/usr/bin/systemctl`, but the rule we shipped said `/bin/systemctl` —
sudo compares the resolved path *literally* against the rule (it
deliberately does **not** follow the `/bin -> /usr/bin` symlink, for
security). The rule never matched → every read returned "permission
denied" → the panel fell through to its `unknown` defaults. The
metrics sampler kept working because it shells out to plain
`systemctl show` without sudo (which any unprivileged user can do).

Two fixes:

1. **`anetbbs/web/control.py` split `_systemctl()` into
   `_systemctl_read()` and `_systemctl_change()`.** The read variant
   never invokes sudo — `systemctl show / status` and `journalctl`
   work for any user. Only `start / stop / restart / reload` still
   need sudo. This makes the panel correct regardless of which
   `systemctl` path sudo resolves to.
2. **`deploy/sudoers.anetbbs` now lists both `/bin/systemctl` and
   `/usr/bin/systemctl` paths**, wrapped in a `Cmnd_Alias` to keep
   the file readable. Future Ubuntu / Debian / Fedora variations
   that pick either path are covered.

Side benefit: the "permission denied" toast on Restart now includes
sudo's stderr verbatim, so future path-mismatch problems surface
immediately instead of looking like a silent no-op.

## v287.26 — SCC Phase 2 graphs + hub auto-self-register (May 2026)

**Service Control Center — live graphs (Phase 2).**

- New `anetbbs/web/metrics.py` — single daemon thread in the gunicorn
  worker that samples each known systemd unit's MainPID every 2 s and
  stores the last 150 values (5 min) of CPU %, RSS in MB, and thread
  count in a per-(unit, metric) ring buffer. Reads /proc via psutil —
  no privileges, no sudo. Rebuilds the psutil.Process handle when
  MainPID changes so cpu_percent baselines reset cleanly across
  service restarts.
- New endpoint `/admin/control/metrics.json` returns a JSON-serializable
  snapshot of every ring buffer. Soft-fails to `available: false` if
  psutil isn't installed, and the front-end shows a banner telling
  the sysop to run `update.sh` instead of a broken chart.
- New endpoint `/admin/control/connections.json` rolls up per-protocol
  connection counts (web / telnet / SSH / rlogin) from NodeActivity +
  UserSession over the last 5 min.
- **Front-end:** Chart.js 4.4 loaded only on the control page.
  - **Per-card sparkline** of CPU % over the last 5 min, colored per
    unit and matched against the aggregate charts.
  - **Aggregate dashboard:** two charts at the top of the page —
    "CPU % per service" (overlapping lines) and "Memory (MB)"
    (stacked area). Auto-refresh every 2 s.
  - **Banner connection chips** for web / telnet / SSH / rlogin
    counts, plus a new "Active connections" total tile.
  - **Live metric row** in every card showing the latest CPU %, RAM
    MB, and thread count alongside the existing PID + uptime.
- Front-end summary counts (services-up, listener-problems) are now
  recomputed from the live `/status.json` payload on every refresh,
  so the banner stays correct after a restart that flips a unit's
  state without a page reload.

**Federation hub — auto-self-register.**

New module `anetbbs/msp/hub_self_register.py`. When
`REGISTRY_MODE_ENABLED=true`, the hub seeds its OWN `RegistryEntry`
row on startup (pre-verified, pre-approved, listed) and a daemon
thread refreshes `last_heartbeat_at` every 6 hours so the entry
never trips the 48-hour stale cutoff. Without this, the hub's own
`/anetbbs.lst` was empty of itself, so `/imsg/directory` never saw
the hub's own BBS on the directory pull — only the manually-pinned
"Your BBS" card from v1.0a2.26 surfaced it.

New config key (sensible default in code, not in `.env.example`):
- `REGISTRY_HUB_SELF_HEARTBEAT_SEC` — default 21 600 (6 h).

**Public Downloads page (`/downloads/`).**

Sysops can now drop a release tarball into a designated directory
and it auto-appears on `/downloads/` — no copy to `personal_pages/`,
no manual link plumbing. Scans `DOWNLOADS_DIR` (default
`{INSTALL_DIR}/data/releases`) on every (cached) page load, sorts
newest-first, shows filename + size + mtime + MIME type, with a
Download button and a SHA-256 sidecar button per file.

Defense in depth:

- Filename whitelist by extension (`DOWNLOADS_EXTENSIONS`, default
  covers archives, ISOs, checksum sidecars, and BBS-era text drops).
- Filename regex blocks any name containing `..` or other path
  separators.
- `realpath()` check on the resolved file path before serving —
  symlinks and traversal can't reach outside the configured dir.
- No subdirectory listing or recursion.

SHA-256 sidecars (`<file>.sha256`) are generated on demand the first
time they're requested and cached on disk next to the file, so
repeat hits don't re-hash multi-GB ISOs.

Three new config keys (defaults sensible — `.env.example` updated):

- `DOWNLOADS_ENABLED` (default `true`)
- `DOWNLOADS_DIR` (default `{INSTALL_DIR}/data/releases`)
- `DOWNLOADS_EXTENSIONS` (default
  `tar.gz,tgz,zip,7z,bz2,xz,rar,iso,img,asc,sig,sha256,md5,txt,nfo,diz`)

Nav: added a "Downloads" item in the **More** dropdown when enabled.

**Dependencies.**

Added `psutil>=5.9.0` to `requirements.txt` and `setup.py`. Also
backfilled `aiosmtpd` + `aiosmtplib` into `setup.py` (they were in
`requirements.txt` only, so fresh `pip install -e .` installs
missed them).

## v287.25 — Sysop Service Control Center, Phase 1 (May 2026)

Pivoted the v1.0a3 roadmap: Email is on the back burner; the headline
feature is now a proper Service Control Center. Phase 1 ships the
foundation; Phase 2 (live graphs, psutil-driven CPU/memory ring buffer)
and Phase 3 (live log tail, threshold alerts) land in the next two
releases.

**What changed in Phase 1:**

- **Stale unit list fixed.** `anetbbs/web/control.py::KNOWN_UNITS` was
  still listing the pre-merge `anetbbs-telnet` / `anetbbs-ssh` /
  `anetbbs-rlogin` units — the panel showed "unknown" for half the
  service tree and Restart buttons silently no-op'd because sudoers
  matched names that no longer exist. KNOWN_UNITS now matches the
  current four: `anetbbs-web`, `anetbbs` (unified terminal),
  `anetbbs-mrc-bridge`, `anetbbs-finger`.

- **Per-listener TCP/UDP probes.** Each unit declares its associated
  ports; the panel probes them independently of systemctl, so the
  sysop can tell a healthy process from one where the listener
  crashed. TCP via `socket.create_connection`; UDP via `/proc/net/udp`
  scan (UDP can't be probed by `connect()` alone).

- **New `/admin/control/status.json` endpoint.** Drives a 5-second
  live refresh of every card without a full-page reload.

- **Card-grid UI.** Dark professional layout, status pill per service,
  per-port chips with green/red dots, restart/stop/start + log
  buttons. Replaces the cramped table-only layout.

- **Journal modal + .txt download.** "Logs" opens an in-page modal
  with a line-count selector (100 / 500 / 2 000 / 5 000). Each card
  also gets a one-click `.txt` download of the last 2 000 lines for
  bug reports.

- **sudoers auto-refresh.** `update.sh` now rewrites
  `/etc/sudoers.d/anetbbs` on every run, substituting the live service
  user into the canonical template. Past releases shipped a sudoers
  file with the pre-merge unit names — fresh installs got Restart
  buttons that 403'd. Templated and `visudo`-validated before swap so
  a syntax error can't lock the sysop out.

**Inter-BBS Directory: "Your BBS" pin.**

Sysops reported their own BBS not showing on `/imsg/directory` — the
directory only renders rows that exist in `BbsDirectoryEntry` (pulled
from Vertrauen's `sbbsimsg.lst` + the federation hub's `anetbbs.lst`).
For your own BBS to appear there it needs an approved `RegistryEntry`
on the hub. The hub at `bbs.a-net.fyi` hadn't yet auto-self-registered
its own row.

Quick UX fix: a pinned "Your BBS" card at the top of the directory
that's populated from local config (`BBS_NAME`, `BBS_DOMAIN`,
`SYSOP_NAME`, `BBS_LOCATION`, `MSP_PORT`, `SYSTAT_PORT`) — always
visible, with a status badge showing whether the BBS is `Listed` /
`Pending` / `Not registered` on the federation hub. Independent of
registry state, so it works on day-1 installs that haven't gone
through self-registration yet.

## v287.24 — Terminal: carrier-drop spin + door-exit menu loop (May 2026)

Two related telnet/SSH bugs that both showed up as the same symptom —
a tight redraw loop that pinned a CPU core:

1. **Carrier drop at any menu spiked CPU.** `read_key` and `read_line`
   returned `''` for both "user pressed bare Enter" and "transport went
   away." The menu engine's `if not choice: continue` then redrew the
   menu and immediately re-prompted against an EOF stream — infinite
   loop. Now the four read primitives (`read_raw`, `read_key`,
   `read_line`, `read_password`) raise a new `CarrierLost` exception
   on EOF and on the idle-timeout path. `session.start()` and
   `menu_engine.run_menu` catch it and unwind cleanly; bare-Enter still
   returns `''` so menu redraw semantics are unchanged.

2. **Game menu looped forever after exiting any door (LORD, etc.).**
   `door_runner` cancelled its input/output pumps with `t.cancel()` but
   never `await`ed them. The in-pump's pending `session.reader.read(1)`
   was still registered as the StreamReader's waiter when the
   post-game `Press Enter` prompt fired — the two collided and the
   reader returned `b''` on every subsequent read, which then tripped
   the same EOF-spin as bug #1 in the game menu. Now all three door
   paths (`play_door_game_telnet`, `play_rlogin_telnet`,
   `play_dos_game_telnet`) drain their pumps via
   `asyncio.gather(..., return_exceptions=True)` before the post-game
   prompt. Also switched the prompts from raw `session.reader.readline()`
   to the wrapped `session.read_line()` so leftover telnet IAC bytes
   from the door don't choke the prompt terminator.

The number-guessing game was unaffected — no door pump, so the
StreamReader was never put in a wedged state.

## v287.23 — Echomail import: PATH-loop misfire fix (May 2026)

After v287.22 fixed the transaction rollback, 25k messages still didn't
land — only ~2,300 made it. The log finally surfaced the culprit:

> INFO — Echomail loop: dropping msg with our addr 3/231 in PATH

The previous loop check rejected ANY inbound message whose PATH kludge
contained our address. But per FTS-0004, the sending tosser
**correctly** appends the destination address to PATH right before
shipping. So Mystic was putting `1337:3/231` (us) into PATH on every
single message destined for us — and our check dropped the entire
feed as a "loop."

Real echomail loop detection happens via:
- `msg_id` deduplication (per-area unique constraint) — covers the
  "same message arrived twice" case.
- SEEN-BY checks during **forwarding** decisions — covers the
  "don't relay back to a node that's already seen this" case.

Neither needs a PATH-based import filter. Removed the check entirely.

After this + the v287.22 savepoint fix + the v287.21 BinkP receive
rewrite, rescanning TQWnet should land the full 25k+ message archive
into the proper TQW_* areas.

## v287.22 — Echomail import: per-message savepoint (May 2026)

After v287.21 wired up correct BinkP receive, ~50,000 messages parsed
out of a TQW rescan but **the per-area count stayed at 0**. The poller
log showed:

> ERROR — Poller loop error: This Session's transaction has been
> rolled back due to a previous exception during flush. To begin a new
> transaction with this Session, first issue Session.rollback().
> Original exception was: Can't reconnect until invalid transaction is
> rolled back.

Root cause: `_import_message` was adding every message to the same
SQLAlchemy session and the loop committed only after **all** messages
landed. ONE bad row (length overflow, FK mismatch, unique-clash race
on msg_id) raised on the next flush and left the session in 'invalid'
state. Every subsequent add silently piled onto the broken transaction,
and the final `commit()` rolled back the ENTIRE batch. 50k messages
vanished even though every one of them was structurally a valid
FTS-0001 packet.

Fix: wrap each insert in a `with db.session.begin_nested():`
(SAVEPOINT). A bad row now rolls back only its own savepoint; the
outer transaction stays valid; the next message inserts cleanly. Plus:

- Length-truncate the inbound fields to their column maxes
  (`from_name[:120]`, `subject[:200]`, etc.) so the most common cause
  of `IntegrityError` — a misbehaving sender exceeding column lengths
  — silently truncates instead of crashing.
- Force `db.session.flush()` inside the savepoint so the constraint
  check happens NOW, not at commit-time half a batch later.
- Log a single WARNING per skipped message with msgid + from-name +
  the underlying exception, so a future "where did message X go?"
  trace is one `grep` away.

After this fix, rescanning the same 50k batch should land them in
their proper areas. The user's existing %RESCAN gave no new messages
because Mystic already shipped them once; need another %RESCAN to
re-pack.

## v287.21 — BinkP CLIENT: real fix for ZIP-wrapped mail (May 2026)

**Root cause of the entire TQWnet-not-flowing saga.** v287.20 fixed the
extension regex in the BinkP LISTENER (`binkp_server.py`) but the
outbound POLLER uses `binkp.py` (the CLIENT side) which had a much
worse bug: file-completion was detected by looking for `\x00\x00` at
the tail of the data stream — the FTS-0001 raw-packet end marker.
Mystic (and most modern hubs) ship echomail as ZIP-wrapped bundles
which never end in `\x00\x00`. So:

  - 5 files arrived ✓ (logged as `BinkP: receiving file ...`)
  - 0 files reached the "completion → parse → ACK" path ✗
  - 0 messages imported, 0 `M_GOT` ACKs sent to the hub
  - `Poller: tqwnet — sent=0 received=0` every cycle

Rewrote `_receive_messages` to:

- Parse the byte-count from CMD_FILE (`name size mtime offset`) and
  detect completion by byte-count, not by content marker.
- Dispatch the completed file: raw FTS-0001 → parse; ZIP → unzip and
  parse each packet member; anything else → stash to
  `data/binkp/inbound` for the TIC scanner.
- Log `BinkP: imported N msg(s) from <file>` so the sysop sees progress.
- Send M_GOT promptly so the hub stops re-queueing.

Same defensive content sniffing as the listener side. Loop budget
raised from 500 to 5000 frames so very fat batches (a year-of-rescan
in one session) don't truncate.

**Recovery:** after deploying, send another `%RESCAN R=5000` to the
TQW hub. Mystic will re-pack the 15,038 / 26,909 / whatever messages
and this time they'll actually land.

## v287.20 — BinkP: day-of-week bundle extensions + persistent inbound (May 2026)

Mystic hubs deliver bundled mail to nodes using FTS-5003 day-of-week
extensions: `.mo[0-z]` (Monday), `.tu[0-z]` (Tuesday), … `.fr[0-z]`
(Friday), `.sa[0-z]`, `.su[0-z]`. Our acceptor regex only covered
Wednesday (`.we[0-9a-f]`) — every other day's mail got silently
filed to the inbound dir and ignored by the TIC scanner. StingRay's
%RESCAN of 26,909 messages dropped on the floor as `.frk` through
`.fro` (Friday bundles k-o).

Also fixed two adjacent issues that made this hard to diagnose:

- `BINKP_INBOUND_DIR` defaulted to `/tmp/binkp-inbound` — tmpfs on
  most Linux distros, so unrecognized files vanished on every service
  restart. New default: `data/binkp/inbound` (persistent).
- No log line when a file failed to match anything. Now logs an INFO
  line on every unrecognised file: filename, size, where it landed.
  Future "where did my mail go?" debugging is one `journalctl | grep`
  away.

**Recovery for the v287.20 deploy:** after deploying + restarting, the
sysop should send another %RESCAN to the hub — Mystic will re-bundle
and the new regex will now accept the resulting `.fr*` files.

## v287.19 — Federation self-registration client (May 2026)

Day 4 of the federation build. Completes the federation loop:
- v287.13 added the hub side (accept registrations).
- v287.15 added the puller (read anetbbs.lst → BbsDirectoryEntry).
- **v287.19 (this) adds the self-register / heartbeat client** so a
  brand-new ANetBBS install can opt in to the federation directory by
  flipping one flag in `.env`.

How it works:

1. Set `REGISTRY_SELF_REGISTER=true`, `BBS_DOMAIN=<your-public-hostname>`,
   `SYSOP_EMAIL=<sysop-inbox>`, and the friendly fields (`SYSOP_NAME`,
   `BBS_LOCATION`) in `.env`.
2. On service start, a daemon thread POSTs `/registry/api/v1/register`
   to the configured `REGISTRY_URL` (default `https://bbs.a-net.fyi`).
3. The hub returns a verify token + URL, which we persist to
   `data/registry_state.json` (sysop-private, not committed).
4. Daily, the thread heartbeats to keep `last_seen` current. If the
   hub 404s us (we got removed, or rehosted), it falls back to a
   full re-register.
5. New admin page `/admin/registry/self` shows: hub URL, our
   metadata, last hub response, the verify URL (so the sysop can
   click it without digging through gunicorn logs), and a "Register /
   Heartbeat Now" button for manual ticks.

Three new config keys: `SYSOP_NAME`, `SYSOP_EMAIL`, `BBS_LOCATION`.
Required for self-registration; otherwise harmless metadata.

## v287.18 — Dialout: telnet IAC + raw key reads (May 2026)

First bug filed against the pre-alpha public release. The dialout
feature (terminal → another BBS via outbound telnet) was broken in
two visible ways:

1. **No ANSI rendering on the remote** — we never negotiated the
   telnet protocol, so the remote BBS asked our terminal "do you
   support TTYPE / BINARY / NAWS?" and got no reply. It fell back
   to dumb-terminal mode and stripped ANSI escapes from everything
   it sent back.
2. **Single keypresses didn't reach the remote** — `_proxy` read the
   user's input via `session.read_line()`, which is line-buffered and
   blocks until Enter. Hotkeys (ESC, `*`, menu shortcuts, bot-defense
   challenges) never made it across until the user pressed Enter.

Rewrote `_proxy` in `anetbbs/features/dialout.py` with:

- **Minimal telnet IAC state machine** on the remote→user direction
  that strips protocol bytes, handles DO/DONT/WILL/WONT, responds
  to subnegotiation (TTYPE → "ANSI"), and announces our capabilities
  up-front so the remote enters full-ANSI mode.
- **Raw single-byte reads** via `session.read_raw(1)` on the user→
  remote direction. Each keypress is shuttled immediately, IAC bytes
  are doubled per RFC 854.
- **Ctrl+] escape actually works now** — was a half-implemented dead
  constant before. Ctrl+], Q to quit; any other key resumes.

## v287.17 — Admin user delete cascade fix (May 2026)

`Admin → Users → Delete` returned 500 Internal Server Error because
`UserSession.user_id` is `NOT NULL` but the relationship had no cascade.
SQLAlchemy tried `UPDATE user_sessions SET user_id=NULL` to detach the
session before deleting the user, which the constraint rejected.

Fixed by switching the backref to `db.backref('session', uselist=False,
cascade='all, delete-orphan')` so the session row is deleted instead
of unlinked. Bonus: corrects the relationship cardinality (UserSession
is 1:1 with User via the `unique=True` user_id, so `uselist=False` is
the right shape anyway).

## v287.16 — SYSTAT now sees web users (May 2026)

Peer BBSes querying `/imsg/directory/<host>/who` against ANetBBS hit
SYSTAT (UDP/11), which historically read only the `NodeActivity` table
— the multi-node terminal slot tracker. Any user signed in via the web
front-end (the majority on ANetBBS) lived in `UserSession` and was
invisible to SYSTAT. Result: "Who's online" pages on peer BBSes
showed `No users currently active.` even when the BBS was busy.

Fixed by unioning `NodeActivity` (terminal) + `UserSession` (web)
inside `_build_response`. Dedupes on username so a user logged in via
both transports counts once. Web sessions get synthetic slot names
`web1`, `web2`, ... and their page paths are sanitized through the
same `_friendly_where()` map as `/who/` so peer BBSes don't learn the
exact URL each user is on.

## v287.15 — anetbbs.lst → BbsDirectoryEntry pull (May 2026)

Bridges the federation registry into the existing inter-BBS IM
directory. Without this, peers registered against the hub appeared in
`anetbbs.lst` but **not** in `/imsg/directory/`, because that view
reads from `BbsDirectoryEntry` (historically populated only by
Vertrauen's `sbbsimsg.lst` for Synchronet hosts).

- New module `anetbbs/msp/anetbbs_directory.py` — pulls
  `REGISTRY_URL/anetbbs.lst` daily, upserts each peer into
  `BbsDirectoryEntry` with `source='anetbbs'`.
- New columns on `bbs_directory`: `sysop`, `location`, `software`,
  `software_version`, `msp_port`, `systat_port`, `source`. Synchronet
  rows keep their `source='sbbsimsg'`; ANetBBS rows get `'anetbbs'`.
  `_lightweight_migrate` adds the columns automatically.
- Pruning: rows with `source='anetbbs'` that disappear from the
  upstream list get deleted on the next refresh. Synchronet + manual
  rows are left untouched (owned by other refresh paths).
- `/imsg/directory/` template now shows **Software** and **Sysop /
  Location** columns with a badge per BBS family (ANetBBS = blue,
  Synchronet = grey).
- Refresher runs in a daemon thread on every install with
  `REGISTRY_URL` set (default `https://bbs.a-net.fyi`), independent of
  the hub-mode flag.

## v287.14 — Registry CSRF hotfix (May 2026)

v287.13 shipped the registry API but the `POST /registry/api/v1/*`
endpoints required a CSRF token — fine for browser forms, not for
peer ANetBBS hosts calling the API. First attempt to register against
the live hub returned `400 The CSRF token is missing.`. Fixed by
exempting the entire registry blueprint from CSRF protection at
register-time (`csrf.exempt(registry_bp)` in `web_app.create_app`).

Admin-side routes at `/admin/registry/*` still require CSRF because
they're part of the admin blueprint, which keeps its protection.

## v287.13 — Federation registry, days 2-3 (May 2026)

First two days of the v1.0a3 federation registry build. Adds the
"central hub" half — the side that accepts registrations + emits
`anetbbs.lst`. The peer-side client (auto-register + heartbeat + daily
pull) lands in v287.14 tomorrow.

**Day 2 — registry API:**
- New `RegistryEntry` model (`registry_entries` table)
- New `anetbbs/web/registry.py` blueprint:
  - `POST /registry/api/v1/register` — peer announces, gets verify token
  - `POST /registry/api/v1/heartbeat` — daily keep-alive + soft-metadata update
  - `GET /registry/verify/<token>` — sysop confirms ownership via email link
  - `GET /anetbbs.lst` + `GET /registry/api/v1/list` — JSON of listed peers
- Rate limits: per-host (5s register / 10s heartbeat) + per-IP hourly caps
- Hub-mode gate (`REGISTRY_MODE_ENABLED=true`) — endpoints 404 on non-hub installs

**Day 3 — sysop admin UI + prober:**
- `/admin/registry/` — full approval queue UI:
  - Counts cards: pending verify / pending approval / listed / total
  - Approve / reject / edit / delete per-entry
  - Inline edit form for soft metadata (name, sysop, location, ports, notes)
  - Approve button gated on `is_verified=True` (can't approve unverified
    entries — prevents drive-by sysop approval of typo'd emails)
  - Reject de-lists but keeps the row; delete is one-click-with-confirm
- New `anetbbs/msp/probe.py` — periodic SYSTAT prober:
  - Runs in a daemon thread inside the hub's web service
  - Probes every approved+verified+active entry on
    `REGISTRY_PROBE_INTERVAL_SEC` (default 1 hour)
  - Drops `is_listed=False` after `REGISTRY_PROBE_FAILURE_THRESHOLD`
    consecutive failures (default 3)
  - Auto-re-lists if a previously-dropped entry starts probing OK again
- Admin nav link added under Subsystems

Two acceptance gates before an entry shows up on the public list:
**email verification + sysop approval**. Designed to keep
`anetbbs.lst` clean even if the hub is publicly exposed.

## v287.12 — FTP user docs (May 2026)

The FTP server shipped in v287.8 but the user docs hadn't caught up.
Updated:

- `docs/PORTS.md` — new rows for FTP control (21) + passive range
  (40000-40050), updated privileged-ports section with the systemd
  drop-in recipe for `CAP_NET_BIND_SERVICE`, added the iptables rule.
- `docs/07-file-areas.md` — new **FTP access** section explaining the
  three permission tiers (anonymous / authenticated / sysop) and how
  uploads create `FileUpload` rows.
- `docs/01-installing.md` — port list updated.
- `docs/00-overview.md` — architecture diagram now shows FTP next to
  telnet/SSH/rlogin inside `anetbbs.service`.
- `README.md` — front-page protocol list mentions FTP + IFC.
- `FEATURES.md` — protocol table row.

## v287.11 — FTP settings in `/admin/settings` (May 2026)

Added eight FTP rows to the sysop settings page so the config matches
the other protocol settings (telnet / SSH / rlogin) rather than being
.env-only:

- `FTP_ENABLED`
- `FTP_PORT`
- `FTP_ANON_ENABLED`
- `FTP_PASV_PORTS`
- `FTP_TLS_CERTFILE`
- `FTP_TLS_KEYFILE`
- `FTP_ROOT_DIR`
- `FTP_BANNER`

All marked `requires_restart=True` (same as the other protocol toggles)
because the FTP daemon binds its ports at startup.

## v287.10 — FTP: hide server-side path in symlink listings (May 2026)

`LIST` output was leaking the absolute server-side `storage_path` to
every client (including anon) as the symlink target:

```
lrwxrwxrwx 1 anetbbs anetbbs 39 May 15 01:34 FILES.GAMES -> /home/stingray/anetbbs/data/files/games
```

That exposes internal directory structure and could help a remote
attacker fingerprint the install. Fixed by overriding `lstat()` in
`SymlinkAwareFS` to use `os.stat()` instead, so symlinks resolve
through and look like regular directories to the FTP client:

```
drwxrwxr-x 2 anetbbs anetbbs 4096 May 15 01:34 FILES.GAMES
```

## v287.9 — FTP hotfix: don't break telnet/SSH login (May 2026)

**v287.8 shipped FTP integration that broke telnet/SSH login** with
`Session error: cannot notify on un-acquired lock`. Root cause:
`anetbbs.main` was calling `anetbbs.web_app.create_app()` to obtain a
Flask app for the FTP daemon thread — but that path registers all
~50 blueprints AND starts the echomail / RSS / MSP / SYSTAT
background pollers, each of which uses `threading` primitives. Turning
the previously pure-asyncio terminal-server process into a mixed
asyncio+threading process broke the `threading.Condition` semantics
that the login flow's session lock relies on.

Fixed by adding `anetbbs.ftp.server.build_minimal_app()` — a 5-line
Flask app builder that initializes only `db.init_app(app)` and the
config. Zero blueprints, zero pollers. `anetbbs.main` now uses this
instead of `create_app()`, so the FTP thread has just enough to do
`User.check_password` + `FileUpload` writes without dragging the
full app surface into the process.

Verified post-fix:
- Minimal app: 0 blueprints registered, DB queries work.
- SSH/telnet login no longer raises the lock error when FTP is enabled.
- FTP server still serves anonymous + authenticated correctly.

## v287.8 — FTP server (May 2026)

**FTP front-end.** A new `anetbbs/ftp/` module serves the existing
`FileArea` tree to the internet, completing the four-protocol set
(web / telnet / SSH / rlogin / FTP). Earns you the FTN nodelist `IFC`
flag (Internet File transfer Capability) once you advertise it.

Architecture:

- **pyftpdlib backend.** Mature, async-friendly. Drives the whole wire
  protocol — we add the auth + filesystem + upload-tracking layers.
- **Auth via existing `User.check_password`** — same bcrypt as web /
  telnet / SSH. Anonymous login is enabled by default (`FTP_ANON_ENABLED=true`).
- **Three-tier symlink trees** at `data/ftp_root/{anon,users,admin}/`,
  rebuilt on every server start:
  - `anon/` — public areas (`is_active AND NOT is_sysop_only`), read-only.
  - `users/` — non-sysop-only areas, full r/w subject to per-area perms.
  - `admin/` — every active area including sysop-only.
  Each tree is just symlinks pointing at `FileArea.storage_path`. The
  authorizer maps the right tree onto each session at login.
- **`SymlinkAwareFS`** — pyftpdlib's default `AbstractedFS` uses
  `os.path.realpath()` for path safety, which dereferences symlinks and
  treats every CWD through one as "outside the user's home." We
  override `realpath()` → `abspath()` and `validpath()` to compare
  against abspath — preserves `..` traversal safety while letting our
  deliberate symlink tree work.
- **Upload tracking.** `on_file_received` hook creates a `FileUpload`
  row keyed to the parent directory's `FileArea`, so files uploaded
  via FTP show up in the web UI's file-area browser. Per-area
  `upload_permission` enforced post-hoc: if the user lacks permission
  the file is deleted and the violation logged.
- **Optional FTPS** — set `FTP_TLS_CERTFILE` + `FTP_TLS_KEYFILE` (reuse
  the same Let's Encrypt cert nginx uses) and connections can `AUTH TLS`
  on the same port.
- **Passive port range** configurable via `FTP_PASV_PORTS` (default
  `40000-40050`). Open + forward those on the firewall.
- **Process integration.** Runs in a daemon thread inside the existing
  `anetbbs.service` — no new systemd unit. Driven by `FTP_ENABLED` in
  the `.env` file. If pyftpdlib is missing, the server logs a warning
  and skips startup instead of crashing the BBS.

Verified end-to-end with curl:
- Anonymous lists only public areas, can download.
- Anonymous upload is denied at the auth layer (perm string is `elr`).
- Authenticated user sees non-sysop-only areas; admin sees all.
- Authenticated upload lands on disk **and** creates a `FileUpload` row.

## v287.7 — Nodelist auto-import + send-netmail-to-sysop (May 2026)

Two enhancements inspired by Craig Hendricks's (codefenix) **NetLister**
door for Synchronet (https://conchaos.synchro.net):

- **FileArea → Nodelist auto-import.** New `is_nodelist_source` +
  `nodelist_domain` columns on `file_areas`. When the sysop flags an
  area (e.g. `Z1DAILY` for FidoNet, `tqwinfo` for TQWnet) and sets a
  domain, every inbound TIC for that area is unwrapped (ZIPs included
  — TQWnet's `tqwnet.zNN` style works without naming the .zip
  extension) and auto-imported into the `Nodelist` table. Tagged by
  domain so `/nodelist/?domain=tqwnet` filters work. Verified
  end-to-end against real-world archives — `tqwnet.z46` (145 entries),
  `fsxnet.zip` (323), `Z1DAILY.ZIP` (1208).
- **Send-Netmail-to-Sysop button** on `/nodelist/<id>`. One click jumps
  to the existing netmail composer with `to_address` + `to_name`
  pre-filled from the nodelist row. The compose route already auto-picks
  the FROM AKA whose zone matches the destination, so clicking a TQWnet
  entry composes from your `1337:3/231` AKA, a fsxnet entry from your
  `21:1/100` AKA, etc.
- **Bulk-import admin route** at `/nodelist/admin/bulk-import`. Scans a
  configurable directory (default `data/nodelists`, override via
  `NODELIST_SCAN_DIR`), lists every plausible nodelist file or ZIP,
  and lets the sysop tick + tag → import in a single submit. Useful
  for first-run when the sysop already has a stash of nodelist
  archives on disk (e.g. from infopack downloads).
- **`import_from_path()`** is the new public entry point in
  `anetbbs/echomail/nodelist.py`. Accepts text files, archives, or
  ZIPs with non-standard extensions (`.z46`, `.a07`, etc — detected by
  magic bytes, not suffix). Picks the highest-priority nodelist member
  from a ZIP using `_looks_like_nodelist()` heuristics.

## v287.6 — /who/ privacy + inbound netmail status (May 2026)

Two fixes surfaced while a sysop was watching their own /who/ page:

- **`/who/` "Where" column sanitized for non-admins.** The column used
  to leak the exact URL each user was viewing (e.g. `/echomail/53/25407`)
  to every other logged-in user. Anyone could copy-paste the link and
  follow someone around. Sysops still see raw paths; non-admins now see
  a coarse area label ("Echomail", "Profile", "Boards", "MRC Chat", …).
  The map is `_WEB_AREA_LABELS` in `anetbbs/web/who.py` — extend as new
  blueprints land. Telnet/SSH/rlogin sessions are unaffected (their
  `where` is already a friendly menu/game name, not a URL).
- **Inbound netmail status was sometimes `draft`.** The listener path
  (`binkp_server.py`) correctly set `status='received'` on incoming
  netmail, but the **poller path** (`poller.py`, where we dial out to
  pull mail) was creating `NetmailMessage` rows without setting status —
  so they inherited the model's `draft` default (which is right for the
  *compose* flow but wrong for inbound). AREAFIX responses received via
  poll-out vanished from the sysop's inbox UI as a result. Fixed:
  `poller.py:458` now sets `status='received'` + `received_at`. Existing
  stuck rows can be backfilled with
  `UPDATE netmail_messages SET status='received', received_at=created_at WHERE direction='inbound' AND status='draft';`

## v287.5 — profile form nested-`<form>` fix, deploy-script path bomb (May 2026)

Two more profile-edit bugs found while v287.4 was being deployed:

- **Profile edits never submitted at all** — `templates/profile/edit.html`
  had a nested `<form action="/profile/avatar/remove">` inside the main
  profile form. HTML forbids nested forms; browsers auto-close the outer
  form at the inner `<form>` tag, so the Privacy / Signature / Tagline /
  Theme dropdown / **Update Profile** submit button ended up outside any
  form. Clicking Update did literally nothing — no POST left the browser,
  no field ever saved. Why nobody's `users.theme_id` was ever non-NULL.
  Fixed by lifting the avatar-remove form out of the main form and
  pointing the Remove Avatar button at it via the HTML5 `form=` attribute,
  so the visual layout is unchanged but the markup is now valid.
- **`anetbbs-deploy-latest.sh` step 4.5 path bomb** — the script was
  copying `deploy/*.service` straight into `/etc/systemd/system`. The
  source units ship `/opt/anetbbs` as the install path, but real installs
  often live elsewhere (e.g. `/home/stingray/anetbbs`). After a deploy,
  systemd would try to exec `/opt/anetbbs/venv/bin/gunicorn`, fail with
  `status=203/EXEC`, and crash-loop the web service. Step 4.5 now rewrites
  `/opt/anetbbs` → `$INSTALL` (the script's own install-path variable)
  before installing the unit. Existing live boxes that already got
  clobbered need a one-time `sudo sed -i 's|/opt/anetbbs|/home/stingray/anetbbs|g' /etc/systemd/system/anetbbs*.service && sudo systemctl daemon-reload`.

## v287.4 — profile form + nginx upload limit (May 2026)

Two reported bugs in one cut:

- **Profile edits silently failed for anyone with a `.local` email.**
  WTForms `Email()` delegates to `email-validator` 2.x, which rejects
  RFC 6761 special-use TLDs (`.local`, `.test`, `.invalid`, `.example`).
  The seeded admin user is `admin@anetbbs.local`, so every profile save
  — including the theme dropdown — bounced off email validation before
  reaching the DB. Theme picker looked broken, but the real problem
  was upstream. Replaced `Email` with a permissive regex validator
  (`anetbbs/web/validators.py:PermissiveEmail`) in `auth.py`,
  `profile.py`, and `admin.py`. Any RFC-shaped address now passes,
  including FidoNet-style aliases and internal-only TLDs.
- **Avatar uploads under 2 MB returned 413 Request Entity Too Large.**
  The bundled nginx template (`deploy/anetbbs-nginx.conf.template`)
  inherited nginx's 1 MB default `client_max_body_size`. Added an
  explicit `client_max_body_size 110m;` directive (covers the 100 MB
  `UPLOAD_MAX_SIZE` plus headroom). On upgrade, regenerate the live
  nginx config or add the directive by hand inside the `server {}`
  block and `nginx -t && systemctl reload nginx`.

## v1.0b.1 — branding consistency pass (May 2026)

Tiny but everywhere: replaced `ANET BBS` / `Anet BBS` / `ANet BBS` /
`anet bbs` / `ANET-BBS` → `ANetBBS` across **61 source files**.
User-visible places include the "Powered by …" footer line, default
BBS_NAME fallbacks, systat banner, copyright lines, log messages,
admin tool descriptions, etc.

Functional code unchanged. Just one consistent spelling.

## v1.0b — alpha 2 (May 2026)

Cut from internal build v287.1. Bundles every internal release from
v197 through v287.1; the per-build notes below capture the granular
changes. Headline additions:

- **LORD** ships pre-installed and plays end-to-end under the Node +
  Synchronet compat shim (v283.x–v287 added the deep API stubs the
  game exercises).
- **Wiki** at `/wiki/` with revision history, diff, search,
  wanted/orphan reports, 41 seeded pages (v284).
- **RSS reader** — web + terminal (v281).
- **Web doors at 80×25** with the real CGA palette, plus pre-join
  output buffering so welcome screens render immediately (v287, v287.1).
- **NodeSpy kick** for stuck terminal sessions, cross-process via
  DB-flag/poll (v283.x).
- **Echomail terminal** reader properly paged with `[Q]=quit`,
  CP437 body passthrough (v283.5–v283.7).

## v287.1 — Web doors: buffer pre-join output (May 2026)

Some doors (ANetSIMS, A-Net Sixel TV, anything that draws a welcome
screen then waits for input *before* the user does anything) showed
nothing in the web terminal until the user pressed Enter. The
keystroke produced new output which then DID appear. Telnet / SSH /
rlogin worked fine because they don't have this race.

Root cause was a race in `web/games.py:handle_start_game`:

1. `launch_door_game()` starts the PTY-reader thread *synchronously*.
   Within milliseconds the reader calls `_emit_output(welcome_bytes)`.
2. At that instant `sid_box[0]` is still `None` (set on the next
   line), so `_emit_output` returned early — **bytes lost**.
3. Even after `sid_box[0]` was set, the next emit went to
   `room=str(session_id)` — but `join_room()` hadn't run yet, so the
   client wasn't in the room. **Also dropped.**
4. First user keystroke → door writes more output → by then
   `join_room()` had run → bytes flow.

Fix: `_emit_output` now appends to a pre-join buffer (under a lock,
the reader is on its own thread) while a `_buffering` flag is true.
After `launch_door_game` returns, `sid_box` is set, the room is
joined, `game_started` is emitted, and `_flush_pre_join_buffer()`
drains the buffer as one chunk and flips the flag — subsequent
reads bypass the buffer and emit directly.

## v287 — Web door terminal: pin to 80x25, real CGA palette (May 2026)

After LORD saved/loaded cleanly in v286.10 there were two remaining
visual issues:

1. **ANSI art bled into menus.** Violet's portrait and her flirt
   menu appeared overlapped. Cause: `play_terminal.html` started the
   xterm at 80×24 but immediately ran `fitAddon.fit()` to fill the
   viewport — typically 150+ columns. LORD's `gotoxy(50, 12)` then
   landed at the wrong column and the menu overprinted the art.
2. **Gray where colors should be.** xterm.js's default red/green/blue
   are web-ish midtones, not the CGA primaries doors are written for.

Fixes:

- **Pin xterm.js to 80×25** in `play_terminal.html`. Removed the fit
  addon and the window-resize handler. Wrapped the terminal in a
  centered, shrink-to-fit container so it sits cleanly in any
  browser width.
- **Real CGA palette** — full 16 colors set on the xterm theme.
  Matches what door authors saw on a PC (DOS bright red is
  `#ff5555`, not whatever the browser thought).
- **`screen_rows = 25`** in `synchronet_compat.py` (was 24).
- **PTY initial winsize 80×25** in `door_runner.py`. Some kernels
  default new ptys to (0, 0) — without an explicit `TIOCSWINSZ`
  the door reads `console.screen_rows` as 0 and gotoxy breaks.
- Font bumped to 16px Cascadia Mono / Consolas / Courier New for
  a sharper CP437 render.

## v286.10 — LORD: File.write at-cursor + exit-hook silence (May 2026)

**Critical** — `File.prototype.write(str, len)` in the compat shim
was `this._content += str` — *append to EOF, ignore position*.
But `recordfile.js` calls `this.file.write(wr, len)` for every
String / Date / Float field at a seeked record offset
(`this.file.position = rec * RecordLength` then a chain of writes).
Every string field on every record landed at the file's tail
instead of its proper slot. That's why the player-list rankings
showed character-name fragments mixed with timestamps, and
nonsense XP values: not corrupt bytes, just bytes in the wrong
columns.

Fixed `write()` to honor `_pos` and *overwrite* at the cursor
(padding with NULs if seeked past EOF), matching `writeBin/writeStr`.
`writeln()` now goes through the same path.

Also silenced the cosmetic `[BBS] exit hook failed: player is not
defined` line that prints at every clean quit — LORD's
`js.on_exit` cleanup references variables that are already out
of scope by exit. The hook's purpose was already-redundant
cleanup; swallow the error. Set `BBS_DEBUG_EXIT_HOOKS=1` if you
ever want to see them.

**Save data after this update:** old `player.bin` / `state.bin`
files were written with the broken layout. They'll READ wrong
in the new code (and may corrupt further on write). Recommended
to delete and start fresh:
```
sudo -u stingray rm -f \
  /home/stingray/anetbbs/anetbbs/games/sbbs_doors/lord/state.bin \
  /home/stingray/anetbbs/anetbbs/games/sbbs_doors/lord/player.bin \
  /home/stingray/anetbbs/anetbbs/games/sbbs_doors/lord/*.lock
```

## v286.9 — LORD: check_gameover null-deref guard (May 2026)

Same pattern as v286.8's flirt-with-Violet patch:
`check_gameover` runs at the top of every door entry. If
`state.won_by >= 0` (someone "won"), it does
`wb = player_get(state.won_by)` then immediately `wb.name`.
When the referenced player record is gone, the door crashes
with `Cannot read property 'name' of null` — and the next
entry crashes again because the bad flag is still in state.

Patched `lord.js` to null-check `wb`, reset `state.won_by = -1`,
`put_state()`, and let the door continue. The game effectively
re-opens for play instead of being stuck on a perpetual "game
over" screen.

## v286.8 — LORD: flirt-with-Violet null-deref guard (May 2026)

In-game crash patch: `flirt_with_violet()` assumed
`player_get(state.married_to_violet)` always returns a record,
and dereferenced `op.name` directly. If `state.married_to_violet`
points at a stale record (record deleted between marriage and
the next flirt; or our fresh state file has a non-(-1) default),
`player_get` returns null and the door bombs with
`Cannot read property 'name' of null`.

Patched `lord.js` to:
- Check `op === null` after `player_get`
- Reset `state.married_to_violet = -1` and `put_state()` (so the
  flag stays cleared after the door exits)
- Show a generic Grizelda-kisses-you-painfully line instead of
  the personalised `you curse <name>` text

This is an upstream LORD-JS bug we should ideally push back; for
now the patch is marked `// ANetBBS patch:` in the source for
easy review.

## v286.7 — LORD: File.readBin / writeBin / Str + full mode parsing (May 2026)

Next missing piece: `RecordFile.writeField` → `this.file.writeBin(val, N)`.
Synchronet's File has typed-binary I/O (`readBin(N)` / `writeBin(value, N)`)
for fixed-width LE unsigned integers, plus `readStr(N)` / `writeStr(s, N)`
for fixed-width strings. recordfile.js (the library LORD uses to store
the per-record game state in lord.dat etc.) wires every field through
them.

Added in this round:

- `File.prototype.readBin(bytes)` — read N-byte LE unsigned int
- `File.prototype.writeBin(value, bytes)` — write same, overwriting at pos
- `File.prototype.readStr(n)` / `writeStr(str, n)` — fixed-width strings
  with space-pad/truncate semantics

Also overhauled `File.open(mode)` to honor the full fopen language:
`'r'`, `'w'`, `'a'`, `'r+'`, `'w+'`, `'a+'` (with the `'b'` suffix
ignored — same as glibc). Previously only the presence of `'w'` was
detected, so opening `rb+` was silently treated as read-only and writes
never flushed. Now tracks `_can_write` separately from the truncate /
append flags, and `writeBin/writeStr/flush/close` all gate on that
single signal.

## v286.6 — LORD: File.lock / unlock / flush / truncate (May 2026)

After `file_mutex`, LORD's next stop was `RecordFile.lock()` →
`this.file.lock(rec*RecordLength, RecordLength)`. `recordfile.js`
uses Synchronet's File range-locking on every record open/close.
Single-node ANetBBS has no contention so:

- `File.prototype.lock(start, length)` → returns `true` (always grant)
- `File.prototype.unlock(start, length)` → returns `true`
- `File.prototype.flush()` → persists in-memory `_content` to disk
- `File.prototype.truncate(n)` → trims `_content` and the on-disk
  file (LORD uses this for the "reset save" path)

All added to `synchronet_compat.py` File prototype.

## v286.5 — LORD: `file_mutex` stub (May 2026)

After v286.4 cleared the RIP-probe stall, the next missing
built-in tripped: `ReferenceError: file_mutex is not defined` at
LORD's first `get_state()` call. Synchronet's `file_mutex` is an
atomic single-writer lock primitive — creates a `.lock` file
carrying an owner identity, returns false if a peer holds it.

Single-node BBSes never contend, so a stub that always grants
the lock (and writes `contents` if provided, since LORD uses
the lock file as a write-once data drop for things like war
reports, mail messages, and fairy logs) is enough.

Stub added to `synchronet_compat.py`, exposed on globalThis.

## v286.4 — LORD: skip the 10-second RIP probe (May 2026)

The DOS LORD had a `/NORIP` command-line flag to disable the RIP
terminal-detection probe. The JS port doesn't — it unconditionally
sends `\x1b[!\x1b[6n` and blocks `read_str(10000, /RIPSCRIP/)` for
up to ten seconds waiting for a RIPSCRIP response that no modern
terminal (xterm.js, SyncTERM, NetRunner, mTelnet) sends. That's
the 10+ second stall users felt before the welcome ANSI appeared.

Patched `anetbbs/games/sbbs_doors/lord/lord.js` to comment out
the probe + the if-block that loads RIP icons. `rip` stays `false`
(its var default), the welcome screen and main menu render
immediately. To re-enable: uncomment the block — it's marked with
`// ANetBBS patch:` in the source.

## v286.3 — LORD: input plumbing actually works now (May 2026)

v286.2 cached `Queue("name")` so same-name calls returned the same
instance — but missed the case where two scripts pass DIFFERENT
suffixes:

- `dorkit.js` → `new Queue("dorkit_input" + bbs.node_num)`
  → `"dorkit_input1"`
- `ansi_input.js` → `new Queue("dorkit_input" + (argv[0] ?? ''))`
  → `"dorkit_input"` (argv is empty in our shim)

Different names = different cache entries = still two queues =
bytes still nowhere. Fixed in `sbbs_stubs/dorkit/sbbs_input.js` by
explicitly re-pointing `ai.input_queue = dk.console.input_queue`
after loading ansi_input.js, so processed keystrokes definitely
land where dorkit polls.

## v286.2 — LORD: input + draw-speed fix (May 2026)

Two issues from a live launch of v286:

1. **Input did nothing.** LORD's welcome screen drew but
   keystrokes were eaten. Cause: Synchronet's `Queue("name")`
   is a named IPC channel — two `new Queue("dorkit_input"+N)`
   calls in different scripts (dorkit.js + ansi_input.js) bind
   to the same wire. Our shim's Queue was a plain JS class, so
   the two became *separate* objects. `ai.add(byte)` wrote
   processed keystrokes into instance B; dorkit polled instance A.
   Forever empty. Fixed by name-caching: same name returns the
   same Queue instance.

2. **Welcome screen drew slowly.** ~20 s on the live BBS. Cause:
   `dk.console.print` writes to BOTH `local_io` and `remote_io`.
   In sbbs mode local_io is unused — but our compat forces
   `local_console.js` to load unconditionally (it's required at
   the bottom of dorkit.js), leaving local_io defined. Every
   print byte went through a 24×80 Screen grid with per-cell
   `setCell` updates. Patched `sbbs_console.js` to
   `delete dk.console.local_io` so writes go straight to
   remote_io → stdout.

Test instructions if you're scripting smoke tests against the
shim: keep the test window >= 12 s for LORD's start path — it
has a hard-coded 10 s RIP-probe timeout before the welcome
display happens.

## v286.1 — Games admin: silent-save fix (May 2026)

Clicking Save on `/admin/games/<id>/edit` did nothing — appeared
to be a no-op. Cause: the `drop_file_type` SelectField (and to a
lesser extent `category`) rejected empty/NULL values via WTForms'
default "must be in choices" validator. LORD's seeded row leaves
`drop_file_type` NULL, so editing it would silently fail
validation; the page re-rendered identically with no flash, no
error display, no apparent action.

Fixed in two places:

- `web/games_admin.py:GameForm` — `drop_file_type` and `category`
  now use `validate_choice=False`, accepting empty / NULL as
  "no value".
- `templates/games/admin/form.html` — added an `alert-danger`
  block at the top of the form that lists every field error
  when save fails. Future invisible-save bugs become visible
  immediately.

## v286 — LORD: now boots under Node compat shim (May 2026)

Following v285 which bundled the LORD source and recommended real
Synchronet `jsexec`, this release closes the remaining gaps so LORD
actually renders under Node — no Synchronet install required. The
welcome ANSI screen draws; the input loop reads keystrokes; play
proceeds.

**Compat-shim additions in `anetbbs/games/synchronet_compat.py`**

- `server` and `client` global stubs — without those, dorkit's
  `dk.system.mode` test fell through to undefined and no console
  driver loaded, so LORD ran to completion with zero output. Now
  dorkit picks `'sbbs'` mode and loads `sbbs_console.js`.
- Beefed-up `bbs.*`: `logon_time`, `get_time_left()`, `online`,
  `sys_status`, `start_time`. `system.*`: `node_dir`, `data_dir`,
  `text_dir`, `ctrl_dir`, `exec_dir`, `mods_dir`, `qwk_id`,
  `os_version`, `matchuser/matchuserdata/username` no-ops.
- Beefed-up `user.*` (security.password, stats.bytes_uploaded/
  downloaded, laston_date, expiration_date, alias, location, …).
- `console.right/left/up/down(n)` aliases — sbbs_console.js calls
  these names rather than `cursor_right` etc.
- `console.ctrlkey_passthru` slot so doors can set the bitmask.
- `load(true, "file.js", args)` background form returns a stub
  Queue for the on-exit cleanup hook.
- `File.readln()` returns `null` at EOF (not `''`) so LORD's
  `build_txt_index` loop terminates instead of spinning.
- `File.position` becomes a real getter/setter (used by
  `build_txt_index` to record byte offsets).
- `File.length` is now a property (not a method) — matches what
  sauce_lib and others expect.
- Load resolver prefers `<stubs_dir>/dorkit/<file>` over the flat
  `<stubs_dir>/<file>` so the dorkit-internal Screen/Graphic with
  the right prototype methods wins over older bare copies.

**Node-side input plumbing** (`sbbs_stubs/dorkit/sbbs_input.js`)

Replaced upstream's busy-loop background-thread with a callback
that registers on `dk.console.input_queue_callback` and runs from
dorkit's own waitkey() loop. Sets `stty min 0 time 1` once at load
so each readSync returns within 100 ms — no per-iteration stty
thrash, no busy-wait, the door is responsive.

**Seed Game row flipped to active**

`_create_default_data` inserts LORD with `is_active=True` now. The
door appears in `/games/` ready to play on first start; sysops who
prefer jsexec can install it later and the door_runner auto-prefers
real Synchronet binaries when found.

**Honest caveats**

- The compat shim handles LORD specifically. Other Synchronet doors
  vary; this is the foundation, not a guarantee everything works.
- The Node `Queue` is a single-process in-memory FIFO; doors that
  rely on inter-process communication via named queues will need
  more work.
- File operations stay in latin-1 binary mode for CP437 fidelity;
  pure-text doors that expect UTF-8 might surprise.

## v285 — LORD: Synchronet JS port pre-installed (May 2026)

Bundles Synchronet's JavaScript port of *Legend of the Red Dragon*
inside the BBS, plus the upstream `dorkit/` helper library it needs.

**What ships**

- `anetbbs/games/sbbs_doors/lord/` — full upstream `xtrn/lord/`
  tree from `github.com/SynchronetBBS/sbbs` (lord.js, lordsrv.js,
  recorddefs.js, IGM subdirs, ANSI art, name lists; 16 MB total).
- `anetbbs/games/sbbs_stubs/dorkit/` — the upstream `xtrn/dorkit/`
  console drivers (screen.js, local_console.js, ansi_console.js,
  ansi_input.js, attribute.js, graphic.js, …) so LORD's
  `require("screen.js")` chain resolves.
- `dorkit.js` + `recordfile.js` synced to current upstream.

**Compat-shim improvements**

The `synchronet_compat.py` shim grew the bits LORD (and any other
real Synchronet door) reaches for:

- `Queue` class — Synchronet's inter-script FIFO; backed by stdin
  reads when the queue name starts with `dorkit_input`.
- `strftime(fmt, unix_seconds)` — C-style with the common
  conversion specifiers (`%H %M %S %Y %m %d %a %A %b %B …`).
- `js.load_path_list`, `js.on_exit(code)`, `js.exec()`, `js.gc()`,
  `js.global`, `js.auto_terminate`, `js.terminate_signaled`.
- `require()` now accepts the scope-prefix form
  (`require(scope, "cnflib.js", "CNF")`) used by LORD.
- `load()` consults `js.load_path_list` first, then the new
  conventional `<exec_dir>/{dorkit,load}/` fallbacks.
- `Queue` exposed on `globalThis` via the existing global-registry
  sweep so `vm.runInThisContext`'d sub-files see it.
- `sbbs_stubs/cnflib.js`: SpiderMonkey `for each (var p in struct)`
  → standard `Object.keys(struct).forEach(...)` so V8 parses it.

**Pre-seeded Game row**

`_create_default_data` inserts a "Legend of the Red Dragon" game
(`game_type='door_synchronet'`) pointing at the bundled LORD. Sysop
must flip `is_active=true` once Synchronet's `jsexec` runtime is on
the host — see the updated [[LORD Setup]] wiki page for the three
ways to get jsexec (apt, build-from-source, or point `SBBS_JSEXEC`
env at an existing install).

**Why jsexec instead of Node**

The compat shim gets simpler doors running under Node, but LORD's
dorkit library binds its console driver to a full Synchronet
`bbs/server/client/user/console` global quintet and depends on the
forked input-thread model. Emulating that on top of Node's
single-threaded loop is a much deeper rewrite. Real `jsexec` is a
small standalone binary that gives upstream behaviour for free.

## v284 — Wiki (May 2026)

A full community wiki at `/wiki/` — collaborative documentation
with revisions, diff, search, and markdown + `[[wiki-links]]`.

**Models**

- `WikiPage` — slug-keyed page with current body, title, summary,
  view count, lock flag, soft-delete flag, created/updated audit.
- `WikiRevision` — every edit gets one. Stores the full body
  (no compression — sqlite is fine at this scale), edit summary,
  author user-id, author IP, rev-num monotonic per page.
- Auto-sweep adds both tables on next `anetbbs-web` start.

**Renderer** (`anetbbs/wiki/render.py`)

- Python-markdown with `fenced_code`, `tables`, `nl2br`,
  `attr_list`, `toc`, `sane_lists`.
- `[[Page Title]]` → `/wiki/page-title`. Missing pages render as
  red dashed-underline links so editors notice.
- `[[slug|display text]]` and `[[Page#anchor]]` both supported.
- Wiki-link preprocessing skips fenced code blocks and inline
  `\`code\`` spans so example tokens don't become real links.
- Output sanitized by bleach with a whitelist that keeps headings,
  tables, code, images, our wiki-link CSS classes, and heading
  anchor IDs.

**Slug helpers** (`anetbbs/wiki/slug.py`)

- NFKD-fold + lowercase + dash-collapse.
- "Café — édition" → `cafe-edition`.
- "BinkP & QWK" → `binkp-and-qwk`.

**Routes** (`anetbbs/web/wiki.py`)

| URL | What |
|-----|------|
| `/wiki/` | Home page render + recent edits sidebar |
| `/wiki/<slug>` | View a page (red-link template if missing) |
| `/wiki/<slug>/edit` | Edit form with live preview |
| `/wiki/<slug>/preview` | JS-called preview endpoint |
| `/wiki/<slug>/history` | Revision list w/ compare picker |
| `/wiki/<slug>/rev/<n>` | View a specific old revision |
| `/wiki/<slug>/diff/<a>/<b>` | Unified diff between revisions |
| `/wiki/<slug>/revert/<n>` | Roll back to revision N |
| `/wiki/<slug>/lock` (admin) | Toggle edit lock |
| `/wiki/<slug>/delete` (admin) | Soft-delete |
| `/wiki/<slug>/restore` (admin) | Undo soft-delete |
| `/wiki/<slug>/rename` (admin) | Change slug |
| `/wiki/new` | Create-new flow with suggested slug |
| `/wiki/all` | Alphabetical index |
| `/wiki/recent` | Every edit, newest first |
| `/wiki/search?q=…` | Full-text across title + body |
| `/wiki/wanted` | Pages linked-to but not created |
| `/wiki/orphans` | Pages no other page links to |

**Templates** — 13 Jinja templates extending `base.html`, all
dark-theme-aware. Wiki-specific CSS (red links for missing pages,
diff colourization) lives inline in `wiki/_layout.html`.

**Auth**

- Anyone (incl. logged-out) can read.
- Logged-in users can edit and create.
- Locked pages: only admins can edit.
- Lock, delete, restore, rename: admin-only.

**Seed content** — 41 pages covering: connecting via web /
telnet / SSH / rlogin / gemini / finger; reading and posting in
boards, echomail, netmail, PMs, instant messages, RSS, files;
playing doors (web, rlogin, DOS); sysop guide; door setup; BinkP
setup; LORD-specific recipe; DosBridge architecture; the codepage
story; NodeSpy; backup; full architecture overview; QWK; TIC
processor; IRC and MRC bridges; web terminal; and a glossary of
BBS jargon. 41 revisions in history on day one — each seed entry
gets an r1.

**Nav**

- `/wiki/` link added to the Help dropdown next to Documentation.

**Note on seeded pages** — they're a starting point, not the final
word. Anyone with an account can improve them, and the wanted
pages report at `/wiki/wanted` shows the queue of pages that
existing pages already link to.

## v283.7 — Echomail: CP437 body passthrough + Q-skip (May 2026)

Two issues from the Echomail reader:

**CP437 / ANSI bodies were mangled.** Echomail bodies received via
BinkP from FidoNet/Synchronet networks contain CP437 line-drawing,
block characters, and embedded ANSI color escapes — stored as
latin-1 mojibake (each original byte 0xNN → codepoint U+00NN).
The reader was passing them through `session.write()` which
re-encodes everything to CP437, scrambling the bytes. Body lines
now go straight to the writer via `line.encode('latin-1')`, so
the original bytes reach the user's CP437 terminal unchanged.
Falls back to `cp437` encoding with replacement if the line has
genuine unicode codepoints above 0xFF.

**Q-skip on `-- more --` prompts.** Walking past 100 areas to
find #22 was painful. Every paging prompt in the echomail flow
(area list under E, message index after picking an area, message
body, area list under C compose) now reads:
`-- more (Enter, Q=stop listing) --`. Pressing Q at any of them
breaks out of the loop and goes straight to the picker (or back
to the message index, for body view). Mirrors the bulletin
reader's existing `[Q]=quit` behavior.

## v283.6 — Echomail message reader: color + paging (May 2026)

After picking an area under Echomail (E), the resulting message
list and message body were plain monochrome and dumped without
paging. Now matches the rest of the terminal UI:

- Banner + footer wrap on both list and body screens
- Colored columns on the message index (yellow #, white subject,
  green from, cyan date)
- `-- more (Enter) --` paging every 18 lines on both the index
  and the message body, so long posts don't scroll past on a
  24-row terminal
- Same paging treatment in Compose Echomail (C) for the area-empty
  screen, prompts, and the queued-for-BinkP confirmation

## v283.5 — Echomail (E) area list paging (May 2026)

The terminal main menu's Echomail (E) area listing scrolled past on
24-row terminals — `compose_echomail` (C) already paged every 18
lines with `-- more (Enter) --`, but `list_echo_areas` dumped
everything at once. Now uses the same paging.

## v283.4 — Dark-theme: list-group + table row tints (May 2026)

Bootstrap's default `.list-group-item` and `.table-warning/-info/...`
row tints render light-on-light, which clashes with the dark theme.
The most visible offenders were:

- The RSS Reader feed list and River feed (white cards, hard to read).
- The Inter-BBS Instant Messages inbox unread-row highlight (cream
  yellow on white).
- @mention autocomplete popup, leaderboards, stats, profile,
  oneliners, calendar, docs nav — all used `.list-group-item`.

`base.html` now overrides these classes to use the same dark palette
as the `.card` and `.alert-*` styles. No template changes needed —
all consumers benefit automatically.

## v283.3 — NodeSpy kick audit-log fix (May 2026)

The kick endpoint in v283.2 raised
`TypeError: 'action' is an invalid keyword argument for UserActivity`
on every kick attempt — the audit-log column is `activity_type`, not
`action`. (The legacy v283 code had the same bug but never ran far
enough to trip it.) Now uses the correct field and also records
`ip_address` and `service='web'`.

## v283.2 — NodeSpy kick: cross-process fix (May 2026)

The kick button in v283 / v283.1 *appeared* to work but didn't actually
disconnect anyone. Root cause: `anetbbs-web` (gunicorn) and
`anetbbs-telnet` are separate systemd services with separate Python
processes. The kick endpoint flipped a flag in the web process's
in-memory `_NODES` dict, but the active terminal session lived in the
telnet process — different memory, no effect.

Now the kick crosses the process boundary via the database:

- `NodeActivity` gains two columns: `kick_requested` (bool) and
  `kick_reason` (string). Auto-sweep adds them on next gunicorn start.
- `/admin/control/nodespy/<slot>/kick` simply updates those columns
  and commits.
- A new `_kick_watchdog` task in `BBSSession` polls its own
  `NodeActivity` row every 5 seconds. When `kick_requested` is set, it
  writes the goodbye banner, closes the writer, and the session
  unwinds via the normal teardown path.
- The watchdog is cancelled on session close, before
  `_close_node_activity` deletes the row.

Worst-case latency: ~5 seconds between kick click and disconnect.
Stuck/idle users are unaffected (the watchdog runs on its own timer,
not on user input).

## v283 — NodeSpy kick (May 2026)

Sysop can now disconnect a stuck or misbehaving terminal user
straight from the NodeSpy panel:

- **Kick button** in the per-row NodeSpy table (red door-arrow icon)
- **Kick form** in the per-node detail page with an optional reason
  text field
- New endpoint: `POST /admin/control/nodespy/<slot>/kick` (CSRF-
  protected, admin-only)
- New function: `multinode.kick_node(slot, reason)` — pushes a 'kick'
  payload to the session's chat queue, writes a goodbye line to the
  user's terminal, then closes the underlying transport. The session's
  reader EOFs and the session cleans up naturally.
- `NodeEntry` gained a `session` reference (passed by `acquire_slot`
  in `core/session.py`) so the kick handler has a handle to close.
- Audit log entry in `user_activity` for every kick (action=`kick_node`,
  details = "slot N (username): reason"). So sysop kicks are
  traceable.

Different from a ban — this just drops the current connection. The
user can reconnect immediately. To prevent reconnect, sysop must
also add an IP ban under `/admin/ip-bans/`.

## v282 — consolidation cut (May 2026)

Clean release after the v280-v281.3 hotfix series. No new behavior
beyond what the v281.3 patches already shipped — this is purely
a docs + memory + audit-pass roll-up so the next deploy carries
matching docs alongside the running code.

- Docs updated: CHANGELOG, FEATURES, 14-door-games (door_rlogin
  worked example, dosemu removal note), 16-rss-reader.
- Memory entries updated: project_status reflects current cursor
  + RSS + door_rlogin work; rlogin handshake quirk recorded at
  project_rlogin_format.md; LORD setup guidance at
  project_lord_setup.md; door-files-hands-off feedback at
  feedback_door_files.md.
- Final audit: 50 blueprints, 319 routes, 189 templates parse,
  zero broken `url_for()` references, app boots clean. Pyflakes
  has no undefined-name / redefinition warnings — only intentional
  drains and load-bearing imports remain.

## v1.0a — RSS reader (May 2026, internal v281)

Built-in RSS / Atom feed reader for both web and terminal. Sysop
manages feeds at /admin/rss/. Background poller refreshes every
30 minutes (configurable via `RSS_POLL_INTERVAL` env var). X-News
seeded by default so a fresh install has at least one feed populated.

**New tables** (auto-created by the lightweight migration sweep):
- `rss_feeds` — sysop-configured feeds
- `rss_items` — articles (deduped per-feed by GUID)
- `rss_read_status` — per-user read markers

**New blueprints:** `rss` at `/rss/`, `rss_admin` at `/admin/rss/`.

**New file:** `anetbbs/rss/poller.py` — background daemon thread.

**New dependency:** `feedparser>=6.0` (handles RSS 2.0 / Atom / RSS 1.0).

**Web UI:** Tools → RSS Reader. Feed list with unread badges, "all
feeds" river view, paginated per-feed item lists, single-item full
content view, mark-as-read tracking per user.

**Terminal UI:** Main BBS Menu → R. Feed picker, river, paginated
item lists, single-item viewer with word-wrapped body. Same mark-read
state shared with the web UI.

**Pre-seeded feed:** `https://x-bit.org/rss/rss.xml` (X-News).

**v281.1** — fixed Jinja name collision. Renamed template var from
`unread` to `feed_unread` because base.html does `{% set unread = ... %}`
for the PM badge counter, which shadowed our context variable inside
child templates and turned the dict into an int.

**v281.2** — added RSS to the data-driven menu engine. v281 only
added it to the hardcoded `BBSMenuUI.show_main()` fallback; the
running terminal sessions use the `BbsMenu` table-driven menu, which
needed a new `rss` action_type registration + a `R` hotkey entry in
`DEFAULT_MENUS` (auto-backfilled to existing installs by
`seed_default_menus`).

**v281.3** — typo: `FG['ylw']` → `FG['yel']`. Crashed `show_rss()`
on first launch.

See [`docs/16-rss-reader.md`](16-rss-reader.md) for full feature docs.

## v1.0a — A-Net Game Server / rlogin doors (May 2026, internal v280)

New game type: **A-Net Game Server (rlogin)**. Lets BBS users
transparently rlogin into a remote Synchronet door game server
(or DoorParty, or any other rlogin-accepting BBS host). The user
sees one continuous session — no second login prompt.

**Architecture:**

- New `anetbbs/games/rlogin_bridge.py` with `RloginConnection` —
  same write/stop/bind_emit shape as `DosBridge` so it slots into
  the existing DoorSession machinery (send_input, terminate_session,
  cleanup all work without changes).
- New `launch_rlogin_session()` in `door_runner.py` for the web flow.
- New `play_rlogin_telnet()` in `door_runner.py` for the terminal
  flow. Both use the same `RloginConnection`.
- Game type `door_rlogin` selectable in `/admin/games/` with its own
  helper section in the form (server host:port + user template +
  password + optional terminal hint for direct-to-door launches).

**Wire format:** Synchronet's BBS-mode rlogin server uses INVERTED
field order vs RFC 1282 — the password goes in the client-user-name
slot (1st field) and the BBS username in the server-user-name slot
(2nd). Verified against game.a-net-online.lol on 2026-05-09. Memory
note saved at `memory/project_rlogin_format.md`.

**dosemu removed.** The half-finished `door_dosemu` game type was
yanked from the dropdown — drive-letter detection was wrong and the
admin form fields were missing. `_build_dosemu_command` stays in the
source tree as dead code in case someone wants to validate it
properly later.

**v280.1–v280.4 — admin form polish + rename:** dropdown renamed
"Synchronet xtrn / DoorParty" → "A-Net Game Server (rlogin)". Added
a dedicated form section so the executable_path / command_line_args
fields are visible when door_rlogin is selected (same fix pattern as
the gallery_admin disappearing-fields bug). Updated helper text with
working examples for game.a-net-online.lol direct-to-door
(`xtrn=LORD408`, `xtrn=ASSASSIN`, etc.).

## v1.0a — door I/O safety + auto-recovery (May 2026, internal v279.1–v279.9)

A series of hotfixes after v279 wired up the DosBridge — fixing
real-world door issues that surfaced during testing.

- **v279.1:** `_build_dos_command` now accepts `token_ctx=None` for
  validation-only callers (the terminal launcher previewed the
  command before allocating a bridge port; without this it crashed
  with "unexpected keyword argument").
- **v279.2:** DOOR.SYS / DORINFO / DOOR32.SYS now identify as `COM1:`
  + 38400 baud + comm type 1 (FOSSIL) instead of `COM0:` + 0 + comm
  type 2. Most classic doors (LORD especially) interpret COM0/0 as
  "local console — bypass FOSSIL, write to BIOS only", which dropped
  all output before it could reach the bridge.
- **v279.3:** bridge diagnostic logging — first 5 chunks logged
  verbatim, "0 bytes after 10s" warning if door isn't writing to
  FOSSIL, total chunk/byte count on close.
- **v279.4:** terminal `door_dos` now uses the unified launch path
  (`launch_door_game` via `play_door_game_telnet`). The legacy
  `play_dos_game_telnet` had its own DOSBox-launching code that
  predated the bridge wiring and never got updated; terminal users
  were getting silent hangs.
- **v279.5:** waitpid watcher thread. xvfb-run can leave Xvfb running
  past the door exit (Xvfb inherits the PTY slave_fd, keeping
  master_fd from EOFing), which means our PTY watcher never wakes
  to fire cleanup. The new watcher does `os.waitpid(pid, 0)` and
  triggers `_cleanup_session` when the door subprocess actually
  exits — independent of PTY EOF.
- **v279.6:** bridge close → cleanup signal. When DOSBox closes its
  end of the TCP nullmodem (which it does on exit), the bridge fires
  a new `on_close` callback that runs `_cleanup_session` immediately.
  Faster than waitpid for the common case.
- **v279.7:** real Ctrl+]q abort. The launch banner has been telling
  users "press Ctrl+] then q to abort" since forever, but the input
  pump just forwarded those bytes to the door. Now the pump
  actually watches for the sequence and triggers cleanup.
- **v279.8:** idle-timeout watchdog (default 300s). If zero bytes
  flow in either direction for the timeout, the bridge force-closes
  and triggers cleanup. Catches stuck-door cases (LORD's exit-loop,
  infinite "press any key" prompts on hidden screens).
- **v279.9:** idle timeout reduced to 60s default + made configurable
  via `DOOR_IDLE_TIMEOUT`. Also force-kills the door process group
  via SIGTERM (then SIGKILL 2s later) when timeout fires, instead of
  just trusting `_cleanup_session` to do it.

End result: stuck DOS doors always return to the BBS within 60
seconds, the BBS user's session never permanently hangs, orphan
DOSBox processes get reaped.

## v1.0a — DOS door bridge wired up (May 2026, internal v279)

**v279 — actual DOS door I/O.** The previous DOSBox configs wrote
`serial1=stdio`, which DOSBox-staging 0.80+ silently downgraded to
`dummy` — meaning DOS door output and keystrokes never reached BBS
users. The whole DOS door path was end-to-end broken on modern DOSBox.

This release wires `dos_bridge.py` (which has been complete-but-unused
in the tree for a while) into the launch flow:

- DosBridge picks a free TCP port (5000–5100) and listens.
- DOSBox config is `serial1=nullmodem server:127.0.0.1 port:NNNN
  transparent:1`. DOSBox dials in at boot.
- New `DosBridge.bind_emit(emit_fn)` reads bytes off the TCP socket and
  forwards them to the BBS user's terminal via `socketio_emit_fn`.
- New `DosBridge.write(data)` accepts BBS-side keystrokes and pushes
  them onto the TCP socket so DOSBox's COM1 receives them.
- `DoorSession.write()` routes through the bridge when present, so the
  rest of the BBS doesn't have to care which transport is in use.
- `DoorSession.close()` also stops the bridge so the listener doesn't
  leak.
- The PTY remains open on door_dos sessions but only as a process-exit
  watcher; door I/O no longer flows through it.
- DOSBox config also stripped invalid `output=null` and `autolock`
  options that staging 0.82.x rejects.

End result: vanilla DOSBox 0.74-3, DOSBox-staging 0.82.x, and DOSBox-X
all work for `door_dos` games via TCP nullmodem. No special build
required.

## v1.0a — door-runtime polish (May 2026, internal v277–v278)

Hot-fixes plus the dosemu2 path, deployed on top of the v276 audit pass.

**v278:**
- New `door_dosemu` game type — uses dosemu2 (`-dumb` stdio) instead of
  DOSBox. Better latency, no FOSSIL gymnastics over TCP nullmodem,
  apt-packaged via dosemu2 PPA on Ubuntu / native on Debian. Mounts
  game dir + per-node temp dir + bundled FOSSIL driver dir, autoexec
  loads `BNU.COM` on COM1 for parity with the DOSBox path so the same
  LORDCFG / TWCFG settings work either way.
- Snap-confine detector in `_build_dos_command`: when `dosbox` /
  `dosbox-staging` resolves to a `/snap/` path, we reject it up front
  with a clear "remove snap, install apt or AppImage instead" message.
  Avoids the cryptic `cap_dac_override not found` error on first
  launch from systemd.

**v277:**
- DOSBox per-node mount: the per-node temp dir is now mounted as drive
  E: in addition to the game dir on C:. Sysops set LORDCFG's "Path to
  dropfile" to `E:\` and multi-node LORD / TradeWars / TQW just works
  without overwriting each other's drop files.
- `docs/14-door-games.md` and `docs/15-synchronet-compat.md` published.

## v1.0a — pre-release polish (May 2026, internal v195–v276)

Final shake-out before tagging the alpha.

**New: Image galleries**
- `/gallery/` — paginated thumbnail grid + full-screen modal viewer.
  Browser-native, lazy-loaded.
- `/admin/galleries/` — full CRUD on collections (label/slug/path/sort/
  active) plus per-gallery file management (drag-drop multi-upload,
  click-to-delete, pagination).
- Storage: `gallery-config.json` at install root, auto-seeded on first
  run from any DSR `gifs` / `swim` directories that exist. Excluded
  from deploy so sysop edits survive `rsync --delete`.
- Standalone terminal viewer: `/home/<user>/anet-gallery.sh` (chafa /
  img2sixel) — kept outside the install dir so deploys don't remove it.
- Galleries link lives under **Tools** in the top nav (kept off the
  primary bar to avoid wrapping on smaller widths).
- New doc: [`13-image-galleries.md`](13-image-galleries.md).

**Removed: DSR (Digital Showroom)**
- The Synchronet sixel image-viewer door is no longer wired into the
  games menu. The `convert → .sixel` pipeline returned rc=0 with no
  output file when the door was launched from a gunicorn-spawned PTY
  child (worked manually but not under the web service); root cause
  not identified after extensive investigation. The web `/gallery/` +
  terminal `anet-gallery.sh` cover the same use-case at higher quality.
- DSR files remain on disk under `doors/sbbs/dsr/` so a future
  reinstatement is possible without re-extracting from the .zip.

**Codebase audit**
- Real bug found and fixed: `current_app` referenced in `web/admin.py`
  but missing from the flask import line (would have NameError'd at the
  affected admin routes).
- Real bug found and fixed: missing `requests` from `setup.py`
  install_requires — `webhooks.py` imports it unconditionally so a fresh
  `pip install -e .` would crash on first webhook dispatch.
- Real bug found and fixed: missing `admin/time_budgets.html` template
  — `/admin/time-budgets` would 500 on GET. Template added matching the
  rest of the admin UI.
- Real bug found and fixed: 4 broken `url_for()` references that would
  raise `BuildError` at render time (`echomail.view_message`,
  `boards.view_thread`, `pm.read_message`, `echomail.list_messages` —
  endpoints renamed in earlier work but the saved-messages and
  permalinks blueprints still pointed at the old names).
- Real bug found and fixed: 3 duplicate method definitions in
  `core/session.py` (`write` / `read_line` / `clear_screen`) — Python
  silently kept only the later definitions, the first ones were dead
  code. Removed.
- Real bug found and fixed: 3 stray `f''`-prefixed strings without
  placeholders in `web/echomail_admin.py` and `web/irc_web.py`.
- Real bug found and fixed: `update.sh` rsync was missing `--exclude`
  for `/doors/` and `/gallery-config.json` — closes the same hole that
  caused the v195 deploy incident.
- Duplicate `import json` in `echomail/tic.py` removed.
- Duplicate `from typing import Dict` in `core/session.py` consolidated.
- Explicit `__all__` added to `anetbbs/core/__init__.py` (silences
  pyflakes false-positive on re-exports).
- Orphan templates removed: `admin/callers.html` (replaced by
  `admin/caller_log.html`), `chat/index.html` (legacy local-chat,
  replaced by MRC).
- ~80 unused imports trimmed across the package — pyflakes warning
  count down from ~110 to 22, with the remaining warnings all
  intentional (load-bearing `SessionProtocol` re-export, protocol-level
  drain variables, dead `global` declarations).
- All 180 Jinja templates parse cleanly.
- All 308 `url_for()` references resolve to a registered endpoint.
- 79/81 SQLAlchemy models are queried in code; the two stragglers
  (`ChatMessage`, `MenuTranslation`) are leftover scaffold tables that
  stay in the schema for now to avoid alpha migration churn.
- Zero outstanding `TODO`/`FIXME`/`XXX`/`HACK` markers in our code
  (matches in vendored Synchronet `.js` stubs are upstream's).
- New top-level [`FEATURES.md`](../FEATURES.md) — single-page inventory
  of every user-visible feature for the alpha.
- `docs/00-overview.md` updated with the gallery feature.
- `docs/PORTS.md` + `deploy/README.md` now document the Finger
  service (port 79).

**Error pages + nav coverage**
- New branded `404.html` / `403.html` / `500.html` templates wired up
  via `errorhandler()` (previous behaviour fell through to Flask's
  default monochrome error pages).
- Sysop nav: added `Time Budgets` link to the Admin → Users dropdown
  (the `/admin/time-budgets` page was registered but unreachable from
  the UI — only the URL bar got you there).

**Config completeness**
- `.env.example` expanded with previously-undocumented but code-used
  settings: `SYSOP_NAME`, `BBS_DOMAIN`, `BBS_PUBLIC_HOST`, `BBS_EMAIL`,
  `BBS_LOCATION`, `BBS_NODES`, `IDLE_TIMEOUT_SECONDS`,
  `FINGER_LISTEN_HOST/PORT`, `BINKP_LISTEN_HOST/PORT`,
  `BINKP_OUR_ADDRESS`, `BINKP_SYSTEM_NAME`, `CLAMSCAN_PATH`,
  `IRC_LOG_CHANNELS`, `NUV_ENABLED`, `RATIO_MIN`.

**Door-game runtime parity with Synchronet / Mystic**

A real BBS gives each active node its own scratch directory and
substitutes shortcodes (Synchronet `%f`, Mystic `%P` etc.) in door
command lines. Both were missing; both shipped this session.

- New `anetbbs/games/node_paths.py` — per-node temp dirs at
  `<install>/data/temp/nodeN/` (1..`BBS_NODES`), auto-created on app
  boot. Owns the substitution table.
- Synchronet `%`-token vocabulary supported in `Game.executable_path`,
  `Game.working_directory`, `Game.command_line_args`, and
  `Game.drop_file_path`: `%a`, `%c`, `%d`, `%e`, `%f`, `%g`, `%h`,
  `%i`, `%j`, `%k`, `%l`, `%m`, `%n`, `%o`, `%p`, `%r`, `%s`, `%t`,
  `%u`, `%w`, `%y`, `%z`, `%!`, `%%`.
- Mystic `%`-token vocabulary supported in the same fields:
  `%P` (per-node temp dir w/ trailing `/`), `%N`, `%M`, `%U`, `%T`,
  `%H`, `%R`, `%E`, `%S`, `%L`. So `%Pdoor32.sys` correctly resolves
  to e.g. `/data/temp/node3/door32.sys`.
- `Game.drop_file_path` is now %-token expanded (was only `{node}`).
- New doc: [`docs/14-door-games.md`](14-door-games.md) — full token
  table, LORD walk-through, and the rest of the door-config story.
- New doc: [`docs/15-synchronet-compat.md`](15-synchronet-compat.md) —
  xtrn coverage survey, what stock Synchronet doors are likely to work
  through our Node shim vs. need a real `jsexec`.

**Mystic .mps auto-compile**
- New `_ensure_mps_compiled()` in `door_runner.py` — if `mplc` is on
  the host, automatically compile `.mps` source → `.mpx` bytecode
  before launch (only if source is newer than bytecode). Falls back to
  an existing `.mpx` next to the source if `mplc` isn't installed.
- New `MYSTIC_MPLC_PATH` env var for explicit override.

**Installer modes (test vs production)**
- `install.sh` now starts with an "Install mode" prompt:
  - **production** = real BBS facing the public internet — wants
    nginx, SSL, a domain pointed at the box, privileged-port services
    (Finger/MSP/SYSTAT) reachable from peers.
  - **test** = local-only / behind NAT / no static IP / just trying it
    out — auto-skips nginx, certbot, Finger/MSP/SYSTAT/BinkP. Gunicorn
    binds 0.0.0.0:5000 directly. Sysop can flip individual flags later
    without re-installing.
- New optional install step: download Mystic Linux freeware tarball,
  extract `mystic` + `mplc` to `/opt/mystic/`, symlink into
  `/usr/local/bin/`, write `MYSTIC_MPLC_PATH` to `.env`. Soft-fails
  if the download URL is unreachable (no network, mysticbbs.com down).
- `anetbbs-finger.service` unit now installed by `install.sh` when
  `ENABLE_FINGER=y` (was never deployed by the installer despite the
  unit file existing in `deploy/`).
- Test-mode summary message at end of install spells out exactly what
  was skipped and how to re-enable later.

**Deploy / ops**
- `update.sh` and the documented rsync recipe explicitly
  `--exclude=doors --exclude=gallery-config.json --exclude=data/` to
  prevent the v195-era incident where a `--delete` deploy wiped the
  doors tree (DSR, BotWars, RDQ3, the 6500-GIF library) from a
  production install.

**Misc**
- `permission denied` regressions on the MRC bridge fixed by ensuring
  service unit `User=stingray` matches actual file ownership on the
  reference deployment (was getting accidentally chown'd to anetbbs).

## v1.0a — alpha refresh (May 2026, internal v194)

Subsequent rolling internal builds (v165-v194) refine the alpha. Public
tarball at `ANetBBS-v1.0a.tar.gz` always tracks the latest internal cut.

**Highlights since the v164 alpha:**

- **MSP / Inter-BBS IM correctness** — the previous wire format was a
  homegrown 5-field variant that Synchronet rejected silently and we
  mis-parsed in the inbound direction. v168 ships a real RFC 1312 MSP-2
  encoder/decoder (7 fields with a leading 'B'/'A' type byte), strips
  `@host` from inbound recipients, optionally answers acked-mode requests.
- **Synchronet `@CODE@` substitution** in our Synchronet stub layer's
  `printfile` — `@BBS@`, `@USER@`, `@SYSOP@`, `@TIME@`, `@DATE@`,
  `@SECURITY@`, `@CALLS@`, `@NODE@`, etc. resolve from the BBS_*
  environment variables that `door_runner.py` exports.
- **Display-code preprocessor** for ANSI screens and BbsMenu screens —
  Synchronet @-codes (`@USER@`, `@BBS@`, `@TIME@`, ...) and Mystic
  named codes (`|UN`, `|BN`, `|DT`, ...). Color-pipe codes like `|07`
  remain handled by the existing `_pipe_to_ansi`.
- **QWK polling improvements** — Synchronet's `550 No QWK packet
  created (no new messages)` is now treated as benign instead of a
  fatal poll error (was generating thousands of false ERROR lines per
  day). Poll interval clamped to ≥5 minutes to prevent a misconfigured
  DB from hammering remote hubs.
- **Web-stack hardening** — `KillMode=mixed` + `RestartSec=10` on the
  gunicorn unit so eventlet workers don't leak past the master and
  cause EADDRINUSE crash loops; `_create_default_data` `os` shadowing
  bug fixed; `body` keyword for `PrivateMessage` (was `content`);
  `/help` endpoint repaired.
- **Permissions** — install.sh asserts ssh_host_key + sbbs_stubs/
  + doors/ permissions on every run (not only at first generation),
  with sbbs_stubs source files now shipped world-readable so future
  rsync deploys don't reset BotWars/RDQ3 to broken.
- **Terminal MRC client** — extensive UX work:
    - anetmrc-style stationary status bar at the top (room, topic,
      mention badge, server latency)
    - DECSTBM scroll region for chat below it
    - HH:MM timestamp prefix on every chat line, ANSI-aware word-wrap
      with continuation indent
    - Tab-complete usernames (active list seeded from chat events,
      `/who`, `/chatters`)
    - New slash commands: `/afk [msg]`, `/back`, `/status`,
      `/roomconfig`, `/termsize`, `/whoon` alias of `/who`
    - `/info <id>` now passes the BBS index to the server
    - `/scroll` removed (terminal-native scrollback works fine)
    - Removed the doubling-on-wrap bug (input is slide-windowed; chat
      uses ANSI-aware word wrap instead of native terminal wrap)
- **Terminal BBS UI** — single-key hotkeys (no Enter required), screen
  clear before each menu, Q logoff confirms with Y/N, banner+colored
  rendering on Boards / Echomail / Bulletins / Who's Online / Sysop
  Tools / Profile / PM compose / IM compose / inboxes, paged area
  picker for compose-echomail, terminal IM reader (`[I]` and `[J]`).
- **Web nav reorg** — Inter-BBS IM badge in the top bar (next to PM
  envelope and notification bell), Web Terminal moved from Chat
  dropdown to top of Tools dropdown.
- **Privacy** — IP column on `/who` now hidden from non-admins.
- **CP437 mojibake recovery** for ANSI screens stored as Latin-1 in
  the DB (encode→bytes→write-as-CP437 path so SyncTERM and modern
  terminals both render correctly).
- **Dialout default directory** trimmed to a single seed entry
  (sysop adds the rest via `/admin/dialout`).
- **Door runner** — door cleanup callback now runs inside a captured
  Flask app context (was leaking "Working outside of application
  context" errors when sessions ended).
- **Web service unit** — config templated for `/opt/anetbbs/`; the
  `install.sh` rewrite handles `INSTALL_DIR` substitution properly.

## v1.0a — first alpha (May 2026)

Bundles all internal versions through v164.

**Major features:**
- Web BBS (Flask + SocketIO) + telnet + SSH + rlogin front-ends
- FidoNet binkp echomail + netmail with full kludge support (MSGID, REPLY,
  INTL, FMPT/TOPT, CHRS, PID, TZUTC), CRAM-MD5 BinkP auth, optional TLS
- DOVE-Net QWK echomail with REP packet outbound, CONTROL.DAT-driven
  area auto-create, 1-on-1 QWK netmail compose
- Inter-BBS Instant Messaging via MSP (RFC 1312) on TCP/18 + SYSTAT/
  ActiveUser on UDP/11, with the Synchronet `sbbsimsg.lst` directory
  mirrored daily and a built-in BBS picker UI
- **Terminal MRC chat** (telnet/SSH/rlogin) — talks to the same local
  websocket bridge that web users hit, so terminal and browser users
  share the same hub identity and chat rooms. Full command set
  (`/identify /msg /me /join /list /who /motd /banners /topic /scroll
  /mentions /trust /broadcast …`); ←/→ arrow keys cycle outgoing text
  color; `/identify` mid-line password masking; @mention highlighting
  with bell; outgoing 140-char cap with auto-split on word boundaries;
  ROOMTOPIC banner + topic snippet baked into the prompt
- Local boards with voting, search, sticky/lock, threading, ANSI banners,
  per-board moderators
- File bases with TIC ingest, outbound TIC hatching, `FILE_ID.DIZ`
  auto-extract, per-area upload permissions
- Doors: DOSBox (auto-detect staging/x/vanilla), Mystic .mps and .mpy,
  Synchronet .js (real jsexec when available, Node.js shim fallback),
  native binaries — all with DOOR.SYS / DOOR32.SYS / DORINFO drop files
- Private messages, FTN netmail, CP437 + ANSI rendering everywhere
- AreaFix in/out with per-network admin queue, BadArea sysop-review queue
- Admin: rate limits on login/MSP/votes, random initial admin password
  written to `data/admin_password.txt`, SECRET_KEY guard refusing dev
  default in production, open-redirect-hardened login

## Internal version history (build numbers)

| ver  | summary |
| ---- | ------- |
| v164 | MRC: ROOMTOPIC captured + shown in prompt + topic banner; mention bell + brighter highlight; `/scroll` empty-state; `/mentions` to clear counter |
| v163 | MRC terminal: dropped DECSTBM scroll regions (unreliable on BBS terminals); switched to inline-redraw model |
| v162 | MRC terminal: truncate-to-width on emit, dropped SAVE/RESTORE_CURSOR |
| v161 | Taglines append on echomail + QWK netmail (was netmail-only) |
| v160 | MRC terminal: separator/input row collision fix; ←/→ arrow-key color cycling |
| v159 | MRC terminal: split-screen UI, 140-char outgoing cap, scrollback, mention highlighting, full command roster |
| v158 | MRC terminal: trigger-space stays visible on /identify; broader server-noise suppression (BANNER, TYPING, CAPABILITIES, NEWROOM) |
| v157 | MRC terminal: inline /identify password masking; USERLIST: protocol-leak suppression |
| v156 | MRC terminal: `/join` uses NEWROOM (was resetting trust state via re-handshake) |
| v155 | MRC terminal: dropped MRC submenu — choice 3 takes you straight in; masked-input tip |
| v154 | MRC terminal: IDENTIFY/REGISTER/UPDATE corrected to top-level verbs (not TRUST subcommands) |
| v153 | MRC terminal: full slash-command set (motd/banners/topic/roompass/lastseen/last/etc.); cleaner event renderer |
| v152 | Restored SessionProtocol import (telnet/SSH service crash fix) |
| v151 | Terminal MRC client (talks to local websocket bridge — terminal+web users share rooms) |
| v150 | Hands-off installer: CAP_NET_BIND_SERVICE in systemd unit; optional-deps prompts (DOSBox/ClamAV/lhasa); MRC bridge gated; UFW prompt |
| v149 | Cleanup: stale tarballs, __pycache__, egg-info, dead imports; file-delete bug (uploader_id vs user_id) |
| v148 | Upgrade-wizard fix: auto-heals SECRET_KEY in .env; ANETBBS_SCHEMA_MIGRATE_ONLY bypass for migration subprocess |
| v147 | SECRET_KEY guard relaxed for migration mode (incomplete; superseded by v148) |
| v146 | Security pass: random admin pw, SECRET_KEY guard, decorator fix, open-redirect, rate limits |
| v145 | TZUTC netmail kludge + board search route |
| v144 | File-area scoping on uploads + `upload_permission` enforcement |
| v143 | Synchronet doors prefer real jsexec; SBBSEXEC/SBBSCTRL/SBBSDATA/SBBSNODE env in fork |
| v142 | MSP send form accepts `user@host` paste; wire-tested to a-net-online.lol |
| v141 | SYSTAT/ActiveUser UDP service + sbbsimsg.lst directory + BBS browser UI |
| v140 | MSP / RFC 1312 Inter-BBS Instant Messaging — server, client, inbox, send form |
| v139 | FidoNet netmail compose actually sends (was dead-letter); FTS-0001 12-byte routing header fixed; @INTL/@FMPT/@TOPT |
| v138 | Up/down voting on board posts, echomail, PMs |
| v137 | File upload: bug fixes + `FILE_ID.DIZ` / README auto-extract; ALLOWED_EXTENSIONS broadened |
| v136 | AreaFix log viewer |
| v135 | QWK auto-create areas from CONTROL.DAT; poll-log no-longer-shows-negative-counts fix |
| v134 | AreaFix-on-subscribe; QWK quick-add UI |
| v133 | Auto-sweep schema migration; BadAreaLog viewer |
| v132 | SBBSecho parity: CRAM-MD5 BinkP, soft-CR strip, UTF-8 detect, PATH dupe, BadAreaLog model |
| v131 | CP437/ANSI rendering filter + image-link helper for boards |
| v130 | CP437/ANSI Jinja filter; QWK netmail (private 1-on-1) compose + inbox + REP-with-status='*' |
| v129 | QWK parser fix — num_chunks is ASCII not binary (caused garbage areas + crammed bodies) |
| v128 | Echomail nav link, misc UI |
| ...  | (earlier history archived) |

The full per-version diff lives in git.
