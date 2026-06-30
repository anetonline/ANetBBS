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
