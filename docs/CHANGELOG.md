# ANetBBS Changelog

Versions are internal build numbers. Public releases are tagged
separately. Current release: **`v1.0b2.167`** (July 2026). Full release: August 1 2026.

## v1.0b2.167 — Meadowlark Valley: volume control (July 2026)

- FEATURE: a volume slider next to the mute button sets a master volume (0-100%) for every sound effect and the ambient music, independent of mute — muting/unmuting restores whatever volume was last set.

## v1.0b2.166 — Meadowlark Valley code review + game docs (July 2026)

- FIX: Fire Station's real fire-coverage radius had silently drifted to match Police's widened radius instead of its own stated 5 tiles — now derived consistently for every building type.
- FIX: a farm's single job slot could get double-booked by multiple villagers over time, and villagers with no reachable road to their job were still counted as employed.
- FIX: co-op autosave could silently overwrite a guest's own save slot with the host's town; a guest's own placements could briefly show a phantom building before the host's correction arrived.
- FEATURE: in-game Help (❓) explaining the mechanics; info panel now shows a farm's assigned farmer.
- DOCS: new wiki pages for the Game Center, Ebook Reader, and Meadowlark Valley; a new sysop-facing games catalog doc; fixed a stale reference to a game that doesn't exist.

## v1.0b2.165 — Meadowlark Valley: sidebar overlap fix + auto-harvesting farmers (July 2026)

- FIX: the Services/Zones sidebar rendered every section on top of each other on shorter screens — reported live via screenshot. A flexbox default let sections shrink below their own content instead of the sidebar scrolling.
- FEATURE: Farm Plots now employ a farmer (like a Shop or Factory) who harvests the crop automatically the day it ripens, credited straight to your treasury. Unstaffed farms still work exactly like before — click to harvest.

## v1.0b2.164 — Meadowlark Valley: ANetBBS web game + co-op, tax/vandalism balance fixes (July 2026)

- FEATURE: Meadowlark Valley (the town/farm-builder sim) is now a full Game Center entry — server-side save/load tied to your account (3 slots), no more import/export files.
- FEATURE: Co-op mode — host a room, share a code, build the same town together with friends in real time.
- FIX: default tax rate was a guaranteed slow bankruptcy for any real town; rebalanced so default tax roughly breaks even and higher tax genuinely pays off, instead of max tax being the only way to survive.
- FIX: vandalism could start before Police Stations even unlock, and extra stations barely helped a spread-out town; both fixed.

## v1.0b2.163 — Who's Online font-path leak + "Default Theme" fixes (July 2026)

- FIX: Who's Online sometimes showed a user "on" /static/fonts/Ac437_IBM_VGA_9x16.woff instead of their real page — a lazily-loaded font fetch could be the last request recorded for a session. Static asset requests no longer update presence.
- FIX: setting a theme as the site's "Default Theme" in Admin had no effect — nothing ever read that flag. Both the web UI and terminal UI now actually fall back to it, and their "Default" theme-picker labels show which theme that really is instead of a hardcoded "Classic Green".

## v1.0b2.162 — Tagline picker selection visibility: give up on reverse-video (July 2026)

Third attempt at the same bug, reported live each time via screenshot: the selected row in the tagline picker was still invisible after both (1) adding an explicit color to compete with the reverse-video highlight, and (2) removing that color to match every other lightbar row's convention of relying on reverse-video alone. Both were still invisible on the user's terminal (SyncTERM), which means reverse-video + bold itself doesn't render usably on that client, regardless of what color the row text has. Stopped guessing at the SGR interaction and sidestepped reverse-video for this row entirely: it now explicitly cancels the wrapper's escape codes and draws its own `> ` marker in a plain bright color instead, which doesn't depend on how any given client's reverse-video happens to interact with bold.

## v1.0b2.161 — Wide-terminal row overflow, and the real selected-row visibility fix (July 2026)

- FIX: on a wide (132-col) terminal, the "Compose Echomail" area-picker lightbar corrupted itself after scrolling down then back up — stray fragments ("tegory", "S Scene", "neral") left on screen. Root cause: the row's Name-column width was computed as `terminal_width - 28`, undercounting the row's real overhead (tag + spacing + category = 37 visible chars, not 28) by 9 characters. On a 132-col terminal each row silently overflowed the line and the terminal auto-wrapped it onto the next line; a later partial redraw (scrolling) only overwrote part of that wrapped-in text, leaving the rest behind. Fixed the width math; added a test that measures every rendered row's real visible width against the terminal width at 132 columns.
- FIX (real fix this time): v1.0b2.160's tagline-picker color fix (adding an explicit bold-white foreground to the row text) was still invisible when selected, confirmed via a second live screenshot. The actual cause was the opposite of the first guess — an explicit foreground color competing with the reverse-video highlight is what's invisible, not the lack of one. Every other lightbar row in the app already avoids this by leaving the selected row's main text uncolored (relying purely on the reverse-video wrapper against default terminal colors) while only non-selected rows get an explicit color; the tagline picker now follows that same convention.

## v1.0b2.160 — Tagline picker: defer to send time, fix invisible selected row (July 2026)

- FIX: the terminal tagline picker showed up before the user even started typing — reported live ("it should not ask you about a tag line until you send, when you send it should bring up the tagline"). Moved from a pre-editor prompt to a `tagline_picker` callback that ANEdit itself calls once, at actual send time (Ctrl+W/`/send`). Removed the now-redundant `/tag` slash command and its toggle state — the picker is the only path now, and it always runs (if the pool isn't empty) right before finalizing a send, never on abort.
- FIX: the selected row in the tagline picker rendered as a blank, unreadable highlighted bar — the row text had no explicit foreground color, and reverse-video + bold with no explicit color apparently collapses to white-on-white in real terminal clients (SyncTERM, confirmed live via screenshot). Every other lightbar row in the app already colors its text for this exact reason; this was the one that didn't.

## v1.0b2.159 — Real root cause: the tagline seed file never shipped in ANY release tarball (July 2026)

Found after v1.0b2.158's fix still didn't work live: `build-release.sh` builds its file list from git (tracked + untracked-but-not-ignored), and `.gitignore`'s runtime-data rule was a bare `data/` — which git matches against every directory named `data` anywhere in the tree, not just the intended top-level `data/` (sysop DB/uploads/mail spools). That silently caught `anetbbs/data/` too, which holds bundled shipped content, not runtime state — so `anetbbs/data/default_taglines.txt` (~200 taglines) never made it into v1.0b2.157 or .158's tarball. The seed step's `open()` always hit a missing file on a real install, silently logged and skipped, so `/tag` correctly reported "no taglines available" — the pool was genuinely empty on every deployed copy, no matter which compose path you used. Every test in the suite passed throughout, because tests run against the repo checkout, which still has the file locally — none of them exercised "what actually ends up in the tarball."

Fixed by anchoring the rule to `/data/` and adding explicit entries for the two genuine runtime-state directories that were incidentally relying on the old broad pattern (`anetbbs/games/sbbs_doors/data/`, `vendor/games/anetsims/data/`). Also added a small regression test that shells out to `git check-ignore`/`git ls-files` to catch this exact class of "silently never shipped" bug going forward, since the existing test suite structurally couldn't.

## v1.0b2.158 — Fourth tagline call site: replying from inside an echo area (July 2026)

v1.0b2.157 wired the tagline picker into three terminal compose points (`_post_compose`, `_send_pm`, `_compose_echomail`), but missed a fourth: replying to (or starting a new message from) an echo area's message list — reached by reading an area, then pressing R or N inside the ANView reader — calls `launch_anedit()` directly from inside `read_echo_area()`, a separate code path. Reported live ("I still dont see an add tagline option in terminal when sending an echomail", then confirmed via `/tag` reporting "no taglines available" from that exact screen). Fixed; new regression test drives the real reply flow end-to-end.

## v1.0b2.157 — Tagline picker (browse & choose), compose-echomail lightbar, bad-area visibility (July 2026)

- Taglines now work as a **scrollable picker** in both terminal and web, not a blind random pick — you browse the pool and choose one, or skip. Terminal uses the same lightbar as everywhere else; web uses a visible multi-row listbox instead of a checkbox.
- FIX: composing a message in the terminal never actually asked whether to add a tagline — `/tag` was the only way in, and it wasn't even listed in ANEdit's real help screen (only in an unused internal help string). Added an active picker at all three terminal compose points, and added `/tag` to the real help screen too.
- FIX: the "Compose echomail" area picker (network → area → message) used the old numbered list with a `-- more (Enter / Q) --` page break every 17 areas. Now a scrollable lightbar, matching how areas are already browsed when reading.
- Added visibility for echomail dropped because a known area is unsubscribed/deactivated — previously silently discarded with no record at all (unlike the existing "unknown area" case). Both reasons now share the same Bad Areas admin review queue, tagged by reason, with a re-subscribe action.

## v1.0b2.156 — Message-board ANView fix, actually wired this time (July 2026)

v1.0b2.155's message-board fix targeted `read_thread()`/`list_threads()` (class-body methods), but both were dead code — `BBSMenuUI.list_threads` gets reassigned near the bottom of `bbs_ui.py` to a different implementation (`_list_threads_v2`, calling `read_thread_v2`) that shadows them at every real call site, so the original fix never actually ran. Found via a live terminal capture still showing the old `--MORE--` pager after the .155 build. Fixed the actually-reachable `read_thread_v2` to use ANView, and removed the now-confirmed-dead `read_thread`/`list_threads` methods. New end-to-end regression test drives the real `list_threads()` entry point (not just the function in isolation) to guard against this exact class of bug recurring.

## v1.0b2.155 — Shared tagline pool, and ANView for message boards (July 2026)

- Added a shared, sysop-editable pool of ~200 taglines. Opt in per message with a checkbox (web) or `/tag` (terminal) — works across local boards, private messages, netmail, and echomail. Distinct from the existing fixed per-user FTN tagline, which still auto-appends unconditionally to netmail/echomail.
- FIX: terminal message boards used the old page-break `[MORE]` pager instead of the scrollable ANView reader already used for echomail/private messages. Board threads now render through the same CP437/ANSI-aware pipeline, and reply/new-thread shortcuts work the same way they already do when reading echomail. (Note: this fix was incomplete — see v1.0b2.156.)

## v1.0b2.154 — File area fixes, per-network netmail options, and poll-in-progress visibility (July 2026)

- FIX: file area "Delete" silently saved instead of deleting (a hidden form field always overrode the button)
- Added bulk actions for file areas (multi-select, select-by-network, bulk enable/disable/subscribe/delete)
- FIX: TIC log showed a false "nodelist import failed" error on ordinary (non-nodelist) files
- Added per-network Crash/Hold/Direct netmail delivery defaults and a packet-header password field (separate from the BinkP session password)
- Added visibility for in-progress BinkP polls (previously showed nothing until a poll finished) — an admin can now see a poll is running, with a live elapsed-time counter and partial transcript

## v1.0b2.153 — Release-readiness audit: 10 install/update fixes ahead of the August 1 full release (July 2026)

Top-to-bottom pass over install.sh, update.sh, and their generated configs, prompted by wanting the BinkP fix (v1.0b2.147-152) to be the last "oh, our side is broken" surprise before the full release. All 10 findings below were fixed, in priority order:

- FIX (CRITICAL): install.sh computed a mode-specific `WEB_BIND` (e.g. `127.0.0.1` for a reverse-proxied test-mode install) but only ever displayed it — never wrote it to `.env`. `deploy/serve.py` therefore always fell back to its own `0.0.0.0` default regardless of install mode, silently defeating test mode's entire "gunicorn binds localhost-only, nothing reaches the LAN" security promise. Now written to `.env`; also added to `.env.example` so update.sh's existing key-backfill mechanism picks it up on upgrades of already-affected installs.
- FIX (HIGH): update.sh's rollback-on-failed-update path restored `.env`, the database, and systemd units, but never the application code itself — a bad update that failed its health check left the broken new code in place, so the "rollback" just restarted the same broken code. Added a pre-update code snapshot (excludes logs/data/venv, ~28MB) and restore-on-failure.
- FIX (HIGH): install.sh's fresh-install `anetbbs.service` (telnet/ssh/rlogin/FTP) never granted `CAP_NET_BIND_SERVICE`, so FTP (port 21) would silently fail to bind if a sysop later set `FTP_ENABLED=true` — until their next update.sh run self-healed it. Now granted at install time, matching what update.sh already does.
- FIX (MEDIUM): install.sh had no disk-space preflight check at all; update.sh gained one after a real disk-full corruption incident. Added the same check to install.sh.
- FIX (MEDIUM): the MRC bridge config self-heal path (update.sh) and `mrc/bridge/config.example.json` still defaulted to the old unencrypted `mrc_port: 5000` / `use_ssl: false`, stale since v1.0a2.111 changed the real default to `5001`/SSL. install.sh itself already had it right.
- FIX (MEDIUM): re-running install.sh against an existing install silently reset the sysop's password (if the same username was retyped) or created a second, duplicate admin account (if it wasn't) — no warning either way. Existing accounts are now preserved unless `--force` is passed.
- FIX (LOW): neither script checked for `systemctl` before assuming systemd — added a clear fail-fast message instead of scattered "command not found" errors partway through a 9-step install.
- FIX (LOW): update.sh's backup directory was created with default (world-readable) permissions and only `chmod 0700`'d after `.env.bak` (SECRET_KEY, DB credentials) and everything else was already written into it. Now locked down at creation.
- FIX (LOW): install.sh's nginx CVE-2026-42945 check tried to distinguish "vulnerable version, exploitable config" from "vulnerable version, safe config" using a crude "config contains both `rewrite` and `set`" heuristic. Verified the CVE itself against NVD/vendor advisories (real, CVSS 9.2, confirmed version range) but the heuristic doesn't match the actual trigger condition (an unnamed PCRE capture + literal `?` in a rewrite's replacement, followed by another rewrite/if/set) and could have told a genuinely vulnerable config it was safe. Dropped the exploitability guess; now always recommends patching when the version is in range.
- Documented `install.sh --uninstall` (and `--defaults`/`--force`) in the README — previously undocumented anywhere despite being a real, working flag.

No behavior changes to the running BBS itself — every fix in this release is in the install/update tooling. Full test suite (1032 tests) still green throughout.

## v1.0b2.152 — AKA announcement fix, plus a netmail send/receive correctness pass (July 2026)

Sysop-noticed oddity: some inbound sessions' M_ADR line only listed the default Fidonet address instead of all four configured network identities, while every outbound session correctly listed all four. Root cause: the AKA-lookup fix in v1.0b2.147 disposed the database connection *before* reading each network's address/domain fields instead of after, a detached-instance hazard that usually goes unnoticed (the values are normally already in memory) but can intermittently fail and silently fall back to a single default address. Moved the disposal to after all the fields are read. No automated regression test added for this one — reliably reproducing the exact timing needed to trigger a real SQLAlchemy detached-instance failure would need a heavier real-database test fixture than this module's existing mock-based ones; the fix itself is a straightforward reordering, safe by inspection.

Also did a netmail send/receive correctness pass while in this code (prompted by the M_GOT fix above). Found: the FMPT/TOPT kludges that carry a point system's point number (e.g. the ".5" in "1200:1/2.5") were parsed on receive and then silently discarded — our own outbound side never puts the point number inside the INTL kludge itself, only in these separate fields, so any point-addressed netmail lost its point number on import. The inbound listener also had its own separate, redundant re-derivation of the sender/recipient addresses that didn't use the (now-fixed) parser output at all. Both fixed; 4 new regression tests, each verified to fail without its corresponding fix.

## v1.0b2.151 — Found it: M_GOT was sending a hard-coded 0 instead of the file's real timestamp (July 2026)

The actual root cause of the multi-month BinkP resend loop, found by comparing our code line-by-line against binkd's own real source. When ANetBBS receives a file and acknowledges it with M_GOT, the reply is supposed to echo back the file's name, size, AND timestamp — ours sent the timestamp as a literal `0` instead of the real value, in both the inbound listener and the outbound poller.

binkd's own matching logic (`tfile_cmp()` in its `prothlp.c`) requires an *exact* match on all three fields before it will recognize our M_GOT as acknowledging the file it sent, and only then does it remove the file from its own outbound queue. A real timestamp looks like `1784314217` — it never equals `0`, so that match silently failed on every single file, every single session, regardless of anything else going on. The hub kept re-offering its entire backlog every poll because it never once got a M_GOT it could actually match, no matter how correctly ANetBBS otherwise received and acknowledged every file.

This fully explains the resend loop on its own — independent of session timing or the end-of-batch handshake work in v1.0b2.148-150, which remain valid improvements but were not the actual fix. 4 new regression tests, each verified to fail without the fix.

## v1.0b2.150 — Respond faster after a BinkP transfer instead of waiting out most of a minute first (July 2026)

Real measurement against a real Fidonet hub showed the TCP connection consistently dying ~15 seconds after its last file, well under either side's configured wait (120s inbound, 60s outbound) — meaning our own confirmatory end-of-batch signal (v1.0b2.148) was often being sent well after that window had already closed. Shrunk the wait to 5 seconds on both the inbound listener and outbound poller, so that signal has a real chance of reaching the hub while the link is still alive.

This is a genuine improvement to session responsiveness, verified with 2 new regression tests (each confirmed to fail without the fix) — but it's not confirmed to be the actual fix for the hub's resend loop. Direct inspection of binkd's own source turned up a more likely explanation: binkd deletes a file from its outbound queue the instant it receives our `M_GOT`, independent of anything that happens afterward in the session — and we've confirmed via transcript that our `M_GOT` has always been sent correctly. That points toward a stuck or misconfigured outbound-queue file on the hub's own system, not something fixable in our protocol timing. Investigation continues.

## v1.0b2.149 — Diagnostic logging: confirm whether the post-transfer M_EOB is actually reaching the hub (July 2026)

Follow-up to v1.0b2.148: the Fidonet hub's backlog still isn't shrinking even with that fix active. `_send_cmd()` logs its transcript line before attempting the actual socket write, so a failed write (e.g. the peer already closed the connection) can look identical in the transcript to a successful send. Added explicit success/failure logging around the proactive post-transfer M_EOB in both the inbound listener and outbound poller, to tell the two cases apart on the next poll. No behavior change.

## v1.0b2.148 — Inbound and outbound BinkP still weren't confirming delivery to the hub (July 2026)

Reported live: even after v1.0b2.147's fix, the same Fidonet hub kept resending its entire backlog every poll — the file list never shrank, even across sessions ANetBBS logged as clean successes in both directions (hub polling in, and ANetBBS polling out).

- FIX: ANetBBS only ever sent one BinkP end-of-batch signal (M_EOB) per session, sent before any files changed hands. Real file activity that followed voided it, and nothing ever sent a second, post-transfer confirmation — so a peer that never explicitly replies with its own end-of-batch signal (confirmed live: this hub doesn't) was left with no way to know the transfer was actually done, no matter how many files were individually acknowledged. Now sent unconditionally after every transfer completes, in both the inbound listener and the outbound poller.
- FIX: the inbound BinkP listener's address announcement never checked the per-network domain override (`ftn_domain`), always deriving one from the network's display name instead — a long name like "ANotherNetwork" always announced as `@anothern` regardless of any override already configured.

8 new regression tests, each verified to fail without its corresponding fix.

## v1.0b2.147 — Inbound BinkP sessions leaked a database connection every time (July 2026)

Reported live: a Fidonet hub kept resending the same packets dozens of times, even though ANetBBS was actually receiving and acknowledging every one of them successfully. A second sysop's own install hit the identical symptom.

- FIX: every inbound BinkP connection opened its own database connection and never closed it — twice per connection, in fact. The leak accumulated over hours of polling from multiple networks, occasionally causing just enough delay to trip a peer's own session timeout, even though our side finished and logged the poll as a success. Connections are now explicitly closed after every session, success or failure.

3 new regression tests, each verified to fail without the fix.

## v1.0b2.146 — Netmail flood was arriving through a second, unpatched path (July 2026)

Reported live: the same "Area Management Request"/"List of Available Areas" flood kept appearing every ~10 minutes even after v1.0b2.145 shipped, confirmed via direct database inspection.

- FIX: the peer polls into ANetBBS's inbound listener independently of — and around the same cadence as — the outbound poll of the same hub. That's a separate code path (`binkp_server.py`) from the one fixed in v1.0b2.143/145 (`poller.py`, used when ANetBBS dials out), and it never had any content-based dedup fallback at all — only the exact-MSGID check, which the peer's fresh-MSGID-per-resend behavior always defeats. Both directions now share the same sender+subject+network dedup approach.

3 new regression tests, each verified to fail without the fix.

## v1.0b2.145 — Web UI freezing for every user, and the netmail flood fix from v1.0b2.143 wasn't actually catching everything (July 2026)

Reported live, urgent: the entire web UI started freezing solid (unresponsive tab, not even a slow page) for minutes at a time, shortly after v1.0b2.143 shipped.

- FIX: `NetmailMessage.received_at` had no database index, but v1.0b2.143's new content-based netmail dedup check filters on it. This app runs under eventlet, which does not make SQLite non-blocking — so an unindexed, growing scan on every inbound netmail blocked the *entire* process for *every* user on *every* page until it finished. Added the missing index, plus an automatic backfill so already-installed databases pick it up on next startup with no manual step required.
- FIX: v1.0b2.143's dedup check also required an exact match on message body — confirmed live this wasn't sufficient (the same "Area Management Request"/"List of Available Areas" netmail kept creating new entries every ~10 minutes even with that fix deployed), which was also what fed the freeze above by continuously growing the unindexed table. Now matches on sender+subject+network within the dedup window instead, without requiring the body to be byte-identical.

If you already applied the manual `CREATE INDEX` workaround, this release's migration is idempotent and won't error on top of it.

9 new regression tests, each verified to fail without its corresponding fix.

## v1.0b2.144 — Inbound BinkP: crashed sessions left zero diagnostic trace (July 2026)

Reported live: a peer sysop's own binkd log showed our inbound listener closing the connection abruptly, mid-transfer, with none of his files ever getting an acknowledgment from our side — but there was no corresponding ANetBBS-side record of what happened, anywhere a sysop could see it.

- FIX: an inbound BinkP session only ever wrote a Poll Log entry on successful completion — that write happened as the very last step of handling a connection, so any exception before it (a network error, a bug, anything going wrong mid-transfer) meant the entire session vanished with no trace beyond a bare stack trace in the raw application log. A crashed session now writes an error-status Poll Log entry with the full frame-by-frame transcript captured up to the failure point, matching how the outbound poller has always behaved on failure.
- Also: the Poll Log transcript viewer loaded scrolled to the top of a fixed-height box — for any real file transfer (hundreds of frame-by-frame lines), this made it easy to believe a transcript ended wherever the visible area happened to stop, when it actually continued below. Now auto-scrolls to the end (the diagnostically relevant part) on load, with a "jump to start" button for the handshake.

2 new regression tests, each verified to fail without the fix.

## v1.0b2.143 — BinkP: peers resending their entire backlog forever, even after every file was correctly acknowledged (July 2026)

Reported live: a real FTN peer kept resending its complete inbound backlog on every single poll, indefinitely, despite ANetBBS correctly M_GOT-acknowledging every file every time — visible as a specific netmail ("Area Management Request" / "List of Available Areas") repeating every ~10 minutes.

- FIX: a shared internal helper used by both the outbound poller (`binkp.py`) and inbound listener (`binkp_server.py`) opportunistically captures frames a peer interleaves while we're still waiting on our own acknowledgment — but it only recognized file-offer frames. A peer's own end-of-batch signal (M_EOB), which a spec-compliant mailer sends the instant its own outbound queue empties — independent of whether it's still waiting on us — fell straight through and was silently discarded, with zero trace. Our own end-of-batch handshake would then wait indefinitely for a signal the peer had already sent once and had no reason to resend unprompted. From the peer's own side, its session never registered as successfully completed (the binkp spec requires both sides to receive the other's end-of-batch signal), so it kept requeuing and resending everything on every subsequent connection — root-caused against a complete real session transcript and the official binkp protocol specification. Fixed on both the outbound and inbound sides: an early end-of-batch signal is now recorded and credited instead of waited for a second time.
- Also added a content-based dedup fallback for inbound netmail: if a message's MSGID doesn't match anything on file, but its sender, subject, and body exactly match something received within the last 48 hours, it's now treated as a duplicate too — a backstop for any peer software that regenerates its MSGID on every resend.

15 new regression tests across three files, each verified to fail without its corresponding fix.

## v1.0b2.142 — Echomail web preview didn't match what actually gets sent (July 2026)

Reported live: after the v1.0b2.139/141 origin-line fixes, a newly
composed message still showed no address in its "* Origin:" line when
viewed in the web UI — the fix looked like it wasn't working.

- FIX: the message-read page displayed the raw stored `origin_line`
  field, which never includes the FTN address — that gets appended
  separately, per network, at actual BinkP send time. The preview
  simply didn't reflect what would really go out over the wire. The
  read page now computes the same address-append logic for outbound
  messages, so what a sysop sees in the web UI matches what a peer
  actually receives.

2 new regression tests, each verified to fail without the fix.

## v1.0b2.141 — BinkP: stop re-sending the same netmail/echomail forever when a peer talks a lot before acking (July 2026)

Reported live: an AreaFix subscription request to a real SBBSecho hub
kept getting re-sent on every single poll, forever — the peer replied
fresh each time, since as far as it could tell it was receiving a
brand-new request every time.

- FIX: the wait for a peer's M_GOT acknowledgment was bounded by a
  fixed count of frames (20), not wall-clock time. A peer that
  responds with substantial content of its own before finally
  acking — exactly what SBBSecho does, replying with real content
  spread across many small frames — could exhaust that budget with
  our own GOT never reached, even though the peer had already fully
  received and processed what we sent. The packet then got treated as
  unacknowledged and left queued for retry, so the next poll sent the
  identical request again — and the peer, having no reason to think
  otherwise, replied again. Now bounded by wall-clock time instead, so
  any amount of peer chatter before the real acknowledgment still gets
  fully drained.

3 new regression tests, each verified to fail without the fix.

## v1.0b2.140 — Echomail origin line: show how to reach the BBS, not just its name (July 2026)

Follow-up to v1.0b2.139's address fix — requested live: the default
origin-line text itself should follow the common FTN sysop convention
of listing how to actually connect, not just the BBS's name.

- `ECHOMAIL_ORIGIN_LINE`'s default is now `<hostname>, Telnet:N SSH:N
  HTTP:N` (e.g. `joesbbs.com, Telnet:23 SSH:22 HTTP:80`), using
  whichever of those services are actually enabled and their real
  configured ports. Combined with v1.0b2.139's per-network address
  fix, a full origin line now reads e.g. `joesbbs.com, Telnet:23
  SSH:22 HTTP:80 (1:342/201)` — matching the standard FTN format.

5 new regression tests.

## v1.0b2.139 — Outbound echomail origin lines were missing the FTN address entirely (July 2026)

Reported live: outbound messages showed `* Origin: ANetBBS - A Modern
BBS System` with no address at all, instead of the FTN convention
`<text> (<our address>)` a peer's own reply routing depends on.

- FIX: the address-inclusive fallback only ran when the message's
  origin line was completely empty — but the web compose form always
  populates it from a single global tagline, so that fallback never
  actually fired for any real outbound message, on any network. The
  network's own address (already resolved per-network for multi-hub-
  identity installs) is now always appended, regardless of what origin
  text is configured.
- Fresh installs also no longer default to generic ANetBBS-branding
  text for this — the origin line now defaults to the sysop's own
  configured BBS name instead.

4 new regression tests, each verified to fail without the fix.

## v1.0b2.138 — Scheduled events: one hung handler could silently kill the entire scheduler forever (July 2026)

Reported live: a sysop's scheduled events — including the stock
defaults, not just the one they'd added — simply stopped firing
entirely, with no error anywhere. A 4-day log showed the scheduler's
own startup line exactly once and nothing after.

- FIX: a handler that hung (a `shell` command waiting on input, a
  network call with no effective timeout, anything) froze the
  scheduler thread forever — silently, with no crash logged — taking
  down every other scheduled event with it, including ones that had
  nothing to do with whatever hung. This was a known, deliberate
  tradeoff ("a runaway is a sysop bug, not a security threat"), but
  the real-world consequence turned out to be worse than that framing
  accounted for. Every handler call is now bounded to a generous
  5-minute ceiling — comfortably above what any of the built-in
  handlers actually need — so one hung handler can no longer take
  every other event down with it.

4 new regression tests, each verified to fail without the fix.

## v1.0b2.137 — BinkP inbound listener: stop making peers wait in silence (July 2026)

A peer sysop's own binkd log showed it delivering files to our
inbound listener successfully — every one individually acknowledged —
then sitting in total silence for over 2 minutes hearing nothing back
from us, before giving up, closing the connection, and marking the
whole transfer failed. It kept resending the same backlog on every
subsequent connection as a result.

- FIX: the inbound listener used to fully drain everything a peer
  sent (waiting up to 120s for the *peer's own* end-of-batch signal)
  before ever sending our own outbound mail or announcing that we
  were done. If the peer's own mailer was itself waiting to hear from
  us first — which nothing in the protocol requires either side to
  do — neither side would say anything until somebody's timeout fired.
  We now send our own outbound mail (if any) and announce we're done
  immediately after authenticating, before waiting on the peer's
  stream at all.
- As a consequence of sending earlier, a peer may now interleave its
  own file delivery while we're still waiting on an acknowledgment for
  ours — those were previously logged and silently dropped; they're
  now received correctly, same as the outbound side already handled.

5 new/updated regression tests, each verified to fail without the fix.

## v1.0b2.136 — Docs/wiki full accuracy pass (July 2026)

First full docs+wiki pass since v1.0b2.45-46 — 5 parallel audit agents
checked all 29 docs/*.md files and all 47 wiki pages against actual
current code.

- Fixed stale references to gunicorn (dropped for eventlet's native
  WSGI server back at v1.0a2.67, several docs never caught up), a
  badly outdated Service Control Center wiki page, and a factually
  wrong claim about echomail duplicate handling.
- Documented several real, previously-undocumented features: a new
  Scheduled Events wiki page, ANetCRAFT (the bundled door game),
  the Ebook Reader, and the terminal Node Monitor's message capability.
- Found and fixed 43 broken internal wiki links, plus assorted smaller
  path/port/version-number corrections.

No code changes — docs and wiki content only.

## v1.0b2.135 — BinkP inbound listener: fix unbounded duplicate-message import (July 2026)

Reported live: a peer sysop's poll log showed the same ~570-message
backlog "received" fresh on every inbound BinkP connection, a few
minutes apart, with no sign of slowing down.

- FIX: the inbound BinkP listener (a peer connecting *into* this BBS
  to deliver mail) imported every message in a delivered packet
  unconditionally — no deduplication at all. For echomail it didn't
  even capture the MSGID kludge onto the row in the first place; for
  netmail it captured MSGID but never checked for an existing one
  before inserting. The outbound poller's own import path already did
  both correctly (`poller.py`) — this second, separate import path
  (`binkp_server.py`, used specifically when a peer dials in rather
  than when we dial out) never got the same treatment. Any redelivery
  of a backlog — a peer's own retry logic, a flaky link, anything —
  got re-imported as entirely new messages, unbounded, every time.
  Now deduplicates by MSGID the same way the outbound path already
  does.

3 new regression tests, built from a real FTS-0001 packet round-trip
(not a hand-built dict), each verified to fail without the fix.

## v1.0b2.134 — BinkP GOT-ack fix, Service Control Center + scheduled-events reliability (July 2026)

Three unrelated reports: a peer sysop's poll log showing a real
FidoNet hub rejecting our file acknowledgments mid-session, a Control
Center screenshot showing a terminal-services crash with an alarming
(but misleading) 99.3% CPU reading, and a peer sysop reporting his
nightly/hourly scheduled events just weren't firing.

- FIX: receiving files during a poll we initiated ourselves (as
  opposed to a peer polling us) acknowledged each one with a bare
  `GOT: filename` — no size or timestamp. FTS-1026 defines `M_GOT` as
  `filename size time`, the same three fields as `M_FILE`; our own
  inbound listener already sent this correctly, but this second,
  separate code path never got the same fix. Tolerated by most peers,
  but confirmed live against a real, stricter binkd: the first two
  files' bare GOT acks were silently accepted, the third was rejected
  outright (`ERR: M_GOT: cannot parse args`) and the peer hung up —
  reproduced identically across two independent sessions.
- FIX: the Service Control Center's CPU%/RAM/thread pills kept showing
  a dead service's *last-known* reading indefinitely, with nothing
  marking it stale — a crashed process could look like it was still
  pinning the CPU for up to 5 minutes after it actually exited. Now
  blanked to "—" the moment the sampler's most recent tick comes back
  empty. Same bug also silently inflated the aggregate CPU/RAM totals
  at the top of the panel.
- FIX: the Control Center's own `/status.json` polling (systemctl
  state + journal reads, once per known unit, every 5s while the panel
  is open) ran raw `subprocess.run()` under gunicorn+eventlet — the
  same tight-loop shape already found crash-looping in the metrics
  sampler (v1.0b2.131, "Second simultaneous read on fileno N
  detected"). Routed through `eventlet.tpool.execute()` the same way.
- FIX: one scheduled event with an out-of-range time value (the
  `daily`/`weekly` schedule kinds were never format/range-validated,
  unlike hourly/weekly-day/interval) could crash the *entire* event
  scheduler sweep every single tick — silently blocking every other
  scheduled event (nightly maintenance, log rotation, VACUUM, custom
  events) from ever running again, with only one generic log line to
  go on. The scheduler now isolates each event so one bad row can't
  affect the others, and the form validates daily/weekly times the
  same way the other schedule kinds already were.

17 new regression tests across the three testable fixes (the stale-
metrics display fix is front-end JS with no test harness in this
project), each verified to fail without its fix.

## v1.0b2.133 — Terminal MRC: fix false "Rate limit" error on split messages (July 2026)

Reported live ("still getting this rate error when dming, even
though it's not [too long]"). Not actually related to message length
in the sense reported — the fix is real, but the mechanism is
different from what it looked like.

- FIX: a message that needs to split into multiple wire chunks (this
  can happen well under the 140-char hub limit — a decorated display
  handle's prefix/suffix eats into that budget before your own text
  even starts) was sending every chunk back-to-back with zero delay.
  The bridge only allows one message every 0.5 seconds per
  connection, so any chunk after the first was always instantly
  rejected with "Rate limit: please slow down." Fixed by adding a
  small pause before each chunk after the first, in all four places
  a message can split: room chat, `/me`, `/broadcast`, and DMs
  (`/msg`, `/r`).

4 new regression tests, each verified to fail without the fix.

## v1.0b2.132 — Web: fix door game output silently stopping after the first idle pause (July 2026)

Root-caused the actual "dosemu2 door games show a black screen over
the web UI" report — confirmed via a live log capture showing dosemu2
booting and rendering correctly server-side the entire time, while
the browser never received any of it.

- FIX: `handle_start_game()`'s background output-draining task has no
  Flask app context of its own. Its idle-timeout check (does the
  queue sit empty for 5+ seconds? then verify the session is still
  active) needs a database query, which needs that context — and
  didn't have one. The very first time a door produced no output for
  5+ seconds (a completely normal boot/loading pause — near-
  guaranteed for dosemu2 specifically, since it's much slower to boot
  than a native door), that query crashed with `RuntimeError: Working
  outside of application context`, which the surrounding code treated
  as "the session must be gone" and permanently stopped draining the
  queue for the rest of that browser session. The backend kept
  running the door correctly the whole time; nothing it rendered
  after that point ever reached the browser again — exactly a stuck
  black screen.

1 new regression test that reproduces the real 5-second idle gap
directly (not simulated) and confirms output sent after it still
reaches the browser — confirmed to fail with the exact live error
message before the fix.

## v1.0b2.131 — Web: fix Service Control Center metrics sampler crash-loop (July 2026)

Found while investigating a separate live "dosemu2 door games show a
black screen over the web UI" report — this was the actual thing
crash-looping in the same process, every ~2 seconds, confirmed via
`journalctl`.

- FIX: the Service Control Center's background per-PID metrics
  sampler called `subprocess.run()` once per known systemd unit in a
  tight sequential loop. Under gunicorn+eventlet, that call runs
  through eventlet's greened subprocess module; any transient hiccup
  could leave its fd-listener registered in the shared epoll hub past
  the point its fd number got recycled by the next call, colliding
  with it (`RuntimeError: Second simultaneous read on fileno N
  detected`) and crash-looping from that point on, indefinitely. Now
  dispatches the call to a real native OS thread via
  `eventlet.tpool.execute()`, bypassing eventlet's greened machinery
  entirely — the officially recommended pattern for exactly this.

4 new regression tests, each verified to fail without the fix.

## v1.0b2.130 — MRC: stop sending LOGOFF on individual leave — this is what was breaking trust (July 2026)

Root-caused from a real, complete packet transcript
(`MRC_BRIDGE_LOG_LEVEL=DEBUG`) captured on the live server — the first
fix in this saga backed by actual evidence instead of spec/source
comparison.

- FIX: the bridge sent `LOGOFF` every time an individual caller left
  a room. The transcript showed this ends the hub's MRC Trust state
  for that handle **immediately** — the very next join got "Cannot
  join ROOM, please IDENTIFY to use this handle" even though the
  bridge's own connection to the hub never dropped in between. Since
  this bridge holds one persistent shared connection to the hub per
  BBS install across every local caller's join/leave, there's no
  need to tell the hub "this handle is logging off" the way a
  single-session client would. `LOGOFF` is no longer sent on an
  individual leave (neither an explicit `/quit` nor an abrupt
  disconnect) — `NOTME`'s "has left chat" message still covers the
  visible room-presence announcement other users see.
- Trade-off worth knowing: the hub's own `/who`/`CHATTERS` listing
  may show your handle lingering briefly after you leave, until your
  next reconnect's fresh join or the hub's own idle timeout cleans
  it up.

4 new regression tests, each verified to fail without the fix.

## v1.0b2.129 — MRC: fix debug-level packet tracing not actually activating (July 2026)

`.128`'s `MRC_BRIDGE_LOG_LEVEL=DEBUG` had no effect — confirmed live,
zero `MRC RAW` lines after enabling it and restarting.
`logging.basicConfig()` is a documented no-op if the root logger
already has a handler attached before it runs (plausible under
systemd, depending on import order) — it was silently doing nothing.
Now sets the level directly on the `mrc_bridge` logger itself, which
takes effect regardless.

2 new regression tests (one specifically simulating the pre-existing-
root-handler scenario that caused this), verified to fail without the
fix.

## v1.0b2.128 — MRC: add full raw packet tracing for diagnosing the identify-persistence bug (July 2026)

Three rounds of wire-format fixes verified against reference client
source and the actual official protocol spec haven't resolved the
live "still have to identify every time" report — rather than guess a
fourth time, this adds a way to capture a complete real transcript for
direct comparison.

- Every outgoing packet (`send_packet`) and every incoming line from
  the hub are now logged verbatim, tagged `MRC RAW OUT`/`MRC RAW IN`,
  at `DEBUG` level only — gated behind a new `MRC_BRIDGE_LOG_LEVEL`
  environment variable (defaults to the existing `INFO`, unchanged).
  Deliberately not on by default, since full tracing would otherwise
  mean every private chat message lands in plaintext in the server's
  own logs permanently.

1 new regression test, verified to fail without the fix.

## v1.0b2.127 — MRC: correct wire format against the actual official protocol spec (July 2026)

Direct follow-up to `.123`/`.124`, after obtaining the real MRC
protocol developer documentation (not inferred from client source).
Two important corrections:

- FIX: `.124`'s "empty `toRoom` for every generic command" change was
  based on one reference client's own implementation shortcut, not
  the actual spec — the documented template for most commands
  (`MOTD`, `WHOON`, `BANNERS`, etc.) is
  `user~bbs~room~SERVER~msgext~room~COMMAND~`, with `toRoom`
  populated. Reverted to populate it, keeping the empty-`toRoom`
  exception only for `IDENTIFY`/`REGISTER`/`UPDATE`, which the spec
  documents separately.
- FIX: same correction for `LOGOFF` (sent every time you leave) — the
  documented template has **both** `fromRoom` and `toRoom` populated
  with the room name; `.124` had emptied `toRoom` based on the same
  wrong assumption. Reverted to match spec.
- FIX: a genuinely new finding from the real spec — `USERIP` should
  send an empty `fromRoom` (`user~bbs~~SERVER~msgext~~USERIP:
  ipaddress~`), which the code never did correctly in any prior
  version. Fixed.
- Also confirmed from the spec: MRC Trust is keyed to (Handle, BBS
  Name, BBS's own IP address) with a 30-day window — not the
  individual caller's IP. Confirmed the BBS has a stable static IP,
  ruling that out as a contributing factor, which puts the weight
  back on these wire-format corrections actually being the fix.

10 new/updated regression tests, verified against the reference spec,
each confirmed to fail without the fix.

## v1.0b2.126 — Terminal: bulletins now use the scrollable ANView reader (July 2026)

- FEATURE: reading a bulletin longer than a page used the old
  page-break `[MORE]` pager instead of the scrollable ANView reader
  already used for echo/private messages. Now uses ANView (Up/Dn/
  PgUp/PgDn to scroll, Q to back out) instead.
- Bulletins are authored via the web admin's plain-text form, not
  composed at a terminal, so this deliberately does *not* reuse the
  message reader's CP437-mojibake decode pipeline — that would have
  silently corrupted any non-ASCII bulletin text (curly quotes, em
  dashes, accented letters) into unrelated glyphs. Raw ANSI escapes
  in a bulletin still render correctly.

3 new regression tests, verified to fail without the fix.

## v1.0b2.125 — Terminal: file areas now lightbar-scrollable, matching message areas/RSS (July 2026)

- FEATURE: the terminal "File Library - Areas" screen used to dump
  every configured area top-to-bottom with a plain number-entry
  prompt — for any sysop with more file areas than fit one screen,
  this meant relying on the terminal client's own scrollback just to
  see the top entries. Now uses the same arrow-key lightbar selector
  already used for message areas and the RSS reader: Up/Dn/PgUp/PgDn
  to scroll, Enter to open, A for All Files, Q to back out.

3 new regression tests, verified to fail without the fix.

## v1.0b2.124 — MRC: fix real LOGOFF/command wire-format mismatch behind repeated forced re-identify (July 2026)

Direct follow-up to v1.0b2.123, after live testing showed identify was
still required on every leave/rejoin. Root-caused by comparing exact
packet fields against the reference client's C source, field by field.

- FIX: the `LOGOFF` packet sent every time you leave MRC populated the
  `toRoom` field with the room name; the reference client always sends
  it empty for `LOGOFF`. If the hub's trust/session tracking keys off
  that field, a populated one on logoff would plausibly end a handle's
  trusted session — a direct, verifiable candidate for "have to
  identify every time."
- FIX: the same mismatch existed on every other generic command
  (`MOTD`, `WHOON`, `CHATTERS`, `USERIP`, etc.) — previously only
  `IDENTIFY`/`REGISTER`/`UPDATE` sent an empty `toRoom`; the reference
  sends it empty for all of them.

7 new regression tests verified against the reference wire format,
each confirmed to fail without the fix.

## v1.0b2.123 — MRC: fix "have to /identify every time" root cause + topic/userlist bugs from a full client review (July 2026)

Prompted by a report that MRC Trust never seemed to persist, plus two
bugs found live while testing. Root-caused by comparing ANetBBS's
terminal MRC client against the wire protocol line-by-line.

- FIX: the protocol expects a `USERIP:` packet on every room join, and
  the hub appears to use it to recognize a returning already-identified
  connection — but ANetBBS's bridge never sent one, for any user, on
  either the terminal or web client. This forced a fresh `/identify` on
  every single connect, no matter how recently the same handle had
  already identified. Now sent correctly: the web client's real address
  is captured server-side from the incoming connection (honoring
  `X-Forwarded-For` since the bridge sits behind nginx), and the
  terminal client — whose connection to the bridge always looks like
  `localhost` — now reports its own real caller address explicitly.
- FIX: the room topic line (`── Topic: ...`) was re-printed above the
  input line every time the hub re-sent a `ROOMTOPIC:` packet, even
  when the topic text hadn't actually changed — spamming the same line
  repeatedly. Now only announced when the topic genuinely changes.
- FIX: the sidebar user list only ever added names, never removed
  them — so if a single user's leave notice was ever missed, their
  name stuck around forever, since even a fresh full list refresh from
  the hub couldn't clean it up. Now treated as the authoritative
  snapshot it is: a fresh list fully replaces the known set.
- Reviewed CTCP handling, DM/reply, room switching, and away/back
  against the protocol's actual wire behavior — all already correct,
  no further changes needed there.

12 new regression tests, each verified to actually catch its bug
(reverted and confirmed failing, then restored).

## v1.0b2.122 — ANetIRC: fix broken function/nav keys on SyncTerm + 4 more real bugs from a deep review (July 2026)

Prompted by a bug report ("F2 doesn't remove the user listing") from a
SyncTerm/SSH user. Root cause was much bigger than F2 alone.

- FIX: the key parser was built entirely around xterm's keyboard
  conventions, but SyncTerm — likely the most common BBS terminal —
  uses a completely different, non-standard set of codes, confirmed
  directly against SyncTerm/CTerm's own official documentation. As a
  result **F1 through F12, PgUp, PgDn, End, Insert, and Back Tab all
  silently did nothing** for SyncTerm users, not just F2. Rewrote the
  parser to correctly handle SyncTerm's real sequences while keeping
  existing xterm/vt220 support working for other clients.
- FIX: CTCP requests other than ACTION (VERSION, PING, etc.) used to
  leak into the chat window as a blank ghost line from that user, and
  the requester never got a reply (real IRC etiquette expects one;
  some bots/clients flag nicks that never answer). VERSION and PING
  now get a real reply and don't show in chat; anything else is
  silently ignored instead of leaking a blank line.
- FIX: nick tab-complete could never actually cycle to a different
  match on repeated Tab presses — the second press looked for a match
  in the already-completed text, found none, and silently did
  nothing. Now correctly cycles through every match.
- FIX: a single malformed line from the IRC server could silently
  kill the background connection with zero visible error — chat would
  just stop receiving anything, indistinguishable from a hung
  connection. Now logs a visible error and keeps going.
- FIX: a literal `|` character typed into a bookmark field (most
  plausibly the label, e.g. "Home | Personal") silently corrupted and
  misaligned every field after it the next time bookmarks loaded, with
  no error. Fixed at the point bookmarks are saved.

31 new regression tests, each verified to actually catch its bug
(reverted and confirmed failing, then restored) rather than just
passing by coincidence.

## v1.0b2.121 — BinkP inbound transcripts; FileFix docs; Ask Anet troubleshooting content (July 2026)

- FIX: inbound BinkP sessions (a peer connecting TO this BBS) never
  saved a frame-by-frame transcript — only outbound polls did, since
  v1.0b2.47. This mattered most for exactly the direction this
  session's whole BinkP audit was chasing: a peer hub pushing mail in
  and then stalling, with no in-app log to look at afterward. Inbound
  sessions now save a transcript the same way outbound polls do,
  viewable in the same **Admin → Echomail Networks → Poll Logs** UI
  (no UI changes needed — it already just checks whether a log row
  has one).
- DOCS: FileFix (the file-echo counterpart to AreaFix) was never
  documented anywhere, despite being a fully-working bot — added a
  full section to `docs/07-file-areas.md`.
- FEATURE: Ask Anet's help search only ever indexed the wiki, not
  `docs/*.md` — and the wiki had zero troubleshooting content, so
  searching for help with a real problem always came back empty.
  Added a comprehensive Troubleshooting wiki page consolidating real
  symptom→fix entries from across the docs (web/nginx, FTPS, MRC,
  BinkP/echomail, doors, Docker, webhooks, scheduled events, MSP,
  Raspberry Pi, RSS), linked from Home and the Sysop Guide, and
  verified end-to-end that Ask Anet's search now actually finds it.

## v1.0b2.120 — BinkP/echomail: close 3 remaining alignment gaps found in follow-up spec research (July 2026)

Direct follow-up to v1.0b2.119's BinkP audit — closes three specific
gaps identified after that release, each now verified against a real,
fetched primary source rather than inference:

- FIX: the outbound BinkP client never cross-checked a peer's claimed
  address (M_ADR) against the hub address we actually dialed — a
  wrong host answering on the expected IP/port (stale DNS,
  misconfiguration) would sail through unnoticed as long as it also
  had our password. Now logs a clear warning on mismatch (the
  password remains the real auth gate, so this doesn't abort the
  session — a legitimate multi-AKA hub could otherwise trigger a
  false positive and break a working link).
- FIX: nodelist header parsing verified directly against the actual
  FTS-5000 spec text (fetched and confirmed, not guessed) — the real
  example given in the spec now round-trips correctly, in addition to
  this software's own generated format from v1.0b2.119.
- VERIFIED (no bug found): re-checked the QWK MESSAGES.DAT
  message-header field layout against the actual published QWK format
  spec, byte by byte. Everything already matched — added dedicated
  regression tests built independently from the spec's own offset
  table so this can't silently drift later.

## v1.0b2.119 — BinkP: fix a real mail-loop/mail-loss bug, full-subsystem audit (July 2026)

Prompted by a real peer sysop's report of repeated duplicate bundle
deliveries and mid-session disconnects. Root-caused against the FTS-1026/
FTS-1027 specs, binkd's own C source, and Synchronet's binkp.js reference
implementation, then verified end-to-end with new regression tests
(scripted real BinkP frames, not mocks).

- FIX: inbound listener could freeze **every other concurrent BinkP
  connection** while importing a large batch of mail — the listener
  runs one shared event loop for all sessions, and importing (DB
  writes, ZIP extraction, parsing) ran as blocking code directly on
  it. A big catch-up on one connection could stall a brand-new,
  unrelated connection for minutes, causing the peer to give up before
  ever getting an acknowledgement — the direct cause of the reported
  loop. Import now runs on a background thread so it can't block other
  sessions.
- FIX: the inbound listener could also delay closing its own session
  until after importing what it just received; a slow import meant the
  peer's connection timed out before the session formally ended, so
  the peer's own bookkeeping never marked those files delivered and
  resent them next time. The session now finishes (and the socket
  closes) immediately after files are received/acknowledged, before
  import runs.
- FIX: the outbound client used to mark a batch of messages "sent"
  regardless of whether the hub actually acknowledged it — a busy or
  unstable hub replying "skip" or "error" (both normal, spec-legal
  responses) silently and permanently discarded real outbound mail
  with no retry. It now only marks messages sent on actual
  acknowledgement; anything else is safely retried next poll.
- FIX: the outbound client didn't handle the hub closing the
  connection while waiting for that acknowledgement — it would crash
  instead of retrying gracefully.
- FIX: a path-traversal gap in both the inbound listener and outbound
  client — an inbound filename was never reduced to a safe basename
  before being used to write a local file.
- FIX: two silent-failure spots (a malformed FTN address, corrupted
  routing history on a message) now log a warning instead of failing
  invisibly, so a sysop debugging misrouted mail has a trail to follow.

Follow-up full audit of the rest of the echomail subsystem (AreaFix/
FileFix, the hub tosser, QWK-hub FTP, InterBBS sync, nodelist import)
surfaced several more real issues, now also fixed:

- FIX (security): inbound AreaFix/FileFix netmail was never checked
  against the AreaFix password — any netmail addressed to the areafix/
  filefix robot could subscribe/unsubscribe echo areas with no
  authentication at all. Now verified against the same password the
  outbound side already sends (FTS-0024), on both the leaf and hub
  paths.
- FIX: the hub's SEEN-BY loop-prevention check never actually worked
  (comparing a zone-qualified address against untokenized raw SEEN-BY
  lines, which could never match) — a hub could re-toss a message right
  back to the exact node that's also subscribed to receive it, a
  needless bounce and a contributor to loops.
- FIX: a single bad message in a multi-message QWK REP upload could
  silently discard earlier, already-successfully-imported messages from
  the same batch.
- FIX: InterBBS Wall/Last-Callers/Game-Scores sharing was re-sending a
  redundant AreaFix subscribe request on every single post/caller/score
  event instead of only when actually needed.
- FIX: nodelist header parsing (day-of-year, release date) never worked
  against this software's own generated nodelists.

## v1.0b2.118 — MRC bridge crash fix + protocol correctness; new terminal/web MRC features (July 2026)

- FIX: MRC bridge crash on a WebSocket-disconnect race (16 unguarded response sends) — confirmed via a live production crash-loop; very likely the actual cause of MRC trust unexpectedly lapsing.
- FIX: MRC bridge now handles the hub's version-enforcement messages instead of silently reconnect-looping or leaking them into chat, plus a few other protocol-correctness fixes (graceful shutdown notice, accurate capability list).
- NEW: MRC terminal client — `/set defaultroom`, `/set twitfilter`, `/set clockformat`, `/welcome`, `/changes`, plus `/q` `/b` `/cls` aliases.
- NEW: MRC web client — scrolling ticker/banner, latency display, clock, and timezone-offset setting (all previously missing entirely from the web client).

## v1.0b2.117 — Full docs/wiki accuracy pass ahead of Aug 1; exec-door drop-file fix; new terminal gallery viewer (July 2026)

- DOCS: Full docs/wiki accuracy pass ahead of the Aug 1 release — every claim checked against the actual code; fixed dozens of stale, wrong, or fabricated entries across `docs/` and the wiki (including invented MRC hub details, dead admin links, and wrong dosemu2/DOSBox drive-letter guidance).
- FIX: Exec-door drop-file generation (the `exec` menu action) was silently broken since it shipped — a wrong function name meant every configured drop file failed quietly. Now actually works.
- NEW: Terminal image gallery viewer (`anet-gallery.sh`) — documented for a while but never built. Now generated automatically by install.sh/update.sh.
- FIX: RSS feed access-level restrictions weren't enforced on two of four web routes — a restricted feed was directly reachable even though hidden from the river/index.

## v1.0b2.116 — install.sh: FTPS cert-renewal fix; expanded troubleshooting docs (July 2026)

- Fixed a gap where FTPS certificate permissions could silently break after the first Let's Encrypt renewal on a fresh install (only `update.sh` had the fix before).
- Added several new troubleshooting entries to `docs/INSTALL.md`/`docs/INSTALL-PI.md`/`docs/06-echomail.md` covering common newer-sysop pain points (SELinux blocking nginx, MRC config path overrides, Pi `/tmp` confusion, FTPS renewal, BinkP poll intervals).

## v1.0b2.115 — install.sh/update.sh: better distro detection, dependency update checks, extra verification (July 2026)

- Improved Linux distro detection and package handling in `install.sh`/`update.sh` (openSUSE, dosemu2 support, clearer firewall guidance where automation isn't available).
- Added automated dependency update/security checks (`pip-audit` + Dependabot) so outdated or vulnerable packages get flagged instead of going unnoticed.

## v1.0b2.114 — Echomail poller: back off after a failed poll instead of retrying every tick (July 2026)

- FIX: `EchomailNetwork.last_poll_at` was only ever stamped after a
  *successful* poll — a failed poll left it untouched, so the poller's
  own "is this network due yet?" check saw it as still due on the very
  next scheduler tick (60 seconds later) and retried immediately,
  regardless of the network's configured poll interval. Reported via
  an external GitHub issue: some upstream hubs started blocking the
  repeated rapid-fire attempts, which only made the original failure
  harder to recover from. Fixed by moving the `last_poll_at` stamp into
  `_do_poll()`'s `finally` block so it's set exactly once per attempt,
  success or failure — a failed poll now waits out the normal interval
  before retrying, same as a successful one. (Longer-term, a real
  configurable back-off curve — short retry, growing toward the full
  interval — would degrade more gracefully than an all-or-nothing wait;
  noted as a possible follow-up, not needed to fix the reported
  problem.) 3 new tests in `tests/test_poller_backoff_on_failure.py`.

## v1.0b2.113 — Multi-hub-identity: join form, nodelist, QWK hub, and BinkP auth all made identity-aware (July 2026)

Completes the multi-hub-identity feature (Phases 2-7; Phases 0-1 —
the `HubIdentity` model, its `hub_identity_id` foreign keys, and the
admin CRUD — shipped in v1.0b2.92-93). Every install still has exactly
one hub identity by default and sees **zero behavior change** — this
phase is about making the FK columns that already existed actually do
something, for a sysop who wants one install to operate as the
designated hub for more than one real echomail/QWK network at once.

- FEATURE: the public "apply to join this network" form now has a
  second URL, `/join/<slug>/`, for any non-default hub identity
  (`/join/` alone still means the default identity, unchanged) — each
  identity gets its own config, infopack storage, and application
  queue. Approving an application now stamps the created BinkP/QWK
  node with the identity the application was actually submitted to,
  and BinkP node auto-numbering is scoped per identity so two
  identities reusing the same zone:net (unusual, but not disallowed)
  don't collide on each other's node numbers.
- FEATURE: nodelist generation (`generate_nodelist()` and its two
  callers — the public HTTP route and the scheduled file-echo
  publisher) is now identity-aware: a non-default identity's nodelist
  lists only its own downstream nodes and uses its own zone/net/hub-node/
  name instead of always the hardcoded defaults. The scheduled
  file-echo auto-publish stays default-identity-only (no per-identity
  file-area assignment exists yet); every identity's nodelist is still
  directly downloadable via its own URL.
- FEATURE: QWK hub packet generation collapses three previously
  independent, undocumented "what's our hub system ID" resolvers (the
  web download/upload routes, the FTP-side packet writer, and the FTP
  login handler) into one shared, identity-aware resolver — a node on
  a non-default identity gets its own `<HUBID>.QWK`/`<HUBID>.MSG`
  filenames instead of always the install's single global
  `QWK_HUB_ID`. QWK/BinkP node admin forms grow a Hub Identity picker,
  node/request lists grow an Identity column (only shown once a second
  identity actually exists), and cross-identity QWK area subscriptions
  are refused.
- FIX (real bug, not just a missing feature): the inbound BinkP
  listener's auth/matching queries were always safe (address-uniqueness-
  backed, unscoped by identity on purpose — adding a stricter filter
  here was the one thing this whole phase deliberately avoided, to
  never risk locking out a real peer mid-migration), but outbound mail
  to a downstream node was always stamped with the single process-wide
  `BINKP_OUR_ADDRESS`, regardless of which hub identity that node
  actually belonged to. A peer on a second hub identity got outbound
  mail stamped with the *default* identity's AKA. Now resolves the
  matched node's own hub identity (via its BinkP network's own
  configured address, or a zone:net/hub-node reconstruction) and fails
  open — logs a warning and falls back to the previous default-address
  behavior — if that identity has nothing configured, rather than ever
  rejecting an otherwise-valid, already-password-verified connection.
- DOCS: terminal QWK-application wizard screens no longer hardcode a
  literal hub hostname/QWK-hub-ID in their help text — now reflect
  whatever `REGISTRY_URL`/`QWK_HUB_ID` this particular install actually
  has configured. Hub Management dashboard grows a per-identity
  node-count breakdown once a second identity exists. Terminal QWK
  Node Requests screen labels itself "(default hub identity only)"
  once a second identity exists, since the terminal stays
  default-identity-only by design (same precedent as BinkP/QWK peer
  node CRUD already being web-only) — extra identities are managed
  through the web admin. New wiki section under Sysop Control Panel
  explaining the feature for other sysops.
- 786 passed, 2 skipped total — full suite, including 47 new tests
  across five new test files covering each phase (join/nodelist/QWK/
  BinkP/admin-UI), plus a from-scratch characterization harness for
  the BinkP listener's auth path, which had zero real test coverage
  before this (the two pre-existing BinkP server test files short-
  circuit before reaching it, and their query fakes silently ignored
  filter kwargs entirely).

## v1.0b2.112 — MRC bridge: root cause was a config-path mismatch, not code — plus pipe colors restored (July 2026)

- RESOLVED: the BBS-info-fields saga (v1.0b2.107-111) is now fully confirmed working live, and the real root cause turns out to have never been a code bug at all. One sysop's install had its bridge service pointed at a non-default `MRC_BRIDGE_CONFIG` path (in a `config/` subdirectory) via an environment variable — a leftover from running his own MRC bridge for two years before ANetBBS was built around it. He'd been editing the default `mrc/bridge/config.json` path the entire time, a file his own service never actually reads. Every fix shipped in v1.0b2.107-111 (the `send_info_fields()`/`BBSMETA` additions, the field-order correction, the stale-session dedup) was correct and necessary — none of them had anything to verify against until this was found via the raw packet logging added in v1.0b2.111 finally showing default/empty values despite a correctly-edited file. No install/deploy tooling changes needed — this is a one-off historical artifact of that specific install, not something fresh installs or the Pi3 hit.
- FIX: reverted the pipe-color-code stripping added for `send_info_fields()`/`send_bbsmeta()` in v1.0b2.109/.110. That stripping was reasoned only from the reference client's own plain-text `MRCBBS.DAT` example having no color codes in it — the sysop pushed back with two years of hands-on experience with this exact protocol confirming pipe colors in the BBS name/telnet/ssh/website/sysop/description fields are expected and supported, same as the BBS name field itself (which was never stripped). These values now go out exactly as configured, colors included. The *unrelated* pipe-code fix in `_extract_identified_handle` (comparing the hub's identify-success reply against a plain-text stored handle, not anything ever displayed) is untouched and still correct.
- 739 passed total (2 existing tests updated to assert colors are preserved, not stripped).

## v1.0b2.111 — MRC bridge: fixed duplicate self-heal on stale sessions + raw packet logging for BBS-info (July 2026)

- FIX: once v1.0b2.109's pipe-code fix made `/identify` actually get recognized, a single `/identify` started replaying the join once per matching *database* session record for that handle, not once per live connection — and a hard `systemctl restart` (used repeatedly during this same live-troubleshooting session) doesn't run the graceful WebSocket-close cleanup path, so stale records for the same handle piled up across restarts. Reported live as MOTD/CHATTERS showing up 4 times after a single `/identify`. Fixed: the self-heal now only acts on sessions with a genuinely live WebSocket connection right now, skipping any DB record with no matching live connection.
- DIAGNOSTIC: v1.0b2.110's field-order fix + `BBSMETA` addition, while verified byte-for-byte against the reference client, still didn't make the sysop's BBS-directory entry populate live — the real root cause remains unconfirmed after two careful, reference-checked attempts. Rather than guess a third time, added raw outgoing-packet logging (`logger.info`) for both `send_bbsmeta()` and `send_info_fields()` so the exact bytes leaving the bridge can be inspected directly.
- 1 new test (739 passed total).

## v1.0b2.110 — MRC bridge: fixed the BBS-info packet field order + added missing BBSMETA (July 2026)

- FIX: v1.0b2.107/.109's pipe-code stripping alone still wasn't enough — the sysop's directory entry stayed blank even with a fully clean, correctly-configured `config.json`. Re-checking the reference client's `mrc_send_info_fields()` byte-for-byte against its own `create_packet`-equivalent call turned up a genuine field-order bug: the reference client sends these specific "broadcast to everyone" packets with the literal `"ALL"` and the epoch timestamp in the **opposite** wire positions from every other message in this protocol (room-scoped sends put the timestamp before the room; this one puts `"ALL"` before the timestamp) — a quirk (possibly an accidental one even in the reference client itself, inconsistent with its own `BBSMETA` call a few lines above) that real hubs apparently expect literally. ANetBBS's `send_info_fields()` used the "logically correct" order instead, which put a garbage timestamp string where the hub expects `"ALL"` — with nothing valid to route the broadcast by, the hub had no reason to store it, blank directory entry despite zero errors anywhere in the pipeline. Field order now matches the reference client's actual sent bytes exactly.
- FEATURE: also added `send_bbsmeta()` (`BBSMETA: SecLevel(100) Sysop(...)`), sent once right before the info fields — matching the reference client's own connect sequence exactly. ANetBBS never sent this at all; added alongside the field-order fix since both were live-suspected contributors and couldn't be isolated without another full live round-trip.
- 4 new tests (738 passed total), plus an existing field-order test rewritten to check the raw wire bytes positionally instead of through the higher-level parser, since this packet type's field order doesn't match the parser's normal semantic labels.

## v1.0b2.109 — MRC bridge: real root cause found — pipe color codes broke identify detection (July 2026)

- FIX (significant): the v1.0b2.108 diagnostic logging paid off immediately — live capture on the production server showed the hub's actual identify-success reply is `"...Successfully identified, welcome back |10StingRay|07"`. `_extract_identified_handle` (pre-dating this session, never previously verified against a real hub reply) never stripped the `|10`/`|07` pipe-color codes wrapped around the handle, so it returned the literal string `"|10StingRay|07"` instead of `"StingRay"` — which then never matched any real session's plain-text handle. This silently broke *both* the pre-existing strict-mode auto-join-after-identify and the newer v1.0b2.106 default-mode self-heal, on every install, the entire time — it just never surfaced before because every prior test (including the ones written for the v1.0b2.106 self-heal fix) used a fabricated clean message with no pipe codes at all. Added `_strip_pipe_codes()` and applied it in `_extract_identified_handle` before searching.
- FIX: applied the same stripping defensively to `send_info_fields()` (v1.0b2.107) — a sysop's `bbs_telnet`/`bbs_ssh`/`bbs_website`/`bbs_sysop` config values copy-pasted from a colorized source (e.g. `"|15bbs.example.com:2233"`, a pipe-colored sysop name) now get sent as clean plain text, matching what the reference client actually sends (no color codes at all, sourced straight from `MRCBBS.DAT`) and avoiding the same class of hub-side parsing risk just confirmed for the identify reply. (Note: this specific behavior was reverted in v1.0b2.112 — see above — once it turned out pipe colors in these fields are actually expected/supported, contrary to the assumption made here.)
- 7 new tests (734 passed total), including two that reproduce the exact real captured wire text end-to-end and confirm the self-heal now actually fires against it.

## v1.0b2.108 — MRC bridge: diagnostic logging for identify/join-rejection wire text (July 2026)

- DIAGNOSTIC: live testing on the production server (`bbs.a-net.fyi`) after v1.0b2.106/.107 found the identify self-heal still not taking effect there, despite the deployed code, config, and service all being confirmed correct with zero errors in the logs — pointing at the detection logic itself rather than the deployment. The `"successfully identified"` / `"welcome back "` substring match this relies on (`_on_upstream_packet`, `_extract_identified_handle`) predates this session with no confirmed verification against a real hub reply. Added a temporary log line (`logger.info`) that captures the raw text of any incoming SERVER message shaped like an identify or join-rejection notice, so the actual wording can be confirmed against a live capture instead of assumed. No behavior change; safe to remove once the real wording is confirmed and, if needed, the detection updated to match.

## v1.0b2.107 — MRC bridge: broadcast our own BBS info (description/telnet/ssh/website/sysop) (July 2026)

- FIX: reported by the sysop — "the BBS info that is not shown with ANetBBS but is with the mystic mrc client... this is where you look at the bbs info and see their web, telnet, ssh, description." Verified against the real reference C client (`anetmrc_v1.3.9/src/helper_protocol.c`'s `mrc_send_info_fields`): every MRC client is expected to broadcast its own `INFODSC`/`INFOTEL`/`INFOSSH`/`INFOWEB`/`INFOSYS` fields once per connect (sourced there from `MRCBBS.DAT`), which the hub aggregates into a per-BBS directory other callers browse via `/bbses` (to find a BBS's list number) then `/info <n>` (to view that entry). ANetBBS's bridge never sent any of these fields at all, so ANetBBS's own entry was always blank to anyone looking it up from another client — including ones (like the Mystic Python client) that actually surface this. Added `send_info_fields()`, called once right after the existing capabilities announcement on every connect; five new optional config keys (`bbs_description`, `bbs_telnet`, `bbs_ssh`, `bbs_website`, `bbs_sysop`) in the bridge's `config.json`, each field only sent if actually configured (matching the reference client's own per-field guards — an unset field is omitted, not sent blank). The *reverse* direction — ANetBBS's own callers looking up *other* BBSes via `/bbses` + `/info <n>` — already worked with no changes needed: both are, and always were, generic passthrough commands on both clients (confirmed by inspecting the existing terminal `/info`/`/bbses` handling and web's catch-all slash-command forwarding).
- 5 new tests, including one that parses the actual generated packet and checks its wire fields against the reference client's own field order.

## v1.0b2.106 — MRC bridge: registered handles now self-heal after /identify (July 2026)

- FIX (significant): found live on the Pi immediately after deploying v1.0b2.105 (a registered handle, "StingRay") — the v1.0b2.103 default-join fix optimistically marks a session `in_room=True` the moment it connects, but for a handle that's actually *registered and not yet identified*, the real hub silently rejects that join ("Cannot join ROOM, please IDENTIFY to use this handle"). The bridge kept believing it was joined and went on forwarding chat sends anyway, which the hub then bounced back with "No route to a room from your user, /join a room first." — the caller looked joined locally but couldn't actually chat, with no way out except realizing they had to `/identify` *and separately* `/join` again by hand. Fixed: a successful `/identify` now always re-sends the room join (previously this only happened in the opt-in strict `identify_required_mode=True` path), so registered handles self-heal automatically the moment they identify — no follow-up `/join` needed. Unregistered/casual handles are unaffected (their optimistic join already succeeds at the hub and was never broken).
- 2 new tests, including one that captures the exact reported sequence (join → hub-side rejection scenario → identify → confirms the join packets actually get re-sent, not just a local flag flip).

## v1.0b2.105 — MRC Phase G: full regression + cross-client parity audit (July 2026)

Closes out the MRC feature-parity rework (Phases A-G, v1.0b2.94-105). Full automated regression suite re-run clean (720 passed, 2 skipped, 0 failures) plus a systematic code-level audit comparing every feature added in Phases A-F against both clients, specifically looking for the kind of one-sided fix that's already happened twice this rework (a bug found and fixed on one client without checking whether the same bug existed on the other). Found and fixed two real gaps, both in `anetbbs/templates/mrc/index.html`, both additive/non-breaking:

- FIX: the web client never got the STATS: noise fix from v1.0b2.102 (terminal-only at the time) — raw `STATS:175 15 39 2 170 34.0`-shaped lines were still showing up inline on web with nothing to silence them. Added the same suppression terminal already has (`SUPPRESS_STATS_LINES`, defaulting on, matching the existing `SUPPRESS_USERLIST_LINES`/`SUPPRESS_ROOMTOPIC_LINES` pattern).
- FIX: the structured `userlist` WS event added in Phase A (`mrc/bridge/main.py`'s `_send_userlist_control`) was specifically built so both clients could stop depending on regex-parsing free-text chat lines for the nick-list sidebar — the terminal client migrated to it in Phase B, but web was never updated to listen for it at all, silently left on the older, more fragile `tryParseUserListFromServerMessage` regex path the whole time. Web now also consumes the structured event directly (`case 'userlist':`) as a more robust source layered on top of the existing regex fallback, which stays in place unchanged.
- No JS test framework exists in this repo (consistent with every prior web-side change in this rework) — both script blocks syntax-checked clean with `node --check`.

## v1.0b2.104 — MRC: restored the /identify "MRC Trust" notice (non-blocking) on both clients (July 2026)

- FIX: follow-up to v1.0b2.103. Correction from the sysop: the reference client doesn't hide `/identify` entirely — it shows a permanent, non-blocking "Use /identify password for MRC Trust" notice on every connect (confirmed in `helper_protocol.c`), and a registered handle's trust status genuinely does lapse after a stretch of weeks on the real network, so periodically re-running `/identify` still matters even though chat is never gated on it. v1.0b2.103's fix correctly removed the *blocking*, but its plain "Joined #room as handle." message dropped the FYI along with it. Restored: the bridge's immediate-join message now reads "Joined #room as handle. Use /identify \<pass\> for MRC Trust." (surfaced automatically on web via its existing system-message display), and the terminal client's own local connect greeting — which never read the bridge's message field at all — gained a matching tip line, so both clients now show the same non-blocking reminder.
- 1 test updated to match the restored notice (720 passed total).

## v1.0b2.103 — MRC bridge: fixed a hard identify-wall blocking all chat by default (July 2026)

- FIX (significant): reported live as "I still have to identify every single time" / "blocked from chatting entirely until identified" — every MRC caller, on every install, was silently blocked from sending or (in some paths) seeing chat at all until they ran `/identify <pass>` against a *registered* account on the upstream hub, every single connect. Root-caused against the real reference C client (`anetmrc_v1.3.9/src/helper_protocol.c`): it joins the room unconditionally right after the handshake and never waits on identify — `/identify` is purely optional "MRC Trust" for a registered handle, never a requirement to participate, and casual/unregistered handles chat freely on the real network. The bridge's own `identify_required_mode` gate defaulted to `True` with no documented way to discover or disable it (not even in `config.example.json`), and even flipping it off alone didn't help — a second, equally undocumented flag (`post_identify_auto_join`, also defaulting off) additionally had to be set before a caller was ever actually placed in the room. Default changed to match the reference client's real behavior: `identify_required_mode` now defaults to `False`, and joining a room completes immediately, no `/identify` needed, for every existing install without any config change. An admin who deliberately wants the old strict, identify-required behavior can still opt in via `config.json`; that path is unchanged and fully covered by tests.
- 9 new tests, including one that builds a real `BridgeApp` from a config file with no `identify_required_mode` key (matching every existing install's actual config.json) and asserts the new default takes effect.

## v1.0b2.102 — MRC Phase F follow-up #3: BEL character threw off border alignment (July 2026)

- FIX: found live on the Pi — one specific row's `│` border still landed one column early even after the sidebar/status-bar/ticker border fixes (v1.0b2.99-101). Root cause: mention/DM alerts prepend a bare BEL (`\x07`, rings the terminal bell) ahead of the highlighted message text — zero-width on a real terminal, but `_visible_len` counted it as one visible column since `_ANSI_SEQ_RE` only matched ESC-prefixed sequences, not a bare BEL. Every message that happened to mention the caller's own handle (or arrive as a DM) was one column short on padding, throwing its row's border out of alignment by exactly one column. `_ANSI_SEQ_RE` now also matches BEL.
- 3 new tests (711 passed total), including an end-to-end reproduction (a mention message's row lands at the same border column as every other row).

## v1.0b2.101 — MRC Phase F follow-up #2: ticker border fix + STATS: inline noise silenced (July 2026)

- FIX: found live on the Pi as "border still has one spot" even after v1.0b2.100's status-bar fix — the scrolling ticker row (between the status bar and chat area) had no border/sidebar treatment at all, scrolling its text against the full terminal width with nothing lining up against the `│` every other split-screen row draws. Fixed the same way as the status bar: measured/padded against `chat_width` and followed by a matching border + blank nick-column-width gap when the sidebar is enabled.
- FIX: found live on the Pi — raw `STATS:175 15 39 2 170 34.0`-shaped lines were popping up inline mid-chat. This was deliberate, documented, unchanged-on-purpose behavior from when the ticker was first built (STATS: predated the ticker and had nowhere else to go), but now that the ticker pool already captures it, the inline copy is just noise. Silenced the same way `BANNER:` already is.
- 3 new tests (708 passed total).

## v1.0b2.100 — MRC Phase F follow-up: named /set tz zones + status-bar border fix (July 2026)

- FEATURE: `/set tz` now accepts common named zone abbreviations (`EST`, `EDT`, `CST`, `CDT`, `MST`, `MDT`, `PST`, `PDT`, `AKST`, `AKDT`, `HST`, `AST`, `ADT`, `UTC`, `GMT`, `BST`, `CET`, `CEST`, `EET`, `EEST`, `IST`, `JST`, `AWST`, `ACST`, `ACDT`, `AEST`, `AEDT`, `NZST`, `NZDT`) in addition to raw `±H[:MM]` offsets — reported live on the Pi as the raw-offset-only form being confusing. Fixed-offset aliases, not DST-aware, matching the feature's existing plain-UTC-offset design (a caller currently observing daylight time picks the `*DT` name, not `*ST`).
- FIX: found live on the Pi as "the border still messed up in one spot" after the v1.0b2.99 sidebar fix — the status bar (row 1) was drawn full terminal-width with no `│` border of its own whenever the nick-list sidebar was enabled, so its right-aligned badges (mention count, latency, clock) landed past the column where every chat row's border sits, with nothing to match it. Now measured/padded against `chat_width` like every other split-screen row and followed by the same border + a blank nick-column-width gap, so the status bar's right edge lines up with the sidebar below it.
- 5 new tests (705 passed total).

## v1.0b2.99 — MRC feature-parity rework, Phase F: quality-of-life additions (July 2026)

- FEATURE: sixth phase of the MRC feature-parity rework (see v1.0b2.94-98). Terminal MRC gains `/r <text>` (reply to your last received DM), input-history recall on Ctrl+Up/Down (plain Up/Down stay bound to chat-scroll, the terminal's existing convention — Ctrl+arrow required actually preserving the CSI modifier byte in `_read_escape_seq`, which previously truncated every modified cursor sequence the same way legacy PgUp/PgDn does), `/dlchatlog` (download the session's scrollback as a text file via whatever ZMODEM-family protocol is available, same tempfile-then-send_file pattern already used by the ebook reader's download command), and a light `/set palette <name>` chrome color picker (default/green/amber/cyan/mono) covering the status-bar room tag, nick-list sidebar, and ticker line — cosmetic-only scope mirroring the web client's 5 CSS themes, not a full re-theme of every message color. Web MRC gets a matching client-side "Download chat log" button in the Chat Prefs panel. Also reviewed whether either client needed a dedicated welcome screen (a locked-in Phase F item) and found both already surface the bridge's real `welcome` event on connect — terminal via its generic unknown-event fallback, web via an existing explicit handler — so nothing further was added there.
- FIX: found live on the Pi — the MRC clock widget and message timestamps used the *server's* system clock, not the caller's own timezone, silently correct only for a caller who happens to share the server's TZ (server on UTC, sysop several hours off in this case). Added `/set tz <offset>` (e.g. `-5`, `+5:30`, `utc`), persisted per-handle via the same `set_prefs` mechanism as the other Phase E settings; terminal timestamps now compute from UTC + this offset instead of the server's local clock. Web was already correct (browser-local time), no change needed there.
- FIX: found live on the Pi — the nick-list sidebar's `│` border column could drift out of alignment when a buffered chat line had been word-wrapped against a wider chat width before the sidebar narrowed it (e.g. sidebar enabled mid-session, or a stale line surviving a resize). The redraw only ever padded short lines to the border column, never truncated long ones, so an over-width line pushed the border past its column and could auto-wrap the physical terminal row, corrupting the next row's redraw too. Now defensively truncated to the current chat width before the border is drawn.
- 53 new tests (700 passed total, Python side; web JS syntax-validated).

## v1.0b2.98 — MRC feature-parity rework, Phase E: /set command + remaining settings (July 2026)

- FEATURE: fifth phase of the MRC feature-parity rework (see v1.0b2.94-97). Terminal MRC gets a real `/set <field> <value>` command (plus `/set list` and `/set help`) covering nick prefix/suffix/color, custom enter/leave/quit chat messages, and the scrolling ticker toggle — the terminal's `/set` for prefix/suffix/color goes through `set_style`, same wire message the web client's style panel already used, and now `/set` is the terminal's first way to change those at all (previously arrow-key cycling was the only style control terminal had). Web gets equivalent fields added to the "Chat Prefs" panel (ticker toggle, enter/leave/quit messages) for the same shared, server-persisted settings. Toggling the ticker via `/set ticker` or the web checkbox now actually re-lays-out the terminal screen live instead of only taking effect on next reconnect.
- FIX: found while building the above — arrow-key outgoing-color cycling (`_cycle_color`) sent only `{type: set_style, typing_color: ...}` to the bridge, but `_handle_set_style` (`mrc/bridge/main.py`) hard-defaults `prefix`/`suffix` to empty string when the field is simply absent from the request (unlike every other style field, which correctly falls back to the existing session value). This silently wiped any prefix/suffix decoration a user had set via the web style panel every single time they cycled their outgoing color with the arrow keys — a real, previously-shipped bug, not something introduced by this phase. Fixed by always sending the full last-known style with just the changed field(s) overridden, via a new `_style_payload()` helper now shared by both `/set` and arrow-key cycling.
- 20 new tests (Python side; web JS syntax-validated).

## v1.0b2.97 — MRC feature-parity rework, Phase D: twit list + broadcast shield (July 2026)

- FEATURE: fourth phase of the MRC feature-parity rework (see v1.0b2.94-96). Both terminal and web MRC clients gain a twit/ignore list and a broadcast shield, persisted server-side per handle via a new `set_prefs`/`prefs_updated` round-trip and shared between both clients automatically. Filtering happens client-side by design (the bridge fans one event stream to potentially many local clients, so per-viewer muting can't be centrally enforced) — messages from a twitted sender are dropped and counted, not silently invisible. Terminal gets `/twit add|del|list|clear` and `/shield on|off`; web gets a new "Chat Prefs" panel alongside the existing style settings. `/broadcast` (terminal) and the equivalent web slash command are now refused locally when the shield is on. 19 new tests (Python side — bridge protocol + terminal filtering logic; web JS syntax-validated, UI verified by hand).

## v1.0b2.96 — MRC feature-parity rework, Phase C: terminal scrolling ticker (July 2026)

- FEATURE: third phase of the MRC feature-parity rework (see v1.0b2.94/95). Terminal MRC gets a scrolling ticker row between the status bar and chat area, rotating through static tips and — for the first time — real hub-pushed `BANNER:` text, which was previously discarded entirely with no ticker to feed. `STATS:` text (already shown inline, unchanged) now also feeds the ticker rather than going unused after being displayed once. Short items dwell briefly before rotating; items too long for the terminal width scroll a few characters per tick instead. 12 new tests.

## v1.0b2.95 — MRC feature-parity rework, Phase B: terminal nick-list sidebar + clock (July 2026)

- FEATURE: second phase of the MRC feature-parity rework (see v1.0b2.94). Terminal MRC (`anetbbs/features/mrc_chat.py`) gains a live nick-list sidebar on wide-enough terminals (132+ columns) — the DECSTBM split-screen primitive only constrains vertical scrolling, so the sidebar rides along on the same row writes as the chat text rather than being a separate scroll region (no left/right sidebar rendering precedent existed anywhere in ANetBBS's terminal UI before this). Also adds a clock widget to the status bar, refreshed every 30s so it doesn't go stale during a quiet room. Both purely additive UI — no settings/persistence yet (that's Phase E). 10 new tests.

## v1.0b2.94 — MRC feature-parity rework, Phase A: bridge protocol + prefs (July 2026)

- FEATURE: first phase of a multi-phase MRC (Multi Relay Chat) rework bringing ANetBBS's terminal and web clients toward full feature parity with the wider MRC ecosystem. This phase is bridge-only (`mrc/bridge/main.py`, `mrc/bridge/db.py`) and lays the foundation the rest builds on:
  - New structured `userlist` WebSocket event, sent alongside the existing raw-text `USERLIST:` relay whenever the bridge already refreshes a room's roster (on join, periodically, or on certain server-text triggers) — a real, independently-verified wire format (matches the web client's existing parser and the terminal client's own USERLIST:/CHATTERS: handling), not a guess.
  - Periodic `STATS` request added, feeding future ticker/banner work as opaque display text — deliberately **not** parsed into structured fields. An earlier draft of this phase assumed a structured `bbs_count`/`room_count`/`activity_level` format based on the Mystic BBS multiplexer's own internal file-protocol; checking the actual reference C client that talks to a real MRC hub showed no evidence real hubs send anything but free text for STATS (same as MOTD/BANNERS), so the fabricated field parsing was dropped before shipping.
  - New `set_prefs`/`prefs_updated` request/response pair (mirrors the existing `set_style`/`style_updated` pattern): persists twit/ignore list, broadcast shield toggle, ticker toggle, and custom enter/leave/quit message templates per MRC handle.
  - Per-user enter/leave message templates now actually apply (previously the bridge only ever had one global template for every user); explicit `/quit <message>` is now threaded through end-to-end instead of being silently discarded by the bridge's `leave_room` handler.
  - 19 new tests.

## v1.0b2.93 — Hub identity seeded active by default (July 2026)

- FIX: the default `HubIdentity` row (see v1.0b2.92's multi-hub-identity foundation) was seeded with `is_active=True`, so simply turning on `REGISTRY_MODE_ENABLED` made ANotherNetwork look like a live, fully-configured hub — before the sysop had touched anything. Doesn't match the existing convention: the seeded ANotherNetwork `EchomailNetwork` rows (both BinkP and QWK) have always started `is_active=False`, requiring the sysop to fill in real node address/packet-id/password before flipping them on. Fixed the seed to match (`is_active=False`), and changed the admin "Add Hub Identity" form's default to unchecked for the same reason — a freshly created identity shouldn't look live until deliberately activated.

## v1.0b2.92 — Terminal MRC never actually read its configured bridge port (July 2026)

- FIX: a sysop reported MRC chat working fine in the web UI but never connecting over the terminal (SSH/telnet) client, with only a brief, unreadable error flash on screen. Root cause: `MRCChat.show_menu()` (`anetbbs/features/mrc_chat.py`) read `current_app.config` with no Flask app context active — unlike `_chat_flags()` a few lines above it in `chat.py`, which correctly scopes its own DB read in one. Every config lookup therefore raised `RuntimeError: Working outside of application context`, silently swallowed by a bare `except: pass`, so terminal MRC always fell back to the hardcoded `DEFAULT_BRIDGE_URL` (port 8080) — regardless of the real bridge port (`WEB_PORT+1`, 5001 by default). This wasn't specific to one install; it silently broke terminal MRC on every install, unnoticed until now. Fixed by wrapping the lookup in `with _app().app_context():`, the same pattern already used elsewhere in the file. Also added `logger.warning(...)` to the connect-failure path, which previously only ever wrote to the failing user's own terminal screen and vanished on redraw — the exact reason the error was unreadable and left no trace to investigate afterward. 1 new regression test.

## v1.0b2.91 — BinkP: unhandled send-side disconnect during EOB/GOT handshake (July 2026)

- FIX: a sysop reported a BinkP poll to one particular hub failing and repeating on every scheduled poll, while two other hubs on the same install worked fine. Root cause: `_receive_messages()` (`anetbbs/echomail/binkp.py`) already treats the hub closing the connection during our *receive* calls as a clean, expected end of session (added in the v1.0b2.57-60 two-round-EOB work) — but the two acknowledgement sends right next to it, the second `M_EOB` and the per-file `M_GOT`, had no equivalent guard. If the hub closes its side of the socket in the narrow window before either of those sends goes out, `sendall()` raises an uncaught `OSError`, which unwinds out of `poll()` and gets logged as a genuine poll failure even though the file transfer itself completed successfully. Wrapped both sends in the same log-and-break pattern already used for the receive side.
- NOTE: polling that one hub on a 1-minute interval is a very plausible reason only it triggers this — if its mailer has any connection-rate throttling, frequent polling could be causing it to abort sessions mid-handshake, which is exactly the trigger condition here. Slower polling (30-60 min, standard FTN practice) should reduce how often this is hit regardless of this fix.

## v1.0b2.90 — Configurable Last Callers row count + screen clear (July 2026)

- FEATURE: the terminal Last Callers screen (`anetbbs/features/lastcallers.py`, `show_last_callers()`) always fetched and paginated a hardcoded 200 rows, which made for a long scroll on a busy install — the sysop reported the list had grown too long. Added `LASTCALLERS_DISPLAY_COUNT` (5/10/15/20/25/30/50/100, default 20), configurable from `/admin/lastcallers/` alongside the existing InterBBS/hide-sysop settings, following the same `.env`-persisted `current_app.config` pattern as `LASTCALLERS_HIDE_SYSOP`. Only the full terminal screen is affected — the admin audit list (`/admin/lastcallers/`) and the web one-liners page's fixed "Last 10" preview both keep their own independent limits, since neither was the thing the sysop was looking at.
- FIX: the same terminal screen never cleared before drawing, unlike every other full-screen terminal view in the app (`\x1b[2J\x1b[H`). Now clears first.
- 3 new tests: display-count persists a valid choice, an out-of-range/tampered value snaps to the nearest offered choice rather than becoming an arbitrary unbounded limit, and the default (no value submitted) is 20.

## v1.0b2.89 — Move pre-update backups off /tmp onto persistent disk (July 2026)

- FIX: found live, immediately after deploying v1.0b2.87's disk-space check to a real Pi install — the check reported the backup filesystem (`/tmp`) had only 442MB free, well under the 500MB minimum, so `update.sh` correctly refused to proceed. But `df -h /` on the same machine showed the *real* disk at 117GB total, only 4.9GB used, 108GB free — 5% utilization. The disk itself was nowhere near full. The actual cause: on this Pi, `/tmp` is mounted as a RAM-backed tmpfs, entirely separate from the real disk and sized off available memory rather than storage — a detail this codebase had already run into once before in an unrelated context (a prior memory note: "`/tmp` on the Pi is a small tmpfs (~453M, sized off the Pi3's 1GB RAM) and fills up fast"). The pre-update backup (`.env`, both SQLite databases, systemd unit files, nginx config) was landing on `/tmp/anetbbs-backup-*`, so it was competing for space against that same constrained RAM allocation — v1.0b2.87's disk-space check was doing exactly its job, correctly refusing to risk a backup that couldn't actually fit, but the *real* problem was the backup's location, not the amount of free space on the machine as a whole.
  - Moved the backup location in `update.sh` from `/tmp/anetbbs-backup-$(date +%Y%m%d%H%M%S)` to `$INSTALL_DIR/data/backups/anetbbs-backup-$(date +%Y%m%d%H%M%S)`. `INSTALL_DIR/data/` is real, persistent disk — already excluded from the update's own file-sync step (`--exclude='/data/'`), so backups can't collide with anything the sync overwrites — and survives a reboot, unlike `/tmp` on distros that clear it at boot (a second, independent risk the old location carried that this move also happens to fix). The disk-space pre-flight check added in v1.0b2.87 now only needs to check `$INSTALL_DIR` — the backup and the rest of the update both live on that filesystem now, so the separate `/tmp` check (which was the thing that caught this exact problem, doing its job correctly) is no longer needed as a distinct check.
  - Updated `anetbbs/web/backups_admin.py` (the `/admin/backups/` page that lists, deletes, and restores these snapshots) to match — `_BACKUP_ROOT` was a hardcoded module-level `'/tmp'` constant; replaced with a `_backup_root()` function that resolves `INSTALL_DIR` from the running Flask app's config the same way `_restore_helper()` in the same file already did, so it works correctly regardless of where a given install actually lives (`/opt/anetbbs`, a sysop's home directory, etc.) rather than assuming `/tmp` universally.
  - Carefully re-anchored `deploy/run_restore.sh` — the privileged (sudo) helper that actually performs `.env`/DB restores and backup deletion on the admin UI's behalf — since its security path-allowlist was a hardcoded `case "$BACKUP" in /tmp/anetbbs-backup-*)` pattern, specifically designed to stop a compromised or buggy caller from pointing the privileged helper at an arbitrary filesystem path. Reordered the script so `INSTALL_DIR` gets resolved from the root-written `/etc/anetbbs.install` sentinel *before* path validation runs (previously validation ran first), then anchored the allowlist to `"$INSTALL_DIR"/data/backups/anetbbs-backup-*` instead of the old literal — `INSTALL_DIR` itself comes from a root-owned sentinel file, never from caller input, so this doesn't weaken the check. Verified directly (not assumed) that both layers of the original defense still work exactly as before: the prefix-match layer rejects anything outside the new location, and the separate forbidden-character layer (`*..*`, `*;*`, etc.) still catches a path-traversal attempt that manages to satisfy the prefix pattern by embedding `../` *after* a valid-looking prefix.
  - 4 new tests confirming `_backup_root()` tracks two different `INSTALL_DIR` values exactly (rather than checking against a literal `/tmp`, which would have been confounded by test fixtures themselves living under `/tmp` on most systems), that the admin page's directory scan finds backups under the new location, and that path-traversal rejection still works.

## v1.0b2.88 — Fix Synchronet-JS door games leaking temp files on every launch (July 2026)

- FIX: found while a sysop was clearing space to get past the new disk-space check from v1.0b2.87 — a `/tmp` directory listing on a real, long-running install turned up over a hundred `anetbbs_*_synchronet_run.js` and `anetbbs_*_synchronet_compat.js` files, none of them ever cleaned up. Traced to `anetbbs/games/door_runner.py`'s `_build_command()`: for `door_synchronet` games, when no real Synchronet `jsexec` binary is installed (the common case for most sysops, who don't run a full Synchronet install alongside ANetBBS), it falls back to a Node.js compat shim and writes two files that must persist on disk for the life of the door process — a generated `synchronet_compat.js` (via `anetbbs/games/synchronet_compat.py`'s `write_compat_script()`) and a combined `synchronet_run.js` containing both the compat shim and the actual door script concatenated together (`tempfile.NamedTemporaryFile(..., delete=False)`, since the file needs to still exist on disk after Python's own handle closes, for the forked Node.js child process to read). Both are legitimately necessary at creation time — the bug is that nothing, anywhere, ever deleted them afterward. `_cleanup_session()` (`door_runner.py`), the single function responsible for releasing a door session's resources no matter how the session ended — PTY EOF, the browser/bridge closing the connection, forced termination, or the waitpid watcher reaping a crashed child — had no reference to these paths at all; they were local variables scoped entirely inside `_build_command()`, already out of scope by the time any session actually ends. A second, independent leak source doubled the damage: `launch_door_game()`'s terminal-session code path calls `_build_command()` a *second* time purely as a pre-flight validation, "so we can show a clear error" before the real launch — that call's return value (including the two temp files it creates) was discarded entirely, so every terminal-initiated Synchronet-JS launch attempt orphaned temp files twice, once from the validation dry run and once from the real launch.
  - Fixed by giving `_build_command()` an optional `temp_files_out` list parameter — when the Node.js compat path creates its two files, it appends both paths to this list if the caller provided one (`None` by default, so every pre-existing caller that doesn't care about cleanup keeps working unchanged; all other game types — native binaries, Mystic, DOSBox — never append anything, since they don't create files with this lifetime problem).
  - `DoorSession` (the class already tracking a running door's PTY/process/bridge state) gained a `temp_files` list, populated from `_build_command()`'s collector right after a real launch succeeds. `DoorSession.close()` — already responsible for the exact same kind of cleanup for `door_dosemu`'s pts symlink (`self.pts_path`) — now unlinks everything in `temp_files` the same defensive way (try/except OSError, tolerant of a file already being gone), so it's cleaned up through every single path that can end a session, not just the happy path.
  - Also added cleanup to `launch_door_game()`'s own early-failure branches (PTY open failure, fork failure, `_build_command()` itself raising) — a session that never makes it far enough to construct a `DoorSession` at all would otherwise still orphan any temp files created before the failure.
  - Fixed the validation-dry-run leak at its source: it now passes its own throwaway collector list and deletes everything in it in a `finally` block immediately after the dry run completes (whether it succeeded or raised), since that code path never uses the built command for anything beyond checking whether `_build_command()` raises.
  - 5 new tests in `tests/test_door_synchronet_temp_cleanup.py`, run against the *real* Node.js code path (a real `game` stand-in, a real door script, real Node.js execution via the actual `/usr/bin/node` on the test machine, skipped only if no Node.js binary is present) rather than mocked — confirms `temp_files_out` is populated with exactly the two real files the command actually depends on (including that the file `_build_command()`'s own returned command line points at is one of the tracked paths, so cleanup can never delete a file still needed to run the door), that `DoorSession.close()` actually deletes them from disk, and that close() doesn't raise for game types with no temp files or for a file that's already gone.

## v1.0b2.87 — update.sh: disk-space check + backup rotation + fix silent backup failure (July 2026)

- FIX/FEATURE: a real sysop reported their disk hit zero free space partway through running `update.sh`, corrupting the install beyond recovery. They'd accumulated a dozen old pre-update backup snapshots without realizing it — nothing had ever pruned them — and `update.sh` had no check for available disk space before it started writing. Investigated by reading `update.sh`'s actual backup step directly rather than assuming the existing safety net worked as expected, and found three real, concrete gaps:
  - **No disk-space check anywhere.** `update.sh` never called `df` or checked free space at any point before starting Step 2 (the pre-update backup) or any later step. Added a pre-flight check requiring at least 500MB free on both `/tmp` (where backups land) and `$INSTALL_DIR` (where the update itself writes) before proceeding — on most single-disk VPS installs these are the same filesystem, but checking both costs nothing and covers a split-filesystem setup too. If either is below the threshold, the script now refuses to start at all, with a message pointing the sysop at Admin → Backups to clear old snapshots first, rather than proceeding into a disk-full corruption risk.
  - **Backups accumulated forever, by design.** `anetbbs/web/backups_admin.py`'s own docstring stated this explicitly: "Those dirs accumulate forever because we don't trust ourselves to GC them automatically." That caution turned out to be the wrong tradeoff in practice — unbounded accumulation of backup snapshots (each containing a full SQLite DB copy) is exactly how a sysop who wasn't actively managing them via the admin UI ended up silently consuming disk space until an update ran the filesystem to zero. `update.sh` now prunes to the 3 most recent pre-update backups after each successful backup, sorted by the existing `YYYYMMDDHHMMSS` timestamp in the directory name. Updated the now-stale docstring in `backups_admin.py` to match.
  - **A real silent-failure bug in the backup step itself.** The `.env` backup was two separate, unchained statements — `cp "$ENV_FILE" "$BACKUP_DIR/.env.bak"` followed unconditionally by `ok "Backed up .env"` — meaning if the `cp` failed for any reason (disk full being the obvious one), the script printed a success message anyway and continued straight into the actual update, with no valid `.env` backup to roll back to if something then went wrong. `backup_sqlite()` (used for both the production and dev SQLite databases) had the same class of gap in its final `cp` fallback path. All three backup steps now check their real result and abort the entire update immediately if a backup genuinely can't be written, rather than silently proceeding with what looks like a safety net but isn't one. `backup_sqlite()` deliberately treats a backup failure on *either* `anetbbs.db` or `anetbbs_dev.db` as equally fatal, since some installs run their real production data against either filename via a custom `DATABASE_URL` and there's no reliable way for the script to know which one is actually in use.
  - Verified the pruning logic and the disk-space arithmetic directly (created real timestamped backup directories and confirmed exactly the 3 newest survive; confirmed the free-space check correctly triggers on a simulated low-space value and reports accurate real numbers against the actual filesystem) before trusting either.

## v1.0b2.86 — Fix SELinux blocking nginx's reverse proxy entirely on Fedora/RHEL (July 2026)

- FIX: found while live-debugging the v1.0b2.85 MRC fix on a real fresh Fedora install — even after that fix, web MRC still didn't connect, and it turned out the main web UI didn't fully work through nginx either (accessing the site required going straight to the app's own port). Diagnosed by walking through the actual evidence step by step rather than assuming the first fix was wrong: the MRC bridge's own log (`journalctl -u anetbbs-mrc-bridge`) showed a completely clean, stable upstream connection with no errors at all, which ruled out the bridge itself; the browser-side "Connection lost — reconnecting" message turned out to originate from `anetbbs/static/mrc/client.js`'s own WebSocket reconnect logic (confirmed by reading the code — the message text is templated client-side, `data.attempt`/`data.delayMs` come from the browser's own exponential-backoff counter, not anything the Python bridge sends), meaning the browser's connection to the *local* bridge (via nginx's `/mrcws` proxy) was the thing failing, not the bridge's connection to the real MRC network. That pointed straight at nginx, and nginx's own error log confirmed it directly: `connect() to 127.0.0.1:5000 failed (13: Permission denied)` — repeating for every proxied request, to both the main app's port and the MRC bridge's port alike.
  - Errno 13 (`EACCES`) on an *outbound* `connect()` call from nginx, on a system where `nginx -t` passes clean and the service itself reports "active (running)," is the well-documented signature of SELinux blocking it — Fedora/RHEL/CentOS ship SELinux enforcing by default, and nginx (running under the `httpd_t` policy domain) is denied permission to open connections to arbitrary backend ports unless the `httpd_can_network_connect` boolean is explicitly turned on. This is a stock, out-of-the-box SELinux restriction, not anything specific to this app — but `install.sh` never touched SELinux at all, on any Fedora/RHEL install, ever.
  - This fully explains every symptom observed on the fresh Fedora install: the main web UI needing the app's raw port instead of nginx's port 80/443, and web MRC "sometimes" appearing to connect before dropping again (SELinux denials aren't perfectly deterministic under concurrent connection attempts, so an occasional connection slipping through before the policy blocks the next one produces exactly this flaky, intermittent-looking pattern).
  - Fixed in `install.sh`: right after generating the nginx config (same step that writes `/etc/nginx/sites-available/anetbbs`), detects SELinux enforcing mode via `getenforce` and, if enforcing, runs `setsebool -P httpd_can_network_connect 1` (the `-P` makes it persist across reboots, not just the current session). Systems without SELinux at all (Debian/Ubuntu, the other officially supported install targets) are completely unaffected — `getenforce` simply doesn't exist there, so the whole check is skipped, not just a no-op branch.
  - Extended `update.sh` with the matching self-heal check (same `getenforce`/`setsebool` logic, gated on the boolean not already being on) so installs that predate this fix get it applied automatically on next update, following the same self-heal precedent established by the v1.0b2.85 MRC port fix in the same file.
  - This was found and fixed on the same day as, and is entirely independent of, the MRC-specific nginx port-mismatch bug fixed in v1.0b2.85 — both were real, separate bugs affecting the same fresh-install report, but this one is more fundamental on any SELinux-enforcing system: it blocks nginx's reverse proxy globally (the main app included), not just the MRC bridge specifically.

## v1.0b2.85 — Fix web MRC never connecting on fresh installs (July 2026)

- FIX: web MRC chat consistently failed to connect on fresh installs — reproduced independently on multiple separate fresh installs, including a stock Fedora box, with identical results each time. Investigated by tracing `install.sh` in full alongside the actual bridge/web-side connection requirements, rather than guessing from the symptom. Root cause: `install.sh`'s generated nginx config for the `location /mrcws` block (`install.sh` line ~1473) hardcoded `proxy_pass http://127.0.0.1:8080/ws;`, but the MRC bridge's actual listen port is deliberately derived as `WEB_PORT+1` (`MRC_BRIDGE_PORT_DEFAULT=$((WEB_PORT + 1))`, `install.sh` line 347) — 5001 on the default `production`-mode `WEB_PORT` of 5000. That derivation was introduced correctly in v1.0b2.35 to fix a *different* port-collision bug (the bridge and gunicorn both defaulting to 8080 in test/behind modes), and got wired correctly into `.env` and the bridge's own `config.json` at the time — but the nginx `/mrcws` proxy target was never updated to match, and the two pieces of port logic have been silently drifting apart ever since. The bridge process itself starts and runs with zero errors regardless (`journalctl -u anetbbs-mrc-bridge` shows a perfectly healthy service bound to 5001), because it's never told anything is wrong — nginx is the only thing that knows about the mismatch, and it fails the WebSocket upgrade silently from the browser's perspective, with no signal anywhere in `install.sh`'s own post-install health checks (which only confirm the *service* is active, not that nginx's proxy target actually points at it). This reproduces identically on every OS and every default `production`-mode install, which is the wizard's first-listed and default mode — exactly matching "happens on every fresh install, regardless of environment."
  - Fixed the hardcoded port in three places: `install.sh`'s generated nginx config (now interpolates `${MRC_BRIDGE_PORT_DEFAULT}`, the same variable already used correctly everywhere else in that block), the static `deploy/anetbbs-nginx.conf.template` reference file (used for manual `behind`-mode nginx setup — corrected to `5001` with a comment tying it to `WEB_PORT`), and `.env.example` (was documenting the same stale `8080`, now `5001` with an explanatory comment).
  - Extended `update.sh`'s existing nginx auto-repair logic (which already had precedent for self-healing known-bad nginx values from old `install.sh` templates, e.g. a prior `/mrcws` *path* fix and a missing-block auto-insert) to detect this exact *port* mismatch — reads the real configured port from `.env`'s `MRC_BRIDGE_PORT` (which was always correct) and patches nginx's proxy target to match if they differ. This means installs that are already carrying this bug get it fixed automatically on their next update, not just future fresh installs. Also fixed the same stale `8080`/`5000` literals in the "add missing `/mrcws` block entirely" repair branch (for sysops upgrading from before web MRC existed), which now reads the real ports from `.env` too instead of hardcoding defaults that may not match a customized install.
  - Confirmed via direct code tracing (not assumption) that `/mrcws` is handled *exclusively* by nginx's proxy — Flask has no route for it at all (`anetbbs/web/mrc_web.py` only exposes the internal `/mrc/auth-check` endpoint nginx's `auth_request` calls, never the WebSocket itself). This means `test`/`behind` install modes, which default to nginx disabled, have **no working path for web MRC at all**, independent of the port bug — a real gap, not something a port fix alone resolves. Added explicit warnings to both modes' install-time summaries: `test` mode now states plainly that web MRC won't connect without nginx (with the fix being either switching to `production` mode or manually proxying `/mrcws`), and `behind` mode's sample nginx block (which previously showed only the main `location /` proxy) now tells the sysop they need two more locations from `deploy/anetbbs-nginx.conf.template`, since copying the printed example alone would never cover MRC.
  - Also confirmed terminal MRC (over SSH/telnet) is entirely unaffected by any of this — `anetbbs/features/mrc_chat.py`'s `MRCChat` class connects directly to `127.0.0.1:{MRC_BRIDGE_PORT}` server-side (reading the same `.env` value that was always correct), bypassing nginx and the browser entirely.
  - Caught and fixed a bug in my own fix during testing: an early version of the `update.sh` auto-repair's port-extraction regex (`grep -oE '[0-9]+'` applied to the whole `127.0.0.1:PORT/ws;` string) matched the IP address's own leading octet (`127`) before the actual port, since `head -1` just takes whichever number appears first in the string — verified with a simulated before/after repair test before trusting it, and corrected to anchor specifically on the `:PORT/ws;` segment.

## v1.0b2.84 — BinkP: inbound CRAM-MD5 support + poll log for inbound-delivered mail (July 2026)

- FIX: a real downstream FidoNet peer running binkd reported `CRAM-MD5 is not supported by remote` when polling IN to an ANetBBS install, while the reverse direction (this install polling that peer) worked fine. Root-caused by reading the code directly rather than guessing: `anetbbs/echomail/binkp_server.py` — the inbound BinkP listener, which accepts connections FROM remote systems — never implemented CRAM-MD5 as the answering side at all. Its M_NUL handshake preamble (`SYS`/`ZYZ`/`LOC`/`NDL`/`TIME`/`VER`) never included the `OPT CRAM-MD5-<hex-challenge>` line FTS-1027 requires an answering side to offer, and its password check (`binkp_server.py`, both the upstream-`EchomailNetwork` and downstream-`BinkPNode` branches) only ever did a literal string comparison against `M_PWD` — no `hmac`/`hashlib` usage anywhere in the file. Meanwhile `anetbbs/echomail/binkp.py`'s outbound client has a correct, previously-verified CRAM-MD5 implementation (confirmed spec-correct in an earlier, unrelated investigation into a *different* bug) — but it is exclusively calling-side logic (`BinkPClient._connect()` always initiates outbound; the class never accepts a connection), so it has no bearing on the inbound path. The two sides had simply never been given matching implementations — this wasn't a regression, CRAM-MD5 support for inbound sessions never existed. binkd's "CRAM-MD5 is not supported by remote" is its literal, correct message for exactly this condition: a caller that expects/prefers CRAM-MD5 sees no challenge offered in the preamble it receives. Fixed by generating a random 32-byte challenge per inbound session (`secrets.token_bytes(32)`), advertising it via `OPT CRAM-MD5-<hex>` in the preamble (sent before `M_ADR`, matching the outbound client's own ordering convention), and adding `_verify_binkp_password()` — checks for a `CRAM-MD5-` prefix on the caller's `M_PWD` response and verifies it via `hmac.new(password, challenge_bytes, hashlib.md5).hexdigest()` against the session's own challenge, falling back to the original plain-text comparison for callers that don't send a CRAM-MD5 response — so peers that don't support it keep working exactly as before. 13 new tests, including one that specifically verifies a correct-looking digest computed against the *wrong* challenge is rejected (the single most likely way this class of bug hides — verifying a real digest against stale/mismatched challenge bytes would silently accept it).
- FIX: found while investigating a related report — echomail was genuinely arriving from two connected networks (messages landing correctly in message areas) but the admin Poll Log showed 0 messages for both, every time. Traced the full pipeline rather than assuming a miscount: `EchomailPollLog` (the model backing the Poll Log page) turned out to be written from exactly one place in the entire codebase — `anetbbs/echomail/poller.py`'s `_do_poll()`, which only runs for *outbound* sessions (ANetBBS dialing out to a hub). The actual real-time delivery path for those networks is the *inbound* listener (`binkp_server.py`) — their hubs call IN and push mail, rather than waiting to be polled — and that path's `_import_pkt_payload()` (which does correctly compute and even log, at INFO level, a real imported-message count) had that count silently discarded by its caller; no `EchomailPollLog` row was ever created for an inbound-initiated session, success or failure. The 0s seen weren't corrupted data — they were the *outbound* poller honestly reporting that a later dial-out to the same network found nothing new left to pull, since the hub had already pushed everything via the inbound path moments earlier. This is a known, previously-documented gap, not a new regression: `docs/CHANGELOG.md`'s own v1.0b2.47 entry (which added the poll-log transcript feature) explicitly noted "the inbound/server side has no equivalent session model today, noted as a natural follow-up" — this release is that follow-up. Fixed by having `_handle_connection()` track a real `imported_total` across every packet it imports during a session (previously computed and immediately thrown away), and write an `EchomailPollLog` row after each authenticated inbound session that received or sent anything, with accurate `messages_received`/`messages_sent` counts, a `poll_type` of `'receive'`/`'send'`/`'both'` computed by the new pure `_inbound_poll_type()` helper, and the real session start/end timestamps. Only written for upstream-hub sessions (`net_id` set) — a downstream node polling this install as *its* hub has no `EchomailNetwork` row to log against, matching the model's existing `network_id NOT NULL` constraint. Wrapped in its own try/except so a logging failure can never break the actual mail exchange it's describing.

## v1.0b2.83 — Network join review: detail view, sequential BinkP numbering, visible passwords, secure archive (July 2026)

- FEATURE/FIX: the sysop processed the first real "apply to join this network" application (for ANotherNetwork) and hit three real gaps in `/admin/echomail/hub/join/requests`. (1) The review screen only allowed Approve/Deny off a truncated list row (`join_requests.html`'s pending table shows BBS Name/Applicant/Email/BinkP/QWK/60-char-truncated-Notes only) — added `join_request_detail()` (`anetbbs/web/hub_admin.py`) + a new `join_request_detail.html` template showing every field `NetworkJoinRequest` actually stores (bbs_software, bbs_os, telnet_address, website_url, full untruncated notes, ip_address, user_agent, rules_ack, etc. — none of which were rendered anywhere before), with Approve/Deny/Archive actions available directly from the page, linked from both the pending and reviewed rows in the list. (2) BinkP node addresses were always whatever the applicant typed into the public form, used verbatim — `approve_join_request()` had a uniqueness check but no assignment logic at all. This hub is at `1200:1/1` and the first approved node's address had to be manually edited to `1200:1/2` after the fact. Added two new optional `NetworkJoinConfig` columns (`binkp_zone`, `binkp_net`, configurable in the join-form admin tab) — when both are set, `_next_binkp_node_address()` computes the next unused `{zone}:{net}/N` by scanning existing `BinkPNode.ftn_address` values with the existing `anetbbs/echomail/routing.py` FTN parser (which safely no-ops on non-numeric addresses, like the real domain-style one — `theunderground.network` — the first applicant actually submitted, and on addresses in other zone:net pairs), defaulting to node 2 when none exist yet (node 1 reserved for the hub, matching `nodelist()`'s existing hardcoded convention a few hundred lines down in the same file). Leaving zone/net unset preserves the exact old verbatim-address behavior — this is opt-in, not a forced migration. QWK's `qwk_packet_id` is completely unchanged, by design: that stays sysop/applicant-chosen, no auto-numbering wanted there. Also fixed the post-approval credentials email, which was still referencing the applicant's originally-*requested* BinkP address rather than the actually-*assigned* one — a real bug this same change surfaced, since those two can now differ. (3) Confirmed via a direct live database query that the generated node password was never actually lost — `approve_join_request()` correctly generates and persists it in three places (`BinkPNode.password`/`QWKNode.password`, plus `NetworkJoinRequest.generated_binkp_password`/`generated_qwk_password`) — but there was no read path anywhere in the admin UI, so once approved without SMTP configured, the sysop had no way to retrieve it short of a raw SQL query. Added a plain `<code>` display to both `binkp_node_detail.html` and `qwk_node_detail.html` (both already login+admin-gated, no extra masking needed) and inline next to the "view node" links in the review list's reviewed table. Also added a deliberate, sysop-triggered "Archive Application to Disk" action (`archive_join_request()`) that snapshots the full application to a timestamped, pretty-printed JSON file under `data/network_join_archive/` — confirmed this directory has no web route pointing at it anywhere in the app (the only things ever served out of `DATA_DIR` are narrow, named, single-file routes like the existing infopack download), so it's genuinely unreachable except by direct filesystem/SSH access, matching the "no one else could reach it" requirement. 8 new tests covering node-number computation (empty/existing/cross-zone/non-numeric cases), end-to-end auto-numbered approval, the detail view, the password now showing in the review list, and the archive file landing on disk while staying unreachable via any URL.

## v1.0b2.82 — Remove remaining box-drawing borders, refresh main menu content (July 2026)

- CLEANUP: two prior passes (v1.0b2.12 "Widescreen: 132-col art border removal", v1.0b2.23 "Multi-screen welcome/goodbye/newuser sequences") removed the old full-rectangle `╔═╗║╚═╝` box-drawing border from most stock terminal screens in favor of the borderless block-shaded bar style used by `anetbbs/features/ansi_ui.py`'s `banner()` helper — but neither pass ever touched the plain 80-column `.ans` files for `anetbbs/screens/newuser.ans`, `anetbbs/screens/menus/main.ans`, `menus/chat.ans`, and `menus/game_center.ans`. Those four were still carrying the old box border while their 132-col widescreen siblings and `welcome.ans`/`goodbye.ans` had already been fixed. Rebuilt all four with a small script (`\xdc`/`\xdf` block-shaded header/footer bars, `\xb0` shade fill behind the centered title, `\xc4` single-line separator — no `\xba` side walls) to exactly match the already-correct convention, generating the raw CP437 bytes directly rather than hand-editing (these files are CP437-encoded binary, not UTF-8 text — a text editor would corrupt the block/line-drawing glyphs). `newuser.ans` intentionally ships with no `@PAUSE@` marker, matching `newuser132.ans`'s existing (and, on inspection, deliberate) behavior: registration continues straight into security-question prompts with no separate pause screen. Confirmed via a full scan of every stock `.ans` file that zero box-drawing bytes (`\xc9\xcd\xbb\xba\xc8\xbc`) remain anywhere.
- FIX: while rebuilding `menus/main.ans`, found it (and `main.asc`) had also drifted out of sync with the actual menu system over several past releases — missing `[J] Send InterBBS IM`, `[K] Ebook Reader`, `[A] Page Sysop`, `[D] Dial Out`, and `[W] Change Password`, none of which had ever been back-ported into these static hand-crafted art files after their corresponding features shipped. `main132.ans` was missing `[K]` too. All three main-menu variants (`main.ans`, `main132.ans`, `main.asc`) now render the complete, current 22-item hotkey list — including the new `[L] Ask Anet` from this release — via a small Python column-layout generator (2 columns for 80-col, 5 for 132-col, plain aligned columns for the `.asc` fallback) instead of hand-typed spacing, so column alignment is guaranteed consistent and future hotkey additions are a one-line list edit instead of manual retyping.

## v1.0b2.81 — Ask Anet: fix CP437 mojibake + raw markdown leaking into result snippets (July 2026)

- FIX: found live on the Pi3 terminal (the web UI was unaffected) — search result snippets in `anetbbs/guru/search.py` showed garbled "?" characters and raw markdown syntax instead of clean text, e.g. `# TIC Processor` and `***«BinkP»` instead of `TIC Processor` and `**BinkP**`. Two compounding causes, both confirmed against a real screenshot of the broken terminal output: (1) the `snippet()` SQL call's highlight/ellipsis markers were Unicode guillemets (`«`/`»`) and an ellipsis (`…`) — these render fine in a browser (UTF-8) but as mojibake on a real terminal session, which this codebase runs in CP437, not UTF-8; (2) `snippet()` extracts text straight from the raw markdown source stored in the FTS5 index (kept as markdown deliberately, since stripping it before indexing wasn't needed for word-level matching), so literal `#` heading marks and `**bold**` markup leaked directly into the displayed snippet, unprocessed — visible for any page whose matched snippet window happened to start at or cross a heading/bold-emphasis boundary. Fixed by switching the SQL-level markers to ASCII-only (`>>`/`<<`/`...`, chosen specifically so `anetbbs/guru/render_plain.py`'s markdown-stripping regexes can't mistake them for real markdown and strip them too) and running every returned snippet through that same markdown-to-plain-text stripper already used for the terminal's full-page reader, rather than displaying FTS5's raw extract untouched. Also improved `anetbbs/features/bbs_ui.py:show_guru()`'s results list to truncate snippets on a word boundary instead of an arbitrary character cut (which was contributing to the "ragged" look reported alongside the character corruption), and made its columns responsive to terminal width via `ui_width()` (was a fixed 40/30-column split) so results use the extra room on a 132-col widescreen session instead of looking sparse, matching how the RSS reader and other doors already size their columns.

## v1.0b2.80 — Ask Anet: fix silently-broken search on upgrading installs (July 2026)

- FIX: found live on the Pi3 test server immediately after v1.0b2.79's first deploy — every search in the new Ask Anet guru door returned "nothing matched", even for exact words like "netmail" that are definitely present in the wiki. Root cause: `wiki_pages_fts` (`anetbbs/guru/fts.py`) is an *external content* FTS5 table — it stores no copy of `title`/`body`, only the inverted search index, reading the real text straight from `wiki_pages` on demand. `count(*)` against an external-content FTS5 table is a passthrough to the content table's own row count, regardless of whether the actual search index has ever been populated — a genuine SQLite FTS5 quirk, confirmed by direct reproduction (deterministic, no concurrency needed) rather than assumed. `ensure_fts_index()`'s original "does this need a one-time backfill rebuild" check compared that always-equal count against `wiki_pages`'s count, so the real `INSERT INTO wiki_pages_fts(wiki_pages_fts) VALUES('rebuild')` command never actually ran on any install where `wiki_pages` already had rows before this feature ever shipped — which is every real ANetBBS install, Pi3 and live both, since the wiki has been seeded for many releases already. Direct non-MATCH reads of the table (`SELECT body FROM wiki_pages_fts WHERE rowid=...`) looked completely normal throughout — same passthrough behavior — which is exactly what made this look like a populated, working index right up until an actual `MATCH` query was tried. Fixed the detection to check `sqlite_master` for whether `wiki_pages_fts` existed *before* this particular call, instead of comparing row counts — that's only ever true on the one process, one time, that actually creates the table, immune to the passthrough-count trap. Also moved the whole DDL-creation-plus-rebuild sequence off SQLAlchemy's `engine.begin()` (a deferred transaction for sqlite that doesn't take a write lock until the first write statement) onto the raw `sqlite3` driver under an explicit `BEGIN IMMEDIATE`, so the several separate OS processes that each independently call this at their own startup (gunicorn web workers, the telnet/SSH terminal service, binkp, etc.) can't race on the one-time rebuild — verified with a dedicated 6-process concurrency stress test in addition to the direct single-process bug reproduction that pinned down the actual root cause. 1 new regression test (simulates the exact "wiki_pages already had rows before the FTS5 table/triggers existed" upgrade scenario).

## v1.0b2.79 — "Ask Anet" help guru door, terminal + web (July 2026)

- FEATURE: a retro-styled in-BBS help assistant, a callback to the old "Lisa AI" chat door of classic BBS culture, named "Anet" for this BBS. Ask a plain-language question — "where can I view netmail", "where do I see my notifications", "how do I chat" — and it searches the wiki's help pages for a match, surfacing the right page. Available in both the terminal (new `L` hotkey on the main menu, "Ask Anet (Help Guru)", `anetbbs/features/bbs_ui.py:show_guru()`) and the web UI (`/guru/`, `anetbbs/web/guru.py`). Deliberately **not** an LLM or any AI model — must run unmodified on a Raspberry Pi 3, which has no spare compute for one. Retrieval-only: SQLite FTS5 (bundled with Python, zero new dependencies) searches the existing 45-page wiki, ranked by bm25, with a small hand-maintained alias dict (`anetbbs/guru/aliases.py`) so different phrasings ("netmail" / "PM" / "direct message") land on the same results. The personality wrapper (`anetbbs/guru/personality.py`) is entirely fixed template strings — no generation. A persistent, always-visible disclosure — shown before the first prompt in the terminal door and permanently at the top of the web page, never a dismissible one-time toast — explains in plain, non-jargony language that this is a smart search tool with a friendly wrapper, not a live AI chatbot; this was an explicit requirement so non-technical users aren't misled about what's actually running. The FTS5 index (`anetbbs/guru/fts.py`, external-content virtual table over `wiki_pages`) is created and kept in sync via SQL triggers scoped to `title`/`body` changes only (not `view_count`, which changes on every page view) inside the existing idempotent `_lightweight_migrate()` schema path — self-heals on fresh installs (populates naturally as wiki pages are seeded) and on upgrades of already-populated servers (one-time FTS5 `rebuild` command if the index doesn't match the row count). The `L` hotkey backfills onto existing installs automatically via the existing `seed_default_menus()` mechanism — no separate migration needed. 15 new tests across search/menu-wiring/web layers.

## v1.0b2.78 — Terminal uploads into disk-backed file areas never showed on the web (July 2026)

- FIX: a file uploaded via the terminal's ZMODEM upload (`anetbbs/features/bbs_ui.py:_upload_terminal_file()`) into a file area with a configured `FileArea.storage_path` showed up correctly in the terminal's own file listing but never appeared on the web UI's file-area page for the same area — confirmed live immediately after the v1.0b2.77 ZMODEM handshake fix let an upload actually succeed. Root cause: the terminal upload always saved into a single generic `uploads_dir` (creating a `FileUpload` DB row), regardless of which area was selected — the area's own `storage_path` was computed by the caller but never passed down or used. Meanwhile `anetbbs/web/file_areas.py`'s `_scan_area()` — which both the terminal's own primary listing branch (when a storage_path exists) and the web view use — only ever scans `area.storage_path` on disk and has no `FileUpload` DB fallback at all. The file existed; it just wasn't where either of them ever looked. Fixed by branching on whether a storage_path was supplied: if so, save directly under it with the real filename and create no DB row (matching `file_areas.py`'s own web upload route, and every other file already in that area); otherwise, the original uuid-named-file-plus-`FileUpload`-row behavior is preserved unchanged for areas with no disk storage at all (the "General / Top-level" case). 2 new tests — and along the way, found and worked around a real `IsolatedAsyncioTestCase` hang (near-zero CPU, no progress) triggered specifically by creating a Flask app inside one of its tests; a bare `asyncio.run()` of the identical coroutine in the identical process completes in under a second, so the tests use that instead, matching how `tests/test_terminal_node_monitor.py` already avoids this same class of problem.

## v1.0b2.77 — Fix ZMODEM upload handshake failure with SyncTERM (July 2026)

- FIX: every terminal file upload via ZMODEM over SSH from SyncTERM failed identically — the client logged `UNEXPECTED ZRPOS received instead of ZRINIT` after repeated `ZRQINIT` retries, then cancelled. 100% reproducible (confirmed live), which pointed at a protocol/flag mismatch rather than a timing race. This codebase already had a proven fix for the mirror-image bug on the *send* side (`anetbbs/features/xfer.py`): `sz --escape` causes `sz` to send `ZSINIT` to negotiate extended escaping, and SyncTERM replies with `ZRINIT` instead of the expected `ZACK`, breaking the handshake — so `--escape` was deliberately omitted from ZMODEM's outbound flags. The *receive* side (`rz --escape`, which sets the `ESCCTL` bit in our `ZRINIT` to request the sender escape control characters) turned out to trigger the same class of SyncTERM handshake failure in the opposite direction. Removed `--escape` from ZMODEM's `recv_flags` to mirror the already-proven send-side fix. XMODEM has no escape flag (not part of the ZMODEM family, no `ZSINIT`/`ZRINIT` negotiation); YMODEM's `--escape` was left untouched — no live report of a YMODEM failure, and changing it wouldn't be justified by evidence. 3 new tests.

## v1.0b2.76 — Diagnostics missed the inbound-listener path entirely (July 2026)

- DIAGNOSTIC: even the v1.0b2.75 manifest never appeared on the live server after a fresh, confirmed post-deploy `%RESCAN` — despite real messages continuing to land in the database. Root cause: `anetbbs/echomail/binkp_server.py` (the separate `anetbbs-binkp` systemd service, which handles inbound connections when a hub calls *us* rather than the other way around) has its own completely independent `_receive_files()` → `_import_pkt_payload()` → `_parse_ftn_packet()` chain that never routes through `binkp.py`'s `_import_completed()` at all. Every diagnostic added in v1.0b2.71-75 was blind to any traffic arriving this way. (The v1.0b2.70/73/74 parsing fixes themselves *do* apply here too, since both files call the same shared `_parse_ftn_packet()` — only the diagnostic capture was missing.) Added matching `_debug_manifest()`/`_debug_dump_packet()` calls to this file's inbound loop, prefixed `SRV:`/`_srv_` in the shared manifest/dump directory so captures from both paths are distinguishable. Same opt-in, zero-cost-unless-`BINKP_DEBUG_DUMP_DIR`-is-set gating throughout.

## v1.0b2.75 — Manifest diagnostic: log-based capture confirmed unreliable in production (July 2026)

- DIAGNOSTIC: v1.0b2.74 fixed the "Men In Black" desync trigger, confirmed against the real captured corpus (0 suspicious out of 3,751 messages) — but a *different* article ("SN_ALIEN" area) still corrupts on every rescan, and `BINKP_DEBUG_DUMP_DIR` only captured one small, unrelated packet from that delivery instead of the actual bulk content. While investigating, found that production's `LOG_LEVEL` silently swallows every `logging`-module message from this file — zero `"BinkP"` lines anywhere in `gunicorn-error.log` despite real traffic flowing through it the whole time, meaning the existing `logger.info`/`logger.warning` calls (including the debug dump's own confirmation message) can't be trusted as a diagnostic signal at all in this environment. Added `_debug_manifest()`: an unconditional, plain-file-I/O log (bypassing `logging` entirely) that records every file handed to `_import_completed()` — filename, size, and detected type (fts/zip/other) — regardless of whether it matches a recognized format. Same opt-in, zero-cost-unless-`BINKP_DEBUG_DUMP_DIR`-is-set gating as the existing dump. Root-cause fix for the SN_ALIEN trigger to follow once the manifest shows what's actually arriving.

## v1.0b2.74 — Chain-validate packet boundaries; fix a second desync vector found while testing (July 2026)

- FIX: v1.0b2.73's marker+date check still wasn't enough. A second `BINKP_DEBUG_DUMP_DIR` capture showed a real message body containing a byte sequence that wasn't just `MSG_TYPE_2` by coincidence, but a *fully well-formed* date+time+null field too — almost certainly quoted/reposted old FidoNet message content embedded verbatim within the article's own prose (a BBS-history piece), not random noise. That candidate's own immediate to/from/subject fields were clean, so a single-level check accepted it; the corruption (a raw `\r` in a header field) only became visible one level further into what turned out to be a fake nested chain. Fix: `anetbbs/echomail/binkp.py:_chain_looks_like_real_messages()` now tentatively parses up to 3 levels deep before accepting a candidate boundary, rejecting it if any header field along the chain contains a raw control character — something no genuine to/from/subject ever does.
- FIX (found while building the above): once a candidate is correctly rejected, its own bytes get treated as unparsed body content — and its routing header's `attr`/`cost` fields (2 bytes each, both commonly `0` in real traffic) can produce an adjacent zero-byte pair that the old code trusted outright as the packet's own end-of-data marker, silently truncating the rest of the packet. The real FTS-0001 terminator is always the literal last bytes of the buffer; `_is_real_packet_end()` now requires the candidate to actually be there instead of matching anywhere. 2 new regression tests, both reproducing the exact structures found live.

## v1.0b2.73 — Close the remaining inbound packet-record parser desync (July 2026)

- FIX: v1.0b2.70's fix for the embedded-null desync only checked the 2 bytes immediately after a candidate null terminator for `MSG_TYPE_2` (`\x02\x00`) before accepting it as a message boundary. A `BINKP_DEBUG_DUMP_DIR` capture of real inbound traffic (added in v1.0b2.71/72 specifically to root-cause this) caught the gap directly: a message's real body ("Men In Black: The Secret Terror Among Us", from a live rescan) happened to contain the exact byte sequence `0x00 0x02 0x00` as ordinary embedded content, with no relation to any real message boundary. The 2-byte check couldn't tell the difference and falsely split the packet there, corrupting that message and the next. Traced byte-for-byte against the actual captured packet (`anetbbs/echomail/binkp.py:_parse_ftn_packet()`) rather than inferred from database fallout this time. Fix: additionally require a date-shaped string (`"DD Mon YY"`) exactly where the packed message header's date field would sit — every real FTS-0001 message record has this by definition, and a coincidental byte sequence satisfying both the type marker *and* this pattern is astronomically unlikely. 1 new regression test reproducing the exact false-positive byte pattern found in production, confirmed to fail against the v1.0b2.70-72 parser and pass against this fix.

## v1.0b2.72 — Widen diagnostic capture to cover arcmail-bundled packets (July 2026)

- DIAGNOSTIC: v1.0b2.71's `BINKP_DEBUG_DUMP_DIR` capture only instrumented the raw-`.pkt`-over-the-wire path in `_import_completed()`. A live poll transcript showed the actual bulk rescan traffic arriving as FTS-0006 arcmail bundles (`0000ff91.fr7`, `.fr8` — day-abbreviation + sequence naming, ZIP-compressed) rather than bare packets, which are unpacked via a separate code path (`_is_zip()` → per-member `zipfile` extraction → `_parse_ftn_packet(inner)`) that the diagnostic never touched. Refactored the dump into a shared `_debug_dump_packet()` helper and wired it into both call sites, so a capture now covers every inbound FTS-0001 packet regardless of whether it arrived bare or inside a ZIP bundle. Still zero-cost and no behavior change unless the env var is set.

## v1.0b2.71 — Diagnostic capture for the still-open inbound packet desync (July 2026)

- DIAGNOSTIC: the v1.0b2.70 fix for the embedded-null packet desync reduced but did not eliminate the corruption — confirmed live, with multiple consecutive garbled `netmail_messages` rows still appearing after deploying v1.0b2.70 and running a fresh `%RESCAN`. Guessing further from already-corrupted database rows isn't productive: by the time a misparsed message is stored, the true wire-level byte layout that caused the misparse is gone. Added an opt-in, zero-cost-by-default diagnostic: setting `BINKP_DEBUG_DUMP_DIR` on the server saves the raw bytes of every inbound FTS-0001 packet to that directory before parsing (`anetbbs/echomail/binkp.py:_import_completed()`), so the actual wire bytes can be captured and inspected directly instead of reverse-engineering them from corrupted output. No behavior change when the env var is unset (the default). Root-cause fix to follow once real packet bytes are captured.

## v1.0b2.70 — Fix inbound FTS-0001 packet-record parser desync on embedded null bytes (July 2026)

- FIX: `anetbbs/echomail/binkp.py:_parse_ftn_packet()` located the end of each message's text by scanning for the *first* `0x00` byte after the header fields. FTS-0001 message records have no explicit length field, so this is normally correct — but a real inbound packet (a Mystic hub's AreaFix `%RESCAN` reply, delivering thousands of backlogged messages after the v1.0b2.69 capValidate fix let a resubscribe actually land) contained an `[ANSI]`-tagged post whose raw ANSI-art body had an embedded `0x00` byte partway through. The old code truncated that message's body at the embedded null and left the read cursor pointed mid-body — not at a real message boundary — permanently desyncing every message record parsed after it in the same packet. Everything downstream came out as a fabricated "message" with From/To/Subject fields built from fragments of real body text, until the cursor happened to re-align on a genuine boundary a message or two later. Confirmed against a live database dump: raw `\r` bytes embedded in `from_name`/`subject` columns, and English sentence fragments split across fields that should never contain them. The fix only accepts a candidate null as the real terminator if what follows it looks like a valid boundary (another message's type marker, the packet's own end-of-data marker, or end of buffer); otherwise it keeps scanning past the embedded byte. Because this only fires on messages containing an embedded null (rare — mostly raw ANSI-art posts), the bulk of a rescan's messages imported correctly even before this fix; only a handful landed corrupted in the Netmail Inbox instead of their real echo areas. 3 new tests in `tests/test_binkp_embedded_null_desync.py`, including one confirmed to fail against the pre-fix parser.

## v1.0b2.69 — Fix malformed FTS-0001 packet header rejected by strict tossers (July 2026)

- FIX: every outbound FTN packet (netmail and echomail, both the outbound-poll client path and the hub-to-downstream-node server path — `anetbbs/echomail/binkp.py:_build_ftn_packet()` is the single shared writer for both) had a malformed Type-2+ header extension field. `capValidate` (header offset 40-41) was written as a plain little-endian copy of `capWord` (offset 44-45); per FSC-0048 it must instead hold the same value **byte-swapped** (big-endian) — confirmed directly against the `hpt`/husky tosser's own source (`pktread.c`/`pktwrite.c`). A plain identical copy only happens to produce a matching pair for palindromic capWord values (high byte == low byte); our `CAP_WORD = 0x0001` is not one, so every packet we ever sent silently failed this check on any strict receiving tosser. Root-caused from a real report by an external FTN sysop (SmallTime BBS, running `hpt`), whose tosser logged the packet rejection outright: `CapabilityWord error in following pkt! rtfm: IgnoreCapWord.` Other tossers (e.g. Mystic, which several of this install's own upstream hubs run) may fail the same way without surfacing as clear an error — silently dropping the packet instead, which is indistinguishable from "the hub has nothing queued for us" from our side. 5 new tests in `tests/test_binkp_capword_byteswap.py`, decoding the header exactly the way `hpt`'s own reader does rather than just asserting the bytes "look right."

## v1.0b2.68 — Close the confirmed gaps from the Synchronet/Mystic comparison (July 2026)

Follow-up to the competitive gap analysis against Synchronet and Mystic BBS (see the research artifact referenced in project notes). Ships the 5 tactical gaps plus a unified access-control helper — the scripting-language gap and JAM/Squish message-base interop stay out of scope as accepted limitations for now.

- FEATURE: unified read-access check. New `anetbbs/features/access_control.py:evaluate_access()` replaces scattered per-feature `min_access_level`/`is_sysop_only`/admin-bypass comparisons (migrated: echomail area access, RSS feed listing, game lobby listing). Fixed two confirmed real gaps found along the way: the web file-area browser never checked an area's `min_access_level` at all (only the terminal client did), and `/search` returned sysop-only boards and access-gated echomail to any user, including logged-out visitors, since neither result branch checked access at all.
- FEATURE: consolidated the 21 separately-duplicated admin-only route gates (`anetbbs/web/*.py`) into one shared `anetbbs/web/access_control.py:require_admin`/`require_admin_or_403`, standardized on `abort(403)` everywhere — `admin.py`'s routes previously flashed a message and redirected instead, a deliberate, confirmed UX change.
- FEATURE: file search — `/search` gained a `files` result category (scoped to the DB-backed file gallery; the FTN-style file-area browser has no per-file database rows to search cheaply).
- FEATURE: duplicate-file detection on upload. New `anetbbs/features/file_dedup.py:hash_file()`; the file gallery gets a real `content_hash` column, the FTN-style file-area routes get a per-area `.hashes.json` sidecar cache (no per-file DB row exists there). A notice, not a hard block — the upload still succeeds.
- FEATURE: archive integrity testing on upload, all four upload routes. New `anetbbs/features/archive_meta.py:test_archive_integrity()` — `zipfile.testzip()`/`rarfile.testrar()`/`py7zr`'s `.test()` where available, best-effort read-through for tar, fails open on anything untestable (missing optional library, unrecognized format) rather than blocking a legitimate upload.
- FEATURE: FileFix bot — the file-echo counterpart to the existing AreaFix bot, so a downstream peer can self-service their own file-echo subscriptions via netmail the same way AreaFix already does for message echoes. The underlying data model and the outbound half already existed; only the inbound netmail-robot dispatch was missing.
- FEATURE: menu translation wiring. `User.language` and `MenuTranslation` existed as schema but nothing ever read them — the data-driven terminal menu system now looks up per-language overrides when a user's language isn't English, falling back to the source text.
- 51 new tests across `tests/test_admin_gate_consolidation.py`, `tests/test_evaluate_access.py`, `tests/test_archive_integrity.py`, `tests/test_file_dedup.py`, `tests/test_search_access_and_files.py`, `tests/test_menu_translation.py`, and `tests/test_filefix.py`.

## v1.0b2.67 — Fix netmail import crash on duplicate-check (CI-caught) (July 2026)

- FIX (caught by GitHub Actions CI, not local testing — no Flask available in the sandbox this was built in): `anetbbs/echomail/poller.py`'s `_import_netmail()` — used whenever inbound netmail is imported via the poll-response path — queried `NetmailMessage.query.filter_by(msg_id=msg_id)` for its duplicate-message check, but the model's actual column is `msgid` (no underscore, confirmed by the very next line's constructor call using it correctly). This raised `AttributeError` unconditionally any time an inbound netmail carried a MSGID kludge — which is virtually all real FTN netmail — meaning this function crashed before importing the message at all, on this specific path, whenever a MSGID was present. Pre-existing bug (not introduced by v1.0b2.65's notification fix, which touched the same function but not this line) — first actually exercised by the new regression test added for that release, and the crash only surfaced once that test ran somewhere with pytest/Flask installed (the CI Docker image), not in this sandbox. Fixed to query the correct `msgid` column. Full local test suite (434 tests) now verified green in a real venv with Flask installed, not just `py_compile`.

## v1.0b2.66 — ClamAV scan timeout now sysop-configurable (July 2026)

- FEATURE: the ClamAV scan timeout (how long `anetbbs/features/virus_scan.py`'s `scan_path()` waits for `clamscan` on one file before giving up and letting it through) was a hardcoded 30 seconds with no way to change it short of editing source. Added `CLAMSCAN_TIMEOUT` — a new field at Admin → Settings (and the matching env var), defaulting to 60 seconds, same pattern as the existing `IDLE_TIMEOUT_SECONDS`/`BOT_GATE_TIMEOUT` settings. Takes effect immediately on save, no service restart needed — every scan call site (per-upload scanning in the file areas and file-queue routes, plus the Admin → Bulk Virus Scan page) already re-reads it per call. 7 new tests in `tests/test_clamscan_timeout.py`.

## v1.0b2.65 — Fix missing notification for inbound netmail (July 2026)

- FIX: new inbound netmail (FTN point-to-point private mail) never triggered any notification — the in-app bell and the terminal "You have new mail" banner both stayed silent even when netmail addressed to you had actually arrived. Root cause was two separate gaps in the two inbound-netmail import paths: the QWK/poll-response path (`anetbbs/echomail/poller.py`) resolved the message to a local recipient but never called the notification helper, and the real-time BinkP listener path (`anetbbs/echomail/binkp_server.py`, used whenever another system polls in live) never resolved a local recipient at all — so netmail received that way wasn't even linked to a `User` account, only findable later by manually browsing and string-matching the "To" name. Both paths now share one recipient-resolution helper (`anetbbs/echomail/routing.py:resolve_netmail_recipient`, matching by AKA address, then username, then display name, then the network's configured DefaultRecipient) and both now create a Notification for the resolved recipient, which the existing web bell and terminal banner already know how to display — no changes needed on that side. 8 new tests in `tests/test_netmail_notification.py`.

## v1.0b2.64 — Terminal sysop tools: Node Monitor, broadened Sysop menu, MSP picker, profile redesign (July 2026)

- FEATURE: new terminal (telnet/SSH/rlogin) **Node Monitor** at Sysop Tools → Node Monitor — a scrollable, Synchronet-UNIX-Monitor-style live list of connected nodes (slot/user/protocol/current page+action/idle time), with per-node **Kick** and **Message** actions. Data comes from `NodeActivity` (already updated on every menu render/action), the same source the web NodeSpy panel uses, so both views stay in sync. Kick sets the same DB flag NodeSpy's kick button does, picked up by the existing 5-second watchdog. Messaging a node reuses the existing sysop-reply inbox; a gap where a pushed message would never surface for installs still on the legacy hard-coded main menu (as opposed to the data-driven `BbsMenu` one) was closed at the same time.
- FEATURE: the terminal **Sysop Tools menu** was 3 static items (Manage Users, Manage Boards, Server Status) — now a scrollable menu of 14 categories, bringing most of the terminal-feasible slice of the web admin UI into telnet/SSH/rlogin for the first time: Users, Boards/Bulletins, Echomail/Hub (networks & areas, QWK node-request approval, bad-area review), Games (active session disconnect, Trade Wars 2002 universe reset), Wall moderation, File Upload Queue (approve/reject), Scheduled Events (cron-style maintenance jobs), RSS Feeds, Login/Logoff Modules, Notifications (Webhooks, Sysop Broadcast, Message of the Day, Sysop Pages), Registry/Peers (federation applicant approval, peer probes), Caller Log, Node Monitor, and Server Status. Deliberately left web-only: the ANSI art editor/theme builder, file/avatar upload, backup restore, in-place upgrades, and full network-join applicant approval (creates multiple node records and emails credentials).
- FEATURE: sending an Inter-BBS Instant Message from the terminal (`New PM`) no longer requires already knowing the exact `user@host` to type — it now offers a scrollable BBS directory, live-probes the picked BBS over SYSTAT for who's currently online there, and lets you pick a name from that list. Manual `user@host` entry is still available at every step (empty directory, failed/empty probe, or just backing out).
- FEATURE: **Edit Profile** in the terminal was a blind sequential prompt-per-field walk (easy to miss settings like Sixel mode entirely). Replaced with a single summary screen showing every current setting plus a scrollable picker for which one to change. Also added three settings that existed on the web profile editor but were unreachable from the terminal: FTN tagline, "show email publicly", and date of birth, plus two that weren't editable from *either* front end yet (codepage, language preference — currently unused by anything else in the codebase, but now at least settable). Password is never shown or editable here, same as before.
- FIX: the AreaFix log's Tags column could overflow its table cell on long comma-separated area lists — now wraps.
- 30+ new tests across `tests/test_terminal_node_monitor.py`, `tests/test_terminal_sysop_menu.py`, `tests/test_systat_parser.py`, and `tests/test_terminal_profile_redesign.py`.

## v1.0b2.63 — Option to hide sysop from Last Callers (July 2026)

- FEATURE: new "Hide sysop logins from the Last Callers displays" toggle at Admin → Last Callers, off by default. Keeps a sysop who logs in several times a day from flooding the user-facing Last Callers list (terminal screen, terminal inline block, and the web one-liners page) with themselves instead of real users. The admin audit list always shows everyone regardless of this setting, and InterBBS-shared entries from other BBSes are never affected by it. 7 new tests.

## v1.0b2.62 — InterBBS Game Score Sharing (July 2026)

- FEATURE: door/web game high scores can now be shared with other ANetBBS installs, the same InterBBS pattern as the Graffiti Wall and Last Callers (riding echomail, opt-in, one BinkP network at a time). Only *new personal bests* relay, not every submission — a game's first score is trivially a personal best; after that, only a strictly higher score relays again. Each game has its own on/off switch (Admin → Games → edit a game, defaults on), plus the install-wide switch at Admin → Games → InterBBS Scores (now linked from the main admin dashboard, along with the previously-unlinked InterBBS Wall and Last Callers pages). Casino games (Blackjack/Slots/Video Poker/Hold'em) are included, tracked as "highest weekly balance" and shown as a dollar amount rather than a bare number, both locally and for synced entries. Enabling score sharing resets the four casino weekly starting balances to a shared standard value across every participating install (so a "$50,000 peak" means the same thing everywhere); changing any of those four afterward automatically turns score sharing back off rather than letting the numbers silently drift out of sync. 20 new tests.

## v1.0b2.61 — QWK packet format cleanup for better reader compatibility (July 2026)

- FIX: cleaned up a few spots in the QWK packet writers/readers where fields weren't laid out quite right, which could affect compatibility with some third-party QWK reader software. No changes needed on the sysop side. 4 new tests.

## v1.0b2.60 — Updated handshake/CMD_EOB for older bink clients (July 2026)

- FIX: BinkP session close-out could end a bit too early for some strict FTN mailers, causing a successful file transfer to still be reported as failed on the other end. Adjusted the M_EOB handshake and connection close-out on both the client and server side to match. Also switched outbound packet filenames to the conventional FTN naming style.

## v1.0b2.59 — Fix BinkP handshake announcing a hardcoded, permanently-stale version (July 2026)

- FIX: found while diagnosing a real FTN interop report — a peer's binkd log showed our BinkP handshake announcing `VER ANetBBS/1.0a binkp/1.1`. The version in that line was a literal, hardcoded string in both `binkp.py` (client handshake) and `binkp_server.py` (server handshake) — it has said `1.0a` since this code was first written, regardless of which release is actually running, making it useless for diagnosing what version a peer is on from their own connection logs. Fixed by pulling the real version from `anetbbs.__version__` in both places. 2 new tests.

## v1.0b2.58 — Fix session disconnect right after a telnet transfer completes (July 2026)

- FIX (live-caught testing v1.0b2.57 against a real telnet client): a ZMODEM download completed correctly (byte-perfect, the actual fix worked), but the session disconnected straight to the goodbye screen the instant the transfer finished. Root cause: the transfer's own reader task — the one whose telnet-unescape codec consumes the client's replies to our BINARY-mode negotiation — was cancelled before the post-transfer "turn BINARY back off" command was sent. The client dutifully replies to that with its own WONT/DONT bytes, but nothing was listening anymore; those bytes sat in the socket buffer and got picked up raw by the very next normal read (the "Press Enter..." prompt), which read them as a disconnect. The "turn BINARY on" side never had this problem, since the transfer's reader is already running by the time that reply comes back — only the "turn it back off" step was affected. Fixed by not sending that command at all: leaving a session in telnet BINARY mode for the rest of the connection is standard, harmless practice (it only suppresses NVT ASCII translation, no effect on normal text/ANSI output), and avoids the reply-timing hazard entirely. Same underlying feature as v1.0b2.57 — see that entry for credit to andy5995's original PR #6 diagnosis.

## v1.0b2.57 — Fix ZMODEM/XMODEM/YMODEM corruption on RFC-compliant telnet clients (July 2026)

- FIX: the terminal file-transfer bridge (`xfer.py`) fed raw socket bytes straight to sz/rz and wrote sz/rz output straight to the socket. On a telnet channel, `0xFF` is IAC and a compliant client doubles it on the wire (RFC 854/856); the bridge never undid that doubling, so every `0xFF` byte in a transfer reached rz as a spurious extra byte and corrupted the stream, which ZMODEM answered with ZCAN. Lenient clients that send raw 8-bit bytes with no telnet processing happened to work by accident; anything RFC-compliant did not. Root-caused and originally patched by andy5995 (GitHub PR #6, drafted with Claude Opus 4.8's assistance) — thank you for tracking this down. Their fix added a telnet escape/unescape codec plus BINARY+SGA negotiation around the transfer, which is correct for telnet — but `send_file()`/`recv_file()` are shared by telnet, SSH, and rlogin sessions with no protocol check anywhere before they're called, and SSH/rlogin channels have no IAC concept at all (already 8-bit clean at the transport layer), so applying that codec unconditionally would have fixed telnet by corrupting SSH/rlogin transfers instead. Landed with a protocol gate added on top so the new codec only ever runs for telnet sessions; SSH/rlogin pass bytes through exactly as before. 21 new tests in `tests/test_xfer_telnet_framing.py` — `xfer.py` had no test coverage prior to this.

## v1.0b2.56 — Wire up the remaining 7 webhook event types (July 2026)

- FEATURE: the webhooks admin form (Admin → Webhooks) has always offered 8 event types, but only `shout` (shoutbox posts) actually fired anything — `post`, `bulletin`, `login`, `achievement`, `broadcast`, `sysop_page`, and `echomail` were selectable and would sit there showing "never called" forever, since nothing in the codebase ever called `fire()` for them. All 7 are now wired to their real trigger points: new board posts and replies (both web and terminal), sysop-created bulletins, logins (web, telnet, SSH, and rlogin — all four, not just web), newly-awarded achievement badges, multinode "Local Chat" broadcasts, sysop pages, and newly-imported inbound echomail messages (BinkP and QWK alike). Each event has its own payload shape (documented in `docs/23-webhooks.md`, which also covers the (lack of) HMAC signing, no-retry delivery semantics, and other gotchas already found documenting the pre-existing `shout` event). 8 new tests in `tests/test_webhooks_all_events_wired.py`.
- New doc: [`23-webhooks.md`](23-webhooks.md) — full sysop-facing setup guide covering all 8 events, payload formats, the Bearer-secret auth model (not HMAC), delivery mechanics, and a worked Discord example.

## v1.0b2.55 — Fix QWK REP uploads never actually importing (July 2026)

- FIX (live-caught, found while verifying the v1.0b2.54 conference-number fix end to end): a reply uploaded from a QWK node showed "sent" on the sending side but never appeared on the hub, with no error visible anywhere a sysop would look. Root cause: `process_rep_upload()` (the hub's REP-file importer) read the parsed message dict using the wrong key names (`conference`/`from`/`to`) — the parser actually returns `conf_num`/`from_name`/`to_name`, so every field silently fell back to its default instead of raising a KeyError. `conference` defaulting to `0` meant every uploaded reply, from every QWK node, always resolved to conference 0 (private mail) — which no node ever has a real subscription to — so the message was silently dropped every time. This was never specific to one node or one test message: inbound REP processing has been completely non-functional since this function was written, for every QWK node on the hub. Fixed by reading the correct keys. 1 new regression test in `tests/test_qwk_rep_upload_detached_instance_fix.py`, which also covers a second, unrelated bug found in the same investigation: a trailing log statement outside the function's app-context block crashed with `DetachedInstanceError` after a successful import, misleadingly logging "REP processing failed" for uploads that had actually already succeeded.
- FIX: the "Pending Outbound" dashboard counter (and the terminal sysop status screen's matching "Echo out (queue)" stat) counted `sent_at IS NULL` regardless of transport — but QWK never sets `sent_at` at all (delivery is tracked via a per-node high-water mark instead), so every QWK message ever sent stayed counted forever whether it was actually delivered or not. The number could only ever climb, never reflect reality. Scoped to BinkP only, the one transport where `sent_at` actually means "not yet sent." 1 new test.

## v1.0b2.54 — Fix QWK areas silently dropping messages, InterBBS Wall/Last Callers restricted to BinkP (July 2026)

- FIX (live-caught testing InterBBS Wall against a real QWK-connected node): a message posted from a QWK node vanished with no error anywhere — reported "sent" locally, never arrived at the hub. Root cause: the QWK wire format only ever carries a numeric conference number (no field for anything else), but the ANotherNetwork install seeder created the QWK-side copy of all 26 built-in echo areas using the same symbolic tag as the BinkP side (`ANN.LINUX`, `ANN.GENERAL`, etc.) A post into one of these fell back to conference 0 (reserved for private mail), and the hub's REP importer has no subscription ever registered at conf 0, so the message was dropped outright, not just misfiled — affecting every install that had ever activated the QWK side of this network, not one area in isolation. Underneath that, conference numbers were being assigned per-node-subscription (auto-incremented independently each time a node subscribed) rather than as a fixed property of the area itself, so two different nodes could even get two different numbers for "the same" area. Fixed with a stable, fixed conference number per area (shared by every subscriber), a self-healing migration that renumbers any already-seeded install's QWK areas and fixes existing subscriptions to match on next startup, and form validation that blocks creating a QWK-network area with a non-numeric tag going forward. 10 new tests in `tests/test_qwk_conf_number_fix.py`.
- FIX: InterBBS Wall/Last Callers admin network picker offered QWK networks even though QWK's numeric-only wire format can never carry the special `ANET_WALL`/`ANET_LASTCALLERS` tag — restricted to BinkP networks only, with a server-side guard against a stale QWK network id too.
- FIX: enabling InterBBS Wall/Last Callers against a valid BinkP network created nothing at all in Echomail Areas — the special area was only ever created lazily, the first time someone posted new local content after enabling. Now created eagerly the moment the feature is enabled.
- FEATURE: QWK hub's "Subscribe to All" bulk action swept in every QWK network on the install with no way to scope it to just one — added per-network checkboxes. BinkP had no bulk-subscribe at all (only one area at a time); added the same feature with the same checkboxes.
- New `tools/manage_qwk_requests.py` — list/hard-delete stuck `QWKNodeRequest` rows that show pending indefinitely (e.g. an application submitted before a feature was finished, later abandoned).
- 21 more new tests across `tests/test_interbbs_binkp_only.py`, `tests/test_qwk_subscribe_all.py`, `tests/test_binkp_subscribe_all.py`.

## v1.0b2.53 — InterBBS Wall + InterBBS Last Callers (July 2026)

- FEATURE: opt-in InterBBS Graffiti Wall — share Wall posts with other ANetBBS installs over a dedicated `ANET_WALL` echomail area, riding the existing QWK/BinkP transport (real per-node auth, FTN dedup/threading fields, hub subscribe/approve UI) instead of a bespoke sync protocol, matching how fsxnet's own wall echo works. Toggle + network picker on the Wall admin page; a scheduled job (auto-created on enable, every 15 min) imports inbound posts. Remote posts are tagged with their origin BBS in the moderation view. Loop-prevention: an imported post is tagged `origin_bbs`, and the relay hook refuses to ever re-relay a tagged post — the only thing standing between this design and an infinite bounce between two hubs, since a re-composed message would get a brand-new msg_id no downstream dedup could catch.
- FEATURE: Last Callers — new paginated terminal screen (mirrors the existing "Last 10 Callers" style already on the one-liners screen) plus a same-shape opt-in InterBBS sharing toggle. Fixed a real gap found while building this: `CallerLog` (the "last callers" table) was only ever written from the web login route — telnet/SSH/rlogin logins never recorded one, so "Last Callers" only ever showed web users on an otherwise telnet-first BBS. Only `service` and login time are ever shared over InterBBS — IP addresses are never relayed across BBS boundaries.
- FIX: BinkP handshakes advertised each address in two forms at once (`addr@domain` and bare `addr`) in the same `M_ADR` line. Real binkd treats each form as a separate token and self-collides on its own busy-lock for the second one, dropping the session with `Secure AKA busy` before password checking ever runs — happens both when polling out to a real binkd hub and when a real binkd hub polls in. Fixed on both the outbound and inbound sides to send one address form only.
- 17 new tests in `tests/test_interbbs_sync.py` covering the loop-prevention invariant, global (not per-area) dedup, NULL-msg_id handling, and the IP-address privacy guarantee, plus 4 more in `tests/test_binkp_dual_adr_fix.py` and `tests/test_binkp_server_single_adr_fix.py` for the BinkP fix. Full suite: 291/291 passing.

## v1.0b2.52 — Fix .env never actually being loaded (July 2026)

- FIX: found live, mid-incident, while walking the sysop through cleaning up the v1.0b2.51 QWK duplicate-message backlog on the Pi3 — running the new `tools/dedupe_qwk_messages.py` by hand reported "nothing to clean up" even though the real database had hundreds of duplicate rows, no error either way. Root cause: `python-dotenv` has been a declared dependency (`requirements.txt`, `setup.py`) for a long time, but `load_dotenv()` was never actually called anywhere in this codebase. The real systemd services (`anetbbs.service`, `anetbbs-web.service`, etc.) worked fine regardless, because they set `EnvironmentFile=/opt/anetbbs/.env`, which injects `.env`'s key=value pairs as real OS environment variables before Python even starts. But any script run by hand from an interactive shell — a one-shot `tools/*.py` maintenance script, a bare `python -m ...`, even just testing something at a prompt — never saw `.env` at all, so `DATABASE_URL` came back unset and `config.py` silently fell back to `DevelopmentConfig`'s `anetbbs_dev.db` path instead of the real `anetbbs.db`. This wasn't unique to the new dedupe script either — the pre-existing `tools/purge_bad_qwk_areas.py` had the exact same latent gap. Fixed by calling `load_dotenv(BASE_DIR / '.env')` once at import time in `anetbbs/config.py`, before any `os.environ.get(...)` calls read from it; `load_dotenv()`'s default `override=False` guarantees it can never clobber a value systemd already injected, so the real services' behavior is unchanged. 3 new tests in `tests/test_dotenv_loading.py`. (One of those tests initially broke unrelated tests elsewhere in the suite by deleting `anetbbs.config` from `sys.modules` to force a fresh re-import — that wiped other test files' `TestingConfig.SQLALCHEMY_DATABASE_URI` monkeypatches when the whole suite ran in one process. Fixed by testing `load_dotenv()` directly instead, which never needed the module-cache manipulation in the first place.)

## v1.0b2.51 — Fix QWK message duplication, CP437 body corruption, and node-edit lockout (July 2026)

- FIX: a sysop reported the Pi3 test install (subscribed as a QWK client to the real ANotherNetwork hub) showing wildly inflated area message counts — 220 messages in an area that should have had roughly 22 — while the main live server didn't show the same problem. Root cause: `_parse_messages_dat()` (`anetbbs/echomail/qwk.py`) only populated a message's `msg_id` from a literal `@MSGID:` kludge line in the body. Vanilla QWK hubs that don't tunnel FTN kludges (the normal case — confirmed against the real ANotherNetwork hub) never send one, so `msg_id` came back `None` for nearly every inbound message. The dedup check in `poller.py:_import_message` (`if msg_id: ...`) silently no-ops whenever `msg_id` is falsy, and since there's no persisted per-network poll checkpoint anywhere in this codebase either, any poll that received any content overlapping a previous poll re-imported every message as brand new — and since `EchoArea.total_messages` only increments on a real committed insert, this was genuine duplicate data accumulating in the database, not a cosmetic display bug. Because `tosser.toss_message()` runs unconditionally after any poll that imports messages, this could also have forwarded duplicates to any downstream nodes subscribed to the same area. Fixed by synthesizing a deterministic content-hash `msg_id` whenever no real `@MSGID:` is present — the same real message re-served on a later poll now hashes identically and gets caught by the existing dedup check — mirroring a fallback the outbound REP-packet writer already had for the same problem in the other direction. New `tools/dedupe_qwk_messages.py` one-shot maintenance script (dry-run by default, `--apply` to execute) wipes affected QWK areas so the next poll repopulates them cleanly under the fixed logic. 5 new tests in `tests/test_qwk_inbound_msgid_dedup.py`, including an end-to-end simulation of the exact bug (same content parsed on two overlapping polls asserts only one row is created).
- FIX: found while testing the above — `_build_messages_dat()` (the QWK hub's own outbound packet writer, `anetbbs/echomail/qwk_hub_ftp.py`) built the QWK paragraph-separator by replacing `\n` with the Python string literal `'\xe3'` (Unicode codepoint U+00E3, "ã") *before* CP437-encoding the message body. CP437 has no character mapped to U+00E3, so `encode(errors='replace')` silently substituted a literal `?` in place of every line break instead of the real QWK separator byte (raw 0xE3) — corrupting every multi-paragraph message this BBS sends outbound to downstream QWK nodes. Fixed by doing the newline substitution at the byte level, after CP437 encoding (`\r`/`\n` are plain ASCII and survive the encode step unchanged, so a byte-level replace is safe and unambiguous). 2 new tests in `tests/test_qwk_packet_roundtrip.py` covering both multi-line plain text and extended CP437 characters (box-drawing, block glyphs).
- FIX: editing an existing BinkP or QWK hub node — for example to fix a sysop's typo'd tag or BBS name — was silently impossible without also retyping a brand-new password. `QWKNodeForm.password`/`BinkPNodeForm.password` were declared `DataRequired()`, which rejected the entire form submission whenever the password box was left blank — which is always, since `PasswordField` never re-renders a stored value on a GET of the edit page. The route logic (`if form.password.data: node.password = ...`) and the template's own "Leave blank to keep current password" hint both already assumed blank-means-keep-current; the validator just made that path unreachable. A sysop hitting this could only fix a typo by resetting credentials that node's own sysop might not have safely on hand, or by deleting and re-registering the node from scratch — reported live after exactly that happened. Fixed by making `password` optional on both forms, with an explicit "password required" check added to the two node-*creation* routes so a brand-new node still can't be saved with a blank password — only editing an existing node's other fields without touching its password is now possible. 4 new tests in `tests/test_hub_node_edit_password.py`.

## v1.0b2.50 — Fix SyncTerm sixel auto-detect + terminal sixel profile option (July 2026)

- FIX: a sysop reported sixel auto-detect (`sixel_mode` = Automatic) never worked on SyncTerm, even though forcing it on/off always worked and auto-detect worked fine on other sixel-capable clients (proving the DA1 mechanism itself wasn't generally broken). Root cause, confirmed against SyncTerm's own CTerm manual: SyncTerm's DA1 reply doesn't use the standard `?`-prefixed capability-flag list that xterm/mlterm/wezterm/contour use — it spells "CTerm" out in decimal ASCII (`CSI = 67;84;101;114;109;rev c`) and never reports sixel support in that reply at all, regardless of whether the terminal actually has it. Sixel support is only exposed through a second, CTerm-specific extended device-attributes query (`CSI < 0 c` → `CSI < 0 ; Ps... c`, where flag `4` means pixel/sixel graphics are supported). `_detect_sixel_support()` (`anetbbs/features/bbs_ui.py`) now recognizes the CTerm signature in the primary DA1 reply and, only then, sends the follow-up CTDA query and checks flag 4 there — other terminals are untouched, still a single round-trip. 3 new regression tests in `tests/test_sixel_detection.py` cover the xterm-style path (no follow-up query sent), a bare CTerm DA1 reply with no CTDA response available (must not be misread as sixel support), and the full CTerm → CTDA handshake.
- FEATURE: the terminal "Edit Profile" menu (telnet/ssh) had no way to set the `sixel_mode` preference at all — only the web `/profile/edit` page did, since the preference shipped in v1.0b2.48. Added a matching Automatic/Always On/Always Off prompt to `_edit_profile()`.
- FIX (dev tooling): `docs/17-development.md` claimed `python -m unittest discover` needed no pytest install, but `tests/test_mrc_integration.py` uses real `@pytest.fixture` decorators — that file fails to even *import* without pytest present, breaking a full local `unittest discover` run for anyone who hadn't separately installed it. Added `requirements-dev.txt` (`-r requirements.txt` plus `pytest>=7.0.0`) and corrected the docs to install it first. Verified: a fresh venv with only `requirements-dev.txt` installed runs the full 258-test suite (256 unittest-discovered + the 3 pytest-fixture tests in `test_mrc_integration.py`, 2 of which skip because the MRC bridge isn't present in a bare dev checkout) with zero import errors.

## v1.0b2.49 — Three real Docker bugs found testing against an actual daemon (July 2026)

- FIX: the single-container "quick start" image's documented `docker run` command referenced `/usr/local/bin/entrypoint-single.sh`, but the Dockerfile never copied the script there — only `chmod +x`'d it at its original `/app/docker/single/entrypoint-single.sh` path. Every quick-start attempt failed immediately with "no such file or directory". Fixed by copying it to `/usr/local/bin` like the other two entrypoints.
- FIX: the terminal service (both single-container `docker/single/supervisord.conf` and docker-compose's `terminal:` service) invoked the bare `anetbbs` console-script, which failed with `ModuleNotFoundError: No module named 'anetbbs'` — its editable-install shim (`pip install -e .`, built against `/src` in the Dockerfile's builder stage) doesn't survive into the runtime image, and the console-script's own `sys.path[0]` is the directory it lives in (`/usr/local/bin`), not the working directory, so `directory=/app` alone didn't fix it either. Fixed by invoking `python -m anetbbs.main` directly instead, matching how every other service (web, mrc-bridge, finger, binkp) already starts.
- FIX: MRC web chat 404'd in single-container mode (terminal MRC worked fine — it's a direct server-to-server bridge connection, no browser/nginx involved). Root cause: the single-container image has no nginx, so `/mrcws` only exists on the MRC bridge's own port (8080, published directly), not the web app's port (5000) the browser was requesting it on. The initial fix targeted the wrong file — `anetbbs/web/mrc_web.py` computed a `bridge_ws_url` that the actual page never read; the real WebSocket URL is built client-side in `mrc/index.html`'s `buildWsUrlForChoice()` (multi-server picker), hardcoded to `location.host` with no override mechanism at all. Fixed by rendering a `window.MRC_WS_HOST_OVERRIDE` JS global (empty for nginx-fronted deployments, which stay on the already-correct `location.host` default; browser-hostname + bridge-port for `ANETBBS_RUNTIME=docker-single`; the explicit public `MRC_BRIDGE_HOST` for docker-compose) that the JS now actually consumes. Also documented the resulting tradeoff: since the browser talks to the bridge directly in both Docker modes, the bridge itself has no login check of its own — don't publish port 8080 to a public interface unless you're OK with unauthenticated MRC access.
- Real-world testing note: this is the first time the single-container Docker path has been run end-to-end against an actual Docker daemon (build, run, all 5 services reaching `RUNNING`, web + terminal + MRC web + MRC terminal all confirmed working after these fixes) — updated `docs/22-containers.md` to reflect that. The docker-compose path is still only unit-tested with mocked Docker calls. 5 new tests in `tests/test_mrc_web_docker_routing.py`.

## v1.0b2.48 — Sixel capability preference + door-game output queue fix (July 2026)

- FEATURE: scoped the highest-priority piece of a Firehawke feature request ("Fuller CTerm support and Display Codes") — sixel detection. Found sixel auto-detection (DA1 device-attributes query) already existed but was dead code in practice: the RSS reader's entry point unconditionally asked a manual "Does your terminal support sixel? [Y/N]" prompt on every session, pre-populating the same cache flag the DA1 detector checks first, so the DA1 logic never actually ran in production. It was also RSS-specific and per-session only, with no way to force it on for a client that supports sixel but doesn't self-report via DA1 (e.g. Windows Terminal over SSH), or force it off. Added a new `sixel_mode` profile preference (`auto`/`forced_on`/`forced_off`, default `auto`, editable at `/profile/edit`), promoted the detector to a general-purpose `_detect_sixel_support()` usable by any feature, and fixed the RSS reader to actually call it instead of the old always-on manual prompt.
- FIX: door-game output (`anetbbs/web/games.py`) previously called `socketio.emit()` directly from the PTY-reader thread; now marshaled through a proper thread-safe queue drained by a `socketio.start_background_task()`, matching documented Flask-SocketIO practice — purely additive, same order and content for the text output that already works. Added alongside a DEBUG-level diagnostic that logs sixel/DCS-shaped output chunks, to help confirm (on a real DSR test session, not reproducible in a sandbox) whether 8-bit C1 control-code framing is getting silently corrupted by the `cp437` decode — a known, already-documented dead end where sixel image rendering has never worked through the gunicorn-spawned PTY chain for Synchronet-compatible doors, despite the frontend already having working sixel rendering capability (`xterm-addon-image`). 9 new tests across `tests/test_sixel_detection.py` and `tests/test_door_output_queue.py`.

## v1.0b2.47 — Full BinkP session transcripts for failed polls (July 2026)

- FEATURE: a sysop reported not having enough BinkP logging to diagnose a failing session. Two real gaps found: the generic poll-failure handler (`anetbbs/echomail/poller.py`) stored only `str(exc)`, which can be empty or unhelpful for some exception types (bare `socket.timeout`, some `ConnectionError`s); and there was zero frame-level logging anywhere in the BinkP client — `_send_cmd()`/`_send_data()` logged nothing, received frames were only logged sporadically during the handshake. Fixed both: every poll failure's error message now always includes the exception type name, and every BinkP poll now captures a full, timestamped, frame-by-frame transcript (what was sent, what was received, connect/disconnect) into a new `EchomailPollLog.transcript` column — viewable from a new "Transcript" link on the Poll Logs admin page, so a sysop can see exactly what happened on the wire without needing server SSH/journalctl access at all. The transcript survives even when a session fails partway through (captured into a list owned by the poller, not the BinkP client object itself, since the client goes out of scope with the exception). Capped in size (~500 lines / ~100KB) since there's no retention/cleanup for poll-log rows. Client-side only for now (a sysop's own install polling out) — the inbound/server side has no equivalent session model today, noted as a natural follow-up. 14 new tests in `tests/test_binkp_transcript.py`.

## v1.0b2.46 — Fill the two docs gaps flagged after v1.0b2.45 (July 2026)

- WIKI: new dedicated `Notifications` page — the `[[Notifications]]` wiki-link had pointed to a nonexistent page since before today's changes; now covers all five user-facing kinds and all five sysop admin-review kinds in one place. `sysop-control-panel` page updated to link to it, and its "Hub Management" section now mentions the second (generic BinkP+QWK) join-request queue, the new Join Form tab, the QWK "Subscribe to All" button, and that new applications trigger a notification.

## v1.0b2.45 — Document today's QWK/notification/join-form changes (July 2026)

- DOCS: `docs/06-echomail.md` and `docs/20-federation.md` updated to describe the current self-referential-poll-skip behavior (no poll-log row created at all, as of v1.0b2.41 — was previously described as a `status='skipped'` log entry), QWK node conference subscription management including the new "Subscribe to All" button (v1.0b2.40), and the new public "apply to join this network" form end to end (v1.0b2.43-44) — gating, applicant flow, infopack upload + rules-text auto-pick, the Join Requests review queue, and the approval logic. `docs/02-sysop-daily-ops.md` gets a new "Admin notifications" section covering all five admin-review notification kinds (v1.0b2.42). Wiki pages `qwk` and `anothernetwork` updated to match, including fixing a self-introduced contradiction (the QWK page said "no self-service BinkP flow exists" right next to a new bullet describing exactly that).

## v1.0b2.44 — Make the network join form discoverable (July 2026)

- FIX: the new `/join/` page (v1.0b2.43) had no link to it anywhere except Hub Management's own "view live" button — a real visitor had no way to find it. Added a "Join Our Network" entry to the Tools nav dropdown, shown only to logged-in users (deliberately not exposed to anonymous visitors) and only when the feature is actually reachable (hub install + enabled), via a new `network_join_enabled` context processor. 1 new test covering all four combinations of logged-in/enabled state.

## v1.0b2.43 — Public "apply to join this network" form (July 2026)

- FEATURE: a hub sysop can now enable a public web page (`/join/`) where anyone can apply to join their echomail network — read the rules, download the full infopack, check a box confirming they read the rules, then submit an application covering both BinkP and QWK transports in one form (leave either section blank if not applicable). Configured from Hub Management's new "Join Form" tab: enable the feature, upload a single infopack zip, and the system auto-picks the largest `.txt` member inside as the rules text (correctly picks `annet.txt` over `readme.txt`/`systems.txt` in a real-world infopack), with a dropdown to override if the auto-pick is wrong. Applications land in a new "Join Requests" review queue (mirrors the existing QWK node request flow) and notify every admin via the notification system built earlier this release cycle. On approval, creates a `BinkPNode` and/or `QWKNode` — whichever transport(s) the applicant filled in, zero, one, or both — with hub-generated passwords (never applicant-supplied, matching the existing QWK approval rule), and emails the applicant their credentials if SMTP relay is configured (falls back to a flash message telling the sysop to relay them manually if not). Denials can include a reason, also emailed if SMTP is set up. Rate-limited per IP to guard the fully public, unauthenticated form against spam. Not hardcoded to any one network — any sysop running their own ANetBBS hub can enable this and upload their own infopack. 29 new tests across `tests/test_network_join.py` and extensions to `tests/test_admin_review_notifications.py`.

## v1.0b2.42 — Admin notifications for things needing sysop review (July 2026)

- FEATURE: the sysop asked "for any incoming request, I should have a notification... I don't know unless I go looking for it," specifically citing MSP federation join requests and QWK node applications. A research pass confirmed the in-app notification system (`anetbbs/features/notify.py`) already existed with per-kind user preferences but had zero call sites for any admin-review event — a sysop had to manually visit each admin page to discover any of them. Added a `notify_admins(kind, ...)` helper (notifies every `is_admin=True` user, each still honoring their own per-kind `notify_prefs` toggle) and wired it into four gaps at their actual creation site: MSP federation registry join requests (fires once the registrant verifies their contact email, not at the earlier unverified-registration step, since that's the first point actually actionable for the sysop), QWK node applications (both the hub-facing API route and the direct-terminal-write path used when someone applies while connected straight to the hub), new users pending NUV approval (deliberately not the separate email-verification path, which is fully self-service and would just be noise), and newly-discovered unknown/bad echomail areas (fires once per area, not once per message, so a large batch of misdirected mail can't flood the inbox). New admin-only preference toggles appear in Notification Settings, visible only to admin accounts. 7 new tests in `tests/test_admin_review_notifications.py`.

## v1.0b2.41 — Stop self-referential polls from flooding the poll log (July 2026)

- FIX (live-caught): a hub's own network row pointing at itself (e.g. a QWK hub's local "ANotherNetwork" rows) gets correctly skipped by the poller every minute, but was logging a full `EchomailPollLog` row each time — dozens of identical "skipped" entries drowned out real poll activity in the admin UI within 20 minutes of uptime. This is static, unchanging configuration state, not a real poll attempt, so it no longer creates a log row at all; a single debug-level line in the application log (invisible at the default INFO level) is enough for anyone actually debugging this specific thing.

## v1.0b2.40 — Fix total QWK message loss on every packet + Subscribe to All (July 2026)

- FIX (live-caught, critical, affects every message to every QWK node ever): `_build_messages_dat()` in `anetbbs/echomail/qwk_hub_ftp.py` wrote every message-header field from byte 113 onward at the wrong offset compared to what `_parse_messages_dat()` (the reading side, verified against real Dove-Net packets) expects — including using binary encoding for the block-count field where the real QWK format requires 6-char ASCII text. The conference number specifically landed 3 bytes off from where the reader looks for it, so every message's conference number came back as garbage on read — which never matched a real conference in CONTROL.DAT, so every single message was silently dropped *inside the parser itself*, before ever reaching the poller's "received" count. Net effect: a QWK poll would report clean "success" with 0 messages received and no error at all, no matter how many real messages existed in the subscribed areas on the hub — every message, to every node, always silently vanished on the wire. Found live testing the real ANotherNetwork QWK flow end-to-end, after confirming real messages existed on the hub and a fresh high-water mark, ruling out every other explanation first. Fixed by rewriting the header to the correct, verified byte layout. 3 new tests in `tests/test_qwk_packet_roundtrip.py` doing a full hub-write → client-parse round trip, verified to reproduce total message loss before the fix and pass after.
- FEATURE: Hub Management → QWK Nodes → node detail gets a "Subscribe to All" button, adding every active area on QWK-transport networks in one click instead of one area at a time. Deliberately scoped to QWK-type networks only — a QWK node has no business receiving areas that only exist on a BinkP-only network. 3 new tests in `tests/test_qwk_subscribe_all.py`.

## v1.0b2.39 — Fix QWK node FTP home directory path-doubling (July 2026)

- FIX (live-caught, real bug, affects every QWK node): `AnetbbsAuthorizer.validate_authentication()` in `anetbbs/ftp/server.py` computed a QWK node's FTP home directory by passing `self.qwk_root` (already `<DATA_DIR>/qwk-hub`, set at construction) into `ensure_node_dir()` — which appends its own `qwk-hub` segment and expects the bare `DATA_DIR` instead. This doubled the path to `<DATA_DIR>/qwk-hub/qwk-hub/<PACKET_ID>`, one level away from where `on_login()`'s `generate_node_packet()` (which correctly receives the bare `DATA_DIR`) actually writes the packet. Net effect: login always succeeded, packet generation always "succeeded" with no exception, but the FTP client's session was rooted at an empty directory no matter how correctly everything else was configured — `RETR` always failed with `550 No such file or directory`. Found live, testing the real ANotherNetwork QWK flow end-to-end between bbs.a-net.fyi and a Pi peer install, after ruling out the network, Python version, eventlet, and the stored credentials as the cause one at a time. Fixed by computing the home directory directly from `self.qwk_root` without the extra `ensure_node_dir()` call. 2 new tests in `tests/test_ftp_qwk_node_home_dir.py`, verified to reproduce the exact bug before the fix and pass after.

## v1.0b2.38 — Better diagnostics for QWK download failures (July 2026)

- FIX: `QWKClient.poll()`'s error message included only `str(exc)`, which is empty for some exception types (e.g. a bare connection timeout) — producing an uninformative "QWK: failed to download packet:" with nothing after the colon. Hit live trying to diagnose a real QWK poll failure between bbs.a-net.fyi and a Pi peer install, where the FTP connection itself was confirmed reachable (a raw `curl -v ftp://` test succeeded end-to-end, including passive-mode data transfer) but the poller's own connection attempt failed instantly with no useful detail and no `_ftp_download()` login log line ever appearing. Fixed to always include the exception's type name alongside its message, so the next occurrence of whatever's actually failing shows up clearly in the poll log instead of a bare colon. 2 new tests in `tests/test_qwk_error_detail.py`.

## v1.0b2.37 — Fix QWK node application crash + FTP login confusion (July 2026)

- FIX (live-caught, real bug): checking QWK node application status a second time (via the terminal's Echomail → "A" apply screen, after already being approved) crashed with `DetachedInstanceError` at `req.created_at.strftime(...)` in `_show_qwk_request_status()`. Root cause: `_apply_qwk_node()` queried the existing `QWKNodeRequest` row inside one `with _app().app_context():` block, then passed that ORM object to `_show_qwk_request_status()` *after* the block (and its DB session) had already closed — and that function's own internal hub-refresh commit (in a second, nested `app_context()`) expired the row's attributes, so touching them afterward with no active session raised the error instead of silently re-fetching. Fixed by having `_show_qwk_request_status()` take an id and do one fresh query inside a single, consistent `app_context`, extracting every field it needs into plain local variables before that context closes. 3 new tests in `tests/test_qwk_apply_status_detached_instance.py`.
- FIX (live-caught, real bug): the QWK poller used only the `qwk_username` field for the FTP login username, with no fallback — but QNET-FTP-style hubs (like ANotherNetwork) authenticate using the Packet ID as the username. A sysop who filled in Packet ID but left QWK Username blank (an easy mistake, since the old form label didn't explain the two fields needed to match) got a silent, always-failing login with an unhelpfully empty error message. Fixed with a defensive fallback — blank `qwk_username` now falls back to `qwk_packet_id`, which can only help since a blank username always failed anyway. Also rewrote the `QWK Username` field's label in Admin → Echomail Networks to explain this requirement explicitly, and updated the applicant-facing approval screen to tell the sysop to set both fields to the same value. 2 new tests in `tests/test_qwk_username_fallback.py`.

## v1.0b2.36 — Fix unreachable QWK node application on a fresh install (July 2026)

- FIX (live-caught, real bug): the terminal "Echomail Networks" screen (`list_echo_areas()` in `anetbbs/features/bbs_ui.py`) returned immediately with "No echomail areas configured." whenever a sysop had zero active networks and zero subscribed areas — which is exactly the state of every fresh install, since both `EchomailNetwork.is_active` and `EchoArea.is_subscribed` default to false on seed. That early return happened *before* the code ever reached the "A = Apply for ANotherNetwork QWK node" option a few lines down, making the apply flow completely unreachable for precisely the sysop it exists for: someone who hasn't joined the network yet. Found live by Jerry testing his Pi peer install after updating and trying to apply for a node. Fixed by no longer bailing out early — the screen now always offers the Apply option, showing a "no areas configured yet — apply below" message instead of just quitting when there's nothing to list. 2 new tests in `tests/test_echo_areas_empty_apply_option.py`, verified to reproduce the exact bug before the fix and pass after.

## v1.0b2.35 — Full documentation + wiki accuracy pass (July 2026)

A ground-up re-audit of every doc file and all 43 wiki pages (5 parallel review passes, then 4 parallel fix passes), covering both correctness and completeness — not just "is this wrong" but "is everything that exists actually documented."

- FIX (real bug, not just docs): `install.sh` hardcoded `MRC_BRIDGE_PORT=8080` unconditionally, while `test`/`behind` install modes default `WEB_PORT` to 8080 too — a fresh Pi/hobbyist-mode install with the MRC bridge left at its (also-default) enabled setting would have gunicorn and the MRC bridge both trying to bind `127.0.0.1:8080`. Fixed by deriving the MRC bridge's default port from `WEB_PORT+1` instead of a fixed value, computed once and reused for both the `.env` template and the bridge's own `config.json`.
- FIX (real bug): the preflight check meant to catch exactly this class of collision after the fact (`anetbbs/web/preflight.py`) was itself broken — it tried to regex a port number out of the MRC bridge's systemd `ExecStart` line, but that line never contains one (the bridge reads its port from its own `config.json`, not a CLI arg), so the check silently never fired. Fixed to read `config.json`'s `web_listen_port` directly. 4 new tests in `tests/test_preflight_port_collision.py`.
- FIX (docs, real mistakes found and corrected): wrong config var cited for terminal-node count (`GAMES_MAX_NODES` → `BBS_NODES`) in the install doc; `/auth/register`'s rate limit misclassified as in-memory when it's actually DB-backed; missing Finger/BinkP systemd unit instructions and an FTP setup walkthrough in the manual-install doc; wrong systemd unit name (`anetbbs-web` → `anetbbs`) for the FTP capability fix in two places; `hub_generate_nodelist` missing from the scheduled-events handler table; two Synchronet-compat-shim doc claims that flatly contradicted the actual shim code (`user.security` flags, `bbs.exec()`/`bbs.menu()`).
- FIX (wiki, real mistakes): wrong Telnet/SSH port numbers on the two most-viewed pages (`home`, `features` said TCP 23/22; real defaults are 2233/2234); a fabricated claim of a real Gemini-protocol (TCP 1965) listener that doesn't exist; a wrong admin path for QWK hub management; a wrong field list for bulletins (claimed Title/Body/Active/Order — real fields are Title/Content/Pinned/Expires-At); `journalctl -u anetbbs-telnet` (retired unit) instead of the real unified `anetbbs` service, in two places; a fictional `flask db migrate`/alembic migration workflow that contradicts the wiki's own (correct) Architecture page describing the real auto-sweep mechanism; a reference to a nonexistent `anetbbs/seed_data.py`; a wiki page (`irc-bridge`) describing an admin-configured Chat↔IRC bridge that doesn't exist, rewritten to describe the real `MrcIrcBridge` (no web UI, one MRC room ↔ one IRC channel, run via a systemd template unit).
- NEW CONTENT (docs + wiki): substantial coverage added for previously-undocumented-or-barely-documented features — ANotherNetwork as a named, pre-configured network (26 message + 9 file echo areas, new dedicated wiki page), the `EchomailNetwork.ftn_domain` field, AreaFix, netmail as a first-class concept, the Hub Management "Generation & Distribution" panel (nodelist/QWK preview/TIC distribution), auto-hatch-on-upload for network-attached file areas, the achievements system, the casino/wallet economy, the ebook reader, the separate `anetbbs-install`/`anetbbs-upgrade` Python installer toolchain (documented as a lightweight alternative to `install.sh`, not brought to parity — its real gaps vs. `install.sh` are disclosed), a new wiki page for the real per-user `/irc/` personal IRC client, and a Docker-containers disclaimer noting the path is unit-tested with mocks but not yet run against a real Docker daemon.

## v1.0b2.34 — Hub "Generation & Distribution" admin UI (July 2026)

- FEATURE: Hub Management (`/admin/echomail/hub/`) gets a new "Generation & Distribution" panel with three tabs:
  - **Nodelist**: manual "Generate Now" button + a weekly-by-default schedule (via the existing `ScheduledEvent` framework), publishing the ANotherNetwork nodelist directly into the `ANN.FILES.NODELIST` file area (replacing the prior copy) so peers pull it like any other file-echo entry, instead of only the pre-existing public HTTP link. New `EchomailNetwork`-agnostic handler `hub_generate_nodelist`, only seeded on installs with `REGISTRY_MODE_ENABLED` (a peer install has no downstream `BinkPNode` data of its own to publish).
  - **QWK Packets**: a "Preview" button per node builds and downloads a packet on demand for testing/verification — deliberately does not call `mark_qwk_sent()`, so previewing never consumes the node's real unsent-message queue.
  - **TIC / File Distribution**: status dashboard (pending/failed counts) for a new real gap that got fixed alongside this — files uploaded to any `ANN.FILES.*` (or any other network-attached) file area now automatically queue for TIC distribution to every subscribed peer (`hatch_local_file()` in `echomail/tic.py`, mirroring the existing inbound-TIC hatch-out fan-out logic), across all 4 upload code paths (regular upload, sysop direct upload, smart-upload, and file-queue approval). Purely local file areas (`network_id` is `None`) are unaffected.
- 6 new tests in `tests/test_hub_generation_features.py` covering nodelist replace-on-generate, the ScheduledEvent hub-only seeding gate, hatch fan-out to multiple peers, a real route-level upload test confirming auto-hatch end-to-end, confirming local-only areas don't hatch, and confirming QWK preview doesn't touch the high-water mark.

## v1.0b2.33 — ANotherNetwork file areas + real infopack (July 2026)

- FEATURE: ANotherNetwork gets 9 new TIC file-echo areas (`ANN.FILES.NODELIST`, `.INFOPACK`, `.BBSSOFT`, `.DOORS`, `.EBOOKS`, `.LINUX`, `.RETRO`, `.ANSIART`, `.TEST`), seeded automatically the same way the 26 message areas already are. `ANN.FILES.NODELIST` is flagged `is_nodelist_source` so inbound TICs there auto-populate the nodelist. Found and fixed a real bug while wiring this up: unlike `EchoArea.tag`, `FileArea.tag` has a database-level `UNIQUE` constraint, so file areas attach to the BinkP network entry only (not duplicated across both BinkP and QWK entries the way message areas are) — TIC distribution is a BinkP-native mechanism anyway.
- New distributable infopack for other sysops to apply with: `annetinfo.zip` (goals, rules, full area list, application form, a new CP437 ANSI banner) and `annetareas.zip` (machine-readable `.na` area lists for both message and file areas), built from the project's real seed data rather than placeholder text.
- FIX (docs): README's ANotherNetwork description had a stale category count (said 8, actual is 9 including Test) and didn't mention the new file areas.
- FEATURE: `EchomailNetwork` gets a new optional `ftn_domain` field — the qualified BinkP address's domain suffix (`addr@domain`) previously always derived from the network's display name, truncated/lowercased to 8 chars, which produced an awkward `anothern` for "ANotherNetwork". Set explicitly to `anet` in the seed data; exposed as an editable field in Admin → Echomail Networks for any network. Blank/NULL falls back to the old name-derived behavior, so existing networks are unaffected.
- FIX (live-caught, real bug not just config confusion): activating the seeded "ANotherNetwork"/"ANotherNetwork (QWK)" network rows *on the hub itself* (bbs.a-net.fyi) made the poller try dialing its own address every 60 seconds, failing both BinkP auth and QWK download — and simply deactivating those rows wasn't a safe workaround either, since the terminal echomail menu (`list_echo_areas`) also filters areas by the network's `is_active` flag, which would have hidden the areas from the hub's own local users too. Fixed properly: the poller now detects when a network's configured address matches its own hub address (BinkP: `our_address == hub_address`) or own public host (QWK: `qwk_host` matches `BBS_PUBLIC_HOST`/`BBS_DOMAIN`) and skips the dial-out with a clear "skipped" poll-log entry instead of attempting and logging a failure — areas stay visible either way, since visibility was never the thing that needed to change. 3 new tests in `tests/test_poller_self_referential.py`, verified to reproduce the exact live error before the fix and pass after.
## v1.0b2.32 — Sysop-specific paths scrubbed from shared code (July 2026)

- FIX: several hardcoded fallback paths/usernames from the maintainer's own install had leaked into shared code (drop-file path resolution, default gallery seed data, installer/upgrade-wizard fallbacks, an IRC CTCP reply URL, the sudoers template) — real bugs that would misconfigure a different sysop's install if the normal config lookup ever failed, not just cosmetic. Fixed all of them; also scrubbed the same path from the in-app tutorial, the seeded wiki content, and the GitHub issue template.
- FIX: 3 systemd unit files (`anetbbs-binkp.service`, `anetbbs-finger.service`, `anetbbs-mrc-irc-bridge@.service`) were missed in the first pass and still had the maintainer's own `User=`/path hardcoded — found via a follow-up audit. Fixed to match every other unit's generic `anetbbs`/`/opt/anetbbs` convention.
- FIX: a stray third-party email address (copied from wherever a bundled tool was originally adapted from) in `tools/bbs-files-tool.py`'s help text pointed sysops at an unrelated stranger instead of this project's own issue tracker.
- FIX: `tests/msp_loopback_test.py` (a manual diagnostic script, not a real test) matched pytest's default test-file naming pattern and had no `if __name__ == '__main__':` guard — every `pytest tests/` run was silently starting real MSP/SYSTAT network listeners and mutating process-wide environment variables as an import-time side effect. Renamed to `msp_loopback_check.py` (no longer matches pytest's collection pattern) and gated its body behind a proper `main()`/`__main__` guard. Also fixed a second, unrelated hardcoded absolute path in the same file left over from a different machine's mount point.
- FIX: two more test files (`test_ebook_terminal_menu.py`, `test_qwk_hub_gating.py`) had the same `os.environ['FLASK_ENV']` leak pattern as last version's `TestingConfig.SQLALCHEMY_DATABASE_URI` fix — set on every test run, never restored. Fixed both.
- FIX: `anetbbs/web/healthz.py` and `anetbbs/web/preflight.py` checked a `FINGER_PORT` config key that was never actually set anywhere (the real setting is `FINGER_LISTEN_PORT`), so the finger service's health check/preflight validation silently ignored any non-default port a sysop configured. Added `FINGER_LISTEN_PORT`/`FINGER_LISTEN_HOST` to `Config` and fixed both call sites.
- FIX (docs): `docs/00-overview.md`'s table of contents was missing links to two existing doc files (`17-development.md`, `20-federation.md`); README's wiki page count was stale (41 → actual 43); README's Doors feature list didn't mention the bundled `door_rlogin`/A-Net Online game server; a few wiki pages used dead internal `[[Page]]`-style links for external tools (SyncTERM, NetRunner, DOSBox-staging) instead of the real external links used elsewhere in the same content.
- FIX (docs, full accuracy pass): a broad cross-check of every doc file, the in-app sysop tutorial, and the seeded wiki against the actual current code turned up a large batch of stale/wrong content, most notably: `docs/12-upgrading.md` and `docs/01-installing.md` described an entirely stale install/upgrade flow (wrong backup path — `/tmp/anetbbs-backup-*`, not next to the install; wrong service names — the unified `anetbbs.service` replaced the legacy split `anetbbs-telnet`/`anetbbs-ssh` a while ago; a wizard prompt list missing half the real prompts; automatic rollback described as an interactive "offer"); the sudoers install instructions in `docs/02-sysop-daily-ops.md`, the in-app tutorial, and two wiki pages all broke when the sudoers template's placeholder was renamed to `__SERVICE_USER__` earlier in this same version — a plain `cp` now installs a non-functional rule, fixed everywhere to show the required `sed` substitution; the wiki's Themes page listed five theme names that don't exist anywhere in the codebase (real theme count is 9, not the fabricated list); the wiki's Doors page and `docs/17-development.md` undercounted door types (7 vs the real 10, missing `door_dosemu`/`door_telnet`/`door_dos_browser`); a wiki page referenced a `tools/db_backup.sh` script that doesn't exist; `docs/INSTALL.md` was missing the `anetbbs` system-user creation step its own systemd section depends on and had a wrong `apt` package list; a real (non-doc) bug found along the way: `anetbbs/web/healthz.py`/`preflight.py` checked a `FINGER_PORT` config key that was never actually set anywhere, silently ignoring any custom Finger port a sysop configured — fixed by adding the real `FINGER_LISTEN_PORT`/`FINGER_LISTEN_HOST` to `Config`.
- No behavior change for the maintainer's own existing install from any of the above.

## v1.0b2.31 — Docker support (July 2026)

- FEATURE: Docker deployment alongside the traditional install.sh/systemd path — single-container quick start (supervisord-managed) and a docker-compose "correct way" (one container per service), both built multi-arch (amd64+arm64). See `docs/22-containers.md`.
- FEATURE: Sysop Control Panel restart/upgrade works under docker-compose via a Docker-socket backend instead of systemctl; one-click self-upgrade pulls a new image tag and recreates containers.
- FIX (hardening, also benefits bare metal): SQLite WAL mode + busy-timeout (`anetbbs/models.py`); `MSP_PORT`/`SYSTAT_PORT` now environment-overridable.
- FIX: the admin **DB Backup download button** streamed the SQLite file with a plain copy and no checkpoint — under the new WAL mode, recent writes could sit only in the `-wal` file and be missed. Now forces `PRAGMA wal_checkpoint(TRUNCATE)` first. `update.sh`'s own automated backups were already safe (`sqlite3 .backup`).
- FIX (critical, live-caught): **reading any echomail message via terminal (SSH/telnet/rlogin) failed with "Menu action failed (see server log)"**. Root cause: the read-only message viewer (`_ViewerScreen.draw_text()` in `anedit.py`) was never updated to accept the `misspell` parameter added when spell-check shipped (v1.0b2.20) — the shared redraw code the viewer inherits from the editor always passes it. Latent since v1.0b2.20; nobody had tried reading an echomail message via terminal since. Fixed + regression-tested (`tests/test_aneview_draw_text.py`, verified it reproduces the exact live traceback before the fix and passes after).
- FIX (release hygiene): `build-release.sh` rewritten to build from git's own tracked/ignored file list instead of a manually maintained `--exclude` denylist, which had let `bbs.log` (containing a plaintext admin password from a local test boot) leak into 7 prior release tarballs, plus assorted stray test-scratch files. See `.gitignore` for the accompanying rule additions.
- 30 new tests. Docker builds/runs themselves still need verification with real Docker access — not available in the environment this was built in.

## v1.0b2.30 — Federation "Register with Hub" nav fix (July 2026)

- FIX: the "Register this BBS with a federation hub" admin page existed but had no link anywhere in the UI. Added a **Register with Hub** card to Admin → Network.
- FIX: the v1.0b2.29 hub-gating fix left the **Hub Management** card linking to a now-404 blueprint on every non-hub install. Hidden unless `REGISTRY_MODE_ENABLED=true`.
- 2 new tests in `tests/test_qwk_hub_gating.py`.

## v1.0b2.29 — Local chat, terminal colors, QWK/federation hub gating (July 2026)

- FIX (critical): terminal **Local Chat** (Chat → 1) was a stub that only echoed your own message back to yourself, never broadcasting to other nodes. Now delegates to the already-working real-time broadcast/queue system in `multinode.py` (previously only reachable via a dead, unwired `multinode` menu action).
- FIX (critical): `broadcast()`'s self-exclusion compared by username, not by node — two nodes logged in under the *same* account (e.g. testing with one account on two terminals) both matched the check, so the message was dropped for everyone. Now excludes by the specific sending node's slot.
- FIX (critical): MagiTerm, NetRunner, and PuTTY showed no color on most screens (wall, file areas, boards, PMs, bulletins, MRC chat, pre-login welcome, games list) while the main menu rendered fine. The main menu's `.ans` art uses the classic ANSI.SYS bright-color convention (bold + base color); nearly everywhere else used the newer "aixterm" 90-97 SGR range with no bold, which those three clients don't recognize (SyncTerm supports both, masking the bug there). Converted every affected color table/constant across 10 files to the classic convention.
- FIX (critical): the terminal QWK-node-application wizard wrote to whichever BBS install ran it instead of the real hub, and the hub admin review queue was exposed on every install with no gate ("all the sysops try to put in for a node and it goes to their system"). Added a real hub-side API (`/qwkhub/apply`, `/qwkhub/status/<token>`) and gated the hub-admin blueprint behind `REGISTRY_MODE_ENABLED`. **The hub install needs `REGISTRY_MODE_ENABLED=true` set in its `.env`.**
- FIX: the federation self-registration wizard always showed "success" even when `REGISTRY_URL` was blank and nothing was sent. `REGISTRY_URL` now defaults to `https://bbs.a-net.fyi`, and the registration tick reports back real success/failure.
- 9 new tests in `tests/test_multinode_chat.py`, plus manual verification against two real concurrent telnet sessions.
- 4 new tests in `tests/test_ansi_color_compat.py`, plus manual verification against a live telnet session.
- 10 new tests in `tests/test_qwk_hub_gating.py`, 4 in `tests/test_registry_tick_status.py`.

## v1.0b2.28 — A-Net Game Server bundled by default + Ebook Reader (July 2026)

- FEATURE: A-Net Online's rlogin game server (450+ door games) now ships pre-installed and active like LORD does. Each install gets its own randomly generated password and BBS tag on first boot — private to that BBS, never in the public source, and never regenerated afterward. Sysops can change either any time at Admin → Door Games → A-Net Game Server.
- FEATURE: new door_rlogin **BBS Tag** field — the tag now has its own admin-form field instead of being hyphen-joined into the user-template text, purely for clarity (the wire format itself, `username-TAG`, is unchanged).
- FEATURE: new built-in web game, **Ebook Reader** — search + read free Project Gutenberg classics in a book-styled reading view (serif type, page-turn navigation), with bookmarks, reading history, and download-to-`.txt`. Also has a **terminal version** (telnet/SSH/rlogin, main-menu hotkey `K`) with the same feature set.
- FEATURE: **per-front-end Web/Terminal toggle** on every game (Admin → Games) — lets a sysop run the Ebook Reader as terminal-only, web-only, or both.
- FIX: bundled-door seeding hardcoded `max_nodes=1` for every stock door — wrong for a 20-slot remote multiplayer server. Now respects a per-door override.
- FIX: `lrzsz` (ZMODEM, used by every terminal file transfer) was missing from `install.sh`'s required packages — fresh installs had no working terminal downloads out of the box. Now installed automatically. Existing installs upgrading via `update.sh` need `sudo apt-get install -y lrzsz` once by hand (update.sh doesn't manage system packages).

## v1.0b2.27 — Telnet door game servers (July 2026)

- FEATURE: new `door_telnet` game type for external telnet-only game servers (e.g. TWGS — Trade Wars Game Server, like `game.a-net-online.lol:2002`, A-Net Online's own TWGS). No pre-authentication handshake like `door_rlogin` — just `host:port`, user logs in interactively on the remote side. Works on both web and terminal doors, mirrors the rlogin door architecture. Includes a small RFC 854 option-negotiation filter so the remote server doesn't hang and negotiation bytes never reach the player's screen. Configure via Admin → Games → Game Type → "Telnet Door Server".

## v1.0b2.26 — MRC: terminal fixes + stale-session cleanup (July 2026)

- FIX: `/mentions` always showed 0 — mention detection was wired to event types the bridge never sends; moved to the real `mrc_message` path. Status-bar `!N` indicator now works live too.
- FIX: messages up to 140 chars could get silently truncated to ~120 — terminal client now accounts for the bridge's handle-prefix overhead and splits long messages into `(1/2)`/`(2/2)` chunks instead of losing the tail.
- FIX: mention indicator was reverse-video red, illegible on some terminals — switched to explicit fg/bg colors.
- FIX: `/mentions` output misaligned on wrap — restructured into a header line + indented body line per mention.
- CHANGE: `/help` and `/helpserver` swapped — `/help` now asks the hub for its own help, `/helpserver` shows the client's local command list.
- FIX: outgoing text color never persisted across reconnects — now restores on join and saves via `set_style` on change.
- FIX: Tab nick-completion gave no feedback on zero matches and could dump an unbounded candidate list — capped at 12 with a "+N more" hint. Also fixed a real bug where a visibly-present user couldn't be found: `/who`'s roster is comma-separated on the wire, but the parser was splitting on whitespace. Bridge now also refreshes the roster on `/who`.
- FIX (critical): a mid-round version of the Tab-completion fix briefly deadlocked terminal MRC solid on the next Tab press. Fixed, covered by a timeout-guarded test.
- FIX (bridge): a dropped connection left MRC sessions looking permanently logged in, causing "you can only be logged on once" from the upstream hub. Added a WebSocket heartbeat so the existing (correct) cleanup logic actually runs.

## v1.0b2.25 — Paginate large file areas (July 2026)

- FEATURE: file area listings are now paginated (50 files/page) instead of rendering the entire area on one page — same fix pattern as the CHANGELOG page's own pagination (v1.0b2.22), reported after a 7,000+ file door-games area took a while to load. Sort (name/size/date) applies before slicing so page contents stay correct, and pagination links preserve the active sort. New shared `ListPagination` helper (`anetbbs/web/list_pagination.py`) with ellipsis-truncated page links, since a large area can span far more pages than the CHANGELOG ever will.

## v1.0b2.24 — Configurable login auto-ban (July 2026)

- FEATURE: the login auto-ban is now sysop-configurable from **Admin → IP Bans** instead of a hardcoded permanent-only trigger. Reported by Firehawke (feature request 2026-07-03): 10 failed logins in 5 minutes triggered a permanent IP ban with no way to configure the threshold, duration, or disable it — caught one of their test users off guard. Now configurable: attempt limit, time window, ban duration in hours (0 = permanent, still available), and an enable/disable switch. Default ban duration changed from permanent to 1 hour, matching the proposed resolution; the 10-attempts/5-minute trigger threshold is unchanged by default but now editable. New `AutoBanConfig` singleton model, settings card added to the existing IP Bans admin page. 8 new tests in `tests/test_auto_ban.py`.

## v1.0b2.23 — Multi-screen welcome/goodbye/newuser sequences (July 2026)

- FEATURE: sysops can drop in multiple variants of any lifecycle ANSI screen (`welcome`, `goodbye`, `newuser`, or a custom slot) and ANetBBS shows all of them, in order, every login — the classic Synchronet `logon1.ans`/`logon2.ans`/`logon3.ans` multi-screen convention. `welcome132.ans` is shown first; add `welcome132_2.ans`, `welcome132_3.ans`, etc. and they display right after, same login — works for the `data/text/<slot>.ans` override and the bundled stock screens, all three width modes. Each variant controls its own `@PAUSE@`; nothing is auto-inserted between screens. A single file (the common case) behaves exactly as before, no persisted state of any kind. Prefer one random pick instead of the whole sequence? Use `_ran` naming (`welcome_ran.ans`, `welcome_2_ran.ans`, ...) — shows just one, chosen at random; wins over plain numbered naming if both exist. See `docs/04-ansi-screens.md`.
- FIX (pre-existing, unrelated to the feature above — found while testing on the Pi3): an active `BbsAnsiScreen` DB row with an empty body blocked the fallback to the bundled stock screen, so the screen silently showed nothing at all instead of falling back. Now an empty-but-active row is treated the same as no row.
- FIX: `welcome132.ans` and `welcome.ans` were the last stock screens with a box border still on them, plus (for the 132-col one) a duplicated Sysop/Version line — rebuilt both borderless to match `goodbye132.ans`/`newuser132.ans`'s style. Added `@PAUSE@` to all three welcome variants and to `goodbye.ans` — none of them had it, so the banner rendered and immediately fell through to the next prompt, easy to miss at normal typing speed.

## v1.0b2.22 — Paginate the web Changelog page (July 2026)

- FEATURE: `/docs/CHANGELOG` was rendering this entire, ever-growing file as one markdown pass on every load, getting slower release after release. Now paginated at 15 version-entries per page (newest first), with Prev/Next + page-number navigation. Other `/docs/*` pages are unaffected. Verified against the real route: page 1 has the newest entry, page 2 differs, out-of-range page numbers clamp instead of erroring.
- Also fixed the "Current release" line above, which was stuck at `v1.0b1.6` for a long time — added a note to the version-bump checklist so it's updated alongside every new entry from now on.

## v1.0b2.21 — Fix: pyspellchecker was never actually installed (July 2026)

- FIX: `setup.py`'s `install_requires` is a separate, hand-maintained list from `requirements.txt` — `update.sh`/`install.sh` install via `pip install -e .`, which reads `setup.py`. v1.0b2.20 added `pyspellchecker` to `requirements.txt` only, so it never actually installed through the normal deploy path on any environment. The feature degraded silently (by design — missing spellchecker just means the feature is unavailable, not a crash) rather than erroring, so it looked like a UI bug ("misspelled words aren't underlined") rather than a missing-dependency bug until traced back. Added `pyspellchecker>=0.8.0` to `setup.py` too.

## v1.0b2.20 — Spell check + clickable URLs, web and terminal (July 2026)

- FEATURE: `anetbbs/features/anedit.py` (terminal message editor) gains live spell check — misspelled words underline as you type, and `/spell` (`/sp`) jumps to the next one with a suggestion box (0-5=replace, N=skip, Esc=cancel). Quoted lines and ALL-CAPS jargon (SSH, QWK, ANSI, ...) are excluded from checking. New dependency: `pyspellchecker` (pure Python, bundled dictionary, no network, degrades silently if missing — same pattern as the optional markdown/bleach deps).
- FEATURE: Web compose forms (PM, board post, echomail, netmail) now set `spellcheck="true"` explicitly on their textareas, enabling the browser's native spellcheck (red squiggly underline, right-click suggestions).
- FEATURE: `https://` URLs are now clickable everywhere a message body is shown. Web: `render_msg.py`'s `msgbody`/`msgbody_rich` filters wrap plain URLs in `<a href target="_blank">` (board posts still auto-embed image URLs as `<img>`, that's unchanged — just extended to non-image links too). Terminal: `bbs_ui.py` wraps URLs in OSC 8 hyperlink escapes when displaying board threads, PM bodies, and InterBBS instant messages — clickable on terminals that support it (Windows Terminal, iTerm2, kitty, gnome-terminal), silently ignored (URL text still shows) on ones that don't (SyncTERM and most classic BBS terminal clients).
- Verified end-to-end against a real running instance (in-memory-DB Flask test client hitting the actual routes/templates) as well as direct unit tests of every new code path, including an explicit check that the spell-check suggestion picker can't recurse unboundedly on a document full of unrecognised words.

## v1.0b2.19 — Fix GitHub #3 for real: the active v2 thread reader was never patched (July 2026)

- FIX: GitHub issue #3 (terminal message reader truncating long paragraphs instead of word-wrapping them) was marked fixed in v1.0b2.15's changelog, but a sysop retested against v1.0b2.18 and found it still truncating — with an exact root-cause diagnosis and a tested patch attached to the issue. The v1.0b2.15 fix touched `read_thread()` in `anetbbs/features/bbs_ui.py`, but that method is shadowed at module import time: near the bottom of the same file, `BBSMenuUI.list_threads = _list_threads_v2` and `BBSMenuUI.read_thread_v2 = _read_thread_v2` monkey-patch the class, replacing the class-body methods with newer "v2" versions that add inline `N`=new-thread and `R`=reply support. The board menu actually calls `list_threads` → `_list_threads_v2` → `read_thread_v2` → `_read_thread_v2`, which still had the original bug: `for line in (p['content'] or '').splitlines(): ... line[:_line_w]`, silently discarding everything past the terminal width on any paragraph without manual line breaks. Rewrote `_read_thread_v2` to build its output through `_wrap_text()` and the `_page_lines()` pager (same infrastructure the v1.0b2.15 fix added), so long paragraphs wrap and long threads now paginate with `--MORE--`, closing the gap the original bug report asked for ("Long messages should be paginated") that even the reporter's own patch didn't fully close. Also fixed `_line_w = max(76, _w - 4)` → `max(20, _w - 4)`: the old floor of 76 could make lines overflow on a narrower-than-80-column terminal.
- Verified by reconstructing the full paragraph from its wrapped output and diffing word-for-word against the original — confirmed no content loss — before shipping. Also grepped every `BBSMenuUI.<name> = <name>` override in the file afterward: private messages and Inter-BBS instant messages have no such shadow, so their v1.0b2.15 fixes are confirmed to still be the active code path.

## v1.0b2.18 — ANetCRAFT: day/night cycle was 40 seconds, now 10 minutes (July 2026)

- FIX: `DAY_TICK` was 500 (40 seconds/day at the 80ms tick rate) — reported by Jerry as far too fast to feel like a real survival mechanic. Changed to 7500 (~10 minutes/day). All consuming code (`DayT()`/`_day_t()`, the "Day N" counters, the saved-game lobby preview) derives from this one constant via division/modulo, so the change propagates cleanly with no other edits needed. Applied identically to both this Python version and the standalone C# ANetCRAFT-Door project.

## v1.0b2.17 — ANetCRAFT: fix severe lighting-related slowdown (July 2026)

- FIX: v1.0b2.16's `World.light_at()` was ported straight from the C# version's per-cell design — an O(R²) 17×17-tile neighborhood scan for every query, called once per visible cell (~1560 cells/frame in `_draw_world`). Cheap in compiled/JIT'd C#; measured 155ms/frame in pure Python against an 80ms tick budget — reported by Jerry as "very slow" on the main ANetBBS server and "pretty much unplayable" on a Pi3. New `World.compute_light_grid(cam_x, cam_y, vp_w, vp_h, is_day)` computes the entire viewport's lighting in one pass: one O(depth) scan per *column* for skylight instead of per cell, and one pass over the viewport+margin that finds each light source once and splats its falloff onto nearby grid cells, instead of every cell independently re-scanning its neighborhood for sources. `_draw_world` now calls this once per frame. Measured 5-7ms/frame typical, ~29ms even in a deliberately dense-torch stress test — both comfortably under budget. Verified byte-identical output against the old per-cell method across a full viewport (0 mismatches) before trusting the optimization.
- FIX: Dropped lava (block 15) from `LIGHT_SRC` in the Python version — a visible lava sea in the Nether can be hundreds of tiles, each of which would trigger an O(R²) splat; torches/glowstone/portals are naturally sparse and don't have this problem. (Lava is still a light source in the standalone C# door — not an issue there, not changed.)

## v1.0b2.16 — ANetCRAFT: full Minecraft-style progression + win condition (July 2026)

- FEATURE: Ported the same expansion built for the standalone C# ANetCRAFT-Door project into the bundled Python `anetbbs/features/anetcraft.py` (1582 → 2468 lines), by hand — the two are separate codebases with no shared code. Added: `World.light_at()` sky+torch/glowstone lighting (hostile spawns now gated on darkness via `is_dark()`, not day/night, so unlit caves are dangerous at noon); furnace block (id 20) + smelting (`SMELT`/`FUEL` tables, iron/gold ore now drop raw ore ids 67/68 instead of ingots directly, sand→glass moved from instant craft to furnace); 4-tier armor system (ids 77-92) with `Player.total_defense()`/`reduced_damage()`; lava burn damage + `fire_ticks` status; a `_respawn_player()` death cycle that didn't exist before; a Nether dimension (`World._gen_nether()`) reached by building + lighting an Obsidian portal with Flint and Steel (flood-fill frame validator in `_try_ignite_portal`), with Blaze and Ghast mobs; Enderman mob → Ender Pearl → Eye of Ender crafting chain; an End dimension (`World._gen_end()`) reached through a ring of 8 End Portal Frames, hosting a full Ender Dragon boss fight (`_dragon_ai`) with healing-crystal pillars, ending in a victory screen (`_render_victory_overlay`) and Dragon Egg trophy. World keeps going after victory.
- FIX: Pressing Enter in the ANetCRAFT inventory screen never actually swapped items — `_handle_key`'s generic "close overlay" branch intercepted Enter before it ever reached `_inv_key`'s swap logic. The "Enter=grab/drop" hint had been non-functional since this feature was written.
- FIX: Starving to 1 HP in ANetCRAFT could leave a player stuck there forever — there was no death/respawn handling at all for the zero-HP case.
- NOTE: Save format changed (`dims` list of per-dimension world+mobs instead of a single top-level `world`/`mobs`). `load()` auto-migrates old single-dimension saves, so existing player saves on the live server load fine.

## v1.0b2.15 — Terminal message wrapping fix + Admin menu reorg (July 2026)

- FIX: Terminal message reader (board threads, private messages, Inter-BBS instant messages) truncated any line past column 78 (`line[:78]`) instead of word-wrapping it — long web-composed paragraphs were cut off mid-sentence when read over telnet/SSH. `read_thread`, the PM viewer, and the InterBBS IM viewer now build their output through `_wrap_text()` and a new shared `_page_lines()` pager (extracted from `_page_text()`), so long messages word-wrap to the session's terminal width and paginate with a `--MORE--` prompt instead of truncating.
- FEATURE: Admin navbar dropdown reorganized from one 58-item flat list into 8 entries (Dashboard + Users/Messages/Files/Content/Network/System/Settings). Each category opens a new `/admin/hub/<section>` page rendering its tools as cards (`ADMIN_HUB_SECTIONS` in `web/admin.py`, `templates/admin/hub.html`). Purely a navigation change — no existing admin route or URL was moved.

## v1.0b2.14 — CP437 encoding fixes: terminal input + web renderer (July 2026)

- FIX: ANEdit dropped CP437 high-byte characters (ä ö ü Ä Ö Ü ß etc.) entered from SyncTERM/CP437 sessions. Root cause: `_Keys` decoded raw bytes as `latin-1`, turning CP437 byte 0x84 (ä) into U+0084 (a C1 control character), which `isprintable()` rejects. `_Keys` now uses `session.encoding` (default `cp437`) so high bytes decode to their correct Unicode glyphs and are inserted normally. UTF-8 multi-byte buffering also added for SSH sessions.
- FIX: Web interface rendered correctly stored Unicode characters (ä ö ü ß etc. in local board posts) as CP437 box/line/math symbols. Root cause: `render_msg_body_rich` always ran a latin-1→CP437 round-trip (e.g. ä U+00E4 → byte 0xE4 → CP437 Σ). Local posts are stored as proper Unicode and need no charset conversion — the round-trip is now skipped when no `chrs` header is present.

## v1.0b2.12 — Widescreen: 132-col art border removal + status bar overflow fixes (July 2026)

- FIX: `║` side border characters removed from all 132-col ANSI art files (main menu, game center, chat, goodbye) for a cleaner borderless look.
- FIX: Status bar overflowed to a second line on game_center132, chat132, and goodbye132 when BBS name + version expanded past 132 cols. Middle spacing trimmed to absorb the template expansion.
- FIX: goodbye132 title banner (`Thanks for calling @BBS@!`) overflowed when BBS name is long — trailing decoration bytes trimmed.
- FIX: Door Games box: `║` side borders replaced with plain `═` horizontal bars.
- FIX: File library area list column overflow with `[inactive]`/`[sysop]` flags at 132 cols.
- FIX: ANView (message reader) header bar, status bar, and content now use terminal width.
- FIX: Door Games list name/type column widths now scale with terminal width.

## v1.0b2.11 — Graffiti Wall: widescreen + ASCII mode support (July 2026)

- FEATURE: Wall box scales with terminal width (131 cols max on widescreen sessions).
- FEATURE: ASCII mode substitutes `+/-/|` for CP437 box chars; pipe colors stripped in post content.

## v1.0b2.10 — MRC: send TERMSIZE to server at connect time (July 2026)

- FEATURE: MRC client sends `TERMSIZE <cols>,<rows>` to the remote MRC server immediately after joining. On a 132-col session this sends `TERMSIZE 132,37`.
- NOTE: WHOON `/who` list format is set by the remote server (mrc.bottomlessabyss.net) for 80-col. Chat word-wrap already uses the full local terminal width.

## v1.0b2.9 — MRC: status bar blue fill now spans full terminal width (July 2026)

- FIX: Root cause of narrow MRC status bar: `room_s`/`topic_s` both embed `\x1b[0m]` which reset the blue background before the gap spaces were written. Added `\x1b[44m]` re-assertion before gap and before `\x1b[K]`. Status bar now fills the full 131-col width on widescreen sessions.

## v1.0b2.8 — MRC: active terminal size detection + status bar fill fix (July 2026)

- FIX: MRC detects actual terminal size via ANSI CPR query when session `window_size` is small (<100 cols). Fixes 40-col MRC status bar on telnet sessions where NAWS was not negotiated at connect time.
- FIX: MRC status bar uses `\x1b[K]` after writing content to fill the rest of the row with blue — no longer depends on `\x1b[2K]` behavior.
- FIX: `_term_columns` floor raised from 40 → 64.

## v1.0b2.7 — Widescreen: MRC chat fixes (July 2026)

- FIX: MRC status bar blue fill stopped at content end — `\x1b[44m` now set before `\x1b[2K` so erase fills blue.
- FIX: Chat Systems menu `banner`/`footer`/`menu_item` not width-aware — now pass `_w`.
- FIX: MRC `_enter_split_screen` checks `session.cols`/`.rows` as fallback for `_term_columns`.

## v1.0b2.6 — Widescreen fixes: RSS pager/lightbar, file browser, ANetIRC (July 2026)

- FIX: RSS lightbar and pager separator lines hardcoded to 76 — now `ui_width(session)`.
- FIX: RSS item list title column capped at 62 — now expands with terminal width.
- FIX: RSS article pager divider hardcoded to 72 — now expands.
- FIX: File area list/detail dividers hardcoded to 44 — now terminal width; name + desc columns expand.
- FIX: InterBBS IM inbox banner not width-aware — now full width with wider message preview.
- FIX: ANetIRC capped terminal width at 79 — now 131 so two-panel TUI fills 132-col sessions.

## v1.0b2.5 — Full widescreen support for all terminal screens (July 2026)

- FEATURE: `ui_width(session)` added to `ansi_ui.py`. Every `banner()`, `footer()`, table header, and content column across all 20+ screens now passes the terminal width. All screens expand to fill wide (132-col) terminals automatically.
- FEATURE: Content column widths (board name, thread subject, bulletin title, RSS feed/item title, echomail area name, file area name, message body) scale proportionally with `ui_width`.

## v1.0b2.4 — Game Center: Q and 3 both exit (July 2026)

- FIX: `Q` did not exit Game Center — only `3` did. Both now accepted (case-insensitive), matching Chat and Door Games behavior.

## v1.0b2.3 — Display @-codes applied in all sub-menu art files (July 2026)

- FIX: `@SYSOP@`, `@BBS@`, `@VERSION@`, `@NODE@`, `@TIME@` not substituted in sub-menu art. Added `write_menu_art(session, slot)` to `ansi_ui.py` — shared helper that applies display codes before writing. All feature sub-menus converted to use it.
- FIX: `game_center` art had `[Q]` for back but code checked `'3'`. Art files corrected.

## v1.0b2.2 — Mode-aware sub-menu art + stock chat/game_center files (July 2026)

- FEATURE: `load_menu_ansi(slot, mode)` is now mode-aware. Wide terminals check `{slot}132.ans`; ASCII terminals check `{slot}.asc`. Both fall back to `anetbbs/screens/menus/` bundled stock art before returning None.
- FEATURE: `chat` and `game_center` menus now ship with stock CP437/ANSI art for all three modes (`.ans`, `.asc`, `132.ans`).
- FIX: All `load_menu_ansi()` callers pass `self.session.term_mode`.

## v1.0b2.1 — Seed stock screens to data/text/ on install/update (July 2026)

- FIX: `update.sh` now seeds `anetbbs/screens/` → `data/text/` and `anetbbs/screens/menus/` → `data/text/menus/` using `cp -n` (no-overwrite). Sysop files are never touched; missing files are filled in on every update.
- FEATURE: `anetbbs/screens/menus/` ships `main.ans`, `main.asc`, `main132.ans` as stock main menu art.

## v1.0b2.0 — Stock slot screens as real files (July 2026)

- FEATURE: `welcome`, `newuser`, `goodbye` now ship as `.ans` / `.asc` / `132.ans` in `anetbbs/screens/`. All three terminal modes get a proper stock screen. `_show_ansi_screen` falls back to these before returning nothing.
- REMOVE: Inline `_stock_ascii_screen()` replaced by the file-based system.

## v1.0b1.9 — ASCII mode: strip ANSI from all writes (July 2026)

- FIX: `write()` now strips ANSI escape sequences when `term_mode == 'ascii'`, covering all hardcoded color strings (bot-gate, security questions, new-user questionnaire, post-login notifications, sysop broadcasts, etc.) without needing per-call changes.

## v1.0b1.8 — ASCII mode login screen fix (July 2026)

- FIX: `login_screen()` rendered the 1/2/3 login menu with hardcoded ANSI escape codes regardless of terminal mode. ASCII-mode connections now get a plain `+---+` box-draw login menu with no escape codes.

## v1.0b1.7 — Telnet TTYPE second-round fix (July 2026)

- FIX: Telnet TTYPE detection was missing the second round of the RFC 1091 handshake. After the client sends `WILL TTYPE`, the server must send `IAC SB TTYPE SEND IAC SE` before the client reports its type string. `init_session()` now sends that request and reads the response, so `TERM=dumb` (and other dumb-terminal types) correctly trigger ASCII mode.

## v1.0b1.6 — Terminal detection fixes: SSH PTY size + telnet NAWS drain (July 2026)

- FIX: SSH sessions now read `process.term_type` and `process.term_size` from asyncssh immediately after session creation so `term_mode` is correct for SSH logins.
- FIX: Telnet NAWS/TTYPE responses are now drained and parsed after the negotiation settle period instead of being discarded by `_buffer.clear()`.
- FIX: Negotiation settle time increased 100ms → 300ms.

## v1.0b1.5 — Multi-terminal mode: ASCII + widescreen detection (July 2026)

- TERMINAL: Telnet NAWS and TTYPE subnegotiation responses are now parsed (previously negotiated but discarded). Window size and terminal type are stored on the session.
- TERMINAL: New `term_mode` property: `wide` (cols ≥ 132), `ansi` (default), `ascii` (dumb/TTY).
- TERMINAL: `_show_ansi_screen` picks `{slot}132.ans` for wide, `{slot}.asc` for ASCII (each falling back gracefully). DB ANSI content is skipped in ASCII mode; stock plain-text fallbacks cover welcome/newuser/goodbye.
- TERMINAL: Menu engine uses `{name}132.ans` / `{name}.asc` / `{name}.ans` in `data/text/menus/`. Auto-render: ASCII gets plain-text layout (no escape codes), wide gets 128-column ANSI layout.
- TERMINAL: Pause prompt strips ANSI color for plain-text screens.

## v1.0b1.4 — Terminal MRC /me fix (June 2026)

- FIX: Terminal MRC `/me` was sending IRC CTCP format (`\x01ACTION ...\x01`), which the MRC bridge doesn't recognise — it displayed as `ACTION andwhatyoutyped` instead of `* Nick action`. Changed to `* text` (matching the web client) so the bridge formats it correctly.

## v1.0b1.3 — Auto-update beta version fix (June 2026)

- FIX: `deploy/run_upgrade.sh` rejected beta version strings (`v1.0b1.N`) with "does not match expected vX.YaZ.NN form" — the validation regex only accepted alpha (`a`) phase markers. Updated to accept `[ab]`.
- FIX: `admin.py` release-notes parser had the same alpha-only pattern for finding section boundaries in RELEASE.md.

## v1.0b1.2 — Message viewer position indicator fix (June 2026)

- TERMINAL: ANView message viewer status bar now shows the **last visible line** instead of the first. `Ln:29/29  END` at the bottom of a 29-line message makes it clear you have reached the end; previously `Ln:8/29` looked like line 8 of 29 with more to scroll.

## v1.0b1.1 — Beta 1 Release (June 2026)

**Milestone: first public Beta.** Version scheme changes from `v1.0a2.NNN` (alpha) to `v1.0b1.NNN` (beta). The auto-update scanner now recognises both `a` and `b` phase markers; beta is ranked higher than any alpha of the same major/minor release so existing installs will detect this as a newer version.

Includes all features and fixes through v1.0a2.232:
- Echomail area list and message list: arrow-key lightbar (no more `-- more --`; 300-message load)
- RSS terminal: CP437 sanitiser; pager exit keys corrected; PgUp/PgDn SyncTERM CSI fix (`ESC[V`/`ESC[U`)
- Message viewer status bar: `Up/Dn` label matches actual key behaviour
- QWK inbound: blank messages (header-only records, kludge-only bodies) are skipped instead of stored

## v1.0a2.232 — Message viewer hint bar: Up/Dn label (June 2026)

- TERMINAL: ANView message viewer status bar now shows `Up/Dn` instead of `PgUp/PgDn` — arrow keys are what scroll the message; label now matches reality

## v1.0a2.231 — PgUp/PgDn SyncTERM CSI sequence fix (June 2026)

- TERMINAL: Fixed PgUp/PgDn keys in all arrow-key lightbars and the RSS pager — SyncTERM ANSI mode sends `ESC[V` (PgUp) and `ESC[U` (PgDn) which were not in the CSI lookup table in `read_key_arrow()`; they fell through to `return 'ESC'` which quit the lightbar. Added `b'V'` → `PGUP` and `b'U'` → `PGDN` to the lookup table in `session.py`.

## v1.0a2.230 — Echomail lightbar + RSS CP437/PgKey fixes (June 2026)

- TERMINAL: Echomail area list replaced with smooth arrow-key lightbar (Up/Dn/PgUp/PgDn/Home/End); no more `-- more --` page breaks
- TERMINAL: Echomail message list replaced with arrow-key lightbar; removed 30-message hard limit (now loads up to 300)
- TERMINAL: RSS article pager no longer exits on ESC (only Q/Enter/Ctrl-C); eliminates spurious exits from unrecognized key sequences
- TERMINAL: Fixed root cause of PgUp/PgDn "exits RSS" bug — trailing `\n` from CR+LF Telnet Enter was being consumed by the pager as an ENTER keystroke (added `\n` discard in `read_key_arrow`)
- TERMINAL: RSS article body, title, author, and feed name are now passed through `_sanitize_cp437()` before display — common Unicode typographic chars (smart quotes, em-dash, ellipsis, bullet, etc.) map to CP437-safe equivalents instead of showing as `?`
- TERMINAL: RSS item-list and river lightbar rows also sanitize titles through `_sanitize_cp437()` for consistent display

## v1.0a2.229 — Font licensing: NOTICE file + CSS attribution comments (June 2026)

- LICENSE: Added `NOTICE` file at project root documenting the IBM VGA font as a
  CC BY-SA 4.0 third-party component with full attribution to VileR / int10h.org.
- LICENSE: Added `anetbbs/static/fonts/LICENSE.txt` co-located with the font file,
  including attribution, source URL, license URL, and derivative-work note.
- CSS: Both theme stylesheets now carry an inline attribution comment above the
  `@font-face` rule (author, source, license, conversion note).
- The font file is served as a static asset at `/static/fonts/Ac437_IBM_VGA_9x16.woff`
  satisfying the CC BY-SA requirement that derivative distributions make the file
  available; the original pack and TTFs remain free at https://int10h.org/oldschool-pc-fonts/.

## v1.0a2.228 — IBM VGA CP437 font in VOID SIGNAL and HACKERS themes (June 2026)

- FEATURE: Authentic IBM VGA 9×16 font (`Ac437_IBM_VGA_9x16.woff`) bundled in
  `anetbbs/static/fonts/` and wired into both the VOID SIGNAL (enhanced) and
  HACKERS (1995) web themes.
- The font (`VGA437`) is declared via `@font-face` and placed first in the
  `font-family` stack everywhere `Share Tech Mono` was previously used — body text,
  nav items, card content, form fields, message bodies, RSS articles, etc.
- `Share Tech Mono` and `Courier New` remain as fallbacks so the theme degrades
  gracefully if the font file can't load.
- Bitmap font rendering is sharpened with `-webkit-font-smoothing: none; font-smooth: never`
  on `body` so the VGA pixels stay crisp rather than blurry.
- Orbitron headings (`h1–h6`, `.navbar-brand`, `.bbs-header h1/h2`) are exempted from
  the no-smoothing rule so the vector heading font still anti-aliases correctly.
- Font requested by Firehawke. Font file from the Ultimate Oldschool PC Font Pack
  (int10h.org).

## v1.0a2.227 — install.sh: libsixel-bin optional package (June 2026)

- INSTALL: Added `libsixel-bin` as an optional install component (provides `img2sixel`).
  The installer now asks "Install libsixel-bin for sixel images in terminal RSS reader?"
  and installs the correctly-named package for apt (`libsixel-bin`), dnf/yum (`libsixel`),
  and pacman (`libsixel`). Without it the terminal RSS reader falls back to text-only.
- DOCS: `docs/INSTALL.md` updated with the `sudo apt install -y libsixel-bin` note under
  optional packages.

## v1.0a2.226 — RSS terminal fixes: PgUp/PgDn, arrow char encoding, sixel detection (June 2026)

- FIX: PgUp / PgDn keys not working in lightbar and pager — root cause: `reader.read(6)`
  returns fewer bytes than requested on a live stream, so the `5~` / `6~` suffix of the
  VT sequence was fragmented. Rewrote `read_key_arrow()` to consume CSI sequences one
  byte at a time until the VT final byte (0x40–0x7E), which handles any fragmentation.
- FIX: `??` appearing in hint lines — Unicode ↑↓ arrows (U+2191/U+2193) don't survive
  the session's CP437/latin-1 encoding. Replaced with plain ASCII `Up/Dn`.
- FIX: Sixel detection now checks `img2sixel` presence first (fast-path false if missing),
  and increases the DA1 response read timeout from 250 ms to 1500 ms to cover higher-
  latency links. The sixel path is still silently skipped if the tool is absent.
- NOTE: Sixel images in terminal require `img2sixel` installed on the BBS server
  (`sudo apt install libsixel-bin`). SyncTERM does support sixel; detection reads
  the DA1 primary device attributes response for the sixel capability flag.

## v1.0a2.225 — RSS Reader overhaul: web images + lightbar terminal (June 2026)

**Web:**
- FEATURE: Feed item lists (`/rss/<id>` and `/rss/all`) now show thumbnail images
  extracted from feed enclosures, media:thumbnail, or the first `<img>` in the article body.
- FEATURE: Item titles are proper hyperlinks to the article detail page; "Original" button
  opens the source URL in a new tab.
- FEATURE: Article reader (`/rss/item/<id>`) shows a full-width hero image, sanitized HTML
  body (via bleach), and Newer / Older navigation within the same feed.
- FEATURE: Feed index groups feeds by category in a card grid.
- FEATURE: Paginator on feed and river pages shows page numbers instead of just «/».
- FIX: `bleach` now sanitizes `content_html` before rendering (raw HTML injection removed).
- FIX: River (`/rss/all`) now filters by `min_access_level` (previously skipped the check).

**Terminal:**
- FEATURE: Feed list and item list both use an arrow-key lightbar scroller (↑↓ PgUp PgDn
  Home End — no Enter needed to navigate).
- FEATURE: Article body is now a scrollable pager (↑↓ PgUp PgDn Home End Q). Full HTML
  body is rendered as plain text via an inline HTML→text converter; previously only the
  `summary` field was shown.
- FEATURE: Sixel image rendering in article view — if the terminal reports sixel support
  (DA1 response) and `img2sixel` is on PATH, the article hero image is displayed before
  the body. Gracefully skipped for non-sixel terminals or missing tool.
- FEATURE: `M` in both feed list and item list marks all items read without leaving the
  lightbar.
- FEATURE: Unread counts on the feed list now use two aggregated SQL queries instead of
  an N+1 per-feed count loop.
- FIX: River now applies `min_access_level` filter (previously showed all active feeds).
- MODEL: `RssItem.image_url` (VARCHAR 1000, nullable) — auto-migrated; populated by poller
  from media:thumbnail, media:content, enclosures, or first `<img>` in HTML body.

## v1.0a2.224 — Terminal compose echomail: network-first selection (June 2026)

- UX: Terminal echomail compose (`E → C`) now mirrors the reading flow — choose a network,
  then choose an area within that network, then compose. Previously showed a flat list of
  all areas across all networks.
- Areas in the compose picker are grouped by category with `── Group ──` headers, same as
  the read flow. `B=back` returns to the network chooser; `Q` cancels at any point.
- Access controls (sysop-only, min_access_level) applied to both the network area-count
  display and the per-network area list.

## v1.0a2.223 — ANotherNetwork QWK node self-registration; terminal echomail crash fix (June 2026)

- FIX: Terminal echomail crash "Menu action failed" — `FG['cyn']` key does not exist;
  corrected to `FG['cyan']` in both the network chooser and per-network area list.
- FEATURE: QWK node self-registration via BBS terminal (Option B).
  From E → Echomail Networks, sysops type A to apply for an ANotherNetwork QWK node.
  Wizard collects BBS name, desired packet ID (validated unique), sysop name, email,
  BBS address, and notes. Saves as a pending `QWKNodeRequest`.
- FEATURE: Hub admin review queue at Admin → Hub → Node Requests.
  Approve creates a `QWKNode` with a 16-char auto-generated password.
  Deny with optional reason. Hub dashboard shows pending count badge.
- FEATURE: Approved/denied status shown to applicant on their next visit to the
  echomail networks terminal screen, including credentials for approved nodes.
- MODEL: New `QWKNodeRequest` table (auto-migrated).

## v1.0a2.222 — Echomail network-first browsing; web + terminal (June 2026)

- UX: `/echomail/` is now a network chooser — one card per network with type, area count, unread badge.
- NEW: `/echomail/network/<id>` — per-network area list grouped by category; area cards with
  unread count, message count, quick Enter / Next Unread / Compose buttons.
- FIX: "All Areas" back button on area pages replaced with "← [Network Name]" to the network view.
- TERMINAL: Echomail menu now shows network list first; selecting a network shows areas grouped by
  category with `── Group ──` headers; `B=back` returns to network list.

## v1.0a2.221 — Echomail bulk area management; network-first UI (June 2026)

- UX: Echomail "Manage Areas" now shows a network chooser first (card grid with area stats).
- NEW: Per-network area view — areas grouped by category, individual toggles and edit/delete.
- NEW: Bulk action toolbar — select All/None/Active/Subscribed, then apply one action to all:
  Set Active/Inactive, Subscribe/Unsubscribe, Set Active+Subscribed, Set/Clear Sysop-Only,
  Set Access Level, Delete Selected.
- FIX: All per-row and per-network actions (toggle, rescan, AreaFix, QWK quick-add, bulk import)
  now redirect back to the correct network's area list rather than the global chooser.
- MOVED: AreaFix quick panel and QWK quick-add panel now live in the per-network view.

## v1.0a2.220 — ANotherNetwork QWK/FTP seeded; 26 conference areas (June 2026)

- FEATURE: ANotherNetwork (QWK) seeded alongside BinkP — FTP transport, hub ID `ANET`.
  FTP to bbs.a-net.fyi with your packet ID: download ANET.qwk, upload <YOURID>.rep.
- NEW: FTP server authenticates QWK nodes by packet_id/password (Hub Management → QWK Nodes).
  QWK packet generated fresh on login; .rep processed + fanned out to BinkP nodes on upload.
- NEW: `echomail/qwk_hub_ftp.py` — packet builder, REP importer, tosser integration.
- EXPANDED: ANotherNetwork areas grown from 5 to 26 across 8 named groups (General, Technology,
  BBS Scene, Retro, Hobby, Trading, Data, SysOp, Test).
- MODEL: `EchoArea.category` field added (VARCHAR 80, nullable); auto-migrated.

## v1.0a2.219 — ANotherNetwork Zone 1200 seeded; nodelist endpoint (June 2026)

- FEATURE: ANotherNetwork (Zone 1200) seeded on every fresh install.
  Five echo areas pre-created: ANN.GENERAL, ANN.SYSOP, ANN.TEST, ANN.BBSDEV, ANN.RETRO.
  Network is inactive by default; sysop enters their assigned address and password
  to activate. Hub: `bbs.a-net.fyi` at `1200:1/1`.
- NEW: Public nodelist endpoint at `/admin/echomail/hub/nodelist` — serves the
  current ANotherNetwork NODELIST (FTS-5000 format, plain text) without requiring login.
  Filename: `NODELIST.NNN` (day of year). Includes all active registered BinkP nodes.

## v1.0a2.218 — Hub mode: BinkP hub + QWK hub (June 2026)

- FEATURE: ANetBBS can now act as an echomail hub (not just a leaf node).
- NEW: BinkP hub — register downstream nodes; per-node echo area subscriptions;
  outbound hold queue fanned out to subscribers on every BinkP connection.
- NEW: Areafix robot now handles per-node subscriptions for downstream peers
  separately from the global `EchoArea.is_subscribed` flag.
- NEW: QWK hub — HTTP endpoints for node download (`/qwkhub/<id>.qwk`) and
  upload (`/qwkhub/<id>.rep`); per-node conference subscriptions with
  high-water mark tracking; configurable hub system ID via `QWK_HUB_ID` setting.
- NEW: Hub tosser (`echomail/tosser.py`) — automatic fan-out to downstream
  BinkP nodes for all inbound messages (BinkP listener, QWK hub, upstream poll).
- NEW: Admin → Echomail → Hub Management — full CRUD for BinkP and QWK nodes,
  area subscriptions, catchup, hold queue viewer.
- NEW: `generate_nodelist()` in `echomail/nodelist.py` for hub operators.
- Admin navbar: new "Hub Management" link under Echomail.

## v1.0a2.217 — Pre-beta audit: bug fixes + doc corrections (June 2026)

- FIX: Private message bodies were always blank — template referenced wrong model
  field name. PM read view, thread sidebar previews, and Quote Reply pre-fill all
  affected. Fixed in template and compose view.
- FIX: File area storage path no longer visible to regular users; shown to admins only.
- FIX: Synchronet compat doc table now scrolls horizontally on narrow screens instead
  of overflowing the page container.
- DOC: Install commands corrected to current format throughout all doc pages.
- DOC: Stale draft notes and outdated version references corrected.
- DOC: Raspberry Pi 3 confirmed working; hardware table and recommendations updated.
- DOC: DSR references removed from Image Galleries doc.
- DOC: Network list updated (tqwNet + zer0net).
- DOC: Incorrect systemd service name corrected in PORTS.md.

## v1.0a2.216 — Clean shutdown fix + socket.send() suppression (June 2026)

- FIX: `anetbbs` service no longer gets SIGKILLed by systemd on restart. SSH keepalive
  was awaiting `create_future()` (never resolves) so SIGTERM could not stop it. It now
  waits on a proper `asyncio.Event` that the signal handler sets — process exits cleanly
  within ~1 second of SIGTERM.
- FIX: `socket.send() raised exception` journal spam suppressed. pyftpdlib uses asyncore;
  its WARNING-level log fires on every SCC health-check probe (connect-and-close to port 21).
  `asyncore` logger now set to ERROR in main.py alongside the existing asyncssh suppression.

## v1.0a2.215 — BinkP service + AKA admin move (June 2026)

- FR: BinkP inbound listener (`anetbbs.echomail.binkp_server`) now installed as a
  separate systemd service (`anetbbs-binkp`), visible in the Service Control Center
  with port probe on 24554, restart/stop/start buttons, and journal log access.
- FR: FTN AKA management moved from the profile dropdown to Admin → Echomail config.
  Accessible via the "FTN AKAs" button on the Echomail Admin dashboard. The old
  `/profile/akas` URL redirects to the new location.

## v1.0a2.214 — HACKERS theme: secret "Hack the Planet" easter egg modal (July 2026)

- HACKERS (1995) theme: "HACK THE PLANET" in the navbar is now a clickable secret link.
  Opens a tribute modal with the full crew roster (Zero Cool, Acid Burn, Crash Override,
  Cereal Killer, Lord Nikon, Phantom Phreak, The Plague), iconic movie quotes, a heartfelt
  tribute paragraph, and a typewriter terminal that types "HACK THE PLANET / ACCESS GRANTED".
  Rainbow-animated title, lime-green crew handles, blinking cursor. Closes with [ LATER, HACKER ].

## v1.0a2.213 — Two epic themes: VOID SIGNAL + HACKERS (1995) (July 2026)

- New built-in theme: **VOID SIGNAL ◈** — Triple neon (electric green + cyan + magenta) on
  pure black. Full-page CRT scanlines, moving phosphor beam sweep, animated brand glitch,
  cycling card border stripe, neon glow buttons, animated rainbow footer sweep line.
- New built-in theme: **HACKERS (1995) ◈** — Neon violet + lime + cyan on black. For those
  who ride the information superhighway. Rainbow cycling navbar border, full rainbow brand
  glitch animation, rainbow card top stripe, rotating footer sweep, "HACK THE PLANET"
  navbar watermark. Orbitron font, all-caps labels throughout.
- Both themes seeded automatically on first run / upgrade — no manual step needed.
- Base template now supports an extended stylesheet marker (`--theme-stylesheet`) in any
  theme's CSS variables, enabling full layout/animation overrides beyond color vars.

## v1.0a2.212 — Fix casino wallet saves; fix stale door game sessions (July 2026)

- Casino wallet saves now work: CSRF token was being read from a cookie that doesn't
  exist (token lives in `<meta name="csrf-token">`). Every POST was silently rejected with
  400. All 4 casino templates fixed.
- Door game WebSocket disconnect now calls `terminate_session()` so sessions end when
  the browser tab closes or disconnects.
- On startup, any game sessions still marked `active` from before the previous restart are
  automatically marked `stale` (in-memory PTY state is gone after restart).
- Admin → Game Sessions now has a "Terminate All" button for bulk cleanup.

## v1.0a2.211 — Web casino games: persistent wallet + weekly reset + leaderboard (July 2026)

Blackjack, Slots, Video Poker, and Hold'em now have a persistent wallet. Balance
carries between sessions and resets every Monday. If you hit zero, play locks until
the next weekly reset. Peak balance posts to the leaderboard. Starting amounts are
configurable per-game in Admin → Settings (`CASINO_BLACKJACK_START`,
`CASINO_SLOTS_START`, `CASINO_VIDEOPOKER_START`, `CASINO_HOLDEM_START`).

## v1.0a2.210 — Fix upgrade checker error when REGISTRY_URL is not configured (July 2026)

Admin → Upgrades no longer shows a `MissingSchema` error when `REGISTRY_URL` is not set. It now shows "upstream not configured" instead.

## v1.0a2.209 — Menu admin: add imsg / imsg_send / rss to action-type dropdown (July 2026)

## v1.0a2.207 — Terminal MRC fixes; QWK blank-body diagnostic (July 2026)

- Terminal `/afk [msg]` now sends `AFK` (not `STATUS AFK`), so the away message is visible to other chatters
- Terminal `/back` now sends `BACK` instead of `STATUS AFK`
- Fixed rate-limit error when sending first message after returning from AFK
- Tab-complete now tracks users who join after you (`USERIN`/`USEROUT`/`USERLIST`/`USERNICK` parsed); `from_user` field used for nick tracking on chat messages
- `/chatters` now sends `CHATTERS` (all rooms); `/who`/`/whoon` send `WHOON` (current room)
- QWK inbound: warning log when body is blank (first 256 raw bytes logged for diagnosis)
- Admin: `autocomplete="new-password"` on QWK password field

## v1.0a2.206 — Fix dosemu2 conf written to /tmp (July 1 2026)

dosemu2 temp files (conf and COM1 PTS path) were hardcoded to `/tmp/`. On
servers with restricted `/tmp/` permissions the door failed immediately with
"Permission denied" before dosemu2 even launched. Both paths now use
`temp_root()` (`<DATA_DIR>/temp/`) which the service user always owns.

## v1.0a2.205 — Fix menu hotkey duplication on update (July 1 2026)

`seed_default_menus()` ran on every startup and backfilled any default hotkey
not present in the sysop's menu. When a sysop rebound a hotkey (e.g. changed
`M` → `A` for Message Boards), the original `M` was seen as "missing" and a
second Message Boards entry was added — causing duplicates on every update.

Fix: backfill now checks by `(action_type, action_args)` instead of hotkey.
If a menu already has a `boards` action (regardless of what key it's bound to),
no new `boards` item is added. Truly new features added in new releases still
backfill automatically, as long as their hotkey doesn't conflict.

## v1.0a2.204 — Remove ANetCRAFT Enhanced and RDQ3 from release (July 1 2026)

- Removed ANetCRAFT Enhanced web game: WEB_GAMES entry deleted from `games/web_games.py`,
  `templates/games/web/anetcraft_enhanced.html` deleted.
- Removed Red Dragon Quest 3 door (`doors/mystic/rdq3/`) from the release package.

## v1.0a2.203 — Beta release: package cleanup, REGISTRY_URL fix, docs update (July 1 2026)

- `REGISTRY_URL` default changed from hardcoded hub URL to empty string. Sysops opt in to
  federation by setting `REGISTRY_URL` in Admin → Settings or `.env`. Preflight check now
  reports "not configured" instead of silently contacting an external host.
- `mrc/bridge/config.example.json`: `bridge_bbs` changed to generic placeholder.
- CHANGELOG v1.0a2.200 entry: removed internal tooling note.
- README: status updated from alpha 2 to beta; removed broken doc link.

## v1.0a2.202 — Auto-ban; IP whitelist; GeoIP country blocking; wiki edit gate (June 25 2026)

**Security hardening and anti-spam protections.**

- **Auto-ban**: When the login rate limiter trips (10 attempts in 5 min), the source IP is
  permanently written to the IpBan table automatically — no sysop action needed. Visible and
  removable from Admin → IP Bans like any manual ban. Auto-bans have no expiry by default.
- **IP whitelist** (`Admin → IP Whitelist`): New table `ip_whitelist`. Entries bypass all
  ban checks AND GeoIP country blocks. Use for sysop home IPs, trusted peers, and VPN exit
  nodes. Supports single IPs and CIDR ranges.
- **Country blocking**: Set `BLOCKED_COUNTRIES=CN,RU,KP` (comma-separated ISO codes) in
  Admin → Settings. Blocks login and registration from listed countries. Uses
  [ip-api.com](http://ip-api.com) — free, no registration, no download, no API key. Results
  cached in-memory for 1 hour so repeated login attempts don't add latency. Fails open
  (lookup error = allow) so a network hiccup never locks out users. Whitelisted IPs are exempt.
- **fail2ban configs** (`deploy/fail2ban/`): Filter (`filter.d/anetbbs-web.conf`) matches
  nginx 429 responses on `/auth/login`. Jail (`jail.d/anetbbs-web.conf`) bans after 3 hits
  in 10 min for 24 h, covering ports 80, 443, 2233, 2234. Belt-and-suspenders on top of the
  in-process auto-ban above.
- **Wiki edit gate**: New users must have at least 5 posts and a 3-day-old account before
  editing any wiki page. Configurable via `WIKI_MIN_POSTS` and `WIKI_MIN_DAYS` in Admin →
  Settings (set to 0 to disable). Admins are always exempt. Message tells the user exactly
  what requirement they don't yet meet.
- New settings in Admin → Settings: `BLOCKED_COUNTRIES`, `WIKI_MIN_POSTS`, `WIKI_MIN_DAYS`.

## v1.0a2.201 — Theme edit/delete; MSP toggle in Admin Settings (June 25 2026)

**Theme management is now fully self-service from the admin UI.**

- Admin → Themes: Delete button added for each theme. The default theme's delete button is
  disabled (can't remove the fallback). A browser confirm dialog guards against accidents.
  Any users who had the deleted theme selected are automatically migrated to the system default.
- Theme Builder: existing-themes table now has Edit and Delete action buttons alongside each
  row — no need to navigate away to Admin → Themes just to fix a name or remove a test theme.
- Admin → Settings: `MSP_ENABLED` and `MSP_PORT` are now listed under Editable Settings.
  Sysops can toggle MSP (inter-BBS instant messaging, RFC 1312) on or off and change the port
  without editing `.env` manually. Flagged as requires-restart so the UI reminds you.

## v1.0a2.200 — File areas manage page; echomail admin polish (June 25 2026)

- File areas: new `manage.html` template for per-area file management.
- Echomail admin `network_form.html`: UI improvements.
- ANetCRAFT web game: `anetcraft_enhanced.html` template added.
- QWK outbound diagnostics removed (verbose body hex dump + `/tmp` REP copy introduced in
  v1.0a2.198 no longer needed after confirmed fix).

## v1.0a2.199 — QWK outbound: fix \\r in body + MSGID space truncation (June 2026)

Two bugs in `_build_rep_packet` confirmed by hex-dumping the REP file:

1. **`\r` bytes left in body** — body encoding converted `\n` → `\xe3` but left `\r` from
   Windows `\r\n` line endings, producing `\r\xe3` instead of bare `\xe3` throughout.
   Fix: replace `\r\n` first, then lone `\r`, then `\n`.

2. **MSGID space → Synchronet dupe truncation** — auto-generated MSGID was
   `ANETBBS_<hash> <timestamp>`. Synchronet's `qwk_import_msg` calls `truncstr(p, " ")`
   on the MSGID, storing only `ANETBBS_<hash>`. That truncated stub is identical on every
   re-upload of the same message → dupe detection silently drops it.
   Fix: use underscore separator → `ANETBBS_<hash>_<timestamp>`.

Outbound messages to Dove-Net/vert.synchro.net confirmed working after this fix.

## v1.0a2.198 — QWK diagnostics: REP saved to /tmp, body hex dump (June 2026)

Temporary diagnostics to pinpoint outbound QWK failures:
save REP to `/tmp/anetbbs_last.rep`; log first 64 bytes of body hex. Removed in v1.0a2.200.

## v1.0a2.196–197 — QWK outbound: uppercase MSG filename, conf_num, space padding (June 2026)

- Inner ZIP entry renamed `VERT.MSG` (uppercase) — Synchronet's `un_rep.cpp` filters on
  `SAFEST_FILENAME_CHARS`; lowercase extension caused silent extraction failure.
- Body block padding changed from null bytes to spaces (0x20) per QWK spec.
- Conference number extraction handles both plain numeric tags and legacy `QWK_N` prefix.

## v1.0a2.189–195 — QWK outbound investigation series (June 2026)

Iterative fixes tracing why outbound messages weren't appearing on Dove-Net:
REP packet structure, FTP upload auth, conference mapping, area-tag normalisation,
hub_id vs packet_id distinction in filenames. See v1.0a2.186–188 for earlier steps.

## v1.0a2.188 — QWK network form: hub_id field added; upload URL uses hub_id (June 2026)

**Hub System ID field was missing from the network config form** — the
`qwk_hub_id` field existed in the form class and was stored in the database,
but was never rendered in `network_form.html`. Sysops had no way to set it,
so it was always blank. The QWK client fell back to `packet_id` for all
filename generation, which produced correct filenames only if the sysop
happened to put the hub's ID in the packet ID field.

Added the Hub System ID field to the form alongside explanatory text
clarifying the two-ID model:
- **Hub System ID** (`VERT`) — used in ALL filenames: `VERT.qwk` download,
  `VERT.rep` upload, `VERT.MSG` inside the zip. The hub's `un_rep` looks for
  `{hub_sys_id}.MSG`; if wrong the entire REP is silently rejected.
- **Packet ID** (`ANETBBS`) — your node's registered ID. Used for FTP login
  identity and auto-generated `@MSGID:` values. Does not appear in filenames.

Also exposed the download URL and upload URL override fields in the form
(both were hidden — also never rendered in the template).

**Default upload URL changed** from `ftp://dove.synchro.net/{packet}.rep` to
`ftp://dove.synchro.net/{hub_id}.rep` — the outer REP file should be named
with the hub's ID (same convention as the download), not the node's ID. The
node is identified by the FTP login credentials, not the filename.

**After deploying**: go to `/admin/echomail` → DOVE-Net → set:
- QWK Hub System ID = `VERT`
- QWK Packet ID = your registered node ID (e.g. `ANETBBS`)

## v1.0a2.187 — QWK area tags: plain numeric format per Dove-Net docs (June 2026)

QWK conference areas are now tagged with the plain conference number (e.g.
`2010`) instead of the `QWK_2010` prefix that ANetBBS was incorrectly
prepending. The QWK spec and Dove-Net documentation identify conferences by
number only; the prefix was an internal ANetBBS convention that wasn't needed.

**Auto-migration**: on first inbound message for each conference, the importer
now detects any existing `QWK_N` area and renames it to the plain numeric tag
automatically — no manual database edits required.

**Outbound conf_num extraction** now handles both plain numeric tags (`2010`)
and legacy `QWK_N` tags (`QWK_2010`) so existing installations keep working
until migration completes.

**Quick-add** (`/admin/echomail` → QWK network → quick-add) also migrates old
`QWK_N` entries to plain numeric tags when encountered.

**Admin form hint** updated to clarify: FTN areas use `FIDO_GENERAL`-style
tags; QWK areas use the plain conference number.

## v1.0a2.186 — QWK outbound: inner MSG filename and header conf_num fix (June 2026)

Two critical bugs found by researching Synchronet's `un_rep.cpp` source:

**Inner MSG file named with node ID instead of hub ID** — Synchronet's REP
processor (`un_rep.cpp`) extracts the REP zip and looks for
`{hub.sys_id}.msg` (e.g. `DOVE.MSG` for Dove-Net). Our code was writing
`{packet_id}.MSG` (e.g. `ANET.MSG` — the node's ID). The hub printed "MSG file
not received" and silently rejected the entire REP packet. No FTP error is
raised; messages were marked "sent" in ANetBBS but never processed by the hub.
Fixed: `_build_rep_packet()` now takes a `hub_id` parameter and names the inner
file `{hub_id}.MSG`. Sysops must set the **QWK Hub System ID** field in the
network config (e.g. `DOVE` for Dove-Net, `VERT` for vert.synchro.net).

**Bytes 1–7 in QWK header contained message sequence number, not conference**
— Synchronet routes REP messages using `atol((char*)block+1)` on bytes 1–7.
In REP mode the spec requires this field to hold the **conference number**. Our
code stored the sequential packet index (1, 2, 3 …), so all outbound messages
were silently routed to conference 1 (or whatever `atol("1")` landed on)
regardless of the actual target conference. Fixed: bytes 1–7 now contain
`conf_num`, consistent with how `pack_rep.cpp` handles REP-mode packets.

## v1.0a2.185 — QWK outbound fixes: FTP path, MSGID, conf_num logging (June 2026)

Three bugs fixed in the QWK outbound (REP packet) pipeline, diagnosed from
alpha-tester report of replies not propagating on Dove-Net:

**FTP STOR path leading slash** — `_ftp_upload()` was sending `STOR /ANET.rep`
(absolute path) instead of `STOR ANET.rep` (relative to FTP home dir). The
download path already stripped the leading slash with `lstrip('/')`; upload now
does the same. On Synchronet's QNET-FTP server, the absolute path deposited the
REP into the wrong directory and the hub's processor never picked it up — no FTP
error, but the messages were silently lost. Also added passive-mode and login
logging to make future FTP issues easier to diagnose.

**MSGID injection** — outbound messages composed on the BBS had no `@MSGID:`
kludge line in their body. Synchronet deduplicates by MSGID; messages without
one may be mishandled on some hub configurations. `_build_rep_packet()` now
prepends `@MSGID: <packet_id>_<hash> <timestamp>` to any outbound body that
doesn't already carry one. The ID is derived from the message's DB id, subject,
and from-name so re-polls don't create duplicate IDs.

**conf_num=0 warning** — if a QWK area's tag is not in the required `QWK_<N>`
format (e.g. sysop renamed `QWK_6` → `DOVE-HLP`), the poller was silently
setting conference number to 0, causing replies to land in personal mail instead
of the echomail feed on the hub. Both the poller and `_build_rep_packet()` now
emit `WARNING` log lines when conf_num=0 is used for a non-private message, so
the sysop can see and fix the area tag.

## v1.0a2.180–184 — ANetCRAFT Enhanced: spawn, sky, NPCs, compass (June 2026)

**Deterministic spawn grid** — replaced the 50-attempt random search (which
could exhaust all attempts for unlucky seeds) with a 3-pass deterministic grid
scan over the full 400×400 world. Pass 0 prefers PLAINS/DESERT/MOUNTAINS with
no ocean or forest within 16 blocks; pass 1 relaxes the neighbour check; pass 2
accepts any non-ocean open column. Each pass uses a seed-offset grid so the
result is deterministic but varies per world. Labeled `break spawnSearch`
terminates all three loops as soon as a valid spot is found.

**groundY() skips LOG blocks** — the new `groundY()` method skips LEAVES,
CACTUS, and LOG when searching from the top of the chunk downward, so spawn
lands on actual terrain (grass/sand/snow) rather than a tree trunk or canopy.
Spawn also checks that the two blocks above `groundY()` are non-solid before
accepting the position, preventing spawning inside a trunk.

**Sun disc, moon disc, 1200-star field** — actual Three.js mesh objects orbit
the scene. The sun (warm cream `CircleGeometry`, radius 7) and moon (cool blue,
radius 4) face the camera via `.lookAt()` and track the day/night angle. 1,200
star `Points` are distributed on the upper hemisphere at radius 180; they fade
out when `dayTime > 15%`. Sun and moon hide below the horizon automatically.

**NPC legs and name tags** — NPCs now have two leg segments. A `SpriteMap`
canvas badge with the NPC's name floats 2.3 units above the root, always
facing the camera.

**Compass HUD** — always-visible top-right overlay: cardinal direction (N/NE/E
…), current biome name, and day percentage.

**Terrain** — mountains use `n²` noise distribution for mostly gentle foothills
with occasional sharp peaks (max height ~96). Plains are very flat (SEA+1 to
SEA+5).

## v1.0a2.179 — ANetCRAFT Enhanced night fix; sysop file manager (June 2026)

**ANetCRAFT Enhanced — night startup fix** — the game was starting in
pitch-black darkness because `dayTime` was initialised to `0.25`, which maps
exactly to `cos(π/2) = 0` (midnight). Changed to `0` (high noon). Night sky
colour raised from near-black (8,8,25) to dark-blue (15,20,55) and minimum
ambient light raised from 0.08 to 0.18 so blocks remain visible at night.

**Sysop file management** — new page at `/file-areas/<id>/manage` (admin-only).
Lists every file in an area with: inline description editor (click pencil →
textarea → Save), per-file delete with confirmation dialog, and a direct
sysop upload form (bypasses moderation queue). Each area row in
`/admin/file-areas` now has a **Manage Files** button linking to this page.

## v1.0a2.177–178 — ANetCRAFT Enhanced (June 2026)

Full 3D WebGL voxel game built into the ANetBBS game centre. Powered by
Three.js r128. Features: procedurally generated world with six biomes (Ocean,
Tundra, Desert, Mountains, Forest, Plains); WASD + mouse-look movement with
AABB collision; day/night cycle (10-min day); 20 block types with a 256×256
texture atlas; DDA raycast block targeting; NPC system (Villager, Sheep,
Zombie, Trader) with wander AI and E-key dialog; F3 debug overlay; Q to quit.

## v1.0a2.174 — ANetCRAFT: hunger, swords, crafting table (June 2026)

**Hunger system** — an orange hunger bar sits next to the health bar. Hunger drains 1 point every ~32 seconds in survival mode. When hunger hits 0, health slowly drains (but not below 1 HP). Press **E** to eat the held food item. **Meat** (dropped by cows, +6 hunger) and **Apples** (15% chance from breaking leaves, +3 hunger) are the current food sources.

**Swords** — four tiers: Wood (+3 dmg), Stone (+5), Iron (+7), Diamond (+11), stacked on top of the base 4-HP hit. All swords craft at a **Crafting Table**.

**Crafting Table requirement** — tools and swords require a placed Crafting Table (block ID 17) within reach. Basic recipes (planks, sticks, torches, the crafting table itself) still work anywhere. The crafting menu now shows `[bench]` next to recipes that need one, and grays them out if no bench is nearby.

**Creative mode** — hunger bar hidden; eating disabled; inventory now includes all swords and food items.

## v1.0a2.173 — ANetCRAFT: multiplayer (June 2026)

ANetCRAFT multiplayer mode. Select **M** from the lobby to join the shared server world. All players share one map; the first player to join becomes the host and runs mob AI. Other players are visible as colored avatars. Press **T** to open the chat prompt and send a message — the last chat line is displayed above the hotbar for 60 seconds. Per-user MP inventory saves separately from single-player saves. The world auto-saves when the last player leaves or every ~1 min while the host is online.

## v1.0a2.172 — ANetCRAFT: mobs + fixes (June 2026)

ANetCRAFT now has living mobs. **Cows** spawn on the surface and drop Meat when killed. **Zombies** and **Skeletons** emerge at night and attack in survival mode. **Creepers** stalk you and explode when they get close, destroying nearby blocks. Point the mining cursor (arrow keys) at a mob and press **F** to attack it. Creepers flash yellow when their fuse is lit. Day/night cycle slowed to half speed; inventory overlay no longer flickers. Periodic auto-save added (every ~1 min of play).

## v1.0a2.171 — ANetCRAFT: lobby + game modes (June 2026)

ANetCRAFT now has a pre-game lobby with **Survival** and **Creative** mode selection. Each user's map is saved per-account and can be loaded or replaced with a fresh world from the lobby. Creative mode gives all blocks, instant mining, no item consumption, and free flight with W/S.

## v1.0a2.167–170 — ANetCRAFT game (June 2026)

Added **ANetCRAFT** — a Minecraft-inspired 2D survival game built into ANetBBS. Playable via SSH, telnet, and the web game terminal.

## v1.0a2.166 — Fix ZMODEM first-attempt failures (June 2026)

- **`features/xfer.py`**: Removed `--escape` from ZMODEM `send_flags`. With `--escape`, `sz` sends a ZSINIT frame to negotiate extended character escaping; SyncTERM (and many other terminal emulators) responds with ZRINIT instead of ZACK, stalling the handshake and causing sz to fail or retry. Without `--escape` the handshake is a clean ZRQINIT → ZRINIT → ZFILE sequence and transfers succeed on the first attempt. `sz` still escapes XON, XOFF, and DLE by default. `--binary` is kept to prevent newline translation of binary data.

## v1.0a2.165 — (superseded — do not use)

## v1.0a2.164 — Fix /imsg/directory crash + RSS short-URL + title word-wrap (June 2026)

- **`templates/imsg/directory.html`**: Fixed 500 crash on `/imsg/directory` — `current_app` is not a Jinja2 global in Flask, causing `UndefinedError` for any admin visiting the page. Replaced with `url_for('admin.registry_self')`. Also corrected the endpoint name (`admin.registry_self_register` doesn't exist).
- **`web/rss.py`**: Added `redirect_bp` with `GET /r/<item_id>` — unauthenticated 302 redirect to the original RSS article URL. No new DB model; uses `RssItem.id` as the short code.
- **`features/bbs_ui.py` `_rss_view_item`**: Replaced truncated raw link with a short URL (`{web_base}/r/{item.id}`) that always fits in 80 columns. Title now word-wraps at 74 cols instead of hard-truncating.
- **`web_app.py`**: Registered `redirect_bp`.

## v1.0a2.161 — Fix mixed-art \n stripping; 80-col viewer; ASCII status bar (June 2026)

- **`features/anedit.py` `launch_aneview`**: Changed `\n` strip logic — only strips for **pure flat block art** (block chars present, no cursor-pos sequences). Cursor-pos art (including mixed flat+cursor-pos like ConstructiveChaos) keeps `\n` so that flat header sections (e.g. the CONSTRUCTION logo) aren't collapsed into a single overflowing row. Cursor-pos absolute positioning is unaffected by artifact `\n` between sequences.
- **`features/anedit.py` `_ViewerScreen`**: Fixed all widths from `_W=79` to `80` — header bar, status bar, and `_ansi_trunc` limit. Prevents the rightmost art column from being clipped.
- **`features/anedit.py` `_ViewerScreen.draw_status`**: Replaced `↑↓` Unicode arrows (rendered as `??` in SyncTERM) with `PgUp/PgDn` ASCII text.
- **`web/render_msg.py`**: Same condition change as terminal — strip `\n` only for flat block art without cursor-pos. Fixes ConstructiveChaos logo being missing in web view.

## v1.0a2.160 — VT renderer for terminal; flat-art \n fix; borderless ANView (June 2026)

- **`features/ansi_html.py`**: Refactored VT engine into `_run_vt(text)` (returns cell grid). Added `to_ansi_lines(text, width=80)` — runs body through VT renderer and returns a list of terminal-ready ANSI-coloured strings. Added `_HEX_TO_FG` / `_HEX_TO_BG` reverse-palette maps. `_to_html_vt` now calls `_run_vt` internally.
- **`features/anedit.py` `_ViewerScreen`**: Removed box-drawing border. Row 1 = dark header bar (subject), rows 2–23 = 22-line content area (up from 17), row 24 = status/hints bar. No side borders or separator lines.
- **`features/anedit.py` `ANView`**: Overrides `_ensure_visible`, `_scroll_view`, `_pgup`, `_pgdn`, `_doc_start`, `_doc_end` to use the new 22-line viewport.
- **`features/anedit.py` `launch_aneview`**: Now handles CP437 decode + pipe-code conversion + art detection internally. Strips `\n` for cursor-pos or block-art bodies before calling `to_ansi_lines()`. All message types (ANSI art, cursor-pos art, plain text) render in the scrollable ANView — the raw full-screen display path is gone.
- **`features/bbs_ui.py`**: Removed cursor-pos detection and raw display path. All messages route to `launch_aneview()`.
- **`web/render_msg.py`**: `render_msg_body` / `render_msg_body_rich` now also strip `\n` for flat block-art (CP437 block chars, no cursor-pos) before VT rendering. Fixes horizontal-bar scatter for flat art in web view.
- **`echomail/qwk.py`**: At import time, if body contains ESC (`\x1b`), `\xe3` record-boundary bytes are stripped (not converted to `\n`). Real line structure in ANSI art comes from embedded `\r\n`. Plain-text bodies unchanged (`\xe3` → `\n`).

## v1.0a2.159 — Fix QWK art scatter: strip record-boundary \\n; pipe colors in terminal (June 2026)

- **Root cause**: QWK `0xE3` record separators are stored as `\n` in the DB. When the body has cursor-positioning sequences, those `\n` characters were inserting spurious line breaks between records, moving the cursor to the wrong row before the next `\x1b[r;cH` could reposition it. Each record's content landed on the wrong visual row, producing the scattered-fragment display.
- **`web/render_msg.py` `render_msg_body` / `render_msg_body_rich`**: Now uses `to_html()` from `features/ansi_html.py` (VT renderer) instead of the local streaming `_ansi_to_html` that silently dropped cursor-pos sequences. When cursor-pos sequences are detected after CSI-fix and pipe-code conversion, `\n` chars are stripped before calling the VT renderer. Flat-art and plain-text messages continue to use the streaming path.
- **`features/bbs_ui.py` `read_echo_area`**: (1) Removed `_BLOCK_ART` fallback — only messages with detected cursor-positioning sequences trigger full-screen raw display; flat art goes to scrollable ANView. (2) For cursor-pos art: `\n` stripped from `body_unicode` before streaming to terminal. (3) Added `|NN` pipe color → ANSI SGR conversion applied to `body_unicode` before display (handles Synchronet/Mystic pipe codes in message signatures and art).

## v1.0a2.158 — Strip SAUCE record from ANSI art before rendering (June 2026)

- **`echomail/qwk.py` `_parse_messages_dat`**: Strip the SAUCE metadata record at QWK import time. At the first `\x1a` (Ctrl+Z) byte in the decoded body, truncate — everything from `\x1a` onward is the binary SAUCE header (title, author, group, date, resolution fields) that, when passed to a renderer, appears as random ANSI sequences and scatters content across the display. Existing DB messages are handled at display time.
- **`features/bbs_ui.py` `read_echo_area`**: Strip the SAUCE record from the body before CSI fix and CP437 decode. Applies to all messages already in the DB regardless of when they were imported.
- **`features/ansi_html.py` `to_html`**: Strip the SAUCE record for string input. The bytes-input path already handled `\x1aSAUCE` stripping; this adds the same protection for the common string path used when rendering messages from the DB.

## v1.0a2.157 — ANSI art full-width VT renderer + block-char detection (June 2026)

- **`features/ansi_html.py` `_to_html_vt`**: VT renderer now renders every row to the full WIDTH (80 columns) instead of stopping at that row's `max_c`. Previously, rows with sparse content (art that writes only a few characters at specific columns) appeared as narrow bars in the browser; now all rows are consistently 80 chars wide, preserving the 2-D portrait shape.
- **`features/ansi_html.py` `to_html`**: Added `_HAS_BLOCK_ART` detection. When the body contains CP437 block/half-block characters (█ ▄ ▀ ░ ▒ ▓ or latin-1 mojibake equivalents U+00B0-B2, U+00DB-DF) together with any ANSI escape, the VT renderer is used instead of the streaming renderer. Covers flat ANSI art (no cursor-positioning) that previously got the fast streaming path.
- **`features/bbs_ui.py` `read_echo_area`**: (1) Added `f` to `_HAS_CPOS` regex (HP-style cursor-pos alias for `H`, was missing). (2) Added block-char detection as a fallback: CP437 block chars in latin-1 mojibake form trigger raw terminal display even when no cursor-pos sequences are detected. (3) Raw terminal output now converts `\n` to `\r\n` so flat-art rows return to column 0 on each new line.

## v1.0a2.156 — ANSI art terminal raw display; web CSS fix; ANView colors (June 2026)

- **`features/bbs_ui.py` `read_echo_area`**: Detect cursor-positioning sequences in the decoded body. If found (ANSI art), display raw to the terminal (`\x1b[2J` → stream body → `\x1b[25;1H` prompt) so full 2-D positioning and colors render exactly as the sender intended — matches Synchronet/SyncTERM behavior. Plain-text messages (no cursor positioning) still use the ANView scrollable frame.
- **`features/anedit.py` `launch_aneview`**: Change ANSI strip from removing ALL escape sequences to removing only cursor-positioning sequences (`[H f A B C D G J K s u r]`). SGR color/attribute codes (`\x1b[...m`) are now preserved so plain-text colored messages display with colors inside the ANView frame.
- **`features/anedit.py` `_ViewerScreen.draw_text`**: New ANSI-aware override. Uses `_ansi_trunc()` helper to truncate lines at `_TW` *visible* characters (ignoring escape sequences) and `\x1b[K` to erase trailing space rather than `.ljust()`. Prevents SGR sequences from being sliced mid-sequence and eliminates visible-width miscounts from ANSI bytes.
- **`features/anedit.py` `_ansi_trunc`**: New helper — truncates a string with embedded ANSI sequences to a maximum of `maxlen` visible characters while preserving all escape sequences.
- **`templates/echomail/read.html`**: Fix CSS for ANSI body display. Changed `white-space: pre-wrap` (allowed long art rows to wrap mid-row, scrambling the image) to `white-space: pre` with `overflow-x: auto`. Removed the per-span `white-space: pre-wrap` override. Fixed `line-height: 1.0` (was 1.5/1.2, adding unwanted gaps between art rows).

## v1.0a2.155 — CP437 decode in ANView; VT ANSI renderer for web; scroll fix (June 2026)

- **`features/anedit.py` `launch_aneview`**: Body was displayed as latin-1 mojibake (`Û` instead of `█`). Apply the same CP437 re-decode as the web path (encode latin-1 → bytes → decode cp437, keeping control chars 0x01–0x1F as-is) before stripping ANSI sequences. Block/line-drawing chars now render as proper Unicode in UTF-8 terminal sessions.
- **`features/anedit.py` `ANView._handle` + `_scroll_view`**: UP/DOWN arrows now scroll the viewport by 1 line immediately (was: moved cy without visible text change until cy left viewport). Space scrolls down a page. PgUp/PgDn/Home/End/Ctrl+Home/End all work. Replaced non-ASCII `↑↓` hint chars with ASCII `Up/Dn` — SyncTERM displayed them as `??`.
- **`features/ansi_html.py`**: Added virtual-terminal renderer `_to_html_vt`. When cursor-positioning sequences (`\x1b[H`, `\x1b[A/B/C/D`, `\x1b[2J`, etc.) are detected in the body, routes to the VT renderer which maintains an 80-column cell grid, processes all cursor moves, and outputs the correct 2-D layout. Streaming renderer kept for plain-text messages. Result: ANSI art on the web viewer displays with correct positioning instead of as a scrambled linear stream.

## v1.0a2.154 — Terminal ANSI fix, no-pager reader, ANView message viewer (June 2026)

- **`features/bbs_ui.py`**: Three improvements to the terminal echomail reader. (1) Apply the same CSI-split fix from v1.0a2.153 at view time so QWK `0xE3` artifacts (`[1;45m` etc.) don't appear in the terminal. (2) Remove the 18-line page-break (`-- more (Enter, Q=back to list) --`) — messages scroll freely. (3) Add `R=Reply` and `N=New Msg` options after reading; both launch ANEdit with the appropriate pre-fills in the current area.
- **`features/anedit.py`**: Added `_ViewerScreen`, `ANView`, and `launch_aneview`. ANView is a read-only ANEdit-frame viewer: navigation keys scroll, R/N/Q exit with action codes, bottom border shows viewer hints, status bar shows `Read-only · Ln:X/Y`, cursor is hidden.

## v1.0a2.153 — Fix ANSI sequences split anywhere by QWK line separator (June 2026)

- **`echomail/qwk.py` `_parse_messages_dat`** + **`web/render_msg.py` `render_msg_body`/`render_msg_body_rich`**: v1.0a2.152 only fixed the case where QWK `0xE3` fell between ESC and `[`. The separator can fall anywhere in a CSI sequence — in the parameter string too (e.g. `\x1b[1;\n37m`). Replaced the narrow `\x1b\n`→`\x1b` string replace with `re.sub(r'\x1b\n?\[[0-9;?\n]*[@-~]', lambda m: m.group(0).replace('\n',''), ...)` which strips `\n` from any position within a matched escape sequence, covering all split positions in one pass.

## v1.0a2.152 — Fix ANSI sequences split by QWK line separator (June 2026)

- **`echomail/qwk.py` `_parse_messages_dat`**: QWK byte `0xE3` (line separator) can fall between ESC (`0x1B`) and `[` when an ANSI CSI sequence straddles a QWK line boundary. After `\xe3`→`\n` conversion the sequence became `\x1b\n[1;45m`, which `_ansi_to_html`'s regex couldn't match. Added `.replace('\x1b\n', '\x1b')` after the separator conversion to rejoin split sequences before storage.
- **`web/render_msg.py` `render_msg_body` / `render_msg_body_rich`**: same `\x1b\n`→`\x1b` replacement at render time so all messages already in the database (imported before this fix) render correctly without a migration.

## v1.0a2.151 — Fix ANSI rendering in web echomail viewer (June 2026)

- **`web/render_msg.py` `_decode_charset`**: CP437 maps byte `0x1B` (ESC) to U+2190 (`←`), breaking `_ansi_to_html`'s `\x1b\[` regex so all ANSI color sequences leaked as visible text. Fix: after CP437 decode, restore bytes `0x01–0x1F` to their original control-char code points. CP437 is a single-byte codec so a `zip(raw, decoded)` rebuild is safe and exact.

## v1.0a2.150 — QWK overhaul: nine bug fixes (June 2026)

Full audit and repair of the QWK subsystem.

- **`poller.py`**: stamp `_qwk_conf_num` on outbound messages before `QWKClient.poll()`. Without it, all outbound echomail went to conference 0 (netmail) — hub discarded it.
- **`qwk_user.py` `upload()`**: REP body decode now uses `latin-1` + `replace('\xe3', '\n')` — CP437 decode turned 0xE3 into π instead of a newline.
- **`qwk_user.py` `upload()`**: added `network_id=area.network_id` to `EchomailMessage` — `NOT NULL` column was unset; `IntegrityError` was silently swallowed and all REP replies lost.
- **`qwk_user.py` `upload()`**: moved `EchoArea.query` outside the parse loop (was running one DB query per message).
- **`qwk_user.py` `_build_qwk_blob`**: fixed body line separator `\r\n` → `\xe3`; encoding `cp437` → `latin-1`. QWK readers saw blank bodies.
- **`qwk_user.py` `_last_read_map`**: `r.last_read_id` → `r.last_message_id`. AttributeError was swallowed; every download included all messages ever.
- **`qwk.py` `_ftp_download`**: hub_id candidates now listed before packet_id — for DOVE-Net you download `VERT.qwk`, not `ANET.qwk`.
- **`qwk.py` `_parse_messages_dat`**: skip killed messages (`active_flag == 0xE2`) after reading body bytes; also added defensive `\r\n`/`\r` normalization.
- **`qwk.py` `_clean_body`**: extract `@CHRS:`/`@CHARSET:` value and return as `chrs` key — previously discarded, so UTF-8 messages from Synchronet displayed as garbled CP437. `_import_message` already accepted `msg_data.get('chrs')`, it just never received it.

## v1.0a2.149 — Echomail area sysop-only flag + security levels (June 2026)

- **Echomail `EchoAreaForm`**: added `is_sysop_only` BooleanField; `min_access_level` already existed in the form but wasn't exposed in the template.
- **Area edit form** (`echomail/admin/area_form.html`): Active, Subscribed, Sysop Only checkboxes now shown together in a flags box; Min Access Level field alongside with hint text.
- **Areas list** (`echomail/admin/areas.html`): Status column replaced with four separate columns — Active ✓/✗, Subscribed ✓/✗, Sysop Only 🔒/—, Level badge (color-coded by range).
- **`new_area` route**: now saves `is_sysop_only` and `min_access_level` on area creation.
- **`web/echomail.py`**: extracted `_check_area_access(area)` helper; applied to area view, thread, compose, and next-unread routes. Area index and compose dropdown filter by both `is_sysop_only` and `min_access_level <= user.access_level`.
- New templates: `auth/verify_sent.html`, `auth/verified.html`, `auth/resend_verification.html`.
- Admin nav: new "SMTP / Email" link above Settings divider.

### 6 new web games
- **Klondike Solitaire** — full rules, undo, auto-foundation, win detection.
- **Video Poker (Jacks or Better)** — 5-card draw, pay table, multi-bet, hold mechanic.
- **Texas Hold'em vs CPU** — 4-player table, easy/medium/hard bluffing AI, full 7-card hand eval.
- **Blackjack** — hit/stand/double/split, 4-deck shoe, chip-click bet UI, dealer to 17.
- **Slot Machines** — 3 themed machines (Classic Bars, Lucky Fruits, Retro BBS) with animated reels.
- **Galaga** — Canvas arcade: enemy formations, dive attacks, tractor-beam boss capture, particle FX, touch controls.

## v1.0a2.146 — Fix dial-out directory DB lookup in terminal context (June 2026)

- Root cause of dial-out directory not syncing: `_load_directory()` in
  `features/dialout.py` queried the DB without a Flask app_context. Terminal
  processes have no ambient Flask context so the query raised silently
  (except pass), always falling back to the hardcoded DEFAULT_DIRECTORY.
  Added `with _app().app_context():` — same pattern as bbs_ui.py / menu_engine.py.
  Both PeerBbs (web directory) and DialoutDestination entries now appear on
  SSH/telnet.

## v1.0a2.145 — BBS Directory: edit local entries + remove Who's Online (June 2026)

- Local BBS entries in the web admin (`/bbses/admin`) can now be edited via a
  modal form — all fields: name, hostname, port, web URL, location, software,
  FTN address, description, active toggle. Previously delete/disable only.
- Removed finger/who's-online polling entirely: `_do_finger`, `_refresh_peer`,
  background refresh, online count badges, "Who's on" button, admin columns.
  `view.html` is now a clean BBS detail/connect page.

## v1.0a2.144 — Dial-out dir sync + User access flags (June 2026)

- Terminal dial-out menu now reads from PeerBbs (web BBS Directory Local tab)
  as primary source, with DialoutDestination as secondary. Entries added via
  the web BBS Directory are now visible on SSH/telnet.
- New `user_access_flags` table; sysop can suspend echomail, MRC, IRC, games,
  QWK downloads, or file downloads per user from `/admin/users/<id>/manage`.
  New table — upgrade-safe, no migration needed. Suspended users see a clear
  message and are returned to the menu.

## v1.0a2.143 — Fix ANSI menu action: @PAUSE@ pagination + end-of-screen hold (June 2026)

- `@PAUSE@` in ANSI body now splits into pages; user presses any key to advance.
  Previously the code passed through as literal text on screen.
- `ansi` menu action now forces a final key-wait after the screen displays so
  the menu doesn't immediately redraw and erase the content.
- `_show_ansi_screen` gains `force_pause=False` param; `_act_ansi` passes `True`.

## v1.0a2.142 — BBS Directory: correct EtherTerm XML parser (June 2026)

- Both TelnetBBSGuide and IPTIA use the same EtherTerm `dialdirectory.xml`
  format: `<BBS name="..." ip="..." port="..." protocol="TELNET" />`.
  Switched from ElementTree (fails on unescaped `&` in BBS names) and
  failed CSV/pipe parsers to a single regex-based `_parse_etherterm_xml()`
  shared by both sources. Parses 1,075 TelnetBBSGuide entries and 1,810
  IPTIA entries correctly. TelnetBBSGuide is fetched as a monthly ZIP;
  `dialdirectory.xml` is extracted from inside it.

## v1.0a2.141 — BBS Directory: correct TelnetBBSGuide + IPTIA formats (June 2026)

- **TelnetBBSGuide**: rewritten from CSV to ZIP parser. Monthly file URL
  constructed dynamically (`ibbs{MM}{YYYY}.zip`); falls back to previous
  month if current month returns 404. Extracts the text file from inside
  the ZIP and parses pipe-delimited rows with flexible column detection.
- **IPTIA**: rewritten from CSV to XML parser. Fetches
  `dialdirectory.xml` and parses with `xml.etree.ElementTree`, trying
  common tag names for name/address/port/sysop/location/software/web/description.
- Both parsers handle unknown field names gracefully; exceptions are caught
  and return 0 instead of crashing the background thread.

## v1.0a2.140 — BBS Directory CSV parser fixes (June 2026)

- **IPTIA parser crash** — `csv.DictReader` produces a `None` key when a CSV
  header row has a trailing comma. `k.strip()` on `None` raised `AttributeError`.
  Both parsers now filter `if k is not None`. Fixed in `web/peers.py`.
- **Parser exceptions no longer crash background thread** — `parser(text)` and
  the DB insert block are now each wrapped in try/except so a malformed CSV
  silently returns 0 instead of killing the refresh thread.
- **TelnetBBSGuide URL updated** to `/bbs/list/full/csv/`; both external URLs
  are now overridable via `TELNETBBSGUIDE_CSV_URL` and `IPTIA_CSV_URL` in `.env`.

## v1.0a2.139 — Fix 500 on BBS Directory (missing PeerBbs columns) (June 2026)

- **Hotfix**: `PeerBbs` model was missing the new columns (`is_approved`,
  `telnet_port`, `web_url`, `location`, `software`, `submitted_by_user_id`)
  added in v1.0a2.138. SQLAlchemy raised `InvalidRequestError` on
  `filter_by(is_approved=True)` because the attribute wasn't defined in
  the model class. `_ensure_column` adds them to the DB but the ORM also
  needs them in the model. Fixed in `models.py`.

## v1.0a2.138 — Enhanced BBS Directory: TelnetBBSGuide + IPTIA tabs (June 2026)

- **Three-tab BBS Directory** — Local (existing peers + user self-submission
  with sysop approval), TelnetBBSGuide (CSV fetched + cached every 6h),
  IPTIA (same pattern). Each entry shows name, sysop, location, software,
  description, and Telnet/Web connect buttons. Admin panel handles pending
  approvals and manual external refresh. New `external_bbs_cache` table;
  new columns on `peer_bbses` via `_ensure_column`.

## v1.0a2.137 — Session crash fix; nginx MRC auto-repair (June 2026)

- **Session crash on early disconnect** — `UnboundLocalError` in `session.start()`
  `finally` block when a connection exited before completing login (bot gate, failed
  login, all nodes full). `_hb_task = None` was inside the outer `try`; moved it
  before the `try` alongside `presence = None`. `core/session.py`.

- **nginx MRC auto-repair** — `update.sh` now detects and inserts the missing
  `location /mrcws` + `location = /mrc-auth-check` nginx blocks if absent.
  Without them web MRC WebSocket connections returned 404 from gunicorn.

## v1.0a2.136 — Security questions at terminal registration; sysop profile field control (June 2026)

- **Sysop profile field control** — new `BuiltinFieldConfig` model lets sysops
  enable/disable each built-in profile field (Display Name, Bio, Location, Website,
  Forum Signature, FTN Tagline, Date of Birth, Show Email Option) from
  `/admin/custom-fields`. Disabled fields are hidden from both the profile edit
  form and the public profile view. Defaults to all enabled. Custom fields
  (add/edit/delete) remain on the same page.

- **Security questions on terminal registration** — new users registering via
  telnet/SSH now complete the same 3 password-recovery security Q&A as web
  registration. Questions are presented numbered; user picks 3 different ones
  and enters answers. Answers saved via `UserSecurityAnswer` (same table/hashing
  as web). Implemented in `_collect_security_questions()` in `core/session.py`;
  `save_security_answers()` added to `UserManager`.

## v1.0a2.135 — Duplicate email DoS fix; case-insensitive usernames (June 2026)

- **DoS crash fix** — registering with a duplicate email caused an unhandled
  `IntegrityError` that terminated the BBS session. Added `email_exists()` and
  `username_exists()` pre-checks in the registration loop; user is re-prompted
  with a clear message instead of crashing. `create_user()` now returns a result
  code string and catches `IntegrityError` as defense-in-depth.
- **Case-insensitive usernames** — all username lookups (registration, login,
  `get_user`, web form validation) now use `func.lower()` so `StingRay` and
  `stingray` are treated as the same account. Existing usernames unchanged.

## v1.0a2.134 — MRC terminal fixes; Custom User Fields; User ID # (June 2026)

- **MRC PAUSED indicator** — `\x1b[1;37;43m` bold white on orange (was invisible reverse-video in SyncTERM)
- **MRC char count** — top-right of status bar shows remaining chars (140 - typed); cyan > 15, yellow ≤ 15, red if over
- **MRC `/t` DM local echo** — after sending DM, client echoes `[DM -> target] message` in pink
- **MRC tab autocomplete case fix** — old code doubled wrong-case prefix; now deletes from start and re-inserts full canonical-case name
- **MRC status bar live update** — `_draw_status_line()` called after every keypress, backspace, and Ctrl+U
- **Custom User Fields** — new `UserField` + `UserFieldValue` models; admin CRUD at `/admin/custom-fields`; field types: text/textarea/url/number/select; shown on public profile and admin user-manage page
- **User ID #** — `user.id` (auto-increment PK) displayed in admin users list and public profile page

## v1.0a2.132 — ANEdit: CP437 char fix (■/·); slash /commands replace F-keys (June 2026)

- **CP437 fix** — title bar used `●`/`○` (not in CP437), displayed as `?` on
  SSH/telnet terminals. Replaced with `■` (CP437 0xFE, modified) and `·` (clean).
  Also fixed em-dash `—` in flash messages and box titles.
- **Slash /commands** — F-keys are unreliable over SSH. ANEdit now intercepts `/`
  typed at column 0 and reads a short command: `/?` or `/help` = help, `/t` = theme,
  `/m` = mark, `/cc` = color picker, `/find`, `/replace`, `/undo`, `/redo`, `/save`,
  `/send`, `/q` = abort. Unknown commands fall back to inserting the text. F-keys
  retained as secondary bindings. Hint bar and help overlay updated.

## v1.0a2.131 — ANEdit wired into message boards, PMs, echomail; board/thread coloring (June 2026)

- **ANEdit now used for all message composition** — `_post_compose`, `_send_pm`,
  and `_compose_echomail` all launch the full ANEdit screen editor instead of the
  old line-by-line prompt. Reply flow pre-loads the parent post as `> ` quoted text
  and pre-fills `Re: subject`.
- **Thread list coloring** — board message list now uses full ANSI color: yellow
  numbers, cyan reply count, white subject, green author, grey timestamp. Clears
  screen and shows the board-name banner.
- **Thread reader coloring** — post header shows yellow [OP]/grey [Reply N] tags,
  cyan subject, green author. Quoted lines (`>`) rendered in grey. Footer + prompt
  added.

## v1.0a2.130 — ANEdit terminal message editor; security questions on registration (June 2026)

- **ANEdit** — new full-featured 79×23 ANSI message editor (`features/anedit.py`).
  Undo/redo (60 levels), block mark/cut/copy/paste, find & replace-all, smart
  word-wrap, live word/char count, three color themes (F9), help overlay (F1),
  Mystic |XX color-code picker (F4), draft auto-save every 30 s with recovery on
  next entry. Entry point: `await launch_anedit(session, quote, subject, username)`.
- **Security questions at registration** — new users must now complete all 3
  password-reset security questions during signup. `RegisterForm` extended with 3
  `SelectField`+`StringField` pairs with duplicate-question validation. Answers
  saved via `UserSecurityAnswer` before commit.

## v1.0a2.129 — IRC ESC exit fix; docs sidebar; bulletins; chat.ans; read/write security (June 2026)

- **IRC startup ESC exit** — ESC from startup screen now correctly exits IRC. Root
  cause: `run()` loop condition checked `not self.bms` which is always False (list
  always has ≥1 bookmark). Fixed by having `_startup_loop()` return a boolean and
  `run()` breaking on `True`.
- **Docs sidebar spacing** — increased padding to 12px/16px, font-size 0.92rem,
  line-height 1.6, min-width 250px (prior fix landed in local files but was not
  in the v1.0a2.125 tarball that was deployed).

## v1.0a2.126 — bug fixes: bulletins, docs sidebar, chat.ans, read/write security (June 2026)

- **Bulletins HTML linefeed** — swapped bulletin view default from `<pre>` to
  markdown (with `nl2br`); `\r\n` from telnet input now renders as `<br>` instead
  of blank lines. Raw view still accessible via toggle button.
- **chat.ans display codes** — `chat.py` now passes `chat.ans` through
  `display_codes.apply()` before writing, so Synchronet @-codes and Mystic |XX
  colors embedded in the ANSI screen are rendered correctly.
- **Docs sidebar spacing** — `py-1` → `py-2`, `0.85rem` → `0.875rem`,
  `line-height: 1.3` → `1.5`; sidebar page list no longer cramped on long names.
- **Separate read/write access levels** — `Board` and `FileArea` now have a
  `min_write_level` column (NULL = inherit read level). Admin UI updated with
  side-by-side fields. `web/boards.py` and `bbs_ui.py` enforce write level on
  post creation and file upload respectively. Auto-migrated on startup.

## v1.0a2.125 — ANetIRC v7: pure-Python asyncio IRC client (June 2026)

Complete rewrite of ANetIRC. Drops the C binary + PTY bridge entirely. The new
`anetirc2.py` runs as a native asyncio coroutine inside the BBS session.

- **TLS fixed** — `asyncio.open_connection(ssl=ssl_ctx)` is non-blocking; the
  old C client's blocking TLS handshake caused hard lockups on SSL servers.
- **Cross-arch** — no binary; works on x86-64, ARM, any Python 3.10+ platform.
- **SASL PLAIN** — full `CAP LS 302` → `CAP REQ :sasl` → `AUTHENTICATE PLAIN`
  state machine. Password field (7th pipe field) triggers SASL; no password = no
  SASL (plain nick/user registration).
- **Startup manager** — inline field editing per bookmark (Tab/Up/Down to move
  between fields, Enter to confirm), TLS toggle (Space), theme cycle (T), save
  (A), delete (D). Three color themes: Cyan/Green/Amber.
- **Word-wrap** — lines break at word boundaries; continuation lines indent to
  align with message text. Adapts to any terminal width.
- **Scrollback** — PgUp/PgDn, 800 display rows, 2000 stored lines.
- **Tab completion** — completes nick from users panel; cycles on repeated Tab.
- **Command history** — Up/Down arrows recall previous input lines.
- **mIRC color** — incoming color codes stripped; CTCP ACTION rendered as
  `* nick action`.
- **Backward-compatible** — same bookmark config format, same entry point
  (`launch_anetirc_telnet`). C binary kept in tarball for reference/revert.

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
