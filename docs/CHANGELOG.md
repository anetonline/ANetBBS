# ANetBBS Changelog

Current release: **`v1.0.31`** (August 2026). This file covers `v1.0.0`
onward, which follows standard semantic versioning — patch releases are
`v1.0.1`, `v1.0.2`, and so on. The full internal beta build-number
history (`v1.0a1.1` through `v1.0b2.239`) that got the project to this
release is preserved in
[`CHANGELOG-beta.md`](CHANGELOG-beta.md).

## v1.0.31 — Fixed the actual root cause behind Minesweeper's missing DOVE-Net scores (August 2026)

**`iniGetObject()` silently discarded a sysop's whole `modopts.ini` override when called with real Synchronet's overloaded boolean-first-argument form.** After v1.0.30's `readAll()` fix, "view winners" still showed nothing — traced live with a real sysop, step by step, against real production data: file permissions checked out, and a pre-existing debug log line in Minesweeper itself revealed `options.sub` was resolving to `false` despite a correctly-placed, correctly-permissioned `modopts.ini` containing `sub=2013`. Root cause: real, unmodified Synchronet library code (`modopts.js`'s own `iniGetObject(/* lowercase */false, /* blanks */true)`, also used identically by `install-3rdp-xtrn.js`) routinely omits the `section` argument entirely and passes the boolean flags positionally instead — a boolean can never legitimately be a section name. The compat shim's `File.prototype.iniGetObject()` didn't account for this, so `section === false` fell through to looking up a section literally named `"false"`, found nothing, and returned `null` — discarding the entire root section (every plain `key=value` line before any `[header]`) with no error anywhere. Fixed by detecting a boolean first argument and treating it as the `lowercase` flag, defaulting `section` to root. This was the actual final blocker in the whole DOVE-Net score-sharing chain — Minesweeper's `get_winners()`, the `MsgBase` caching (v1.0.29), and the `readAll()` fix (v1.0.30) were all correct the entire time.

## v1.0.30 — Fixed a data-loss bug in the JSONL file-reading compat shim (August 2026)

**Minesweeper's "view winners" showed a totally empty list even after the v1.0.29 lockup fix — real report, traced all the way to a live data dump.** After ruling out every filtering/checksum step in `get_winners()` against real production data (confirmed live: the message's `To:`/`Subject:`/`direction` all matched correctly, and the MD5 checksum verified byte-for-byte), the actual culprit turned out to be `File.prototype.readAll()` in the JS compat shim (`anetbbs/games/synchronet_compat.py`): every line written via `writeln()` — the standard JSONL-append pattern `json_lines.js`'s `add()` uses — ends with a trailing `\n`, so a naive `content.split('\n')` produces one spurious empty-string "line" after the real content. `json_lines.js`'s own `get()` then calls `JSON.parse('')` on that phantom line, which throws — and since `get()` has no recovery flag by default, that ONE synthetic empty line made it return an error string instead of the parsed array, silently discarding every real entry. Confirmed live: Minesweeper's `netwins.jsonl` had 85+ correctly-imported real win entries from DOVE-Net the whole time — `get_winners()` was throwing all of them away every single call. Fixed by stripping exactly one trailing newline before splitting (a genuine blank line elsewhere in a file is still preserved). This affects any door using the standard JSONL-append idiom, not just Minesweeper.

**Added the missing `file_getcase()` global — real bug found live via LORD2.** `l2lib.js`'s `getfname()` calls it to resolve asset filenames case-insensitively, a legacy pattern from DOS/Windows-era door development that only ever mattered once running on a real case-sensitive filesystem (every Linux install, including ANetBBS). The global didn't exist in the compat shim at all, so any door calling it hit a `ReferenceError` immediately. Implemented to match real Synchronet's documented behavior (case-insensitive directory scan, returns the actual on-disk filename or `undefined`).

## v1.0.29 — Fixed a real lockup in InterBBS door score-sharing (August 2026)

**Minesweeper's "view winners" screen looked like a total lockup — real report after setting up DOVE-Net/syncdata score sharing.** Not an infinite loop: `get_winners()` scans a synced echo area and calls `get_msg_header()`/`get_msg_body()` once per matching message, and the JS `MsgBase` compat shim backed each of those with a *separate* subprocess spawn — a fresh Python process, fresh Flask app, fresh SQLAlchemy init, every single call. Against a DOVE-Net area with real accumulated InterBBS history, "view winners" meant potentially hundreds of sequential spawns before anything displayed — easily minutes with no progress indicator. `msgbase_bridge.py`'s `get_index` op now embeds each entry's header/body fields inline (the one query already has them loaded), and `MsgBase` caches them per message number in `anetbbs/games/synchronet_compat.py`, so `get_msg_header()`/`get_msg_body()` serve from memory instead of shelling out again — the whole scan is now one subprocess call instead of hundreds. Also added a 30s timeout to the subprocess spawn itself as a safety net, so a single genuinely-stuck call (e.g. real DB lock contention) fails cleanly instead of hanging forever. This fixes score-sharing for any door using the real `MsgBase` API against a configured echo area, not just Minesweeper.

## v1.0.28 — PETSCII new-user registration fixes (August 2026)

**The `newuser` welcome banner displayed as literal garbage on PETSCII — real bug found live on the Pi.** `_show_ansi_screen()` writes raw CP437/ANSI bytes directly to the socket, bypassing `write()`'s petscii translation branch entirely — the exact same limitation already guarded against for the `'welcome'` and `'goodbye'` screen slots, just missed for `'newuser'`. A real PETSCII session saw the sysop's `newuser.ans` banner as literal `ESC[...m` escape codes with case-inverted text instead of a rendered screen. Fixed with the same `if self.term_mode != 'petscii'` guard already used for the other two slots — "Registration successful!" (which already goes through `write()` correctly) still confirms the account was created; petscii users just don't get the customizable ANSI banner, the same tradeoff already accepted for `'welcome'`/`'goodbye'`.

**Security-question and newuser-questionnaire prompts broke mid-word on a 40-column PETSCII screen — another real bug from the same screenshots.** These prompts were written as long unwrapped lines via `session.write()` and left to the terminal's own hardware auto-wrap, with no word-boundary awareness. New `_prompt_width()`/`_wrap_text_lines()` helpers in `session.py` (petscii_width-aware, falling back to window_size/80 for every other term_mode) now word-wrap the security-question list, the "Question N of 3" selection prompt, and the sysop-defined newuser questionnaire prompts.

## v1.0.27 — ASCII MRC chat client; word-wrap fix for embedded newlines (August 2026)

**New `AsciiMRCChat` client for `term_mode == 'ascii'` sessions.** `ascii` has always been a real, selectable terminal mode, but it never had its own MRC client the way PETSCII now does — `chat.py`'s `ChatManager` handed every session the full ANSI split-screen `MRCChat` regardless of mode. `session.write()` strips every ANSI escape sequence outright for ascii sessions, so that split-screen mode's DECSTBM scroll-region setup, CPR terminal-size probe, and cursor-addressed status/input/ticker draws were all silently dropped — a real, structural bug (ascii+MRC had no usable layout at all), not just a missing feature. `AsciiMRCChat` (`anetbbs/features/mrc_chat_ascii.py`) is the same plain-scroll-mode override pattern already proven by `PetsciiMRCChat`, simplified since ASCII has no case-inversion, no color-byte translation, and no special DEL key — just standard `\x7f`/`\x08` backspace. `ChatManager.__init__` now picks `AsciiMRCChat` for `term_mode == 'ascii'` and `MRCChat` for everything else.

**`_word_wrap()` fix for embedded newlines — real bug found live on the Pi.** A multi-line MOTD/banner from the MRC bridge arrives as one string with its own intentional `\n` line breaks. The word-wrap tokenizer (shared by both `MRCChat`'s ANSI split-screen `_emit()` and `PetsciiMRCChat`/`AsciiMRCChat`'s plain-scroll `_emit()`) only charged an embedded `\n` 1 column against its width budget, but the terminal itself resets to column 0 there — so the algorithm's internal column count and the real cursor position diverged, leaving whatever word came right after the newline in the source text stranded alone at the left margin (seen live at 40 columns: "at", "!list", "or", and a URL each appearing as isolated fragments). Fixed by treating `\n`/`\r\n` in the input as hard breaks, word-wrapped independently, before the normal width-based reflow runs.

## v1.0.26 — PETSCII MRC chat: real word-wrap instead of raw terminal auto-wrap (August 2026)

**Another real bug found live-testing on the Pi, worse at 40 columns than 80.** `PetsciiMRCChat._emit()` was just writing each message's raw text and letting the terminal's own hardware auto-wrap break it wherever the physical column happened to land — no word-boundary awareness, so long messages could split mid-word and continuation text had no relationship to the original line. Fixed by reusing `MRCChat`'s own `_word_wrap()` helper (the same one the ANSI split-screen client already uses) so messages wrap cleanly at word boundaries regardless of screen width, written as a single atomic write (still serialized against incoming/outgoing keystrokes via the shared lock from the previous release).

## v1.0.25 — PETSCII MRC chat: password masking, AFK interruption, message-splicing fixes (August 2026)

**Three real bugs found live-testing v1.0.24's new PETSCII MRC client on the Pi, all traced to the same root cause.** `PetsciiMRCChat._read_chat_line()` originally delegated to the generic `session.read_line()` for simplicity — that turned out to be wrong three ways:

1. **`/identify <password>` echoed the password in the clear, unmasked.** `read_line()` has no masking; the real per-keystroke masking logic lives in the ANSI client's raw input loop, which the PETSCII override bypassed entirely.
2. **The AFK warning/screensaver could interrupt an active chat session.** `read_line()` always opts into AFK tracking internally — MRC is specifically designed to never go through that path at all.
3. **An incoming message arriving mid-keystroke spliced into the line being typed**, corrupting the display (the message actually sent was still correct — this was a rendering race, not data corruption). ANSI split-screen mode avoids this because incoming messages and the input line are drawn to separate cursor-addressed regions; plain-scroll mode has no such separation, so both now share the same lock the ANSI client already uses for this.

Fixed by replacing the delegated `read_line()` call with a proper PETSCII-safe character-by-character input loop — reading raw off the connection (matching the ANSI client's own approach, which also sidesteps AFK), reimplementing password masking at the single-character level, and serializing with incoming-message writes via the shared input lock. Also fixed `_term_columns` never being set for PETSCII sessions (stuck at its 80-column default), which affected a couple of width calculations.

## v1.0.24 — PETSCII MRC chat client; colored PETSCII menus (August 2026)

**New: MRC chat is now available on PETSCII (C64/128) sessions.** Previously MRC was never offered to PETSCII users at all — not gated, just never built. `anetbbs/features/mrc_chat.py`'s `MRCChat` class turned out to already have a complete, working plain-scroll fallback rendering mode built in and self-guarded throughout (`_emit()`, `_draw_status_line()`, etc. all already check `if not self._split_screen:`), just never reachable by a real terminal because the ANSI split-screen setup (CPR terminal-size probing, DECSTBM scroll regions, cursor-addressed draws) always ran first and silently failed on a real C64. New `anetbbs/features/mrc_chat_petscii.py::PetsciiMRCChat` subclasses `MRCChat` and overrides exactly three methods to force that plain-scroll mode instead — everything else (the bridge websocket connection, JSON protocol, ping/pong keepalive, slash commands) is inherited unchanged. Available from both the built-in PETSCII menu and sysop-built custom `PetsciiMenu` trees.

**PETSCII menus can show real color now.** The ANSI-to-PETSCII color translation added previously was already fully wired through `session.write()` — menu screens just never embedded any color codes to translate. New `anetbbs/features/petscii_theme.py` (the PETSCII counterpart to the ANSI side's `ansi_ui.py`) adds a colored reverse-video header bar and colored menu hotkeys to both the built-in menu and sysop-built custom menus, which also gain a per-menu color picker in the admin UI.

## v1.0.23 — "Who's online" now shows every simultaneous connection per user (August 2026)

**Fixed a real bug: a user logged in via both web and SSH at once only ever showed up once in "who's online."** Root cause: `UserSession.user_id` was `unique=True` — a hard one-row-per-user constraint — so a second simultaneous connection's presence write found and overwrote the first connection's row instead of getting its own. Removed the constraint and gave each *connection* its own identity (`session_key`, not `user_id`): a fresh UUID per terminal session (`anetbbs/core/presence.py::SessionPresence`), and one stashed in the signed session cookie per browser session (`anetbbs/web_app.py::track_user_session()`). `/who/`, the terminal Who's Online screen, the admin control panel, and the sysop `whoison` console command all now correctly show one row per connection instead of one row per user.

**Bounded the table's growth now that it's no longer implicitly capped at one row per user.** A clean disconnect (terminal `SessionPresence.disconnect()`, web logout) now deletes its own row outright instead of just marking it stale. For connections that never get a clean disconnect (dropped carrier, killed process, browser closed), a new scheduled-maintenance handler, `cleanup_stale_sessions`, deletes anything untouched for more than a day — auto-seeded on every install, not just fresh ones.

**Fixed two related bugs found in the same audit**: three "N users online" counters (the site-wide navbar badge, the admin dashboard, and the terminal sysop stats screen) were counting raw connection rows rather than distinct users, which would have double-counted anyone with two connections open; and `profile.py`'s `is_user_online()` picked an arbitrary session row with no ordering, which could report a genuinely-active user as offline if an older, stale row for the same account happened to be checked instead.

## v1.0.22 — Terminal echomail-reply network bug, live presence detail, activity log drill-down, echomail admin logging, calendar/board polish (August 2026)

**Fixed a real bug: replying to an echomail message from the terminal never actually reached the network.** `read_echo_area()`'s inline reply composer (`anetbbs/features/bbs_ui.py`) was a fourth local-compose write path into `EchomailMessage` that never got the `toss_message()` fix the other three composers (the dedicated Compose Echomail menu item, the web composer, and PETSCII's composer) already had — a terminal reply sat in the local DB, visible on read-back, but was never queued into any downstream node's hold queue at all. This is the exact bug Jerry hit replying to a test message on a real network. Also fixed: none of the three terminal/PETSCII composers set `tear_line`/`origin_line` (the FTN `* Origin:` footer), even though the web composer always has — all three now read `ECHOMAIL_TEAR_LINE`/`ECHOMAIL_ORIGIN_LINE` from config like the web route does.

**"Who's on" now shows real detail instead of being frozen at "main" all session.** Root cause: `SessionPresence.set_page()` — the exact method built for this — was hardcoded to fire exactly once, at login, and never stored on the session for anything else to call again. Now updated from `menu_engine.py`'s central action dispatch (every top-level menu action: games, chat, boards, files, echo, pm, ...) plus finer detail from `games.py` (which door) and `mrc_chat.py` (which room) — both the terminal Who's Online command and the web `/who/` page benefit, along with the sysop's NodeSpy panel.

**New per-session activity log with a real drill-down.** The caller log now has a "View Activity" link per session, showing a full chronological timeline — login, menu actions, doors played/exited, MRC chat sessions with duration, logout — built on the existing (but nearly unused) `UserActivity` audit table rather than a new system. Also fixed a bug found along the way: `CallerLog.duration_seconds` was declared on the model and shown in two admin templates but never actually written anywhere — every row showed 0s; both web and terminal sessions now record real duration on logout.

**Echomail admin logging got substantially more detail**, all things Jerry specifically asked for after going through the hub over the weekend: poll logs are now filterable by downstream node, not just network; the AreaFix log gained a from-address (node) filter; and node detail pages gained a full File Area Subscriptions card — view what file areas a node is subscribed to and add/remove them, mirroring the existing message-area subscription UI, which didn't exist for file areas at all before this.

**Calendar**: a sysop can now delete past events from the main calendar view, not just upcoming ones — the delete button simply never rendered for the "Recent past events" list.

**Message boards**: added a "Recent Activity" sort option (web `?sort=activity`, terminal `A` hotkey) alongside the existing sysop-configured manual order, so recently-active boards are easy to find instead of requiring a scan of every category.

## v1.0.21 — Critical fix: unbounded memory/CPU leak in the terminal service; PETSCII ANSI color translation (August 2026)

**Critical live fix: `anetbbs.service` (telnet/SSH/rlogin/PETSCII/FTP)
leaked memory and CPU without bound** — observed growing to 19.8GB RAM
and 99.7% CPU after ~7 hours uptime with only a couple of concurrent
sessions, causing severe lag and dropped MRC chat connections. Root
cause: `anetbbs/features/bbs_ui.py`'s `_app()` helper built a brand-new
Flask app and registered a brand-new SQLAlchemy engine/connection pool
on *every single call*, never disposed — and `anetbbs/core/session.py`'s
sysop-kick watchdog calls it every 5 seconds for the entire lifetime of
every logged-in session (one of ~150 call sites across that module).
Over hours, with multiple concurrent sessions, that's tens of thousands
of leaked engines. Same root shape as a BinkP per-connection database
leak fixed earlier in this project's history — that fix was never
generalized to this helper. Fixed by caching the Flask app instead of
rebuilding it per call: reusing one shared app across many
`app_context()` pushes is the normal, correct Flask usage pattern (it's
exactly what the web/gunicorn process already does for every concurrent
web request) — building a fresh one on every call was the actual
anomaly. Found via a live user report ("I keep getting disconnected
from MRC" plus general terminal lag) traced in real time through
`systemctl status`/`journalctl` output showing RAM climbing while the
report was being investigated; hotfixed directly to the live server
ahead of this packaged release given the severity.

**PETSCII (Commodore 64/128) sessions now get real translated colors
instead of having ANSI color codes stripped outright.**
`anetbbs/features/petscii_codec.py` gained `ansi_to_petscii()`,
translating ANSI SGR color codes into real C64 color control bytes —
verified against Synchronet's own open-source PETSCII terminal
implementation (`src/sbbs3/petscii_term.cpp`) rather than invented from
scratch; every color byte matches theirs exactly. Replicates the same
reverse-video trick Synchronet uses for combined foreground+background
colors, since C64 text mode has no independent per-character background
color (only one foreground color per cell plus a whole-cell reverse
flag). Non-color ANSI sequences (cursor moves, erase, etc.) are still
dropped, same as before this change — PETSCII still can't honor
arbitrary cursor addressing from ANSI content.

**Audited the rest of the codebase for the same leak shape and fixed
three more call sites that copy-pasted it**, in
`anetbbs/games/door_runner.py` (`_write_msgbase_ini_override()`,
`_cleanup_session_safe()`, `play_door_game_telnet()` — the latter two
hit on every door game launch and exit) and
`anetbbs/features/games.py` (`show_door_menu()` and its game-launch
path). Unlike `bbs_ui.py`, these callers' own tests rely on getting a
genuinely fresh Flask app per call (to point `SQLALCHEMY_DATABASE_URI`
at a different temp DB per test case), so a shared-cached-app fix
wasn't an option here — instead added
`anetbbs/features/db_scope.py::transient_app_context()`, a small
context manager that disposes the fresh app's SQLAlchemy engine on
exit, modeled on `anetbbs/echomail/binkp_server.py`'s existing
`_new_app()`/`_dispose_app_engine()` pattern (which already handled
this correctly and was left untouched).

## v1.0.20 — anetbbs-cfg: standalone terminal admin tool (August 2026)

**New: `anetbbs-cfg`, a full-screen curses terminal admin tool** in the
spirit of Synchronet's `SCFG` / Mystic's `mystic -cfg` — a standalone
console command, independent of the web admin and of whether the
network services are even running. Run it with `python -m anetbbs.cfg`
from a checkout, or `anetbbs-cfg` once installed; it uses the same
`create_app()`/database as the web and BBS processes, so changes show up
immediately everywhere.

First version shipped 5 sections (Boards, Echomail Networks/Areas, File
Areas, Users & Security, System Settings); **expanded to 16 total
sections for near-full web-admin parity**, per Jerry's priority order:

- **Boards & Message Areas** — add/edit/delete/reorder, access levels
- **Echomail Networks & Areas** — pick a network, drill into its echo
  areas; BinkP host/port/passwords, AreaFix password, poll interval
- **Echomail Hub** — AreaFix log, poll log, QWK node request approve/
  deny (mirrors the web admin's exact packet-id validation + random-
  password credential generation, not a loose reimplementation)
- **File Areas** — tag, storage path, upload permission, access levels
- **File Bulletins** — metadata (title/order/active/access) for files
  dropped into FILE_BULLETINS_DIR, auto-synced from disk on view
- **Users & Security** — search/edit users, one-time password reset, IP
  bans, word filters, login auto-ban thresholds, registration attempt log
- **Games** — door games (full field set: DOS/DOSBox/dosemu, Mystic,
  Synchronet, rlogin, telnet, web), categories, active session monitor
  with disconnect/clear-stale
- **Image Galleries** — add/edit/remove gallery collections (JSON-config
  backed, same store the web admin uses)
- **BBS Menus** / **PETSCII Menus** — two-level menu/item editors for
  the telnet/SSH/rlogin and C64/128 terminal menu trees
- **Scheduled Events** — cron-style task config, JSON schedule/params
  validated on save, [R]un Now
- **Graffiti Wall** — post moderation (delete/restore/clear-all)
- **Login Modules** — logon/logoff action config (wall, ANSI screen,
  file bulletins, shell command, native/Python doors)
- **Last Callers** — read-only login log
- **Backups** — browse/delete `update.sh`'s pre-update snapshots
- **System / Network Settings** — a grouped `.env` editor (server ports,
  application settings, logging, BinkP, files/FTP, games, echomail,
  NUV), preserving comments and untouched keys on save

Advanced/rarely-touched fields and a few genuinely risky operations stay
web-admin-only, flagged in the tool itself rather than silently missing:
ANSI board/menu banner screens, BinkP TLS/CRAM-MD5/packet password,
file-area network reassignment, IP whitelist, and — deliberately —
backup **restore** (goes through a privileged sudoers-gated helper
script and can overwrite a live `.env`/database; browsing and deleting
old backups is still available here) and InterBBS Wall/Last-Callers
sharing settings (each is a combined `.env` write + echomail-area
provisioning step in one web route).

**Fixed during Pi3 testing: launching `anetbbs-cfg` on a live install
started a second full copy of the entire BBS background service
stack** (echomail poller, RSS poller, MSP/SYSTAT listeners, the ANetBBS
directory refresher, the metrics sampler, the scheduled-events runner)
alongside the already-running `anetbbs-web` process — double-polling
echomail, double-firing scheduled events, extra CPU/network contention
on top of the real service — just to open a local config screen.
`create_app()` only ever gated these behind `TESTING`; extended the
existing `ANETBBS_SCHEMA_MIGRATE_ONLY` one-shot-CLI flag (already used
by `update.sh`'s schema-migration step) to also skip all of them.

**Then found the real cause of `anetbbs-cfg` still taking ~10 seconds
to start on a Pi3, plus the eventlet deprecation warning and an
exit-time `RuntimeError: greenlet is being finalized` crash report**:
profiling showed `create_app()` — built for the full web server — pulls
in eventlet (+ monkey-patches stdlib socket/threading/ssl), flask-
socketio, and flask-migrate, then registers **76 web blueprints** and
compiles their werkzeug URL-routing tables, none of which a local
config screen needs. `anetbbs-cfg` now uses a new, much smaller
`anetbbs.cfg.db_bootstrap.create_minimal_app()` instead — a bare Flask
app with just `db` bound to it, skipping web_app.py (and eventlet)
entirely. Cuts measured startup from ~3.6s to ~1.1s on a dev machine;
proportionally larger on a Pi3. Since eventlet is never imported at
all now, both the deprecation warning and the eventlet/greenlet
shutdown crash are structurally gone, not suppressed.

Built on a small reusable curses widget layer (`anetbbs/cfg/ui.py`) with
zero new dependencies (stdlib `curses` only) — a scrollable list editor,
a field-driven form (turns a set of model columns into a screen with no
per-section layout code), and confirm/message modals. The `.env` parser
round-trips a file byte-for-byte on a no-op edit and only ever rewrites
the keys actually changed.

## v1.0.19 — Web UI performance pass: message rendering, boards, echomail, wiki, file areas (August 2026)

**The trigger: a single echomail message with a large ANSI-art body was taking 30+ seconds to load.** Root cause was an O(n²) bug in `reflow_hard_wrapped_body()` (the code that rejoins hard-wrapped FTN message lines) — for a long run of qualifying lines with no blank-line/art/list breaks, it folded the whole run into one ever-growing string and then re-scanned that *entire* string on every single line join, twice over (once for a trailing-word regex, once for an art-detection regex). Confirmed 32.7s on a synthetic body shaped like the real report; bounding both re-scans to a small fixed window instead of the whole string dropped that to 0.23s — about 140x faster, with all existing tests still passing.

**That led to a broader pass across the web UI** looking for the same class of bug and other real request-path cost:

- The "Toggle Markdown view" button on every message-read page (echomail/netmail/boards/PM) used to render the *entire* body through python-markdown + bleach unconditionally into a hidden div on every page load, even though almost nobody ever opens it — now deferred to a small on-demand endpoint that only renders on first click.
- Wiki page rendering had the identical O(n²) shape in its own placeholder-restore step (fenced/inline-code protection) — 3.13s → 0.02s on a comparable synthetic page.
- `_linkify()`'s URL-substitution loop re-scanned the whole rendered output once per matched link — rewritten as a single pass; a 3,200-link body now renders in a fraction of the time.
- The public message-board index ran 3 separate count queries *per board* (unread/post/reply) — hit by every visitor, including anonymous ones — collapsed to a small, fixed number of grouped queries regardless of board count.
- The echomail network-chooser page reloaded the user's entire read-status history from scratch once *per network* shown — now one indexed join query for the whole page.
- Viewing a board thread issued one DB query per reply in the tree — now one query per tree depth, so a thread with hundreds of replies at one depth costs 2 queries instead of hundreds.
- The file-areas index page ran a full TIC-DB-query + archive-extraction scan per area just to show a count and total size — replaced with a lightweight directory scan that skips all the per-file description work the index page never needed.
- The wiki's "Wanted pages" / "Orphaned pages" utility pages re-scanned every page's full body on every single visit — now cached (and kept in sync) whenever a page is saved, with pre-existing rows self-healing on first view.

Also fixed a long-standing drift found along the way: `anetbbs/__init__.py`'s `__version__`, `setup.py`'s `version=`, and `FILE_ID.DIZ` had been stuck at `1.0.9` since that release — `VERSION`, `RELEASE.md`, `README.md`, and this changelog were correctly bumped every release since, but those three files were missed for 9 releases running. Back in sync as of this one.

## v1.0.18 — AFK warning + matrix-rain screensaver; MRC wide-terminal sizing fix (August 2026)

**AFK warning + screensaver for the terminal client.** New `AFK_WARNING_SECONDS` setting (`.env`, default `0` = off), mirroring a real Mystic Pascal AFK script Jerry pointed at as a reference. After that many seconds of no keystrokes at any menu prompt, the caller sees a live countdown warning ("You've been idle a while..."); if nobody responds, a generated matrix-rain screensaver takes over the screen. A keystroke at either stage cancels/dismisses it — consumed, not passed through as a real menu selection — and returns to exactly where the caller was (prompt redrawn, plus any already-typed partial line for `read_line`). If the sysop also has `IDLE_TIMEOUT_SECONDS` set and nobody ever comes back, the existing hard idle-disconnect still fires afterward, unchanged.

Scoped deliberately narrow: only `read_key()`/`read_line()`/`read_key_arrow()` (the actual menu-navigation primitives) can trigger this. `read_raw()` is also called directly by several other features (door games' own poll loops, IRC/telnet bridges, the ANSI editor, dialout) with their own timeout/retry semantics and broad exception handlers that would have silently misinterpreted an AFK interruption as "the game/door ended" rather than "resume where you were" — a real risk found auditing every `read_raw()` call site before wiring this up. Those are completely unaffected; only the three intended entry points opt in via a new `allow_afk` parameter.

`AFK_WARNING_SECONDS` is also now a real Admin → Settings field, right alongside its sibling `IDLE_TIMEOUT_SECONDS` — no SSH/`.env` hand-editing needed. Both are read the same way (a direct per-session environment read, not routed through Flask's live-reloadable config) so a service restart is still needed after changing either one, same as `IDLE_TIMEOUT_SECONDS` already required.

Real fix from the first live test: dismissing the screensaver showed "Welcome back!" and a bare `Choice:` prompt with no menu — the caller had to press another key before the actual menu reappeared. `read_key()` only had the trailing prompt text to redraw with; the real menu content had been drawn by the caller beforehand and the screensaver's own screen-clear wiped it. Fixed with a new optional `on_afk_redraw` hook — `menu_engine.py`'s central menu loop (used for most of the BBS's screens) now passes its own full redraw routine, so one keystroke both wakes the session and redraws the menu it's serving.

**MRC wide-terminal sizing.** The Mystic MRC screen recreation always rendered the 80-column layout regardless of actual detected terminal size — `load_theme_layout()` always supported loading wider bundled `.ini` variants (`132x36`, `160x59`, etc.), it just was never wired up. New `best_fit_mode()` picks the largest variant that fits the caller's real terminal size (e.g. a 132x37 terminal now gets the `132x36` layout, with the nick-list sidebar, instead of silently falling back to the 80-col default).

## v1.0.17 — Doc fixes: door_mystic_mps still described the disproven -x flag (August 2026)

A full audit of everything added since GA turned up one real doc bug: `docs/14-door-games.md` and `docs/17-development.md` still described `door_mystic_mps` launching via `mystic -x` and never mentioned the mandatory `-u`/`-p` credentials — stale ever since the actual code was fixed to use the real flag (`-y<script>`; Mystic has no anonymous/no-login mode for scripts at all). Both docs now match what the code has actually done for a while. No code changes.

## v1.0.16 — Mystic MRC themes: full Mystic-style chat screen in the terminal (August 2026)

Added support for a set of MRC chat themes inspired by StackFault's (The Bottomless Abyss / Phenom Productions) Mystic BBS MRC client, plus an optional backend to run that real client directly. Full credit to StackFault for the original client and its five bundled themes — see `docs/27-mrc-chat.md`'s Credit section and `mrc/mystic_client/vendor/PROVENANCE.md`.

- Five new `/set palette` options (terminal) and matching web theme choices — `original`, `minimal`, `bitchx`, `2leet4u`, `least`. The terminal recreates the real Mystic screen layout (border art, room/topic/nick-list/latency/buffer/input positions, all sourced from the vendored theme files) rather than just swapping colors. Palette choice now persists per-handle across reconnects (default: `original`).
- Real round-trip MRC latency shown in both terminal and web clients, replacing the old local-loopback ping/pong number.
- Room topic now shows immediately on joining a room instead of a delayed, inconsistent pop-up.
- New `mrc_backend: "mystic"` option (default remains `native`) runs the real vendored `mrc_client.py` client as a subprocess against a synthetic Mystic directory ANetBBS builds automatically — no real Mystic install or account needed.
- `door_mystic_mps` (Mystic Pascal door scripts) gained ARM64 install support and admin-form fixes for running standalone `.mps`/`.mpx` scripts with real credentials.

## v1.0.13 — LORD (and every dorkit.js door) fixed after "sits stale, never loads" (August 2026)

**LORD — Legend of the Red Dragon — stopped loading entirely: the intro screen never appeared and the door looked completely frozen.** Root cause: `Queue.prototype.poll()` in the Node.js compat shim never flushed buffered terminal output before its wait loop, unlike every other blocking-read call site in the shim. LORD's whole "draw a screen, then wait for a keypress" flow runs through `dorkit.js`'s `waitkey()` → `poll()` — not the code path that was already covered — so the intro art and every prompt sat buffered indefinitely while the door correctly polled for input in the background the whole time, with the player staring at a blank screen with no idea a key was expected. This affects every door built on the shared `dorkit.js` library, not just LORD. Confirmed fixed against the real vendored door end-to-end.

Also this round:
- Door menu `sort_order` is now truly flat/global across the whole menu (terminal and web), not just within each game's own category. Categories used to always render grouped together in their own separate `sort_order`, so a sysop numbering every door 1-N as one flat list (the natural way to think about it) could add a new door with a "should be last" number and see it land in the middle instead, if its category happened to sort earlier. Category headers/sections now fall wherever the category naturally changes while walking that flat order.
- Minesweeper's title bar rendered a garbled `←5C` before the real title text. `console.right`/`left`/`up`/`down` (and `cursor_right`/`left`/`up`/`down`) were string-concatenating a raw fractional cursor-move count straight into the ANSI escape sequence — Minesweeper's title-bar centering math produces a non-integer for odd-width text, and a fractional CSI parameter isn't legal, so terminals abort the sequence mid-parse and print the tail literally. Now rounds before it hits the wire.
- The door-menu submenu screen (introduced in v1.0.12) now supports the same file-based ANSI art override as the top-level Door Games list — drop `door_games_<category-slug>.ans` into `data/text/menus/` to replace the generated layout for that category's second-level menu. See the slot-names reference on [`/docs/04-ansi-screens`](/docs/04-ansi-screens) for the full naming convention.

## v1.0.12 — Door menu sections (drill-down categories) (August 2026)

A game category can now be marked as a **submenu section** (Admin → Games → Categories → edit a category → "Show as a submenu section") instead of always listing its games inline — useful once a category has enough doors to run off the bottom of a real terminal screen. A section shows as one selectable line in both the terminal and web door menus; picking it opens a second screen listing just that category's games. Off by default — existing categories/installs render exactly as before until a sysop opts one in. (PETSCII's Games menu intentionally never listed real doors at all, so there's nothing to change there.)

Also fixed along the way:
- `console.charset` was missing from the Node.js compat shim's `console` object, crashing any door that reads it via the real vendored `modopts.js` (surfaced running Minesweeper) — now returns `"CP437"`, matching ANetBBS's encoding throughout.
- The InterBBS Score-Sharing Area dropdown (Admin → Games → edit a `door_synchronet` game) is now grouped by network (`<optgroup>` per network) instead of one flat alphabetical list — DOVE-Net's areas no longer get buried in a large FidoNet arealist.
- Adding a game whose auto-filled slug collided with an existing one (e.g. typing "Minesweeper" when the built-in browser minigame already owns slug `minesweeper`) crashed with a raw 500 instead of a friendly "slug already in use" message.

## v1.0.11 — Minesweeper InterBBS DOVE-Net score sharing (real MsgBase support) (August 2026)

Synchronet's own official Minesweeper door (bundled in v1.0.9) has a real, built-in feature to share game wins across BBSes via Synchronet's `MsgBase` message-base API — previously unimplemented in the Node.js compat shim (any door calling `new MsgBase(...)` would `ReferenceError`). Now real:

- New per-game admin setting, **"InterBBS Score-Sharing Area"** (Admin → Games → edit Minesweeper) — pick any real configured echo area, e.g. DOVE-Net's "Synchronet Data" conference (2013), to enable score sharing. Leave unset and the feature stays off, same as before.
- A win posts a JSON-encoded report to that area, and other BBSes' win reports read back the same way and merge into the door's own winners list — real `MsgBase` `open`/`save_msg`/`get_index`/`get_msg_header`/`get_msg_body` calls, backed by a new Python bridge (`msgbase_bridge.py`) that reaches ANetBBS's actual echomail data (`EchoArea`/`EchomailMessage`), not a stub.
- Uses the door's own documented `ctrl/modopts.ini` config path (`[minesweeper]` → `sub = <area-tag>`), auto-written before every launch — no changes needed to the door itself.

## v1.0.10 — File Bulletins: configurable .txt/.ans bulletin viewer (August 2026)

A new logon/logoff-style module for file-based bulletins — distinct from the existing DB-authored Bulletins feature. Drop `.txt`, `.asc`, or `.ans` files into `data/text/bulletins/` and they're auto-registered (inactive until enabled). Sysops manage them from Admin → Bulletins → Files: set a title, sort order, and minimum access level, and toggle visibility per file. Users browse a lightbar list and read through the same CP437/ANSI-aware ANView pipeline used elsewhere in the BBS — real file bytes, not DB text, so CP437/ANSI decoding is correct for genuine art bulletins (the kind door games often drop for scores/news). Wired into the LoginModule system as a new `file_bulletin` module type, attachable to logon/logoff sequences like any other module.

## v1.0.9 — Synchronet door game support (17 games tested) + MRC ping/latency display (August 2026)

ANetBBS's Node.js compat shim (`synchronet_compat.py`) can now run real, unmodified Synchronet `.js` door games, including ones using Synchronet's real JSON-RPC "JSON DB" protocol (port 10088) for shared, cross-BBS game state and scoreboards. Confirmed working end-to-end against real live JSON-RPC servers (including real cross-BBS data — existing scores, levels, and player history from other real BBSes already using these games):

- **Chicken Delivery** — real-time delivery arcade
- **Bubble Boggle** — word-search puzzle
- **Synchronetris** — real-time multiplayer Tetris-style
- **Jeopardized** — trivia game show, live rankings
- **Gooble Gooble** — real-time Pac-Man-style chase
- **Synkroban** — Sokoban warehouse puzzle
- **Star Trek** — real-time space combat arcade
- **Fat Fish** — fishing simulation
- **Dice Warz ][** — territory-conquest strategy (Risk-like)
- **Maze Race** — real-time multiplayer maze racing
- **Thirstyville** — café-owner economic simulation
- **Good Time Trivia** — trivia with multiple categories
- **Lemons** — "Lemmings"-style puzzle
- **Star Stocks** — galactic investment strategy
- **DrugLord** — "Dope Wars"-style economic sim
- **Uber Blox** — block-clearing puzzle
- **Minesweeper** — Synchronet's own official Minesweeper (by Digital Man), classic minefield-clearing puzzle with personal-best tracking — the one door in this list that doesn't use JSON-RPC

These games are **not bundled** in the release — they're real, free, open-source software from their own original authors, not ANetBBS's to redistribute. See [`docs/26-synchronet-json-rpc-doors.md`](26-synchronet-json-rpc-doors.md) for download links and setup instructions for each one.

**MRC**: The terminal client's status bar now shows real ping/latency instead of a clock (per-message timestamps already show the time on every line, so the status-bar clock was redundant). Turned out the latency widget already existed in the code but was silently broken — a wire-protocol field-name mismatch meant it never received a valid round-trip time, so only the clock ever showed. Fixed the mismatch and removed the now-redundant clock. The web UI now shows the same live latency figure next to the room topic in its status bar (previously only in the sidebar, which still also shows it).

## v1.0.8 — Poll log dedup guard could block a network's polls forever (August 2026)

Found live, right after the public release announcement: DOVE-Net (a QWK network) had simply stopped appearing in echomail poll activity, with no error anywhere — just silence for over a day.

Root cause: `_do_poll()`'s concurrent-poll dedup guard (`anetbbs/echomail/poller.py`, added in an earlier audit to stop a sysop's manual "Poll Now" from racing the scheduled poller's own tick for the same network) treats any `EchomailPollLog` row still at `status='running'` as proof a poll is genuinely in progress, and skips starting a new one. That's correct for a poll that's actually still running — but a poll interrupted mid-flight (a service restart landing while a session was still open, which is exactly what happens during any `update.sh` run) leaves its row stuck at `'running'` forever, since no exit path ever gets a chance to run and flip it. Every subsequent poll attempt for that network then silently self-skips, permanently, with nothing logged anywhere a sysop would think to look — confirmed live: DOVE-Net's last poll log row was `status='running'`, `started_at` over a day in the past, and nothing after it at all.

Fixed: a `'running'` row older than 30 minutes (`_STALE_RUNNING_POLL_MINUTES`) is now treated as abandoned rather than as a lock — it's flipped to `'error'` with a note explaining why, and the new poll proceeds normally instead of skipping forever. A genuinely recent `'running'` row still blocks a second concurrent attempt exactly as before. 2 new tests (stale-row recovery, and a sanity check that a fresh row still blocks normally) alongside the 3 existing dedup-guard tests, all passing.

This is the kind of bug an automated update can trigger on any network, not just QWK — any BinkP network poll interrupted by a service restart mid-session would hit the same silent-forever-skip. Sysops on `v1.0.6`/`v1.0.7` whose polling has quietly gone silent for a network should check Admin → Echomail → Poll Log for an old `'running'` row for that network; the fix here is automatic once updated, no manual DB edit needed.

## v1.0.7 — MRC bridge reconnect storm against a live hub (August 2026)

Found testing a fresh install on an otherwise-idle VM: the web MRC client showed "MRC upstream disconnected (reconnecting…)" in an endless loop. The bridge's own log showed why — a real TCP-level connection reset from the upstream hub, immediately after every single connect attempt, at a flat ~1-second retry cadence with no growing delay between attempts. A one-off manual TLS probe from the same machine got reset the exact same way, consistent with the hub's own flood/abuse protection having flagged the source IP in response to the retry storm itself.

Root cause: `MRCConnection._reconnect_loop()` (`mrc/bridge/main.py`) only grew its exponential backoff when the initial `connect()` call failed outright (a TCP/TLS-level error). But the actual failure mode here was different — `connect()` completes the TCP/TLS handshake, sends the handshake packet, and returns success immediately; the hub's reset is only noticed moments later by a separate, concurrently-running receive loop, which has no say in the backoff decision at all. Every such cycle reset the backoff delay straight back to its floor, so a hub that starts rejecting connections shortly after they're established got hammered at a constant rate forever instead of backed away from — turning one bad connection into a self-perpetuating one.

Fixed: a connection now has to stay up for a configurable minimum "stable" duration (`mrc_reconnect_stable_seconds`, default 10s) before the backoff resets. A connection that drops before then is treated as a failed cycle — same real delay-and-grow treatment as an outright failed `connect()` call — instead of silently falling through to the flat per-second retry. 2 new tests, confirmed to fail against the old code (zero backoff growth across five simulated fast-flap cycles) and pass against the fix.

**A second, more serious bug turned up once the backoff fix let the real rejection reason surface**: the hub was actively refusing every connection with an explicit `OLDVERSION` packet — `"our advertised version (ANETBBS/Linux.x86_64/v1.0.6) is too old ... hub wants: 1.2.9"`. Traced back through `docs/CHANGELOG-beta.md`: an earlier pre-release audit found `platform_info`'s version component hardcoded to a stale, disconnected `"1.3.7"` in three separate places and "fixed" it by deriving it from ANetBBS's own `VERSION` file instead, on the assumption the old value was meaningless drift. It wasn't — that field identifies MRC client/protocol compatibility to the hub in the hub's *own* numbering scheme, unrelated to ANetBBS's `v1.0.x` release series. Every fresh install from that point on was guaranteed to fail this check, since ANetBBS's version has nothing to do with whatever floor the hub currently enforces — and because `mrc/bridge/config.json` is deliberately never regenerated on an existing install, this was silent on already-running installs and only surfaced on a fresh one.

Fixed by decoupling `platform_info` from `BBS_VERSION`/`NEW_VERSION` entirely in both `install.sh` and `update.sh`, using a fixed, independent value (`1.3.9`, matching the real reference client's own version — comfortably above the hub's observed-live floor of `1.2.9`) via a new `MRC_CLIENT_COMPAT_VERSION` constant. Also fixed the shipped `mrc/bridge/config.example.json`'s placeholder, which previously read `"VERSION-HERE"` — an open invitation to fill in ANetBBS's own version, exactly the wrong instinct. 5 new tests extracting and running the real generation lines from both shell scripts in actual bash, confirming neither can leak the host's own release version into the field.

Also updated four docs files' install-command examples from `v1.0.6` to `v1.0.7` (`README.md`, `docs/01-installing.md`, `docs/INSTALL-PI.md`, `docs/preinstall-tutorial.html`) — same drift the previous release's docs sweep addressed, now current again.

**Note for any install whose `mrc/bridge/config.json` was generated between the original version-unification audit and this fix**: this source-code fix only changes what *new* config.json files get generated — an existing one is never touched by `update.sh`. If web MRC chat isn't connecting and the bridge log shows an `OLDVERSION` rejection, manually edit `platform_info` in that install's `mrc/bridge/config.json` to end in `1.3.9` (or whatever the hub currently requires) and restart `anetbbs-mrc-bridge`.

## v1.0.6 — Pre-release docs sweep: stale install-command versions (August 2026)

A final documentation pass ahead of the public release announcement, checking for exactly the kind of drift a fast string of patch releases (v1.0.1 through v1.0.5, all in the same week) tends to leave behind: install/update instructions still quoting an old tarball filename.

Found and fixed in four files: `README.md`'s two "Quick install" blocks, `docs/01-installing.md`'s Fresh Install section, `docs/INSTALL-PI.md`'s install and update sections, and `docs/preinstall-tutorial.html`'s walkthrough — all still hardcoded `ANetBBS-v1.0.0.tar.gz`/`cd ANetBBS-v1.0.0`, five patch releases stale. The most consequential of the four: `docs/INSTALL-PI.md`'s "Updating ANetBBS on Pi" section built its `wget` URL from `github.com/anetonline/ANetBBS/releases/latest/download/ANetBBS-v1.0.0.tar.gz` — GitHub's "latest" release alias resolves to whatever the current release actually is, but the literal asset filename in that URL has to match an asset that exists in it. With the latest release now shipping `ANetBBS-v1.0.5.tar.gz`, that link would 404 for any Pi user following the doc verbatim, right as the public announcement was about to send more people to it than usual.

Swept everything else that could plausibly carry a stale version claim in the same pass and found it already clean: the in-BBS wiki (`anetbbs/wiki/seed.py`) has no duplicated install instructions and no "current version" claims of its own; `banner.ans`/`banner.utf8.ans` and the welcome/goodbye ANSI screens contain no version text at all (or already use the existing `@VERSION@` template substitution); the main web templates already read the version dynamically rather than hardcoding it; and the various "as of v1.0b2.NNN" mentions scattered through the rest of the docs are legitimate historical notes about when a specific feature shipped, not claims about the current release — left alone on purpose.

**A real bug also caught in this window, before deploy.** A live TIC delivery (`tqwinfo.zip`, a legitimate file-echo distribution) sat unfiled for hours despite arriving cleanly and passing CRC. Root cause: `process_tic()` (`anetbbs/echomail/tic.py`) falls back to a default storage path whenever a `FileArea` doesn't have one explicitly configured — but that default was hardcoded to `/var/lib/anetbbs/file_areas/<TAG>`, a location the BBS service (running as an unprivileged user in every real install) has no permission to create. Every retry failed with a permission error one directory level deeper than the last, only ever recovering if someone manually built the path by hand as root — not something that happens unattended. This would hit any file area with no storage path set, including ones auto-created on the fly from an unrecognized TIC area tag, so it wasn't a one-off. Fixed: the default now lives under `{DATA_DIR}/file_areas/<TAG>`, the same convention every other on-disk default in the app already follows (uploads, avatars, echomail). Also updated the two admin-form placeholder examples that suggested the broken `/var/lib/anetbbs` path by example. 1 new test (`test_unset_storage_path_defaults_under_data_dir_not_var_lib`), confirming an area with no storage path files successfully with zero manual intervention.

Folded into the same v1.0.6 rather than a separate release, since it was caught before Jerry deployed the docs-only build — same pattern as v1.0.5's `find_aka_for_network` fix landing before its own first deploy.

## v1.0.5 — MRC protocol audit: message-length and color bugs (August 2026)

A sysop-requested audit of ANetBBS's MRC chat implementation (`anetbbs/features/mrc_chat.py` terminal client, `anetbbs/templates/mrc/index.html`/`static/mrc/client.js` web client, and the standalone bridge `mrc/bridge/main.py`) against the published MRC protocol documentation, prompted by two real reports: room-chat messages losing their color partway through a long message, and not being able to type/send anywhere close to the documented 140-character limit.

**Color lost on split messages.** A room-chat line long enough to need splitting into multiple wire-safe chunks (`_split_for_wire()`) only had the sender's active color pipe-code at the very start of the pre-split string. Each chunk is sent as its own fully independent `send_message` call — a separate MRC packet on the wire, not a client-side word-wrap of one received message — so only the first chunk ever carried the color; every chunk after it arrived with none and rendered in whatever default/leftover color happened to be active on each recipient's own client. Fixed by giving `_split_for_wire()` a `repeat_prefix` parameter that gets budgeted into the per-chunk cap and re-applied to every chunk, not just the first, and updating the room-chat call site to use it instead of pre-concatenating the color onto the whole string before splitting.

**`/me` and `/broadcast` silently truncated.** Both commands budgeted their outgoing text against the bare 140-char hub limit with zero reservation for the wrapper the bridge adds before transmission: `/me` gets wrapped in a fixed-color `"|15* |13{nick} ...|07"` (not the user's own style), and `/broadcast` gets the literal `"BROADCAST "` prefixed onto Field 7 verbatim. Anything within that wrapper's length of the 140-char limit had its tail silently cut off server-side (`_truncate_wire_message()`) with no warning — in both the terminal and web clients — the exact class of bug `handle_overhead`/`dm_overhead` already existed to prevent for plain chat and DMs, just never extended to these two. Fixed by having the bridge compute and push a new `action_overhead` figure on join (same established pattern as the other two), consumed by a new `_action_wire_cap()` in the terminal client and `_actionTypedLimit()` in the web client; `/broadcast`'s fixed literal-prefix overhead needed no bridge round-trip, just a local constant matching the bridge's own string. The terminal's status-bar remaining-character counter for `/me` was also silently assuming zero overhead (showing more room than truly existed) — same fix closes that too.

**`USERNICK:` parsing bug**, found auditing the wire format directly against the spec rather than inferring it from behavior: the real packet carries exactly ONE nick value (`SERVER~~~CLIENT~~~USERNICK:nick~`), not an "old new" pair. The terminal client's handler split on whitespace expecting two tokens; a single-token value always produced only one element, so the code's "new nick" half was permanently empty while the "old nick" half (actually just the one real value) got unconditionally discarded from the local known-users roster every time this fired, never restored — a slow leak silently shrinking tab-completion and mention-highlighting coverage the longer a session ran. Fixed by parsing it as the single value it actually is and adding it to the roster, with no erroneous discard.

19 new tests (`test_mrc_terminal_wire_overhead_and_color.py`, `test_mrc_bridge_action_overhead.py`) covering the color carryover, the `/me`/`/broadcast` overhead accounting end-to-end through the real call sites, the status-bar counter, and the `USERNICK:` parsing fix.

**A separate, unrelated bug also caught in this release.** Found live while verifying v1.0.4's "reply via arrival network" fix actually worked end to end: the reply's own From address turned out wrong. Root cause: `find_aka_for_network()` (`anetbbs/echomail/routing.py`) picks the `UserAka` whose zone matches the network being sent through — correct when there's a match, but when there wasn't one, it fell back to the sysop's "primary" AKA (or just the first one on file) rather than giving up. Both real callers (`netmail.py`'s `compose()`, `telegram.py`'s `send()`) already have their own correct fallback for exactly this situation — `aka.address if aka else network.our_address` — but it never got a chance to run, since this function always returned *something* non-`None` as long as the user had any AKA configured at all, confidently wrong or not.

Confirmed live: a reply through a real, correctly-configured zone-1 Fidonet network went out From the sysop's zone-1200 AKA (configured for an entirely different, unrelated network, and happened to be marked "primary") instead of that Fidonet network's own `our_address`. The receiving peer's own upstream relay rejected the origin address as unroutable and explicitly warned against replying to it again — a real risk to nodelist standing, not just a cosmetic header mismatch. Fixed by returning `None` on no zone match (same as the pre-existing no-AKAs-at-all case) instead of guessing, letting the already-correct, already-tested fallback in both callers finally do its job. 6 new tests (`test_find_aka_for_network_zone_mismatch.py`).

## v1.0.4 — Reply to crash-delivered netmail with no configured network (August 2026)

Direct follow-up to v1.0.3: a sysop received real crashmail from an address covered by no configured `EchomailNetwork` (exactly the case v1.0.3 made ANetBBS newly compliant to accept), then tried to reply and hit "No active FTN network covers zone of ..." — `find_network_for_address()` (`anetbbs/echomail/routing.py`) requires a same-zone configured network to route outbound netmail through, which is correct for ordinary hub-routed mail but wrong here: there's no hub in this relationship at all. The sender crash-delivered straight to the BBS by dialing in directly; the only correct reply is to crash-deliver straight back, dialing them.

**Inbound side.** `NetmailMessage` gained an `origin_ip` column. `binkp_server.py`'s `_import_pkt_payload()` now accepts an `origin_ip` parameter and stores it on any netmail imported with `network_id=None` (the anonymous-crashmail case) — the real socket IP the peer connected from, threaded through from `_handle_connection()`'s own `peer` tuple via closure. Left `None` for every other case (known upstream hub, known downstream node), since those already have a real `hub_address`/`ftn_address` to route a reply through and don't need this.

**Outbound side.** `anetbbs/web/netmail.py`'s `compose()` route detects a reply to netmail with `network_id is None` and `origin_ip` set, and takes a different path entirely: skips `find_network_for_address()`, forces the destination address to the parent message's own `from_address`/`from_name` (never trusts the posted form fields for this — the only thing that can ever change where a direct-dial reply actually gets sent is the parent's own DB-stored `origin_ip`, which a request body can't influence), and queues the new `NetmailMessage` with `network_id=None` and `origin_ip` copied from the parent. A new `send_netmail_direct_now()` in `poller.py` then dials that IP directly on the standard BinkP port (24554 — there's no local nodelist INA/IBN entry to learn a custom one from) with no session password (there's no shared secret with an unlisted peer), fired in a background thread immediately on submit rather than waiting on a scheduled poll that can't exist for an address with no network row to attach one to — matches the existing "Poll Now" admin-button pattern. The compose template shows a banner explaining what's about to happen and renders the To fields read-only for this case.

Explicitly out of scope for this fix (by design, not oversight): echomail from an unlisted peer is still dropped, not accepted, same as v1.0.3 — this only restores netmail deliverability, not open echo distribution. And a peer's own nodelist INA/IBN port, if non-standard, still isn't looked up anywhere — a sysop mentioned a separate BinkD-format hostname list (`Z1BINKD.TXT`) that could seed a future nodelist-based lookup, but confirmed with him this isn't needed for this fix and deliberately left out to keep this change scoped to the actual reported problem.

13 new tests (`test_poller_direct_crash_reply.py`, `test_netmail_direct_crash_reply_compose.py`, plus 2 more in `test_binkp_anonymous_crashmail.py`), and 5 existing test files (`test_binkp_downstream_node_import_network_id.py`, `test_binkp_eob_sent_before_receive.py`, `test_binkp_finish_before_import_ordering.py`, `test_binkp_import_off_event_loop.py`, `test_binkp_node_network_disambiguation.py`) fixed after their mocked `_import_pkt_payload()` signatures didn't accept the new `origin_ip` keyword argument — caught by a full regression sweep before this shipped, not live.

**A second, related bug turned up live testing the first one.** A sysop's actual reply attempt still hit "No active FTN network covers zone", even after the above shipped — but the netmail he was replying to turned out to have a real, non-NULL `network_id`, not the anonymous-crashmail case at all. It had arrived via completely ordinary FTN store-and-forward routing: his own real, active network's hub relayed a netmail originally sent from a different zone than that network's own `our_address` — hubs commonly carry cross-zone traffic via zone gates, and this one had just proven exactly that by delivering the message in the first place. `find_network_for_address()` only matches a network whose *own* `our_address` zone equals the destination's zone — a reasonable test when composing a brand-new message with no prior routing evidence, but the wrong test for a reply, where the network that just delivered the original already demonstrated it can carry traffic for that zone.

Fixed in `compose()`: a reply (a `parent` message exists) with a real `parent.network_id` now routes through that same network directly, skipping the zone-matching gate entirely — falls back to `find_network_for_address()` only for a fresh compose with no parent, or if the parent's network is no longer active (with a clear "no longer active" error in that case, rather than silently falling through). 3 more new tests (`test_netmail_reply_uses_arrival_network.py`).

## v1.0.3 — BinkP crashmail compliance: accept netmail from unlisted addresses (August 2026)

Real report: a sysop forwarded a netmail from a net's nodelist coordinator flagging that ANetBBS's own node was non-compliant with standard FTN nodelist policy, which requires a listed node (not flagged `Hold` or `Pvt`) to accept crashmail from *any* address, not only addresses it has pre-configured as an upstream hub or downstream node. The coordinator's own connectivity test to the node got `Try result: unknown address ...`.

Root cause: `anetbbs/echomail/binkp_server.py`'s inbound BinkP listener matched the connecting peer's claimed AKA against `EchomailNetwork.hub_address` (configured upstream hubs) and `BinkPNode.ftn_address` (configured downstream nodes) only — anything else got `M_ERR unknown address` and an immediate disconnect, with no password check ever attempted (there was nothing to authenticate against). That's correct for echomail (distribution requires real network membership) but wrong for netmail, which FTN policy expects any listed node to accept from anyone.

Fixed by accepting the session for an unrecognized peer instead of rejecting it (`net_id` and `downstream_node_id` both stay `None` — no password is checked, matching how real binkd/ifcico treat unlisted callers, since there is no shared secret to verify with a peer we've never configured). The import path (`_import_pkt_payload()`) now accepts `network_id=None`: netmail imports normally (`NetmailMessage.network_id` is nullable) and, if addressed to a real local user, still resolves and notifies that user exactly as any other netmail would — this is the actual deliverable, since a sysop's own netmail inbox query filters by recipient only, not by network. Echomail in the same packet is logged and dropped rather than imported, since `EchoArea`/`EchomailMessage.network_id` is `NOT NULL` and an unlisted caller has no real subscription to route it against. AreaFix/FileFix netmail from an unrecognized peer still fails closed with "Network not configured" and applies no subscription change — that safeguard (`process_request(network=None, ...)`) already existed from an earlier audit and needed no changes, just confirmed it's reached safely with `network_id=None` instead of raising.

3 new tests (`tests/test_binkp_anonymous_crashmail.py`) cover the import-level netmail/echomail split and the AreaFix fail-closed path at the DB level; 1 existing test in `tests/test_binkp_multi_hub_identity.py` (`test_unknown_address_gets_err_not_ok`, renamed `test_unknown_address_now_accepted_as_anonymous_crashmail`) updated to assert the new `M_OK`/accept outcome instead of the old `M_ERR`/reject one. `tests/test_binkp_server_engine_disposed.py`'s disposal-count assertion needed no change — the per-connection engine still gets disposed exactly twice either way, just via the normal end-of-session `finally` block now instead of an early-return path.

## v1.0.2 — Real bug: a URL inside dense ANSI art corrupted the message layout (August 2026)

Found live: a sysop composed a CP437 ad screen (bordered box, shading bar, a centered `https://` URL inside one row) and posting it in the web UI corrupted the box border and everything at and after that row — the row's intended white padding turned into default-gray blanks with wrong spacing.

Root cause: the URL auto-linkifier (`_linkify()` in `anetbbs/web/render_msg.py`) split the message text into fragments around each matched `https?://` URL and ran the CP437/ANSI grid renderer (`_to_html_vt`/`_run_vt`) independently on each fragment. That renderer always pads a row out to the full 80-column width with default-color blanks when it reaches the end of its input mid-row — it has no way to know the row was artificially cut short by the URL split rather than genuinely ending there. Any row with a URL inside dense box-drawing/shaded art got its layout corrupted this way; plain-colored text or art with no shading characters never triggered the bug (the dispatch to the grid renderer is gated on the presence of solid block/shade glyphs `█▄▀▌▍▎▏░▒▓`, which is why a border-only repro didn't reproduce it during debugging, only the real shading-bar-containing ad screen did).

Fixed by rendering the full message as one continuous pass, then substituting the clickable link into the already-rendered HTML afterward — the grid renderer always sees the complete, uncut text now, so a URL landing mid-row can no longer corrupt that row's layout. 5 new tests, confirmed to fail against the old code and pass against the fix.

## v1.0.1 — Unclaimed-netmail AreaFix-reply noise; File Areas network filter (August 2026)

Two fixes/features found while wrapping up the v1.0.0 rollout.

**Unclaimed netmail piling up with AreaFix confirmation noise.** Admin → Echomail → Unclaimed Netmail was meant to exclude AreaFix/FileFix bot traffic from its review queue, but the filter only checked the *recipient* name (`to_name`). It missed the reverse case: a peer's AreaFix robot replying with an automated "AREAFIX response" confirmation, generically addressed `To: Sysop` (a common FTN default, not a real local username) rather than `To: AREAFIX`. Found live: 50+ of these had piled up unbounded on a single network, all pure noise. Fixed by checking both `to_name` and `from_name` against the bot-name list — extracted into a shared `_unclaimed_netmail_query()` helper so the list view and the new bulk-clear action below can't drift out of sync. Also added an admin-only "Clear All" button (same filter criteria as the list, not a raw wildcard delete) so an existing backlog can be discarded in one click instead of one-at-a-time. 6 new tests.

**File Areas had no way to filter by network.** A real usability complaint once a sysop has enough file areas (local + several FTN networks) that hunting through one long flat list gets tedious. New client-side "Show:" filter dropdown on Admin → File Areas — no new route, no server round-trip — reusing the same `data-network-id` values the existing bulk-select-network dropdown already relies on. "Select all" now only selects currently-*visible* areas (previously it selected everything regardless of any filter, which combined with a filtered view and a bulk-delete could have silently caught areas from a different network scrolled off-screen). 3 new tests.

## v1.0.0 — Full release (August 2026)

Primarily the version cutover from the internal beta build-number
scheme to standard semantic versioning, marking ANetBBS's first stable
release — no other behavior changes from v1.0b2.239.

One real fix caught live during this rollout: a sysop ran `update.sh`
and got a garbled warning — `nginx /mrcws proxy points at
port 127` followed by `0`, `0`, `1`, and `8080` each on their own line
— on an install that was actually configured correctly. The MRC
nginx-proxy verification check (added v1.0b2.232-235) extracted the
configured port with `grep -oE '127\.0\.0\.1:[0-9]+/ws;' ... | grep
-oE '[0-9]+'`, but that second grep matches *every* run of digits in
the matched line, not just the port — `127.0.0.1:8080/ws;` contains
five separate digit runs (`127`, `0`, `0`, `1`, `8080`), so the
extracted value could never equal the bridge's actual port and the
check false-positived on every correctly-configured install. Fixed by
capturing just the port group with `sed` instead. 3 new tests run the
real extraction line from `update.sh` in actual bash against synthetic
nginx configs, so this can't silently regress again.

A second real fix, also caught live: Admin → Upgrades 500'd with
`AttributeError: 'str' object has no attribute 'strftime'` the moment
it tried to display the upstream release's publish date.
`datetime.fromisoformat()` only accepts a trailing `Z` (the shape
`/api/releases/latest` actually emits) as of Python 3.11 — the live
server's venv is 3.10. Fixed by normalizing a trailing `Z` to `+00:00`
before parsing in `to_eastern()`. Also hardened `fmt_eastern()` to
check the value is actually a `datetime` before calling `.strftime()`
on it — `to_eastern()`'s own docstring already promised to fail open
and return the raw string on genuinely unparseable input, but
`fmt_eastern()` wasn't honoring that contract, so any malformed
timestamp (not just this one) would have crashed the same way. 2 new
tests, including one reproducing the exact reported crash.

Also rewrote `FILE_ID.DIZ`: the old one had grown to 18 lines, well
past the classic BBS-standard 10-line/45-char DIZ convention. Now 9
lines, leads with the version and "Full Release!", and calls out the
web UI explicitly right after the terminal-access list (telnet/SSH/
rlogin/PETSCII) rather than folding it in as just one more word among
them. Confirmed (with 2 new tests) that a `FILE_ID.DIZ` nested inside
the release tarball's own `ANetBBS-v1.0.0/` wrapper directory — not at
the archive root — still gets picked up correctly if a sysop uploads
the release tarball to a file area.
