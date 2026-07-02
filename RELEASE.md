# ANetBBS v1.0b2.22 — Paginate the web Changelog page (July 2026)

- FEATURE: `/docs/CHANGELOG` was rendering the entire, ever-growing `docs/CHANGELOG.md` as one markdown pass on every page load, getting slower to load release after release. Now paginated at 15 version-entries per page (newest first, matching the file's existing order), with Prev/Next + page-number navigation styled to match the site's active theme. Other docs pages are unaffected — this is CHANGELOG-specific, since it's the only doc that grows unbounded over time. Verified with a direct test against the live route: page 1 shows the newest entry, page 2 differs from page 1, and an out-of-range page number clamps to the last valid page instead of erroring.
- Also fixed the CHANGELOG.md header's "Current release" line, which had been stuck at `v1.0b1.6` for a long time while every actual release moved past it — Jerry caught this looking at the deployed Pi3 copy. Added a note to the version-bump checklist so this line gets updated alongside the changelog's new top entry going forward, not just occasionally.

---

# ANetBBS v1.0b2.21 — Fix: pyspellchecker was never actually installed (July 2026)

- FIX: v1.0b2.20's ANEdit spell check silently failed on every fresh install/update — `pyspellchecker` was added to `requirements.txt` but never to `setup.py`'s `install_requires`, and `update.sh`/`install.sh` install dependencies via `pip install -e .`, which reads `setup.py`, not `requirements.txt` directly. Caught on the Pi3: the underline-as-you-type and `/spell` command both silently no-op'd (by design — the graceful-degradation fallback treats a missing spellchecker as "feature just isn't available" rather than erroring), so nothing crashed, it just never worked. Added `pyspellchecker>=0.8.0` to `setup.py` so it actually installs going forward.

---

# ANetBBS v1.0b2.20 — Spell check + clickable URLs, web and terminal (July 2026)

- FEATURE: Spell check in ANEdit (the terminal message editor). Misspelled words are underlined live as you type or view a draft, and a new `/spell` (alias `/sp`) command jumps to the next misspelled word and offers up to 6 suggestions — press a number to replace, N to skip, Esc to cancel. Quoted lines (`>`-prefixed, someone else's text) are never flagged, and ALL-CAPS tokens (SSH, QWK, ANSI, etc.) are skipped as likely jargon rather than typos. Backed by `pyspellchecker` (new dependency — pure Python, bundles its own dictionary, no network calls, degrades silently if ever missing).
- FEATURE: Web message composers (PM, board post, echomail, netmail, netmail-via-echomail) now explicitly enable native browser spell check (`spellcheck="true"`) — the familiar red squiggly underline + right-click suggestions most browsers already provide.
- FEATURE: Clickable URLs, both web and terminal. Web: `https://` links in PM, board, echomail, and netmail message bodies now render as real `<a href>` links (board posts still auto-embed image URLs as `<img>` the way they already did — that behavior is unchanged, just extended to non-image links too). Terminal: the same URLs get wrapped in OSC 8 hyperlink escape codes when reading board threads, PMs, and InterBBS instant messages — modern terminal clients (Windows Terminal, iTerm2, kitty, gnome-terminal) make them clickable; older/classic BBS terminals (SyncTERM, etc.) simply ignore the unrecognised escape sequence and the URL text still displays normally, so nothing is ever hidden.

---

# ANetBBS v1.0b2.19 — Fix GitHub #3 for real: the active v2 thread reader was never patched (July 2026)

- FIX: GitHub issue #3 (terminal message reader truncating long paragraphs) was reported fixed in v1.0b2.15, but a sysop retested against v1.0b2.18 and found it still broken — with a spot-on root-cause diagnosis and tested patch. The original fix touched `read_thread()` in `anetbbs/features/bbs_ui.py`, but that method is shadowed at module load time by `BBSMenuUI.list_threads = _list_threads_v2` / `BBSMenuUI.read_thread_v2 = _read_thread_v2` near the bottom of the same file — a monkey-patch override mechanism that replaces the class-body methods. The actually-active board reader, `_read_thread_v2`, still sliced every line to `line[:_line_w]` and silently discarded the rest. Fixed the same way as before — `_wrap_text()` + the shared `_page_lines()` pager — but this time in the function that's actually called. Also fixed `_line_w = max(76, _w - 4)`, which had a 76-column floor that could overflow a narrower-than-80 terminal; now `max(20, _w - 4)`. Verified full paragraph content survives wrapping byte-for-byte (reconstructed wrapped output matches the original word-for-word) before shipping.
- Grepped the whole file for every `BBSMenuUI.<name> = <name>` monkey-patch override afterward — private messages and Inter-BBS instant messages have no such shadow, so those fixes from v1.0b2.15 are confirmed still active.

---

# ANetBBS v1.0b2.18 — ANetCRAFT: day/night cycle was 40 seconds, now 10 minutes (July 2026)

- FIX: A full ANetCRAFT day/night cycle was only 40 seconds (`DAY_TICK=500` at 80ms/tick) — way too fast to be a meaningful survival mechanic. Changed `DAY_TICK` to 7500, giving a 10-minute cycle. Applied to both this Python version and the standalone C# ANetCRAFT-Door project (same bug, same fix, in both codebases).

---

# ANetBBS v1.0b2.17 — ANetCRAFT: fix severe lighting-related slowdown (July 2026)

- FIX: ANetCRAFT gameplay was very slow (unplayable on a Pi3) after v1.0b2.16's lighting system — it re-scanned an 8-tile neighborhood for every one of the ~1560 visible cells, every single frame (measured 155ms/frame against an 80ms tick budget on a fast desktop, let alone a Pi). Replaced with `World.compute_light_grid()`, which computes the whole viewport's lighting in one pass (one scan per column for skylight, one pass that splats each light source's falloff onto nearby cells instead of every cell re-scanning for sources). Measured 5-7ms/frame typical now, ~29ms even in a deliberately pathological dense-torch stress test. Verified byte-identical output against the old per-cell method first. Lava is no longer an ambient light source in this Python version (a visible lava sea in the Nether could be hundreds of tiles, each triggering an expensive splat) — torches/glowstone/portals are sparse by comparison and don't have that problem.

---

# ANetBBS v1.0b2.16 — ANetCRAFT: full Minecraft-style progression + win condition (July 2026)

- FEATURE: The built-in ANetCRAFT door game (`anetbbs/features/anetcraft.py`) is no longer an endless sandbox — it now has a complete, winnable progression matching the standalone C# ANetCRAFT-Door project. New: torch/glowstone lighting (dark caves are dangerous even at noon), furnaces + ore smelting, craftable armor (leather/iron/gold/diamond) with damage reduction, lava burn damage, a proper death/respawn cycle, a Nether dimension reached by building and lighting an Obsidian portal, Blaze and Ghast mobs, Endermen dropping Ender Pearls, an Eye of Ender crafting chain, an End dimension reached through a ring of End Portal Frames, and a full Ender Dragon boss fight ending in a victory screen and Dragon Egg trophy. The world keeps going after you win, same as vanilla Minecraft.
- FIX: Fixed two long-standing bugs surfaced while building this out — pressing Enter in the ANetCRAFT inventory screen never actually swapped items (the generic "close overlay" key handler was intercepting it first), and starving to 1 HP could leave a player stuck there forever with no way to die/respawn.
- NOTE: Save files from before this update load fine — old single-dimension saves are detected and migrated into the new multi-dimension save format automatically.

---

# ANetBBS v1.0b2.15 — Terminal message wrapping fix + Admin menu reorg (July 2026)

- FIX: Terminal message reader (board threads, private messages, Inter-BBS instant messages) truncated any line past column 78 instead of word-wrapping it. Long paragraphs posted from the web interface were silently cut off when read over telnet/SSH. All three readers now word-wrap to the session's terminal width and paginate with a `--MORE--` prompt, reusing the same wrap/page logic as the bulletin reader.
- FEATURE: Admin navbar dropdown reorganized. The single 58-item "Admin" dropdown is now 8 entries (Dashboard + Users/Messages/Files/Content/Network/System/Settings), each opening a category hub page (`/admin/hub/<section>`) with the previous tools shown as cards. No admin page URLs changed — only the navigation.

---

# ANetBBS v1.0b2.12 — Widescreen fixes: wall CP437, main menu bar, file list, ANView, door games (July 2026)

- FIX: Graffiti Wall CP437 box chars were broken (`\xNN` in f-strings are Latin-1, not CP437 bytes). Replaced all escape sequences with the correct Unicode box-drawing characters (`╔═╗║╚╝─░▒▓`).
- FIX: Main menu 132-col status bar overflowed by 9 chars. Reduced spacer padding in `main132.ans` from 66 to 57 spaces.
- FIX: File Library area list column overflowed when areas have `[inactive]`/`[sysop]` flags. `_name_w` now uses `_w - 40` instead of `_w - 20` to leave room for flag strings.
- FEATURE: ANView (message reader) header bar, status bar, and content width now use `ui_width(session)` instead of hardcoded 80. Header separator also scales.
- FEATURE: Door Games menu box (`╔═╗`) and Game Center menu now scale to terminal width. Name column expands on widescreen sessions.

---

# ANetBBS v1.0b2.11 — Graffiti Wall: widescreen + ASCII mode support (July 2026)

- FEATURE: Graffiti Wall box width now adapts to terminal width. On an 80-col session the box is 79 chars (as before); on a 132-col session it expands to 131 chars. Box, footer, post separator, and the Write prompt all scale with `ui_width(session)`.
- FEATURE: ASCII mode support — when `term_type == 'ascii'`, CP437 box-drawing characters (`╔═╗╚╝║─`) are replaced with plain ASCII (`+`, `-`, `|`) and pipe colors in post content are stripped to plain text.

---

# ANetBBS v1.0b2.10 — MRC: send TERMSIZE to server at connect time (July 2026)

- FEATURE: MRC client now sends `TERMSIZE <cols>,<rows>` to the MRC server immediately after joining a room. This tells the remote server the actual terminal dimensions so it can adjust output formatting (WHOON column widths, etc.) if it supports wider display. On a 132-col session the server receives `TERMSIZE 132,37`.
- NOTE: The WHOON `/who` user list is formatted by the remote MRC server (mrc.bottomlessabyss.net) for 80-col displays. Chat word-wrap on ANetBBS already uses the full terminal width (`max_w = cols - 6`). If the remote server supports wider WHOON formatting in a future update, it will now work automatically.

---

# ANetBBS v1.0b2.9 — MRC: status bar blue fill now spans full terminal width (July 2026)

- FIX: MRC status bar gap spaces and EOL fill were rendered on default (black) background because `room_s` and `topic_s` both end with `\x1b[0m` which reset the blue background before the spaces were written. `\x1b[44m]` is now re-asserted before the gap and before `\x1b[K]`, so the full row is filled blue. The status bar now spans the complete terminal width on widescreen connections.

---

# ANetBBS v1.0b2.8 — MRC: active terminal size detection + status bar fill fix (July 2026)

- FIX: MRC `_enter_split_screen` now performs an ANSI CPR query (`ESC[999;999H ESC[6n`) to detect the actual terminal size when `window_size` reports a small value (<100 cols). This handles telnet sessions where NAWS negotiation hasn't completed or is skipped.
- FIX: MRC status bar now uses `\x1b[K]` (Erase to End of Line) AFTER writing content with blue background active, guaranteeing the full row is filled blue regardless of gap calculation accuracy. Removed dependency on `\x1b[2K]`.
- FIX: Minimum `_term_columns` raised from 40 to 64 to match `ui_width()` floor.

---

# ANetBBS v1.0b2.7 — Widescreen: MRC chat widescreen fixes (July 2026)

- FIX: MRC status bar (top row) background color was set after line-erase, so on wide terminals the blue fill stopped where content ended. Background is now set before the erase so the full row fills blue.
- FIX: Chat Systems selection menu (`banner`/`footer`/`menu_item`) not passing `_w` — now width-aware.
- FIX: MRC `_enter_split_screen` now also checks `session.cols`/`session.rows` as a fallback so `_term_columns` is correct even if `window_size` is not available on all session types.

---

# ANetBBS v1.0b2.6 — Widescreen fixes: RSS pager, lightbar, file browser, ANetIRC (July 2026)

- FIX: RSS lightbar separator and pager separator lines were hardcoded to 76 chars — now use `ui_width(session)`.
- FIX: RSS item list rows truncated title at 62 chars regardless of terminal width — now expands with terminal.
- FIX: RSS article pager header divider hardcoded to 72 chars — now expands.
- FIX: File area list/detail header dividers hardcoded to 44 chars — now terminal width; filename and description columns also expand.
- FIX: Inter-BBS IM inbox banner was not width-aware — now full-width with expanding message preview.
- FIX: ANetIRC client capped terminal width at 79 even on 132-col sessions — now caps at 131, so the two-panel Startup Manager and chat view fill the full wide terminal.

---

# ANetBBS v1.0b2.5 — Full widescreen support for all terminal screens (July 2026)

- FEATURE: All terminal screens (message boards, file library, RSS reader, who's online, profile, echomail, bulletins, PMs, compose, sysop tools, and every sub-menu) now expand to fill the full terminal width on 132-col (or any wide) connections. `ui_width(session)` helper added to `ansi_ui.py` — returns terminal width - 1, clamped 64–131. Every `banner()`, `footer()`, `menu_item()`, table header, and content column uses it.
- FEATURE: Content rows scale with terminal width: board name, thread subject, bulletin title, RSS feed name, echomail area name, file area name, and message body wrap width all expand proportionally on wide terminals.

---

# ANetBBS v1.0b2.4 — Game Center: Q and 3 both exit to main menu (July 2026)

- FIX: Game Center only accepted `3` to return to main menu; `Q` (the BBS convention) did not work. Both `3` and `Q` (case-insensitive) now exit the loop, matching the pattern used by Chat and Door Games.

---

# ANetBBS v1.0b2.3 — Display @-codes applied in all sub-menu art files (July 2026)

- FIX: `@SYSOP@`, `@BBS@`, `@VERSION@`, `@NODE@`, `@TIME@` and other display codes were not substituted in sub-menu art files (game_center, door_games, dialout, irc_chat). Added `write_menu_art(session, slot)` coroutine to `ansi_ui.py` — single shared path that loads art, applies display codes, and writes to the session. All callers switched to use it.
- FIX: `game_center` stock art showed `[Q]` for "Return to Main Menu" but code checked `'3'`. Fixed all three art files (`.ans`, `.asc`, `132.ans`).

---

# ANetBBS v1.0b2.2 — Mode-aware sub-menu art + stock chat/game_center files (July 2026)

- FEATURE: `load_menu_ansi()` is now mode-aware: passes `mode` arg, checks `{slot}132.ans` for wide terminals, `{slot}.asc` for ASCII terminals, and falls back to `anetbbs/screens/menus/` bundled stock art before returning None.
- FEATURE: Stock art files `chat.ans`, `chat.asc`, `chat132.ans`, `game_center.ans`, `game_center.asc`, `game_center132.ans` added to `anetbbs/screens/menus/`. Chat and Game Center menus now have CP437/ANSI art in all three terminal modes.
- FIX: All `load_menu_ansi()` callers (chat, games, dialout, irc_chat) now pass `self.session.term_mode` so the correct art variant is selected at runtime.
- NOTE: `door_games`, `dialout`, and `irc_chat` menus have dynamic content (game list, dial directory, IRC presets) — they stay auto-generated; the `write()` ANSI-strip (v1.0b1.9) already makes them ASCII-safe.

---

# ANetBBS v1.0b2.1 — Seed stock screens to data/text/ on install/update (July 2026)

- FIX: Stock slot and menu art files were not visible to sysops — they lived inside the Python package (`anetbbs/screens/`) but were never copied to `data/text/`. `update.sh` now seeds them on every install/update using `cp -n` (no-overwrite), so sysop-customised files are never touched.
- FEATURE: `anetbbs/screens/menus/` added: `main.ans` (79-col ANSI), `main.asc` (ASCII), `main132.ans` (131-col wide) ship as stock main menu art.

---

# ANetBBS v1.0b2.0 — Stock slot screens as real files (July 2026)

- FEATURE: `welcome`, `newuser`, and `goodbye` slot screens now ship as real CP437/ANSI files in `anetbbs/screens/` (`.ans`, `.asc`, `132.ans` for all three modes). Previously these were inline Python strings with no widescreen version.
- FEATURE: `_show_ansi_screen` falls back to the bundled `anetbbs/screens/` files for all three modes (wide → 132.ans → .ans; ansi → .ans → .asc; ascii → .asc → .ans) before giving up. Sysop overrides in `data/text/` always take priority.
- REMOVE: Inline `_stock_ascii_screen()` Python string fallback replaced by the file-based system.

---

# ANetBBS v1.0b1.9 — ASCII mode: strip ANSI from all writes (July 2026)

- FIX: `write()` now strips all ANSI escape codes when `term_mode == 'ascii'`, so every hardcoded color string in the codebase (bot-gate prompt, security questions, new-user questionnaire, notifications, broadcasts, etc.) is automatically plain-text for dumb terminals. No per-call changes needed.

---

# ANetBBS v1.0b1.8 — ASCII mode login screen fix (July 2026)

- FIX: `login_screen()` rendered the 1/2/3 login menu with hardcoded ANSI color regardless of terminal mode. Now uses plain `+---+` ASCII box when `term_mode == 'ascii'`.

---

# ANetBBS v1.0b1.7 — Telnet TTYPE second-round fix (July 2026)

- FIX: Telnet terminal-type detection was incomplete. RFC 1091 TTYPE is a two-round exchange: after the client sends `WILL TTYPE`, the server must send `IAC SB TTYPE SEND IAC SE` before the client will report its type. That second round was never sent, so `terminal_type` was never populated and ASCII-mode detection via `TERM=dumb` never worked. `init_session()` now completes both rounds.

---

# ANetBBS v1.0b1.6 — Terminal detection fixes: SSH PTY size + telnet NAWS drain (July 2026)

- FIX: SSH sessions never picked up the client terminal size or type — `BBSSession` was created before reading the asyncssh PTY attributes. Now reads `process.term_type` and `process.term_size` from the SSH process immediately after session creation, so `term_mode` is correct for SSH logins.
- FIX: Telnet NAWS/TTYPE subnegotiation responses were discarded before they could be parsed. `init_session()` now drains and processes pending reader bytes after the negotiation sleep, so `window_size` and `terminal_type` are populated before `login_screen()` runs.
- FIX: Increased negotiation settle time from 100ms to 300ms to give slow clients time to respond.

---

# ANetBBS v1.0b1.5 — Multi-terminal mode: ASCII + widescreen detection (July 2026)

- TERMINAL: Added terminal capability detection — telnet NAWS subnegotiation is now parsed to get actual terminal width/height; TTYPE subnegotiation is now parsed to get terminal type string. Previously both were negotiated but the responses were thrown away.
- TERMINAL: New `term_mode` property on BBSSession: `'wide'` (cols ≥ 132), `'ansi'` (default), or `'ascii'` (dumb/TTY terminals).
- TERMINAL: Screen file resolution is now mode-aware. Wide: tries `{slot}132.ans` then falls back to `{slot}.ans`. ASCII: tries `{slot}.asc`. Sysops drop custom screens into `data/text/` as before.
- TERMINAL: DB-stored ANSI screens are skipped for ASCII-mode terminals (they contain escape codes). Stock plain-text fallbacks are shown for standard slots (welcome, newuser, goodbye) when no `.asc` file exists.
- TERMINAL: Menu engine updated to match — looks for `{name}132.ans` (wide), `{name}.asc` (ASCII), or `{name}.ans` (ANSI) in `data/text/menus/`. Auto-render adapts: ASCII gets plain-text box-draw with no escape codes; wide gets 128-column ANSI layout.
- TERMINAL: Pause prompt (`[Press any key to continue]`) strips ANSI color when rendering plain-text screens.

---

# ANetBBS v1.0b1.4 — Terminal MRC /me fix (June 2026)

- FIX: Terminal MRC `/me` action was sending IRC CTCP format (`\x01ACTION ...\x01`) which the MRC bridge does not recognise. Changed to `* text` format (matching the web client), so the bridge now correctly formats it as `* Nick action` for all users.

---

# ANetBBS v1.0b1.3 — Auto-update beta version fix (June 2026)

- FIX: `deploy/run_upgrade.sh` had an alpha-only version regex (`vX.YaZ.NN`) that rejected beta version strings, causing auto-update to fail with "does not match expected form". Now accepts both `a` and `b` phase markers.
- FIX: `admin.py` release-notes boundary regex also updated to match beta versions.

---

# ANetBBS v1.0b1.2 — Message viewer position indicator fix (June 2026)

- TERMINAL: ANView message viewer status bar now shows the **last visible line** number instead of the first visible line. At the bottom of a 29-line message you now see `Ln:29/29  END` instead of `Ln:8/29`, making it obvious when you have reached the end of the message. The `END` tag appears only when the bottom of the content is on screen.

---

# ANetBBS v1.0b1.1 — Beta 1 Release (June 2026)

The public-facing echomail browser is now network-first, matching the admin "Manage Areas" UX added in v1.0a2.221.

**Web (`/echomail/`)**
- Landing page is now a network chooser: one card per network with type badge (BinkP/QWK), area count, and total unread badge
- Clicking a card goes to `/echomail/network/<id>` — a per-network area list grouped by category (General, Technology, BBS Scene, …)
- Each area card shows unread count, message count, last-message date, tag, and quick buttons: Enter / Next Unread / Compose
- "All Areas" back button on area pages now reads "← [Network Name]" and returns to the correct network view

**Terminal (telnet/SSH)**
- Echomail menu option now shows a network selector first (number / Q)
- After choosing a network, shows areas for that network grouped by category with `── Group ──` headers
- Per-network area list: `B=back` returns to network list, `Q` exits entirely
- Both levels use the standard `banner()` / `footer()` / `prompt()` ANSI chrome

---

# ANetBBS v1.0a2.221 — Echomail bulk area management; network-first UI (June 2026)

Echomail "Manage Areas" is now a network-first experience. The page opens with a card grid
showing every configured network — name, type (BinkP / QWK), hub address, and live counts
of total / active / subscribed areas. Click **Manage Areas** on any card to drill into that
network's areas.

**Per-network area view**
- Areas grouped by category (General, Technology, BBS Scene, …) with collapsible sections
- Bulk-select toolbar: All / None / Active / Subscribed quick-select buttons + selected count
- Bulk actions: Set Active, Set Inactive, Set Subscribed, Set Unsubscribed, Set Active+Subscribed,
  Set Sysop-Only, Clear Sysop-Only, Set Access Level, Delete Selected
- All per-row actions (toggle subscribe, edit, %RESCAN, delete) now return to the correct
  network's area list instead of the top-level chooser
- AreaFix quick panel and QWK quick-add panel moved into the per-network view
- Bulk import and bad-area actions also redirect back to the relevant network

---

# ANetBBS v1.0a2.220 — ANotherNetwork QWK/FTP seeded; 26 conference areas (June 2026)

ANotherNetwork now ships with full QWK support over FTP alongside the existing BinkP path,
and with 26 pre-organized conference areas across 8 named groups.

**ANotherNetwork QWK/FTP**
- New `ANotherNetwork (QWK)` network seeded: FTP transport, hub ID `ANET`
- Download: `ftp://bbs.a-net.fyi/ANET.qwk` — Upload: `ftp://bbs.a-net.fyi/<YOURID>.rep`
- FTP server now authenticates QWK nodes by packet_id + password (from Hub Management → QWK Nodes)
- Packet generated fresh on login; `.rep` processed and fanned out to BinkP subscribers automatically
- Node home dir: `data/qwk-hub/<PACKET_ID>/`
- New `echomail/qwk_hub_ftp.py`: packet builder + REP importer + tosser integration

**26 conference areas in 8 groups**
- General: General, Introductions, Humor & Jokes, Friendly Debate, Network Feedback
- Technology: Technology, Linux & Open Source, Security & Privacy, Networking & Internet
- BBS Scene: BBS News, BBS Dev, ANetBBS Support, Door Games, ANSI/ASCII Art
- Retro: Retro Computing, Games & Gaming, Music
- Hobby: Movies & TV, Books & Reading, Food & Cooking, Sports
- Trading: For Sale / Wanted / Trades
- Data: Data & File Discussion
- SysOp (sysop-only): SysOp Discussion, SysOp Help & Tips
- Test: Test Messages

**`EchoArea` model**
- New `category` field (VARCHAR 80, nullable) — auto-migrated on upgrade

---

# ANetBBS v1.0a2.219 — ANotherNetwork Zone 1200 seeded; nodelist endpoint (June 2026)

ANotherNetwork (Zone 1200) is now seeded on every fresh install — the same way
Dove-Net ships pre-configured with Synchronet.

**ANotherNetwork (Zone 1200)**
- BinkP hub: `bbs.a-net.fyi` at FTN address `1200:1/1`
- Five echo areas pre-created: ANN.GENERAL, ANN.SYSOP, ANN.TEST, ANN.BBSDEV, ANN.RETRO
- Network is seeded but inactive — sysop must enter their assigned node address and
  session password in Admin → Echomail to activate. Apply at bbs.a-net.fyi.

**Nodelist endpoint**
- Public URL: `/admin/echomail/hub/nodelist` — serves the current ANotherNetwork NODELIST
  (FTS-5000 format) as a downloadable text file named `NODELIST.NNN` (day of year).
  No login required. Includes all active registered BinkP downstream nodes.

---

# ANetBBS v1.0a2.218 — Hub mode: BinkP hub + QWK hub (June 2026)

ANetBBS can now act as an echomail hub — not just a leaf node.

**BinkP Hub**
- Register downstream BinkP nodes via Admin → Echomail → Hub Management
- Per-node echo area subscriptions managed via the admin UI or by areafix netmail from the node
- Outbound hold queue: new messages are fanned out to all subscribed nodes; they pick up mail on their next BinkP connection
- Areafix robot now distinguishes upstream hubs from downstream nodes — subscription changes for downstream peers affect per-node `EchoAreaNode` records, not the global `EchoArea.is_subscribed` flag
- Catchup: queue the full backlog of an area for a newly-subscribed node with one click
- Hold queue viewer at Admin → Echomail → Hub Management → Hold Queue

**QWK Hub**
- Register QWK nodes via Admin → Echomail → Hub Management → QWK Nodes
- HTTP endpoints: `GET /qwkhub/<nodeid>.qwk` (download) and `POST /qwkhub/<nodeid>.rep` (upload)
- Per-node conference subscriptions with high-water mark tracking — nodes receive only new messages since last poll
- Inbound REP packets are imported and fanned out to BinkP downstream nodes automatically
- Hub system ID configurable via `QWK_HUB_ID` setting in Admin → Settings

**Hub tosser**
- New `echomail/tosser.py` handles message fan-out: when a message arrives (BinkP listener, QWK hub upload, or upstream poll), it's automatically queued for all subscribed downstream BinkP nodes
- SEEN-BY aware: nodes already listed in a message's SEEN-BY are skipped

**Nodelist generator**
- `echomail/nodelist.py` now includes `generate_nodelist()` — produces a standard FidoNet-format NODELIST for hub operators to publish

**Navigation**
- New "Hub Management" link in Admin dropdown (under Echomail)
- Hub Management button added to Echomail Admin dashboard

---

# ANetBBS v1.0a2.217 — Pre-beta audit: bug fixes + doc corrections (June 2026)

- FIX: Private message bodies were always blank — template used wrong field name.
  PM read view, thread sidebar previews, and Quote Reply all affected. Fixed.
- FIX: File area storage path no longer shown to regular users (admin-only).
- FIX: Synchronet compat doc table overflow — wide tables now scroll horizontally.
- DOC: Install commands updated to current format throughout.
- DOC: Version references corrected; stale draft notes removed.
- DOC: Pi 3 confirmed working; table and hardware recommendations updated.
- DOC: DSR references removed from Image Galleries doc.
- DOC: Network list updated (tqwNet + zer0net replace outdated entries).
- DOC: Incorrect systemd service name in PORTS.md corrected.

---

# ANetBBS v1.0a2.214 — HACKERS theme easter egg: Hack the Planet modal (July 2026)

The "HACK THE PLANET" text in the navbar (HACKERS theme only) is now a secret clickable
link. Clicking it opens a tribute modal: crew roster with rainbow-animated handles
(Zero Cool · Acid Burn · Crash Override · Cereal Killer · Lord Nikon · Phantom Phreak ·
The Plague), iconic quotes, a heartfelt BBS tribute paragraph, and a typewriter terminal
that types "HACK THE PLANET" then "ACCESS GRANTED". Closes with [ LATER, HACKER ].

---

# ANetBBS v1.0a2.213 — Two epic themes: VOID SIGNAL + HACKERS (1995) (July 2026)

Two new built-in themes, both wildly different from the standard color-swap options.
Select either in **Profile → Themes** (auto-seeded on startup, nothing to configure).

**VOID SIGNAL ◈**: Triple neon — electric green + cyan + magenta on pure black.
Full-page CRT scanlines, moving phosphor beam sweep, brand glitch animation,
cycling 3-color card border stripe, neon glow buttons, custom green scrollbar.

**HACKERS (1995) ◈**: For those who ride the information superhighway.
Neon violet + lime + cyan on black with rainbow cycling navbar border, 6-stage
rainbow brand glitch, animated multicolor card top stripe, rainbow footer sweep,
and a "HACK THE PLANET" watermark in the navbar. Hack the planet.

---

# ANetBBS v1.0a2.212 — Fix casino wallet saves; fix stale door game sessions (July 2026)

- **Casino wallet CSRF fix**: wallet balance now saves correctly after each hand/spin.
  The `_csrf()` helper in all 4 casino templates was reading from a cookie that doesn't
  exist — token is in `<meta name="csrf-token">`. Every wallet POST was silently rejected.
- **Door game sessions**: WebSocket disconnect now terminates the game session.
  A socket→session map is maintained so `game_disconnect` can call `terminate_session`.
- **Startup session cleanup**: any sessions still marked `active` from before a server
  restart are marked `stale` on startup (PTY state is gone after restart anyway).
- **Admin → Game Sessions**: "Terminate All" button bulk-clears all active sessions at once.

---

# ANetBBS v1.0a2.211 — Web casino games: persistent wallet + weekly reset + leaderboard (July 2026)

Blackjack, Slots, Video Poker, and Hold'em now remember your balance between sessions.
Balance resets every Monday to the configured starting amount. If you hit zero, play
is locked until the weekly reset. Peak balance is tracked on the leaderboard.
Starting amounts are configurable per-game in Admin → Settings (`CASINO_*_START` keys).

---

# ANetBBS v1.0a2.210 — Fix upgrade checker error when REGISTRY_URL is not configured (July 2026)

Admin → Upgrades no longer shows a `MissingSchema` error when `REGISTRY_URL` is not set.

---

# ANetBBS v1.0a2.209 — Menu admin: add imsg / imsg_send / rss to action-type dropdown (July 2026)

---

# ANetBBS v1.0a2.207 — Terminal MRC fixes; QWK blank-body diagnostic (July 2026)

**Terminal MRC chat (`/mrc`):**

- `/afk [msg]` now sends the correct `AFK` command (was `STATUS AFK`), so the
  away message set with `/afk BRB` is now visible to other chatters
- `/back` now sends `BACK` instead of `STATUS AFK`
- Fixed "Rate limit: please slow down" error when sending the first message
  after returning from AFK — the client no longer fires an extra `STATUS AFK`
  packet immediately before the chat message
- Tab-complete now tracks users who join after you: `USERIN:`, `USEROUT:`,
  `USERLIST:`, and `USERNICK:` packets are parsed to keep the nick pool current
  instead of being silently dropped. The `from_user` field (what the bridge
  actually sends) is also used for nick tracking on chat messages.
- `/chatters` now sends `CHATTERS` (all rooms); `/who` and `/whoon` send
  `WHOON` (current room only). Previously both sent `WHOON`.
- Help text updated to reflect the `/who` vs `/chatters` distinction.

**QWK inbound:**

- Added `WARNING` log line when an inbound Dove-Net message arrives with a
  blank body. Logs the first 256 raw bytes so we can tell whether the hub is
  sending empty blocks or whether `_clean_body` is over-stripping.

**Admin UI:**

- QWK password field now has `autocomplete="new-password"` to prevent browsers
  from auto-filling it with the admin login password.

---

# ANetBBS v1.0a2.206 — Fix dosemu2 conf written to /tmp (July 2026)

dosemu2 temp files (conf + COM1 PTS path) were hardcoded to `/tmp/`. Servers
with a restricted `/tmp/` (noexec or tightened permissions) failed to launch
any dosemu door with "Permission denied". Files now written to
`<DATA_DIR>/temp/` via the existing `temp_root()` helper, which the anetbbs
service user always has write access to.

---

# ANetBBS v1.0a2.205 — Fix menu hotkey duplication on update (July 2026)

Menu seeder now checks by action type instead of hotkey when backfilling new
items on existing installs. Sysops who rebound default hotkeys no longer get
duplicate menu entries added back on every service restart or update.

---

# ANetBBS v1.0a2.204 — Remove ANetCRAFT Enhanced and RDQ3 from release (July 2026)

- Removed ANetCRAFT Enhanced web game (template + WEB_GAMES entry)
- Removed Red Dragon Quest 3 door (doors/mystic/rdq3/)

---

# ANetBBS v1.0a2.203 — Beta release: package cleanup, REGISTRY_URL fix, docs update (July 2026)

Pre-release cleanup for the v1.0 Beta on July 1, 2026.

- Fixed `REGISTRY_URL` default from hardcoded hub to empty string — sysops must now opt in to federation by setting `REGISTRY_URL` in Admin → Settings
- `mrc/bridge/config.example.json`: changed `bridge_bbs` from hardcoded BBS name to generic placeholder
- CHANGELOG: removed internal tooling reference from v1.0a2.200 entry
- README: updated status from alpha 2 to beta; removed broken FEATURES.md link

---

# ANetBBS v1.0a2.202 — Security: auto-ban, IP whitelist, GeoIP country blocking, wiki edit gate (June 2026)

Auto-ban permanently blocks IPs that trip the login rate limiter (10 attempts / 5 min).
IP whitelist bypasses all ban and country checks. Country blocking via ip-api.com (free,
no registration — just set BLOCKED_COUNTRIES=CN,RU,... in Admin Settings). Wiki edit gate
requires 5 posts + 3-day account (configurable). fail2ban configs in deploy/fail2ban/.

---

# ANetBBS v1.0a2.201 — Theme edit/delete + MSP toggle in Admin Settings (June 2026)

Theme management is now fully self-service from the admin UI:

- **Theme edit**: Edit button was wired to the route but missing from the theme_builder listing.
  Now shows in both Admin → Themes and the Theme Builder's existing-themes table.
- **Theme delete**: New `DELETE /admin/themes/<id>/delete` route. Refuses to delete the
  active default theme (the button is disabled with a tooltip). Users who had the deleted theme
  selected fall back gracefully (their `theme_id` is nulled → system default applies).
  Confirm dialog in browser prevents accidental deletion.
- **MSP enable/disable**: `MSP_ENABLED` and `MSP_PORT` added to Admin → Settings
  EDITABLE_SETTINGS list. Sysops can now toggle MSP on/off and change the port from the UI
  without editing `.env` by hand. Requires service restart (flagged in the UI).

# ANetBBS v1.0a2.200 — QWK outbound: remove diagnostic logging

Remove verbose REP diagnostics added in v1.0a2.198 (body hex dump, /tmp REP copy).
The bugs they targeted are fixed; production logging is restored to concise INFO lines.

# ANetBBS v1.0a2.199 — QWK outbound: fix \r in body + MSGID space truncation

Fix two bugs in `_build_rep_packet` confirmed by hex-dumping `/tmp/anetbbs_last.rep`:

1. Body encoding left `\r` bytes from Windows `\r\n` line endings. The replacement
   only converted `\n` → `\xe3`, leaving `\r\xe3` throughout the body instead of
   bare `\xe3`. Fixed: replace `\r\n` first, then lone `\r`, then `\n`.

2. Auto-generated MSGID contained a space: `ANETBBS_<hash> <timestamp>`. Synchronet's
   `qwk_import_msg` calls `truncstr(p, " ")` on the MSGID value, storing only
   `ANETBBS_<hash>` — discarding the timestamp. That truncated form is identical on
   every re-upload of the same message, triggering dupe detection and silently dropping
   the message. Fixed: use underscore separator so the full string is stored.

# ANetBBS v1.0a2.198 — QWK diagnostics: REP saved to /tmp, body hex dump

Add two diagnostics to pinpoint why outbound QWK messages don't appear on Dove-Net:
- Save every REP packet to `/tmp/anetbbs_last.rep` for local inspection
- Log first 64 bytes of body hex in REP header dump

# ANetBBS v1.0a2.196 — QWK outbound: uppercase MSG filename + space padding

Fix outbound QWK (Dove-Net) messages not appearing on the hub. Two bugs in `_build_rep_packet`:

1. Inner ZIP filename was `VERT.msg` (lowercase extension). Synchronet's `pack_rep.cpp` creates `VERT.MSG` (uppercase). The `extract_files_from_archive` call in `un_rep.cpp` filters filenames through `SAFEST_FILENAME_CHARS`; on any configuration that excludes lowercase letters, extraction breaks entirely (the code uses `break`, not `continue`), the MSG file is never extracted, and the hub silently rejects the REP with "MSG file not received".

2. Body block padding used null bytes (`\x00`) instead of spaces (`0x20`). Synchronet pads with spaces; null bytes are skipped in the QWK body parser loop but non-standard.
