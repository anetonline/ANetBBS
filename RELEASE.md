# ANetBBS v1.0b2.225 — TIC manifests never reaching the inbound scanner at all (July 2026)

v1.0b2.224 fixed three real bugs in TIC processing, but a live diagnostic run afterward showed zero `.tic` files anywhere in `inbound/` — only binaries. The processor was never the actual bottleneck. Root cause, confirmed with help from a peer's own sender-side BinkP log: the inbound BinkP listener's file-classification regex (meant to catch Mystic's point-targeted mail-bundle extensions like `.tc1`/`.td2`) also coincidentally matches `.tic` itself, so every inbound TIC manifest was silently misrouted into the mail-packet importer and discarded — never reaching the scanner, never logged as a failure. Fixed by excluding `.tic` explicitly. Also adds a manual "Rescan Inbound Now" button on Admin → TIC In Log, so a sysop can force a rescan on demand instead of waiting for another BinkP session or a service restart.

6 new tests plus a full binkp/binkp_server regression sweep (199 tests), all clean.

---

# ANetBBS v1.0b2.224 — TIC inbound processing fixes (July 2026)

Live-caught: TIC files were piling up in `inbound/` unprocessed (a newly-subscribed ANotherNetwork ANSI-art feed named as the reported example). Three real gaps in `process_tic()`, all classic FTN/TIC interop rough edges:

- CRC comparison didn't tolerate unpadded manifest CRC values (common from older TIC generators) — a file whose real CRC started with a zero nibble failed the check forever, every retry, permanently.
- Binary lookup was exact-case only — Linux is case-sensitive, and mailers commonly re-case filenames on the wire regardless of the TIC's own `File:` field. A binary sitting right there under a different case was never found.
- Successfully-filed TICs were never cleaned up from `inbound/` — only ever copied elsewhere, so every TIC ever received (success or failure) piled up there forever with no way to tell which were actually stuck.

All three fixed; a genuinely wrong CRC and a genuinely missing binary are both still correctly rejected. 10 new tests. Full suite verified clean (1764 passed, 2 skipped).

---

# ANetBBS v1.0b2.223 — Zip-archive image galleries (July 2026)

Image galleries can now point at a directory of `.zip` archives (one photo per zip, Digital Showroom-style) instead of only loose image files — matches how several TIC-fed file areas already arrive (e.g. a daily NASA photo feed), so a gallery can point straight at that same directory with no conversion step. Each zip's one image is extracted in memory only, never to disk, for both thumbnails and full-size view. Extended to the public gallery, the admin file manager, admin upload, and the terminal (chafa/img2sixel) fallback viewer.

Also fixes the CI failure on the previous push — an in-progress edit toward this feature got synced mid-edit and failed the pyflakes gate; nothing wrong with v1.0b2.222's actual content. Confirmed clean now.

14 new tests. Full suite verified clean (1754 passed, 2 skipped).

---

# ANetBBS v1.0b2.222 — Full install/update re-verify + webhook board scoping (July 2026)

Phase 4, the FINAL phase, of the 4-part audit list (auth/session core, file areas, message boards, install/update re-verify). Two parallel research passes (install.sh, update.sh + the installer package), every finding personally verified before fixing.

**High:**
- `anetbbs-upgrade` (the in-app upgrade wizard) never actually restarted the real running service — it referenced legacy unit names that don't exist on any current install, so it silently rsynced new code in but kept serving pre-upgrade code indefinitely. Fixed.
- Same wizard's rsync could have wiped a sysop's doors/ tree and MRC bridge config — missing the same excludes `update.sh` already has after a real prior incident. Fixed.
- Every fresh `install.sh` install silently created two full-admin accounts (the sysop's chosen one, plus an unlisted auto-generated "admin" fallback). Fixed at the root cause plus a cleanup step.
- The alternative `anetbbs-install` Python wizard installed broken legacy systemd units — telnet/SSH/rlogin were effectively non-functional through that documented, supported path. Fixed.
- `TRUST_PROXY_HEADERS` (new in v1.0b2.218) was never set by install.sh even in its default production+nginx mode — every visitor's IP appeared as 127.0.0.1 to Flask, silently breaking IP bans/rate-limiting on a stock install. Fixed.
- `MSP_ENABLED`/`SYSTAT_ENABLED` were hardcoded True regardless of what the sysop configured. Fixed.

**Medium:** a dead env-var-passing path between the web "Check for Updates" button and update.sh; a missing disk-space preflight in the in-app upgrade wizard; `tzdata` missing from setup.py (present in requirements.txt since v1.0b2.202, so upgrades never picked it up); FTP/PETSCII firewall ports never mentioned in the install summary.

**Follow-up (requested):** webhooks can now be scoped to a single board, so a "new post" mirror doesn't leak restricted-board content externally.

~20 new tests. Full suite verified clean (1744 passed, 2 skipped).

**This closes the 4-phase audit list.**

---

# ANetBBS v1.0b2.221 — Full message-boards security audit (July 2026)

Phase 3 of the 4-part audit list (auth/session core, file areas, message boards, install/update re-verify). Two parallel research passes (web `boards.py`, terminal ANSI + PETSCII posting/threading), every finding personally verified before fixing. Same root pattern throughout: the READ-side gap on boards was already fixed in an earlier pass — this pass found it was never mirrored to the WRITE/interaction side.

**High:**
- `reply_post()` had zero board-access enforcement at all (not read-level, not write-level) — any authenticated user could reply into a restricted board's thread by guessing a post_id. Fixed.
- `subscribe()` had no access check, letting a below-level user get every future restricted-board post leaked via the subscriber-notification fan-out. Fixed.
- `notify_mentions()` pushed up to 280 chars of restricted board content to any @-mentioned user regardless of their access level. Now access-filtered.
- Terminal board posting (ANSI + PETSCII) checked neither the board's posting level nor a locked-thread flag at all — PETSCII's reply path specifically missed a check its own new-thread path already had. Both fixed via a single choke point per platform.

**Medium:** `votes.py`'s vote/tally lookup, `react()`, and `saved.py`'s bookmark feature all had the same missing-board-access-check IDOR pattern — fixed. `/sitemap.xml` enumerated restricted boards/posts to unauthenticated crawlers — now filtered. No flood protection on board posting — added a 20/5min rate limit. Terminal posts skipped the sysop word-filter blocklist — now applied. Closed a latent IDOR defense-in-depth gap in the terminal thread reader.

**Deferred:** webhook broadcasts aren't board-scoped (admin-trust-boundary, feature addition not a bug fix) — noted as a known limitation.

~30 new/updated tests. Full suite verified clean (1732 passed, 2 skipped).

---

# ANetBBS v1.0b2.220 — Full file-areas security audit (July 2026)

Phase 2 of the 4-part audit list (auth/session core, file areas, message boards, install/update re-verify). Two parallel research passes (web file-area routes, FTP server), every finding personally verified before fixing.

**Critical:**
- Unsanitized QWK `packet_id` from the public network-join form flowed straight into a filesystem path (the FTP server's per-node home dir) — `"../../.."` passed the old `Length(max=8)`-only check cleanly, and once a sysop approved the request it became a real escape-the-data-dir FTP session root. Fixed at both the public form (charset validation) and the approval handler (server-side re-check, defense in depth).

**High:**
- `smart_upload()` (the auto-detect-target-area upload route) never checked `FILE_MOD_QUEUE_ENABLED` — any user could bypass sysop moderation review entirely via this route while the per-area `upload()` route correctly quarantined. Fixed to match.
- FTP login only checked `User.is_active`, never `is_locked`/`is_verified` — a locked-out or unapproved account could still fully authenticate over FTP. Fixed.
- Zero brute-force protection on FTP auth (same bug class fixed for telnet/SSH/rlogin in v1.0b2.218, missed on this transport). Fixed with the same `AutoBanConfig`/`IpBan` models.

**Medium:** regular FTP users had unrestricted DELE/RNFR/RNTO/MKD/RMD regardless of area upload permission (now gated); FTP uploads skipped the ClamAV scan every web route uses (now scanned); QWK node password comparisons used `!=`/`==` instead of constant-time `hmac.compare_digest`; a path-traversal guard was missing on `manage_desc()`; ratio-enforcement crashes were silently swallowed instead of logged; quarantine filenames had a timestamp-collision risk.

**Deferred:** a file-quota TOCTOU race and a `FileQueueEntry.approve()` TOCTOU — both low-severity, noted not fixed.

~40 new/updated tests. Full suite verified clean (1703 passed, 2 skipped).

---

# ANetBBS v1.0b2.219 — Hotfix: PETSCII/multinode login crash (July 2026)

Emergency fix, live-caught right after v1.0b2.218 deployed: **PETSCII terminal login was completely broken** ("Menu error: f-string: unmatched '(' "), and the multinode "Who's Online" screen crashed the same way.

Root cause: an f-string like `f'...{fmt_eastern(x, '%Y-%m-%d')}...'` — a single-quoted f-string with a single-quoted argument nested inside its `{}`. Valid on Python 3.12 (this dev sandbox), a hard `SyntaxError` on Python 3.10/3.11 (what production runs) — so `py_compile` here never caught it. Fixed in `features/petscii_ui.py` and `features/multinode.py`; swept the whole `anetbbs/` tree for the same pattern and confirmed no other instances.

---

# ANetBBS v1.0b2.218 — Full auth/session security audit (July 2026)

The single most security-critical batch shipped this project. Three parallel research passes (web auth, terminal auth, access-control primitives), every finding personally verified before fixing. See `docs/SECURITY.md` for the full sysop-facing summary.

**Critical:**
- `evaluate_access()`'s anonymous default was 10 ("registered"), not 0 — an anonymous visitor silently passed the "registered users only" gate on boards/echomail/QWK/RSS/file areas using the standard default.
- `X-Forwarded-For` was trusted unconditionally in four places, letting a direct connection spoof past IP bans/rate limits, or make the login auto-ban land on an arbitrary victim IP. Fixed via an opt-in `TRUST_PROXY_HEADERS` setting + Werkzeug's ProxyFix.
- Telnet/SSH/rlogin/PETSCII login had ZERO rate-limiting or lockout, unlike the web login — now shares the same IP-ban/auto-ban policy.

**High:**
- `/auth/forgot` was a reliable username/email enumeration oracle via its redirect target — fixed with a uniform redirect + unanswerable decoy question for nonexistent accounts.
- Security-question brute force had no rate limit or attempt cap — now capped at 5 attempts + rate-limited.

**Medium:** rlogin header parser and terminal read_line/read_password had no length caps (memory DoS risk) — now bounded. `/auth/forgot` and `/auth/verify/resend` had no rate limiting — added.

**Low:** two registration race-condition bugs (wrong error message / raw 500) fixed on both the web and terminal registration paths.

~40 new/updated tests. Full suite verified clean (1680 passed, 2 skipped).

---

# ANetBBS v1.0b2.217 — Uncapped admin pending-queue lists (July 2026)

Two admin pending-request lists (Network Join Requests, QWK Node Requests) had no cap, unlike their reviewed counterparts — both fed by public unauthenticated forms. Capped at 500 with a warning flash if hit, so a real flood isn't silently hidden.

Full suite verified clean.

---

# ANetBBS v1.0b2.216 — Full echomail/QWK subsystem audit (July 2026)

Sysop asked for a full pass over echomail/QWK — every error/gap, docs kept in sync. Five parallel research passes (BinkP transport, AreaFix/FileFix, QWK, web admin layer, docs/wiki), every finding personally verified against the real code before fixing. 20 real bugs fixed, most with new regression tests.

**Security fixes:**
- **AreaFix/FileFix leaf-side password bypass**: `process_request()` in both `areafix.py` and `filefix.py` only ever rejected a request when a password was configured AND wrong — a network with no `areafix_password`/`binkp_password` set at all sailed straight through with zero authentication, letting any spoofable inbound netmail addressed to "areafix"/"filefix" freely subscribe/unsubscribe every echo/file area. The hub-side sibling function had already been fixed for this exact bug class in an earlier session — the fix was never mirrored back to the leaf-side function. Fixed both.
- **Cross-area message leak**: `echomail.thread()` fetched its seed message by bare ID with no `area_id` scoping, unlike every sibling route in the file — a logged-in user could view a message's from/to/subject/body from a sysop-only or restricted-access area by URL.
- **QWK bypassed all echo-area access control**: `/qwk/download` and `/qwk/upload` had zero gating, unlike every other echomail entry point — any logged-in user's QWK packet included sysop-only/restricted area content, and REP upload let them post into those same areas.
- **BinkP inbound listener auto-created echo areas with no review**: any peer that completed a BinkP handshake caused an unrecognized `AREA:` tag to silently create a new, immediately-active, immediately-subscribed echo area. Now routes to BadAreaLog for sysop review, matching the outbound-dial receive path's existing behavior.
- Minor: missing CSRF token on the hub-identity "Make Default" button; `QWKNodeForm.packet_id` and the QWK node-request approval path had no regex validation, unlike the self-service API (packet_id flows into filesystem paths).

**Functional fixes:**
- "Poll Node" (hub dials out to a downstream node) never flushed that node's queued netmail.
- Nodelist import silently dropped every point-address entry (`12.5` style).
- QWK HTTP-hub REP import tagged every message with an arbitrary network on multi-network installs; added per-message error isolation and msg_id dedup, mirroring fixes already made once in the sibling FTP-hub importer.
- Two intentionally-unsecured ANetBBS BinkP links couldn't authenticate with each other (client's `-` placeholder for "no password" wasn't recognized by the server).
- AreaFix/FileFix hub-side log rows never recorded `network_id`, hiding downstream-node activity from the network filter.

**New: AreaFix Log now shows which bot handled each row** (Bot column + filter).

**Docs/wiki**: fixed a wrong `%RESCAN` claim, a fabricated Network Join field list, added FileFix mentions, cross-referenced the TIC Out Log.

Full suite verified clean (1639 passed, 2 skipped).

---

# ANetBBS v1.0b2.215 — Hub-management logging pass + Network Join fixes (July 2026)

Sysop asked for a TIC-out log for hub management and to make sure hub-management logging is complete, plus two Network Join Requests fixes.

**TIC/file-echo delivery logging** — `HatchQueue.retry_count`/`error_message`/`status='failed'` were part of the model from the start (its own docstring: "Failures bump retry_count; we cap at retry_max and mark failed"), but neither delivery path (outbound-dial in `binkp.py`, inbound-listener in `binkp_server.py`) ever actually wrote to them — a failed delivery attempt was silently indistinguishable from one never even tried, and the Hub admin page's "Failed" counter was permanently 0. Now both paths record every failed attempt (bumping `retry_count`, storing the reason) and give up after 20 attempts, flipping the row to `failed` instead of retrying forever with zero visibility.

**New TIC Out Log** (`/admin/hatch-log`) — the Hub admin "TIC" tab only ever showed two aggregate counters with no way to see what's actually queued, for which peer, or why something failed. New page lists every outbound hatch item (pending/sent/failed, filterable), linked from the Hub TIC tab alongside the existing (previously orphaned — no nav link anywhere) TIC In Log.

**AreaFix Log now shows which bot handled each row** — FileFix (file-echo subscriptions) has always logged into the same `AreafixLog` table as AreaFix (echomail subscriptions), distinguished only by an unshown `bot` column. The page was titled/described as AreaFix-only, with FileFix activity silently commingled and unlabeled. Added a Bot column + filter.

**Network Join Requests**:
- Node auto-numbering (added v1.0b2.83) only fired when `NetworkJoinConfig.binkp_zone`/`binkp_net` were configured *separately* from the hub's own `HubIdentity.binkp_zone`/`binkp_net` (already set and used elsewhere — nodelist, default outbound address). A sysop who'd already told the system its own zone:net had no reason to expect a second, redundant setting, and approvals silently used whatever address the applicant typed into the public form instead of auto-numbering. Now falls back to the hub identity's own zone/net; `NetworkJoinConfig`'s fields still override when explicitly set.
- Added an explicit "View" button to the pending requests list (the full detail page already existed, linked only via the BBS Name text — easy to miss next to the prominent Approve/Deny buttons).

Full suite verified clean (1618 passed, 2 skipped).

---

# ANetBBS v1.0b2.214 — TIC/file-echo hatch-out: third delivery direction fixed (July 2026)

Sysop-reported "TIC isn't working right" led to a full re-audit of the TIC/file-echo pipeline. The manifest parsing, security checks, and two of the three BinkP delivery directions were already correct and covered by tests from an earlier audit — but the third was silently missing:

- **Upstream hub dialing IN to us never flushed pending file-echo hatch-out.** binkp_server.py's inbound listener already handled outbound echomail/netmail correctly for this direction (the hub calls us), and already handled file-echo hatch-out for the *other* two directions (we dial our hub; a downstream node dials us) — but never queried `HatchQueue` at all when the connecting peer matched by network `hub_address` instead of a specific node. A file subscribed via FileFix to go out to the hub would sit in `status='pending'` forever unless the sysop happened to poll out to the hub first themselves, with the admin's "Pending" counter the only visible symptom.

Added regression tests for the fixed direction and fixed 8 existing BinkP-listener tests whose hand-rolled query fakes needed to account for the new `HatchQueue` lookup.

Also documented a real-world dosemu2 gotcha reported live: `ERROR: MFS: failed to get xattrs for .../TWERR.LOG, Numerical result out of range` when running door games (e.g. TW2002) means the filesystem `/opt` (or wherever DOS game data lives) is mounted without the `user_xattr` option — dosemu2's MFS layer needs it. Fix is an fstab mount-option change, not an ANetBBS bug.

Full suite verified clean (1605 passed, 2 skipped).

---

# ANetBBS v1.0b2.213 — Pre-release audit #2: pyflakes CI gate green (July 2026)

The new pyflakes CI job (added last batch) was failing on ~40 pre-existing findings across ~20 files that predated the CI gate itself — none were new regressions from recent work. All genuinely dead code (unused local variables, redundant f-string prefixes with no interpolation, `global` declarations that were never needed since the function only mutated the object in place rather than rebinding the name). Also fixed 2 spots where a `# noqa` comment was silently doing nothing: plain `pyflakes` (unlike flake8) has no inline suppression syntax at all, so two "load-bearing side-effect import" cases now use a real reference instead of a comment to satisfy the checker.

One small real bug found along the way: the sysop setup wizard's BBS Name field was captured from the form but never actually persisted anywhere — silently ignored on every submission. Now writes to `.env` like the other wizard fields.

Full suite verified clean (1601 passed, 2 skipped). bandit re-verified clean (0 medium/high).

---

# ANetBBS v1.0b2.212 — Access-control audit batch, web IRC client fixes, full docs/wiki accuracy pass (July 2026)

**Access-control fixes** (Phase 1 of the pre-release audit, remaining routes):
- Vote tallies on private netmail/PMs were readable by anyone who could guess the message id — the read-only tally endpoint skipped the same visibility check the actual voting endpoint enforces.
- The leaderboard page leaked restricted/sysop-only board names and activity (post counts, top posters, reactions) to any visitor.
- Game Center's global scoreboard and per-game leaderboard both skipped the level-gating check every other game route already enforces — a gated game's name and scores were visible by id.
- A user could self-issue an anonymous file-share link for something they can already see and re-download it repeatedly to route around their own daily download quota.

**Web IRC client** (reported live — commands looked broken, had to fall back to the terminal client):
- `/msg target text` never echoed anything back to the sender, so e.g. `/msg nickserv identify ...` looked like it silently did nothing.
- A private reply from someone else (NickServ's included, since those are almost always sent as a NOTICE) landed in a browser tab that was never actually created — captured but invisible.
- Added `/query <nick>` and `/help`.
- The connect form's default server ignored Admin → IRC Server Presets entirely, always falling back to a hardcoded irc.libera.chat — now uses the sysop's configured default the same way the terminal client already does.

**Docs/wiki accuracy pass** — first full systematic sweep of all 52 wiki pages and 30 docs files (previous passes were reactive to specific bug reports). ~22 stale/wrong claims fixed: wrong routes (several pages pointed at URLs that don't exist), a wiki page describing an SSH public-key-auth feature that was never built (and is in fact intentionally disabled), swapped Mystic Python/Pascal door-type descriptions, a stale `/tmp` backup path doc (moved to the install dir a while back), a missing built-in game (ANetDarkForces) absent from two different game listings, and more.

Full suite verified clean (1601 passed, 2 skipped).

---

# ANetBBS v1.0b2.211 — Pre-release audit, batch 3: install/update script fixes (July 2026)

Full re-read of install.sh and update.sh (the actual Full Release critical path) via two independent audit passes, cross-checked against current code. Real bugs found and fixed:

- install.sh only wrote the anetbbs.service systemd unit if the sysop enabled Telnet or SSH during setup — but that same service also owns rlogin/FTP/PETSCII40/PETSCII80, which are documented as "enable later by editing .env" and have no unit to start without it. A sysop who declined both Telnet and SSH but later turned on PETSCII or FTP had nothing to `systemctl enable`. Now always written, matching how update.sh already handles it.
- update.sh's self-healing MRC bridge config generator (used when config.json is missing, e.g. upgrading a pre-MRC-bridge install) hardcoded the bridge's web_listen_port to 8080 instead of reading the real value from .env — silently mismatched nginx's proxy target on any install using a non-default web port.
- update.sh's rollback-on-failure path only restored the production database backup, never the dev database backup, even though both are backed up unconditionally up front. An install running against anetbbs_dev.db (via DATABASE_URL) had no real rollback if an update failed partway.
- Removed a harmless but dead RLOGIN_ENABLED check in install.sh's install summary (rlogin isn't wizard-configurable, the variable was never actually set).

Full suite verified clean; targeted upgrade/install test files re-run clean (27 passed).

Note for later: a cosmetic-only rollback gap was also found (legacy anetbbs-telnet/anetbbs-ssh unit files can be resurrected-but-disabled after a rollback that follows the telnet+ssh-unification migration) — doesn't break anything, left as-is.

---

# ANetBBS v1.0b2.210 — Pre-release audit, batch 2: install/update version-string fix (July 2026)

- Fixed a 3-way inconsistent hardcoded version string used for the MRC bridge `platform_info` field: `install.sh` and `update.sh` both had a stale, unrelated `BBS_VERSION="1.3.7"` literal (matching no real ANetBBS version, ever), and `mrc/bridge/config.example.json` had a third, different stale value (`"1.3.20"`). `install.sh` now derives `BBS_VERSION` dynamically from the `VERSION` file; `update.sh` reuses its existing `NEW_VERSION` variable instead of a separate hardcoded one; the example config now uses an obvious placeholder instead of a fake-looking real version number.
- Part of the pre-Full-Release audit (install/upgrade path re-verification).

Full suite verified clean (1588 passed, 2 skipped).

---

# ANetBBS v1.0b2.209 — Prepare for the v1.0.0 stable release version format (July 2026)

Getting ready for Full Release: v1.0.0 will be a plain stable version number (no alpha/beta marker), not a continuation of the beta build-number sequence. This build teaches every part of the update pipeline to recognize and correctly rank that new format alongside the existing vX.Y[ab]Z.NN beta/alpha form — shipping it now, ahead of the actual v1.0.0 tarball, so every install (including this one) has already updated its understanding before the cutover happens. Covers: the update-checker's version parser and comparison logic, the tarball-name matcher, the release-notes "what's new" panel, and the privileged upgrade wrapper's argument validation (plus its existing self-healing patcher, extended with a 3rd migration step, so an older already-deployed wrapper upgrades itself automatically on next use — no manual server-side intervention needed).

Full suite verified clean (1588 passed, 2 skipped).

---

# ANetBBS v1.0b2.208 — Pre-release security audit, batch 1 (July 2026)

First installment of a full pre-release code audit. Added bandit + pyflakes to CI (there was no lint/security-scanner CI at all before this). Real fixes found, not just tooling:

- Two stored-XSS gaps: the Markdown renderer (message/netmail bodies) and the wiki page renderer both silently fell back to emitting raw, unsanitized HTML if the bleach sanitizer library happened to be missing. The Markdown one is reachable via echomail/netmail from external FidoNet peers; the wiki one via any logged-in user's edit, not just admins. Both now fail safe instead.
- Access-control leak: public profile pages showed post subjects and board names from restricted/sysop-only boards to any visitor, including anonymous ones — the same class of bug already fixed once in board search.
- SSRF hardening on the RSS sixel-image preview (scheme-restricted to http/https).
- An unsafe tar extraction in the upgrade wizard (path-traversal protection added).
- A real crash bug: sending a private message in the terminal always crashed right after the message was saved, due to a missing import.
- ~70 dead imports cleaned up across the codebase.

Full suite verified clean (1577 passed, 2 skipped). This is the first of several planned batches — more to follow covering docs/wiki accuracy, the rest of the access-control sweep, and the install/upgrade paths.

---

# ANetBBS v1.0b2.207 — Wiki content now self-heals on update; docs accuracy + coverage pass (July 2026)

- Real root cause found: the in-app wiki's seed function was idempotent-only — once a page existed, no later content fix ever reached an already-seeded install, across several past docs/wiki accuracy sessions. Fixed: on every startup, any wiki page you haven't personally edited is now automatically refreshed to match the current shipped content; anything you've edited yourself is never touched.
- Fixed a wrong MRC hub port in the wiki (was showing 5000, real port is 5001), and a doc page that presented the retired anetbbs-telnet/anetbbs-ssh services as current instead of the unified anetbbs.service.
- Documented the new daily file download quota feature (wiki + docs/07-file-areas.md).
- Added wiki coverage for ~16 admin tools that had none: New User Questions, Inactive Users, Registration Attempts, Chat Bans, Time Budgets, Default Echo Subs, IRC Server Presets, Setup Wizard, Preflight Checklist, Security Updates, Door Errors, Connection Test, Activity Log, Check for Updates, Logon/Logoff Modules, and Pre-update Backups.

Full suite verified clean (1572 passed, 2 skipped).

---

# ANetBBS v1.0b2.206 — Daily file download quota by access level (July 2026)

New feature — admins can now cap how much a user downloads per day, scaled by access level, so one user can't tag-leech the entire file base overnight.

- Configure tiers under Admin > File System > Download Quotas: a list of (minimum access level, daily quota in MB) pairs. A user gets whichever tier has the highest access level they still qualify for — a level 75 user with tiers set at 50 and 100 gets the level-50 quota, not the level-100 one. No tier configured at or below a level means unlimited downloads for that level. Admins always bypass.
- Enforced everywhere a file can be downloaded: the web file-area and file-gallery download routes, ANSI telnet/SSH terminal (single and batch ZMODEM/YMODEM/XMODEM downloads), the PETSCII/C64 terminal (XMODEM), and FTP (RETR).
- Resets at Eastern midnight.

Full suite verified clean (1567 passed, 2 skipped).

---

# ANetBBS v1.0b2.205 — AreaFix %RESCAN could only ever be used once per area/node (July 2026)

- After the first %RESCAN for a given area, every subsequent %RESCAN request (from the same node, same area) silently reported "0 messages" instead of re-queuing anything. Root cause: hold-queue rows are never deleted once delivered, only marked sent, and a database constraint means a message can only ever have one hold-queue row per node — so a repeat %RESCAN always found everything "already queued" and skipped it. %RESCAN now correctly resets already-delivered rows back to pending instead of skipping them, so a resend request actually resends.

Full suite verified clean (1550 passed, 2 skipped).

---

# ANetBBS v1.0b2.204 — AreaFix/FileFix netmail no longer clutters the personal inbox (July 2026)

- Every AreaFix/FileFix request and bot reply was appearing in the sysop's personal Netmail Inbox/Sent as if it were 1-on-1 mail, since the admin catch-all address matches the hub's own bare address that robot netmail is addressed to/from. This got far more visible after v1.0b2.201's dedup-exemption fix correctly stopped silently dropping repeat AreaFix commands. Robot netmail (AreaFix/FileFix/AreaMgr/FileMgr) is now excluded from the personal Netmail Inbox/Sent views — it's already fully logged in the dedicated admin AreaFix Log view.

Full suite verified clean (1549 passed, 2 skipped).

---

# ANetBBS v1.0b2.203 — AreaFix/FileFix cross-network subscription leak (July 2026)

- A downstream BinkP node's +ALL (or a plain +TAG for a tag belonging to another network) could subscribe it to echo/file areas from EVERY network this hub relays, not just the one it's actually a member of — a real cross-network data leak, found live. Hub-side AreaFix/FileFix now scope the available-area list to the requesting node's own network, failing closed (zero areas) rather than showing everything if a node's network isn't set.
- New tools/cleanup_cross_network_subscriptions.py finds (dry-run) and removes (--apply) any bad subscription rows already created by the bug before this fix.

Full suite verified clean (1547 passed, 2 skipped).

---

# ANetBBS v1.0b2.202 — Netmail zone fix + Eastern time display (July 2026)

- Fixed an outbound BinkP netmail bug where the @INTL zone kludge was skipped whenever our zone matched the recipient's zone — a downstream system with multiple network identities could file the mail under the wrong one, since FTS-0001's binary header has no zone field at all.
- Timestamps across the web UI and terminal/PETSCII interfaces now display in Eastern time (EST/EDT) instead of raw UTC — database storage is unchanged.

Full suite verified clean (1541 passed, 2 skipped).

---

# ANetBBS v1.0b2.201 — Bug fixes (July 2026)

- AreaFix/FileFix netmail could be silently discarded as a duplicate if it reused the same subject as an earlier, unrelated message from the same sender — every later command from that sender was dropped instead of processed.
- Queued outbound netmail (sysop replies, AreaFix/FileFix bot responses) was never actually sent to a downstream node or peer network during an inbound BinkP session — only echomail was flushed, so affected netmail could sit unsent indefinitely.

Full suite verified clean (1526 passed, 2 skipped).

---

# ANetBBS v1.0b2.200 — Bug fixes (July 2026)

- Follow-up fix to v199's message display improvements: mixed CP437/Unicode content now renders correctly in all cases.

Full suite verified clean (1502 passed, 2 skipped).

---

# ANetBBS v1.0b2.199 — Bug fixes + display improvements (July 2026)

- Security and access-control fixes across the web, terminal, and network layers.
- Fixed message display issues (CP437/Unicode rendering, word-wrap on inbound messages).

Full suite verified clean (1501 passed, 2 skipped).

---

# ANetBBS v1.0b2.198 — Bug fixes (July 2026)

- Minor bug fixes.

Full suite verified clean (1442 passed, 2 skipped).

---

# ANetBBS v1.0b2.197 — Full echomail/QWK/BinkP audit + file-area storage validation (July 2026)

A full audit of the echomail/QWK/BinkP subsystem turned up 28 issues, now fixed:

- File-echo hatch-out to downstream nodes was silently broken (subscribed peers never received files, no error anywhere).
- Two real security gaps in TIC processing (path traversal, missing password check) and a third occurrence of a known QWK data-corruption bug (CONTROL.DAT) and a known batch-rollback bug.
- Hub-side AreaFix/FileFix could leak internal sync-only areas to any downstream node.
- AreaFix/FileFix now correctly process requests received during a hub-initiated outbound poll, not just inbound connections.
- Assorted smaller fixes: duplicate netmail kludges, QWK reply-threading, stale-node nodelist flagging, a `%PASSWORD` AreaFix command, and a new "Unclaimed Netmail" admin review queue.
- File areas: typing a Storage Path that doesn't exist now warns and offers to create it (single area and bulk import).

Full suite verified clean (1431 passed, 2 skipped).

---

# ANetBBS v1.0b2.196 — Fixed InterBBS Last Callers flooding from sysop test logins (July 2026)

- "Hide sysop from Last Callers" already hid sysop logins from the local display, but never stopped them from being relayed to other BBSes on the network — a heavily-tested sysop account could flood every peer's Last Callers area. Same toggle now blocks the relay too.

Full suite verified clean (1327 passed, 2 skipped).

---

# ANetBBS v1.0b2.195 — Web: reorganized the Tools navbar dropdown (July 2026)

- The Tools dropdown had grown to 24 flat items — split into 5 category landing pages (Community, Network Directory, Content, My Stuff, Info & Help), same treatment the Admin dropdown already got.

Full suite verified clean (1324 passed, 2 skipped).

---

# ANetBBS v1.0b2.194 — Live bug fixes: FTP login crash, hub message delivery (July 2026)

- Fixed a real FTP login crash on mixed-case usernames.
- Fixed a hub bug where locally-composed echomail never reached downstream BinkP nodes — also added AreaFix %RESCAN so nodes can request a backlog re-send.

Full suite verified clean (1305 passed, 2 skipped).

---

# ANetBBS v1.0b2.193 — PETSCII: word-wrap listing picker prompts (July 2026)

Reported live immediately after the v1.0b2.192 status-message fix, on the real A-Net Software file area (18 files, enough to force M=more): the picker prompt line itself -- `#=download, E=extended info, M=more, Q=back: ` (46 characters) -- exceeded the 40-column terminal width and hard-broke mid-word, splitting "Q=back" into "Q=b" on one line and "ack:" on the next.

- FIX: `_paginated_pick()`'s prompt construction now runs through `_wrap_body()` before being sent, wrapping at word boundaries when the combined `prompt_label` + M=more/B=prev/Q=back options exceed the terminal width. `_wrap_body()` returns a single unchanged line for anything that already fits, so this is a no-op for every other `_paginated_pick` caller (echo areas, boards, PM inbox, file areas) -- only the file-browsing screen's unusually long `'download, E=extended info'` label was actually at risk.

2 new regression tests: one directly against `_paginated_pick()` with a long label forced into `M=more`, and one end-to-end against a 23-file listing (the real reported shape); full suite verified clean (1306 passed, 2 skipped, 0 failed).

---

# ANetBBS v1.0b2.192 — PETSCII: word-wrap XMODEM status messages (July 2026)

Reported live immediately after testing the v1.0b2.191 file-listing redesign (download itself confirmed working end-to-end on real hardware): the "Starting XMODEM send of A-NET-door-scores1.5.zip -- start your terminal's receive now." status message was written as one long unwrapped string, so the terminal itself hard-broke it mid-word/mid-filename at the 40-column boundary ("A-NET-door-score" / "s1.5.zip", "receiv" / "e").

- FIX: added `_write_wrapped()`, a small helper that word-wraps text to the session's width before writing, and applied it to all four of `_files_download`'s status messages (file-not-found, XMODEM-unavailable, transfer-starting, transfer-complete/failed) -- all of which embed a filename of unpredictable length.

1 new regression test using a long real filename to reproduce the exact overflow condition and confirm every line stays within the terminal width; full suite verified clean (1304 passed, 2 skipped, 0 failed).

---

# ANetBBS v1.0b2.191 — PETSCII: file listing redesign — immediate download, E=extended info (July 2026)

Reported live testing v1.0b2.190 against the real ANetBBS file areas: XMODEM downloads work correctly end-to-end on real C64 hardware (confirmed), but the download prompt was buried behind every file's full description -- with real FILE_ID.DIZ-derived descriptions running several pages each (even after the previous release's ANSI-art stripping fix), reaching the download prompt meant paging through all of them first. Quitting early with Q at any `-- More --` prompt along the way exited the whole file area instead of skipping ahead to the download prompt ("there are numbers, but there is no way to download, no download option").

- CHANGED: file listings are now brief -- name and size only, one line per file -- with the numbered download picker (`#=download, E=extended info, Q=back`) shown immediately after the listing itself, using the same `_paginated_pick()` pattern as every other listing screen in this project (echo areas, boards, PM inbox). No pagination is needed before the picker appears unless the file list itself is long.
- ADDED: `E` opens a file's full/extended description (the same art-stripped, word-wrapped, paginated view as before) on demand, then returns to the brief listing.

7 new regression tests, including one asserting zero `-- More --` prompts appear before the download picker on a normal-sized listing, and one confirming `E` shows the description and then returns to the listing rather than exiting; full suite verified clean (1303 passed, 2 skipped, 0 failed).

---

# ANetBBS v1.0b2.190 — PETSCII: file downloads (XMODEM), network-first echomail, ANSI-art description fix (July 2026)

Reported live testing v1.0b2.189 against the real ANetBBS install (real subscriptions across tqwnet/Fidonet/DOVE-Net/sp00knet/ANotherNetwork, real uploaded door-game files with FILE_ID.DIZ-style ANSI-art descriptions):

- FIX: file descriptions full of CP437/Unicode box-drawing art were passed straight to `session.write()`'s PETSCII encoder, which falls back to a literal `?` PER CHARACTER with no representation -- a wall of question marks ("omg file areas look awful when you enter one"). Added `_strip_for_petscii()`, which collapses any run of non-PETSCII-representable characters into a single space before display, wired into `_wrap_body()` (the shared choke-point for all body text: file descriptions, echomail/PM/board post bodies, profile bio) so the same class of bug can't recur anywhere text gets displayed.
- ADDED: file downloads via XMODEM, per the sysop's explicit choice -- the protocol real C64 terminal software (Novaterm, CCGMS, 64NIC+) most reliably supports, and the simplest to integrate given PETSCII's plain-text constraints (no in-band filename/size metadata needed, unlike Ymodem/Zmodem). File listings are now numbered; picking a number launches `features.xfer.send_file()` as-is -- no PETSCII-specific transfer code needed at all, since PETSCII connections are plain telnet sockets (confirmed via `core/petscii_server.py`'s own docstring) and `xfer.py`'s existing telnet IAC-escaping already applies correctly to any non-SSH/rlogin session.
- CHANGED: echomail area picking ("echomail you should be able to pick the network first, then the areas. not all combine") is now two-step -- `_echomail_menu` shows only the networks a user actually has visible areas in, `_echomail_areas_menu` shows that network's areas -- instead of combining every subscribed network's areas into one flat list.

17 new regression tests covering the art-stripping helper, the network-first picker (including that one network's areas never leak into another's listing), and the download flow (real file, missing file, XMODEM unavailable); full suite verified clean (1300 passed, 2 skipped, 0 failed).

**Not yet verified on real hardware**: XMODEM download and the ANSI-art description fix were built and tested against synthetic data only -- the Pi test server has no file areas. Both need a real pass against ANetBBS itself, which is exactly where this release is headed next.

---

# ANetBBS v1.0b2.189 — PETSCII: page-back (B=prev) for listings and body-text readers (July 2026)

Requested live after the M=more pagination fix: once you'd paged forward, the only way "back" was Q -- which exits the whole screen (back to the previous menu level) rather than just returning to the page you'd already scrolled past.

- Added B=prev to `_paginated_pick()` (echo-area/board/file-area listing screens) -- offered only once there's a previous page to go back to, alongside M=more.
- Added the same to `_paginate()` (body-text readers: message reading, file-area contents, profile) -- takes an optional `header_title` now, redrawn on every page transition (forward or back) so B produces a clean re-render rather than appending old content below new.

Also investigated a report that echo message subjects in 80-column mode still looked cut off ("Nintendo says users volun..."): confirmed via direct reproduction that the stored subject itself is already only ~25 characters, truncated mid-word before it ever reaches ANetBBS -- almost certainly by whatever posts those "Tech News Bot" messages, not by the PETSCII renderer, which is already displaying the complete stored value. No code change for this one; nothing to fix on the ANetBBS side.

7 new regression tests covering page-back in both pagination helpers; full suite verified clean (1290 passed, 2 skipped, 0 failed).

---

# ANetBBS v1.0b2.188 — PETSCII: auto-wrap blank-line fix, word-boundary truncation, wider subject column (July 2026)

Reported live testing v1.0b2.187 on the Pi (real SyncTERM transcripts, both 40- and 80-column): the echo-area list's previous column-width fix sized the area-name and network-name columns to add up to EXACTLY the terminal width. That fills the terminal's last column, and most terminals -- real C64 hardware included -- auto-wrap the cursor the instant that happens. The row's own trailing `\r\n` then produced a genuine second, blank line after every row. On a real 25-row screen that doubled the vertical space each row consumed, scrolling the top of an 18-item page off-screen before the prompt even appeared ("you can't see 1-7").

- FIX: column-budget math across the listing screens now always leaves one spare column (`w - 1`, never the full `w`) rather than filling the line exactly.
- FIX: subject/label columns in the echo-message, board-thread, and PM-inbox listings truncated mid-word ("voluntarily" -> "volun") via a plain `text[:n]` slice, and reserved a fixed budget for the trailing from-name/reply-count column regardless of how short the actual values were (e.g. always reserving room for a long name even when every sender was "Tech News Bot"). Added `_truncate_words()` (breaks at the last word boundary, falling back to a hard cut only when a single word alone exceeds the whole budget) and size the trailing column from the actual longest value present in the current page -- the same technique already used for the network-name column -- giving the subject meaningfully more room.

5 new regression tests, including one that renders every echo-area row across a matrix of widths (40/80) and network-name lengths (short to pathologically long) and asserts none ever fills the terminal exactly; full suite verified clean (1283 passed, 2 skipped, 0 failed).

---

# ANetBBS v1.0b2.187 — PETSCII: echo-area column wrap fix, top-level pagination, profile editing (July 2026)

Reported live testing v1.0b2.186 on the Pi (real SyncTERM screenshots, both 40- and 80-column): the echomail AREA list (distinct from the per-area MESSAGE list fixed in the previous release) wrapped onto a second physical line for every row -- `name_w = w - 16` assumed the bracketed network name would never exceed ~9 characters, but a real network name like "ANotherNetwork (QWK)" is 20.

- FIX: both the area-name and network-name columns are now sized from the actual longest network name present in the list, with a floor so the area-name column never drops below 10 chars even for a pathologically long network name. Verified the fix keeps every row within the terminal width on both 40- and 80-column sessions.
- FIX: the echo/board/file-area top-level listings (the screen where you pick WHICH area/board to enter) had no pagination at all -- only the listings INSIDE them (messages, threads) were paginated in v1.0b2.186. A sysop with 25 echo areas (the real reported case) saw them all dumped onto one unbroken screen. Now uses the same page-at-a-time + M=more pattern as everything else.
- Added: the profile screen was view-only; can now edit display name, location, and bio (the same three fields it already displays) via a simple numbered picker.

8 new regression tests, including one that renders every listing row and asserts none exceeds the terminal width; full suite verified clean (1278 passed, 2 skipped, 0 failed).

---

# ANetBBS v1.0b2.186 — PETSCII: pagination/quit fixes, Number Guessing, sysop custom menus (July 2026)

Reported live: reading a long echomail message or browsing a file area with many files forced the PETSCII reader through every single page with no way to back out -- the "-- More --" prompt read a keystroke but never checked what was typed. Separately, the echo message subject list, board thread list, and PM inbox had NO pagination at all (up to 50 rows dumped in one unbroken screen on a 25-row real C64 display), unlike file-area browsing which already paginated.

- FIX: `_paginate()` now checks for Q at the "-- More --" prompt and stops immediately, returning a flag so callers skip a redundant trailing prompt.
- FIX: echo/board/PM listings gained the same page-at-a-time rendering file browsing already had, with an M=more option once a listing exceeds one page. Row numbers stay globally consecutive across pages.

Also added, per request:

- The built-in Number Guessing game is now reachable from a new PETSCII Games menu -- reused directly from `features.games.GameManager.play_number_guess()` rather than reimplemented, since it's pure session I/O with zero ANSI escape codes (the session layer's existing PETSCII encode-on-write path handles it transparently).
- Sysops can now build custom PETSCII menus (Admin → PETSCII Menus, mirrors the existing "BBS Menus" admin screens) -- a fully separate menu tree from the ANSI custom-menu system (`PetsciiMenu`/`PetsciiMenuItem`, not `BbsMenu`/`BbsMenuItem`), since most ANSI action types (art screens, chat, most doors) have no PETSCII equivalent and a sysop building a PETSCII menu wants full layout control rather than an ANSI tree with items silently missing. Opt-in: PETSCII sessions use the hardcoded Phase 1 menu unchanged until a sysop marks a custom menu as default. The interpreter fails safe into the hardcoded menu on a broken `goto` target or any DB error during the initial custom-menu check, so a misconfigured menu (or an upgrade race before the new tables exist) can't break PETSCII login.

26 new regression tests across pagination, the games menu, the custom-menu interpreter, and the new admin routes; full suite verified clean (1270 passed, 2 skipped, 0 failed).

---

# ANetBBS v1.0b2.185 — Hub can now poll downstream BinkP nodes on demand (July 2026)

Added ability to manually poll a downstream BinkP node on demand instead of waiting for it to call in -- a "Poll Now" button on the node's detail page, mirroring the existing manual poll for upstream networks. Requires an optional BinkP host/port (and TLS toggle) set on the node; nodes without one stay poll-in-only, which is normal for anything behind a dynamic IP or firewall. Reuses the existing hold-queue and ack-gated retry logic, so a node that doesn't acknowledge the batch keeps its mail queued for the next attempt rather than losing it.

9 new regression tests; full suite verified clean (1245 passed, 2 skipped, 0 failed).

---

# ANetBBS v1.0b2.184 — BinkP downstream-node network misattribution fix (July 2026)

Reported live: a sysop saw a real downstream node (a peer on ANotherNetwork) connect successfully via BinkP -- CRAM-MD5 auth OK, packet transferred, and the exchange even showed up via the InterBBS Last Callers feature -- but nothing appeared under ANotherNetwork in Admin -> Echomail poll log.

- FIX: the inbound BinkP listener resolves a downstream node's mail purely from its `hub_identity_id`, taking the first matching BinkP `EchomailNetwork` row it finds. That's ambiguous whenever one hub identity owns more than one BinkP network (a sysop who is hub of one network and a leaf member of several others under the same default identity -- a real, common shape) -- inbound mail/poll-log entries were silently landing under whichever network happened to sort first by id, unrelated to the node that actually connected.
- `BinkPNode` now tracks `network_id` explicitly -- the only unambiguous source of truth. Set automatically when a join application is approved and available as an override on the node edit form. Legacy nodes are backfilled on upgrade to the one BinkP network under their hub identity where the install is actually the hub; this also improves the runtime fallback for any node that still can't be backfilled unambiguously.

15 new regression tests, including a scripted-BinkP-session reproduction of the exact reported shape (5 BinkP networks under one hub identity); full suite verified clean (1236 passed, 2 skipped, 0 failed).

---

# ANetBBS v1.0b2.183 — PETSCII Phase 1: real menu screens; fix stale multi-service restart message (July 2026)

With login confirmed working on real hardware across three prior rounds of fixes, PETSCII sessions now get the real Phase 1 core-BBS menu instead of a placeholder stub: message boards (list/read/post/reply), echomail areas (list/read/post -- wired into the same reply-notification hook the web/terminal composers use), private messages (inbox/read/send), file-area browsing, who's-online, and profile. Plain numbered-list menus and a simple line-by-line composer, built fresh for this rendering path rather than reusing any ANSI/lightbar code -- matching the same "not shown at all if not vetted" rule already applied to games/doors/MRC/IRC for PETSCII sessions. Reuses only the existing data-layer models/queries, not any ANSI rendering code.

Also, reported live by a sysop: Admin -> Settings' "restart required" message, and deploy/README.md, still told sysops to restart 4 separate systemd services (anetbbs-telnet / anetbbs-ssh / anetbbs-rlogin, plus anetbbs.service) after a settings change requiring a restart -- anetbbs-rlogin.service never existed as a real unit at all, and anetbbs-telnet/anetbbs-ssh were merged into the single combined anetbbs.service a while back (Ubuntu systemd's EnvironmentFile directive winning over per-unit Environment= overrides meant the split units couldn't reliably share .env). Both now correctly point sysops at just `sudo systemctl restart anetbbs.service`.

12 new regression tests covering the PETSCII menu screens (boards, echomail incl. reply-notification wiring, private messages incl. notification + error handling, files, who's-online, profile) and the restart-message fix; full suite verified clean (1234 passed, 2 skipped, 0 failed).

---

# ANetBBS v1.0b2.182 — Multi-hub-identity BinkP mail loss + local-post reply notifications (July 2026)

Reported live: a sysop testing a second network's (ANotherNetwork) echomail saw a fully successful-looking BinkP transcript -- CRAM-MD5 auth succeeded, the .pkt file was received and M_GOT-acknowledged -- but the message never showed up anywhere on ANetBBS.

- FIX: the inbound BinkP listener resolves a downstream node's hub identity to find its EchomailNetwork row, but only ever used that row to compute the OUTBOUND stamping address -- the network's id itself was never captured, so every downstream-node session imported its received mail with network_id=None. EchoArea/EchomailMessage both require a real network_id, so this failed a database constraint that was silently swallowed by a generic exception handler -- no trace anywhere a sysop could check (this listener never uses the Bad Areas log, that's an outbound-poller-only mechanism). BinkP itself looked entirely successful the whole time.
- Also added: if a hub identity genuinely has no matching BinkP network configured at all, inbound mail is now skipped with a loud, specific log message instead of the same silent crash.

While verifying the above, a second, unrelated gap surfaced: two local ANetBBS accounts replying to each other directly in a shared echo area (no external network transfer at all) never triggered the v1.0b2.176 echomail-reply notification -- that hook only ever fired from the three inbound-network-import paths, never from ANetBBS's own web/terminal message composer.

- FIX: both the web and terminal echomail composers now also check the reply's TO name and notify a matching local recipient, the same way an inbound network message already does.

4 new regression tests: 2 driving a real scripted BinkP session (auth + file transfer) through the actual connection handler confirming the resolved network id reaches the importer instead of None, and 2 covering the local-post notification hook.

---

# ANetBBS v1.0b2.181 — PETSCII: Wall + goodbye screen no longer leak raw ANSI (July 2026)

Reported live on a third real-hardware pass (login now works correctly): after logging in, a "Graffiti Wall" bulletin screen showed garbled raw ANSI escape fragments and CP437 box-drawing artifacts, and the same happened on the goodbye/logoff screen.

- FIX: the Wall logon module and the sysop-configurable goodbye ANSI-art screen both write raw CP437/ANSI bytes directly to the socket, completely bypassing the PETSCII-aware encode path -- and neither is on the Phase 1 core-BBS feature list to begin with. PETSCII sessions now skip all logon/logoff modules (wall, ansi screens, shell hooks) and the ANSI-art goodbye screen entirely, the same way Games/doors/ANetCRAFT/DarkForces/MRC/IRC are already not shown at all rather than shown broken. The plain-text "Goodbye!" fallback (which already goes through the correct PETSCII path) still shows on disconnect.

---

# ANetBBS v1.0b2.180 — PETSCII: real letter-case inversion, not just charset timing (July 2026)

Reported live on a second real-hardware pass (v1.0b2.179 fixed the charset-timing bug, login still failed): the BBS's own output text was rendering with every letter's case flipped -- "Welcome" as "wELCOME", "Login" as "lOGIN", "Invalid username or password" as "iNVALID USERNAME OR PASSWORD".

- FIX: got the actual PETSCII spec backwards in the previous release. In the "upper/lowercase" charset, PETSCII's letter-case byte assignment is INVERTED from ASCII's -- sending the ASCII-uppercase byte value displays as a lowercase glyph, and the ASCII-lowercase byte value displays as uppercase. This isn't just a display quirk -- the same inversion applies to what a real C64 keyboard sends for typed letters, so every letter of a typed username/password arrived with the wrong case and authentication always failed regardless of the charset-timing fix. Both directions are now corrected: the encoder swaps letter case before writing to the wire, and the same swap is applied to bytes read back from the client before they ever reach the login/username/password buffers.

10 new regression tests (encode/decode round-trip, decode_char, and login/password reader coverage); existing PETSCII tests updated to assert the corrected (swapped) byte values instead of the previous identity-mapping assumption.

---

# ANetBBS v1.0b2.179 — PETSCII: login always failed + no admin settings UI (July 2026)

Reported live during first real-hardware testing (Pi + PETSCII port enabled): login always said "invalid username or password" against known-good accounts, and there was no way to enable/see the PETSCII ports in the admin UI at all -- required hand-editing .env with no visibility.

- FIX: a real C64 keyboard's PETSCII byte code for a letter key depends on which charset ROM is currently selected -- a KERNAL keyboard-decode difference, not just a display choice. Unshifted letters send the UPPERCASE byte range in the default power-on "graphics" charset, and only send the lowercase range once the upper/lowercase charset is selected. That charset switch was only being sent AFTER a successful login (in the post-login menu placeholder) -- so login itself always ran in the wrong charset, every password typed arrived silently case-flattened to uppercase, and authentication always failed. Now sent as the very first bytes of the connection, before login ever begins.
- FIX: backspace/delete didn't work at all for PETSCII sessions -- a real C64 keyboard's DEL/INST key sends PETSCII 0x14, not ASCII 0x7f/0x08 (which means something else entirely on real hardware -- "disable Shift-Commodore", not erase-in-place). Both the username/password input readers now recognize the real key and echo the correct PETSCII cursor-left erase sequence.
- NEW: PETSCII40/80 ENABLED+PORT are now in Admin -> Settings (same .env editor as Telnet/SSH/rlogin/FTP) and show live up/down status in the Service Control Center, instead of requiring a blind .env edit with zero visibility anywhere in the admin UI.

13 new regression tests (7 for the two hardware bugs, 6 for the admin settings/status wiring).

---

# ANetBBS v1.0b2.178 — rlogin health-check probe no longer looks like an attack (July 2026)

Reported live: a sysop's rlogin log showed rapid repeated bursts of "rlogin connection from 127.0.0.1" immediately followed by "rlogin connection closed", with no way to see a real attacker IP or trigger a ban -- looked exactly like a sustained, unbannable attack.

- FIX: this was ANetBBS's own Service Control Center health-check prober (/admin/control's status page, polled every ~5s) doing a bare connect-and-close against every listener including rlogin to confirm it's alive -- genuinely from 127.0.0.1, which is why no external IP ever appeared and no ban ever fired. The prober's own zero-byte probe connections now log at DEBUG instead of INFO; a real rlogin connection attempt still gets a clear INFO line the moment its handshake actually completes, so real activity stays fully visible while the SCC's own routine health checks no longer read like an attack.

3 new regression tests confirming a bare probe-and-close produces no INFO-level log while a real handshake still does.

---

# ANetBBS v1.0b2.177 — Terminal notification pop-up + PETSCII support (Phase 1, in progress) (July 2026)

Terminal:
- CHANGE: the login-time notification banner is now a blocking pop-up -- lists every unread notification by name/detail ("Jane wrote to you (in FidoNet (General))", PM/IM counts, etc.) and requires pressing ENTER before continuing into the menu. Previously this was a passive one-line count that scrolled past like any other banner and was easy to miss.

PETSCII (Commodore 64/128) support -- Phase 1, vertical slice only:
- NEW: two dedicated, opt-in listener ports (PETSCII40_ENABLED/PETSCII80_ENABLED, disabled by default, ports 6400/6401) for 40- and 80-column PETSCII clients -- no telnet TTYPE negotiation/guessing, every connection on these ports is treated as PETSCII unconditionally, matching Synchronet's own 40/80-column PETSCII port convention.
- NEW: a dedicated PETSCII byte encoder/control-code module and a from-scratch plain-text login screen, completely separate from the existing ANSI/CP437 pipeline (which stays untouched -- zero risk to existing telnet/SSH/rlogin behavior).
- This is a deliberately staged rollout: this release gets a PETSCII user through login and confirms the wire format works end to end. The actual message boards/echomail/PM/file-area menu screens come in a follow-up release once real hardware testing confirms this first slice looks right on an actual C64/VICE client -- Games/doors/ANetCRAFT/DarkForces/MRC chat/IRC are not planned to ever be offered over PETSCII.

29 new regression tests (10 codec, 10 session plumbing, 6 login/menu-stub, 3 notification pop-up).

---

# ANetBBS v1.0b2.176 — Echomail/QWK reply notifications (July 2026)

When an inbound echomail (FidoNet) or QWK-network message arrives in a public area addressed TO your handle specifically (not the generic "All"), you now get notified -- both at login and while already online.

- FEATURE: all three inbound-message paths now check the TO name against local usernames/display names/FTN AKAs (reusing netmail's own recipient-matching logic) and create a Notification if it's really addressed to someone: the outbound-poll/QWK-REP import path, the real-time BinkP listener, and a hub receiving a REP upload from a downstream node.
- Terminal: the existing login-time "you have new mail" banner now lists these by name ("Jane wrote to you (in FidoNet (General))"), not just a bare count. A new check at every return to the main menu also surfaces ones that arrive while you're already connected.
- Web: the notification bell already showed these after a page reload; now an already-open browser tab gets a live toast and the bell badge count bumps immediately, without needing a reload.
- Skips the generic "everyone in this area" convention (to_name of All/Everyone/Sysop/etc.) so it only fires for messages genuinely addressed to a real person.
- New toggle in Notification Settings to opt out.

13 new regression tests covering all three import paths, the notify-skip rules, and both terminal delivery mechanisms.

---

# ANetBBS v1.0b2.175 — ANetDarkForces (Web): stale terminal mention removed from description (July 2026)

- FIX: the Game Center description for ANetDarkForces still said "(Also playable in the terminal via SSH/telnet, with sixel graphics on capable clients.)" -- stale since v1.0b2.174 pulled the terminal edition out of the live product. Removed.

---

# ANetBBS v1.0b2.174 — ANetDarkForces (Terminal): pulled from the live product for offline rework (July 2026)

After further playtesting, the terminal edition's visuals need substantially more work than incremental fixes can deliver (wall-texture readability, enemy visibility, general legibility over a real SyncTerm/SSH session). Rather than keep shipping partial fixes to players, it's being pulled out of ANetBBS entirely while it gets reworked separately -- the same way the canvas/web edition itself started out as a standalone build before being migrated in.

- CHANGE: ANetDarkForces (Terminal) no longer appears in the Game Center or native SSH/telnet game listings. Its registry entry was removed from web_app.py's BUNDLED_DOORS; an install that already seeded it in a prior release (v1.0b2.168-173) gets the row deactivated (not deleted) on next boot, so any score/session history tied to it survives.
- The module itself (anetbbs/features/darkforces_term.py) and its full test suite (tests/test_darkforces_term.py, 30 tests) are untouched and still pass -- development continues on it separately until it's ready to ship again.
- UNCHANGED: the canvas/web edition ("ANetDarkForces" in the Game Center, full viewport, server-side saves) is unaffected and remains the only reachable way to play right now.

3 new regression tests in tests/test_darkforces_term_retired.py (fresh installs never seed it; an existing active row from a prior release gets deactivated, not deleted, and stays deactivated across further reboots; the Game Center listing and direct play route both correctly 404/omit it once retired).

---

# ANetBBS v1.0b2.173 — ANetDarkForces: exit waypoint, door texture spam, intro word-wrap (July 2026)

Reported live across both editions after further playtesting.

Web (canvas):
- FIX: "stuck on level 4" wasn't a missing-content bug -- all 10 sectors are real and complete (verified). The actual problem: the level exit has always been a pure invisible coordinate with zero on-screen indication anywhere, so a big/open level cleared of enemies and pickups left no clue where to go. The minimap now shows a waypoint marker for the exit, clamped to the minimap's edge (pointing in the right direction) when it's outside the local view radius -- gold once the level's boss (if any) is down, gray otherwise.
- CONFIRMED, no change needed: Escape-to-pause already worked correctly and was already wired to the right screen.

Terminal:
- FIX: standing close to a Security Door (or vault/ammo dispenser) filled large portions of the viewport with the same solid letter ('D'/'V'/'A') repeated in every cell -- reported live as looking like a rendering glitch. These wall types now stencil their identifying letter on a sparse diagonal over a normally-shaded fill instead of tiling it solid.
- FIX: level-intro text was written raw to an 80-col terminal with no word-wrapping of our own, so long lines broke mid-word ("stockpi" / "ling"). Now wrapped at word boundaries before being sent.
- CONFIRMED, no change needed: P-to-pause already exists.
- Enemy visibility on the terminal port needs another pass -- still under active investigation, not part of this release.

3 new regression tests in tests/test_darkforces_term.py (door-texture stencil pattern, plain-wall glyph unaffected, intro word-wrap). The web-side exit waypoint was verified via Node syntax check plus manual trace of the minimap's existing local-radius/clamping logic (no existing JS test harness in this repo to extend).

---

# ANetBBS v1.0b2.172 — ANetDarkForces (Web): fullscreen play page (July 2026)

Reported live: the canvas edition's play area was clipped inside ANetBBS's normal page chrome (navbar, search bar, footer) -- unable to scroll down to see the full play area, and the visible portion sat off-center to the right.

- FIX: the raycaster's CSS was written for a true full-viewport page (like the standalone build had), but the Game Center's play route always renders web games inside base.html's bounded, nav-wrapped .container. That mismatch is what clipped/off-centered the canvas -- boxing a 100vw/100vh element inside a narrower, offset container instead of ignoring it.
- FIX: ANetDarkForces now opts into a new chrome-free play template (a per-registry 'fullscreen' flag on web_games.py entries) so it gets the entire browser viewport, matching how it played standalone -- other web games are unaffected and still play boxed inside the normal site chrome. A small always-present "Game Center" link replaces the removed navbar as the way back out.

3 new regression tests in tests/test_darkforces_web.py (play page renders the chromeless template with CSRF meta + back link; other web games still get the normal chrome).

---

# ANetBBS v1.0b2.171 — ANetDarkForces: fix duplicate terminal entries on upgrade (July 2026)

Reported live: after upgrading, the Game Center showed two ANetDarkForces entries and both launched the terminal edition -- the canvas version was unreachable.

- FIX: the web-game seeding loop only inserted a Game row if its slug didn't already exist, with no logic to correct an EXISTING row. ANetDarkForces's terminal door originally shipped under the plain 'darkforces' slug; when the real web entry was added it needed that slug too, so the terminal door moved to 'darkforces-term' -- but on an in-place upgrade, the OLD 'darkforces' row (still pointing at the terminal port) was left untouched instead of being corrected, leaving two terminal-routed entries and no working canvas entry. The seeding loop now self-corrects an existing row's game_type/web_game_module on every boot (matching how door-game seeding already worked), without touching sysop-customizable fields like name/description/sort_order.
- As a side effect of the same stale data, the canvas game was also incorrectly showing up in native SSH/telnet game listings (which already filter out web-only games by type) -- fixed by the same correction.

3 new regression tests, including one confirming sysop customizations to a game's name/description/sort_order survive a restart.

---

# ANetBBS v1.0b2.170 — ANetDarkForces: full web Game Center migration (July 2026)

The canvas/browser version of ANetDarkForces -- built and polished standalone before this -- is now actually reachable from ANetBBS's own Game Center, not just the terminal port. Previously only the terminal door game was registered, so launching "ANetDarkForces" from the web UI always landed on the terminal edition regardless of intent.

- FEATURE: full canvas game now lives at /games/darkforces/, same pattern as Meadowlark Valley -- 3 save slots tied to your ANetBBS account (server-side, replacing the standalone version's browser-local storage), reachable from the regular Game Center listing.
- FIX: the terminal door game's slug collided with the new web entry (both wanted 'darkforces') -- terminal edition is now 'darkforces-term' internally; both remain full, independent ways to play the same campaign.

No gameplay changes -- this is purely making the already-built web version reachable inside ANetBBS, alongside the terminal port from the last two releases.

---

# ANetBBS v1.0b2.169 — ANetDarkForces (Terminal Edition): minimap + readable enemies (July 2026)

Reported live: a player was killed without ever seeing what killed them -- the terminal port's raycast view only shows a narrow forward-facing slice with no peripheral awareness at all, and enemies attack based on distance/line-of-sight, not on whether they're inside that view.

- FIX: added a top-down minimap (top-right corner of the viewport) showing nearby walls, the player's position/facing, and every nearby enemy -- the single biggest missing piece of situational awareness compared to the browser version.
- FIX: enemies on non-sixel sessions were a single floating dot character, unreadable as "a person" at any distance -- now a real 2-row humanoid silhouette (head + body, taller up close), matching how the browser version's own sprites read as human silhouettes rather than blobs.
- FIX: sixel-capable sessions had enemies rendering too small to read clearly at normal combat range -- bumped both the sprite canvas resolution and the on-screen scaling formula.
- FIX: a minimap edge case where the player's own facing-direction tick could land on the exact same cell as a one-tile-away enemy and paint over it, hiding the enemy marker -- caught by a new regression test before shipping.

2 new tests in tests/test_darkforces_term.py (27 total for this module).

---

# ANetBBS v1.0b2.168 — ANetDarkForces (Terminal Edition): raycasting FPS with sixel sprites (July 2026)

A first-person raycasting shooter now ships as a built-in terminal door game, ported from the standalone browser version -- an original "raid the Dark Forces' hideout computer stores" campaign across 10 sectors, 7 weapons, 8 enemy types, secrets, keycard vaults, and explosive barrels.

- FEATURE: walls render as a real-time truecolor-ANSI column grid (same dirty-diffed double-buffer technique ANetCRAFT already uses over telnet/SSH), with enemies/pickups/barrels composited on top as small sixel-encoded sprites on sixel-capable terminals (SyncTerm, etc.) or a colored ANSI glyph otherwise -- no img2sixel dependency, a from-scratch native sixel encoder runs entirely in-process so the game stays real-time.
- FEATURE: full parity with the browser version's combat/AI -- enemy infighting, memory (an alerted enemy gives up the chase after losing sight for a while), kamikaze drone arm/telegraph, riot-shield frontal damage reduction, barrel chain reactions, ammo caps, and all 10 levels' secrets/locked-door vaults.
- One save slot per player, migration-safe the same way the browser version's own save format is.

25 new tests in tests/test_darkforces_term.py.

---

# ANetBBS v1.0b2.167 — Meadowlark Valley: volume control (July 2026)

- FEATURE: a volume slider next to the mute button sets a master volume (0-100%) for every sound effect and the ambient music, independent of mute — muting/unmuting restores whatever volume was last set.

---

# ANetBBS v1.0b2.166 — Meadowlark Valley code review + game docs (July 2026)

- FIX: Fire Station's real fire-coverage radius had silently drifted to match Police's widened radius instead of its own stated 5 tiles — now derived consistently for every building type.
- FIX: a farm's single job slot could get double-booked by multiple villagers over time, and villagers with no reachable road to their job were still counted as employed.
- FIX: co-op autosave could silently overwrite a guest's own save slot with the host's town; a guest's own placements could briefly show a phantom building before the host's correction arrived.
- FEATURE: in-game Help (❓) explaining the mechanics; info panel now shows a farm's assigned farmer.
- DOCS: new wiki pages for the Game Center, Ebook Reader, and Meadowlark Valley; a new sysop-facing games catalog doc; fixed a stale reference to a game that doesn't exist.

---

# ANetBBS v1.0b2.165 — Meadowlark Valley: sidebar overlap fix + auto-harvesting farmers (July 2026)

- FIX: the Services/Zones sidebar rendered every section on top of each other on shorter screens — reported live via screenshot. A flexbox default let sections shrink below their own content instead of the sidebar scrolling.
- FEATURE: Farm Plots now employ a farmer (like a Shop or Factory) who harvests the crop automatically the day it ripens, credited straight to your treasury. Unstaffed farms still work exactly like before — click to harvest.

---

# ANetBBS v1.0b2.164 — Meadowlark Valley: ANetBBS web game + co-op, tax/vandalism balance fixes (July 2026)

- FEATURE: Meadowlark Valley (the town/farm-builder sim) is now a full Game Center entry — server-side save/load tied to your account (3 slots), no more import/export files.
- FEATURE: Co-op mode — host a room, share a code, build the same town together with friends in real time.
- FIX: default tax rate was a guaranteed slow bankruptcy for any real town; rebalanced so default tax roughly breaks even and higher tax genuinely pays off, instead of max tax being the only way to survive.
- FIX: vandalism could start before Police Stations even unlock, and extra stations barely helped a spread-out town; both fixed.

---

# ANetBBS v1.0b2.163 — Who's Online font-path leak + "Default Theme" fixes (July 2026)

- FIX: Who's Online sometimes showed a user "on" /static/fonts/Ac437_IBM_VGA_9x16.woff instead of their real page — a lazily-loaded font fetch could be the last request recorded for a session. Static asset requests no longer update presence.
- FIX: setting a theme as the site's "Default Theme" in Admin had no effect — nothing ever read that flag. Both the web UI and terminal UI now actually fall back to it, and their "Default" theme-picker labels show which theme that really is instead of a hardcoded "Classic Green".

---

# ANetBBS v1.0b2.162 — Tagline picker selection visibility: give up on reverse-video (July 2026)

Third attempt at the same bug, reported live each time via screenshot: the selected row in the tagline picker was still invisible after both (1) adding an explicit color to compete with the reverse-video highlight, and (2) removing that color to match every other lightbar row's convention of relying on reverse-video alone. Both were still invisible on the user's terminal (SyncTERM), which means reverse-video + bold itself doesn't render usably on that client, regardless of what color the row text has. Stopped guessing at the SGR interaction and sidestepped reverse-video for this row entirely: it now explicitly cancels the wrapper's escape codes and draws its own `> ` marker in a plain bright color instead, which doesn't depend on how any given client's reverse-video happens to interact with bold.

---

# ANetBBS v1.0b2.161 — Wide-terminal row overflow, and the real selected-row visibility fix (July 2026)

- FIX: on a wide (132-col) terminal, the "Compose Echomail" area-picker lightbar corrupted itself after scrolling down then back up — stray fragments ("tegory", "S Scene", "neral") left on screen. Root cause: the row's Name-column width was computed as `terminal_width - 28`, undercounting the row's real overhead (tag + spacing + category = 37 visible chars, not 28) by 9 characters. On a 132-col terminal each row silently overflowed the line and the terminal auto-wrapped it onto the next line; a later partial redraw (scrolling) only overwrote part of that wrapped-in text, leaving the rest behind. Fixed the width math; added a test that measures every rendered row's real visible width against the terminal width at 132 columns.
- FIX (real fix this time): v1.0b2.160's tagline-picker color fix (adding an explicit bold-white foreground to the row text) was still invisible when selected, confirmed via a second live screenshot. The actual cause was the opposite of the first guess — an explicit foreground color competing with the reverse-video highlight is what's invisible, not the lack of one. Every other lightbar row in the app already avoids this by leaving the selected row's main text uncolored (relying purely on the reverse-video wrapper against default terminal colors) while only non-selected rows get an explicit color; the tagline picker now follows that same convention.

---

# ANetBBS v1.0b2.160 — Tagline picker: defer to send time, fix invisible selected row (July 2026)

- FIX: the terminal tagline picker showed up before the user even started typing — reported live ("it should not ask you about a tag line until you send, when you send it should bring up the tagline"). Moved from a pre-editor prompt to a `tagline_picker` callback that ANEdit itself calls once, at actual send time (Ctrl+W/`/send`). Removed the now-redundant `/tag` slash command and its toggle state — the picker is the only path now, and it always runs (if the pool isn't empty) right before finalizing a send, never on abort.
- FIX: the selected row in the tagline picker rendered as a blank, unreadable highlighted bar — the row text had no explicit foreground color, and reverse-video + bold with no explicit color apparently collapses to white-on-white in real terminal clients (SyncTERM, confirmed live via screenshot). Every other lightbar row in the app already colors its text for this exact reason; this was the one that didn't.

---

# ANetBBS v1.0b2.159 — Real root cause: the tagline seed file never shipped in ANY release tarball (July 2026)

Found after v1.0b2.158's fix still didn't work live: `build-release.sh` builds its file list from git (tracked + untracked-but-not-ignored), and `.gitignore`'s runtime-data rule was a bare `data/` — which git matches against every directory named `data` anywhere in the tree, not just the intended top-level `data/` (sysop DB/uploads/mail spools). That silently caught `anetbbs/data/` too, which holds bundled shipped content, not runtime state — so `anetbbs/data/default_taglines.txt` (~200 taglines) never made it into v1.0b2.157 or .158's tarball. The seed step's `open()` always hit a missing file on a real install, silently logged and skipped, so `/tag` correctly reported "no taglines available" — the pool was genuinely empty on every deployed copy, no matter which compose path you used. Every test in the suite passed throughout, because tests run against the repo checkout, which still has the file locally — none of them exercised "what actually ends up in the tarball."

Fixed by anchoring the rule to `/data/` and adding explicit entries for the two genuine runtime-state directories that were incidentally relying on the old broad pattern (`anetbbs/games/sbbs_doors/data/`, `vendor/games/anetsims/data/`). Also added a small regression test that shells out to `git check-ignore`/`git ls-files` to catch this exact class of "silently never shipped" bug going forward, since the existing test suite structurally couldn't.

---

# ANetBBS v1.0b2.158 — Fourth tagline call site: replying from inside an echo area (July 2026)

v1.0b2.157 wired the tagline picker into three terminal compose points (`_post_compose`, `_send_pm`, `_compose_echomail`), but missed a fourth: replying to (or starting a new message from) an echo area's message list — reached by reading an area, then pressing R or N inside the ANView reader — calls `launch_anedit()` directly from inside `read_echo_area()`, a separate code path. Reported live ("I still dont see an add tagline option in terminal when sending an echomail", then confirmed via `/tag` reporting "no taglines available" from that exact screen). Fixed; new regression test drives the real reply flow end-to-end.

---

# ANetBBS v1.0b2.157 — Tagline picker (browse & choose), compose-echomail lightbar, bad-area visibility (July 2026)

- Taglines now work as a **scrollable picker** in both terminal and web, not a blind random pick — you browse the pool and choose one, or skip. Terminal uses the same lightbar as everywhere else; web uses a visible multi-row listbox instead of a checkbox.
- FIX: composing a message in the terminal never actually asked whether to add a tagline — `/tag` was the only way in, and it wasn't even listed in ANEdit's real help screen (only in an unused internal help string). Added an active picker at all three terminal compose points, and added `/tag` to the real help screen too.
- FIX: the "Compose echomail" area picker (network → area → message) used the old numbered list with a `-- more (Enter / Q) --` page break every 17 areas. Now a scrollable lightbar, matching how areas are already browsed when reading.
- Added visibility for echomail dropped because a known area is unsubscribed/deactivated — previously silently discarded with no record at all (unlike the existing "unknown area" case). Both reasons now share the same Bad Areas admin review queue, tagged by reason, with a re-subscribe action.

---

# ANetBBS v1.0b2.156 — Message-board ANView fix, actually wired this time (July 2026)

v1.0b2.155's message-board fix targeted `read_thread()`/`list_threads()` (class-body methods), but both were dead code — `BBSMenuUI.list_threads` gets reassigned near the bottom of `bbs_ui.py` to a different implementation (`_list_threads_v2`, calling `read_thread_v2`) that shadows them at every real call site, so the original fix never actually ran. Found via a live terminal capture still showing the old `--MORE--` pager after the .155 build. Fixed the actually-reachable `read_thread_v2` to use ANView, and removed the now-confirmed-dead `read_thread`/`list_threads` methods. New end-to-end regression test drives the real `list_threads()` entry point (not just the function in isolation) to guard against this exact class of bug recurring.

---

# ANetBBS v1.0b2.155 — Shared tagline pool, and ANView for message boards (July 2026)

- Added a shared, sysop-editable pool of ~200 taglines. Opt in per message with a checkbox (web) or `/tag` (terminal) — works across local boards, private messages, netmail, and echomail. Distinct from the existing fixed per-user FTN tagline, which still auto-appends unconditionally to netmail/echomail.
- FIX: terminal message boards used the old page-break `[MORE]` pager instead of the scrollable ANView reader already used for echomail/private messages. Board threads now render through the same CP437/ANSI-aware pipeline, and reply/new-thread shortcuts work the same way they already do when reading echomail. (Note: this fix was incomplete — see v1.0b2.156.)

---

# ANetBBS v1.0b2.154 — File area fixes, per-network netmail options, and poll-in-progress visibility (July 2026)

- FIX: file area "Delete" silently saved instead of deleting (a hidden form field always overrode the button)
- Added bulk actions for file areas (multi-select, select-by-network, bulk enable/disable/subscribe/delete)
- FIX: TIC log showed a false "nodelist import failed" error on ordinary (non-nodelist) files
- Added per-network Crash/Hold/Direct netmail delivery defaults and a packet-header password field (separate from the BinkP session password)
- Added visibility for in-progress BinkP polls (previously showed nothing until a poll finished) — an admin can now see a poll is running, with a live elapsed-time counter and partial transcript

---

# ANetBBS v1.0b2.153 — Release-readiness audit: 10 install/update fixes ahead of the August 1 full release (July 2026)

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

---

# ANetBBS v1.0b2.152 — AKA announcement fix, plus a netmail send/receive correctness pass (July 2026)

Sysop-noticed oddity: some inbound sessions' M_ADR line only listed the default Fidonet address instead of all four configured network identities, while every outbound session correctly listed all four. Root cause: the AKA-lookup fix in v1.0b2.147 disposed the database connection *before* reading each network's address/domain fields instead of after, a detached-instance hazard that usually goes unnoticed (the values are normally already in memory) but can intermittently fail and silently fall back to a single default address. Moved the disposal to after all the fields are read. No automated regression test added for this one — reliably reproducing the exact timing needed to trigger a real SQLAlchemy detached-instance failure would need a heavier real-database test fixture than this module's existing mock-based ones; the fix itself is a straightforward reordering, safe by inspection.

Also did a netmail send/receive correctness pass while in this code (prompted by the M_GOT fix above). Found: the FMPT/TOPT kludges that carry a point system's point number (e.g. the ".5" in "1200:1/2.5") were parsed on receive and then silently discarded — our own outbound side never puts the point number inside the INTL kludge itself, only in these separate fields, so any point-addressed netmail lost its point number on import. The inbound listener also had its own separate, redundant re-derivation of the sender/recipient addresses that didn't use the (now-fixed) parser output at all. Both fixed; 4 new regression tests, each verified to fail without its corresponding fix.

---

# ANetBBS v1.0b2.151 — Found it: M_GOT was sending a hard-coded 0 instead of the file's real timestamp (July 2026)

The actual root cause of the multi-month BinkP resend loop, found by comparing our code line-by-line against binkd's own real source. When ANetBBS receives a file and acknowledges it with M_GOT, the reply is supposed to echo back the file's name, size, AND timestamp — ours sent the timestamp as a literal `0` instead of the real value, in both the inbound listener and the outbound poller.

binkd's own matching logic (`tfile_cmp()` in its `prothlp.c`) requires an *exact* match on all three fields before it will recognize our M_GOT as acknowledging the file it sent, and only then does it remove the file from its own outbound queue. A real timestamp looks like `1784314217` — it never equals `0`, so that match silently failed on every single file, every single session, regardless of anything else going on. The hub kept re-offering its entire backlog every poll because it never once got a M_GOT it could actually match, no matter how correctly ANetBBS otherwise received and acknowledged every file.

This fully explains the resend loop on its own — independent of session timing or the end-of-batch handshake work in v1.0b2.148-150, which remain valid improvements but were not the actual fix. 4 new regression tests, each verified to fail without the fix.

---

# ANetBBS v1.0b2.150 — Respond faster after a BinkP transfer instead of waiting out most of a minute first (July 2026)

Real measurement against a real Fidonet hub showed the TCP connection consistently dying ~15 seconds after its last file, well under either side's configured wait (120s inbound, 60s outbound) — meaning our own confirmatory end-of-batch signal (v1.0b2.148) was often being sent well after that window had already closed. Shrunk the wait to 5 seconds on both the inbound listener and outbound poller, so that signal has a real chance of reaching the hub while the link is still alive.

This is a genuine improvement to session responsiveness, verified with 2 new regression tests (each confirmed to fail without the fix) — but it's not confirmed to be the actual fix for the hub's resend loop. Direct inspection of binkd's own source turned up a more likely explanation: binkd deletes a file from its outbound queue the instant it receives our `M_GOT`, independent of anything that happens afterward in the session — and we've confirmed via transcript that our `M_GOT` has always been sent correctly. That points toward a stuck or misconfigured outbound-queue file on the hub's own system, not something fixable in our protocol timing. Investigation continues.

---

# ANetBBS v1.0b2.149 — Diagnostic logging: confirm whether the post-transfer M_EOB is actually reaching the hub (July 2026)

Follow-up to v1.0b2.148: the Fidonet hub's backlog still isn't shrinking even with that fix active. `_send_cmd()` logs its transcript line before attempting the actual socket write, so a failed write (e.g. the peer already closed the connection) can look identical in the transcript to a successful send. Added explicit success/failure logging around the proactive post-transfer M_EOB in both the inbound listener and outbound poller, to tell the two cases apart on the next poll. No behavior change.

---

# ANetBBS v1.0b2.148 — Inbound and outbound BinkP still weren't confirming delivery to the hub (July 2026)

Reported live: even after v1.0b2.147's fix, the same Fidonet hub kept resending its entire backlog every poll — the file list never shrank, even across sessions ANetBBS logged as clean successes in both directions (hub polling in, and ANetBBS polling out).

- FIX: ANetBBS only ever sent one BinkP end-of-batch signal (M_EOB) per session, sent before any files changed hands. Real file activity that followed voided it, and nothing ever sent a second, post-transfer confirmation — so a peer that never explicitly replies with its own end-of-batch signal (confirmed live: this hub doesn't) was left with no way to know the transfer was actually done, no matter how many files were individually acknowledged. Now sent unconditionally after every transfer completes, in both the inbound listener and the outbound poller.
- FIX: the inbound BinkP listener's address announcement never checked the per-network domain override (`ftn_domain`), always deriving one from the network's display name instead — a long name like "ANotherNetwork" always announced as `@anothern` regardless of any override already configured.

8 new regression tests, each verified to fail without its corresponding fix.

---

# ANetBBS v1.0b2.147 — Inbound BinkP sessions leaked a database connection every time (July 2026)

Reported live: a Fidonet hub kept resending the same packets dozens of times, even though ANetBBS was actually receiving and acknowledging every one of them successfully. A second sysop's own install hit the identical symptom.

- FIX: every inbound BinkP connection opened its own database connection and never closed it — twice per connection, in fact. The leak accumulated over hours of polling from multiple networks, occasionally causing just enough delay to trip a peer's own session timeout, even though our side finished and logged the poll as a success. Connections are now explicitly closed after every session, success or failure.

3 new regression tests, each verified to fail without the fix.

---

# ANetBBS v1.0b2.146 — Netmail flood was arriving through a second, unpatched path (July 2026)

Reported live: the same "Area Management Request"/"List of Available Areas" flood kept appearing every ~10 minutes even after v1.0b2.145 shipped, confirmed via direct database inspection.

- FIX: the peer polls into ANetBBS's inbound listener independently of — and around the same cadence as — the outbound poll of the same hub. That's a separate code path (`binkp_server.py`) from the one fixed in v1.0b2.143/145 (`poller.py`, used when ANetBBS dials out), and it never had any content-based dedup fallback at all — only the exact-MSGID check, which the peer's fresh-MSGID-per-resend behavior always defeats. Both directions now share the same sender+subject+network dedup approach.

3 new regression tests, each verified to fail without the fix.

---

# ANetBBS v1.0b2.145 — Web UI freezing for every user, and the netmail flood fix from v1.0b2.143 wasn't actually catching everything (July 2026)

Reported live, urgent: the entire web UI started freezing solid (unresponsive tab, not even a slow page) for minutes at a time, shortly after v1.0b2.143 shipped.

- FIX: `NetmailMessage.received_at` had no database index, but v1.0b2.143's new content-based netmail dedup check filters on it. This app runs under eventlet, which does not make SQLite non-blocking — so an unindexed, growing scan on every inbound netmail blocked the *entire* process for *every* user on *every* page until it finished. Added the missing index, plus an automatic backfill so already-installed databases pick it up on next startup with no manual step required.
- FIX: v1.0b2.143's dedup check also required an exact match on message body — confirmed live this wasn't sufficient (the same "Area Management Request"/"List of Available Areas" netmail kept creating new entries every ~10 minutes even with that fix deployed), which was also what fed the freeze above by continuously growing the unindexed table. Now matches on sender+subject+network within the dedup window instead, without requiring the body to be byte-identical.

If you already applied the manual `CREATE INDEX` workaround, this release's migration is idempotent and won't error on top of it.

9 new regression tests, each verified to fail without its corresponding fix.

---

# ANetBBS v1.0b2.144 — Inbound BinkP: crashed sessions left zero diagnostic trace (July 2026)

Reported live: a peer sysop's own binkd log showed our inbound listener closing the connection abruptly, mid-transfer, with none of his files ever getting an acknowledgment from our side — but there was no corresponding ANetBBS-side record of what happened, anywhere a sysop could see it.

- FIX: an inbound BinkP session only ever wrote a Poll Log entry on successful completion — that write happened as the very last step of handling a connection, so any exception before it (a network error, a bug, anything going wrong mid-transfer) meant the entire session vanished with no trace beyond a bare stack trace in the raw application log. A crashed session now writes an error-status Poll Log entry with the full frame-by-frame transcript captured up to the failure point, matching how the outbound poller has always behaved on failure.
- Also: the Poll Log transcript viewer loaded scrolled to the top of a fixed-height box — for any real file transfer (hundreds of frame-by-frame lines), this made it easy to believe a transcript ended wherever the visible area happened to stop, when it actually continued below. Now auto-scrolls to the end (the diagnostically relevant part) on load, with a "jump to start" button for the handshake.

2 new regression tests, each verified to fail without the fix.

---

# ANetBBS v1.0b2.143 — BinkP: peers resending their entire backlog forever, even after every file was correctly acknowledged (July 2026)

Reported live: a real FTN peer kept resending its complete inbound backlog on every single poll, indefinitely, despite ANetBBS correctly M_GOT-acknowledging every file every time — visible as a specific netmail ("Area Management Request" / "List of Available Areas") repeating every ~10 minutes.

- FIX: a shared internal helper used by both the outbound poller (`binkp.py`) and inbound listener (`binkp_server.py`) opportunistically captures frames a peer interleaves while we're still waiting on our own acknowledgment — but it only recognized file-offer frames. A peer's own end-of-batch signal (M_EOB), which a spec-compliant mailer sends the instant its own outbound queue empties — independent of whether it's still waiting on us — fell straight through and was silently discarded, with zero trace. Our own end-of-batch handshake would then wait indefinitely for a signal the peer had already sent once and had no reason to resend unprompted. From the peer's own side, its session never registered as successfully completed (the binkp spec requires both sides to receive the other's end-of-batch signal), so it kept requeuing and resending everything on every subsequent connection — root-caused against a complete real session transcript and the official binkp protocol specification. Fixed on both the outbound and inbound sides: an early end-of-batch signal is now recorded and credited instead of waited for a second time.
- Also added a content-based dedup fallback for inbound netmail: if a message's MSGID doesn't match anything on file, but its sender, subject, and body exactly match something received within the last 48 hours, it's now treated as a duplicate too — a backstop for any peer software that regenerates its MSGID on every resend.

15 new regression tests across three files, each verified to fail without its corresponding fix.

---

# ANetBBS v1.0b2.142 — Echomail web preview didn't match what actually gets sent (July 2026)

Reported live: after the v1.0b2.139/141 origin-line fixes, a newly composed message still showed no address in its "* Origin:" line when viewed in the web UI — the fix looked like it wasn't working.

- FIX: the message-read page displayed the raw stored `origin_line` field, which never includes the FTN address — that gets appended separately, per network, at actual BinkP send time. The preview simply didn't reflect what would really go out over the wire. The read page now computes the same address-append logic for outbound messages, so what a sysop sees in the web UI matches what a peer actually receives.

2 new regression tests, each verified to fail without the fix.

---

# ANetBBS v1.0b2.141 — BinkP: stop re-sending the same netmail/echomail forever when a peer talks a lot before acking (July 2026)

Reported live: an AreaFix subscription request to a real SBBSecho hub kept getting re-sent on every single poll, forever — the peer replied fresh each time, since as far as it could tell it was receiving a brand-new request every time.

- FIX: the wait for a peer's M_GOT acknowledgment was bounded by a fixed count of frames (20), not wall-clock time. A peer that responds with substantial content of its own before finally acking — exactly what SBBSecho does, replying with real content spread across many small frames — could exhaust that budget with our own GOT never reached, even though the peer had already fully received and processed what we sent. The packet then got treated as unacknowledged and left queued for retry, so the next poll sent the identical request again — and the peer, having no reason to think otherwise, replied again. Now bounded by wall-clock time instead, so any amount of peer chatter before the real acknowledgment still gets fully drained.

3 new regression tests, each verified to fail without the fix.

---

# ANetBBS v1.0b2.140 — Echomail origin line: show how to reach the BBS, not just its name (July 2026)

Follow-up to v1.0b2.139's address fix — requested live: the default origin-line text itself should follow the common FTN sysop convention of listing how to actually connect, not just the BBS's name.

- `ECHOMAIL_ORIGIN_LINE`'s default is now `<hostname>, Telnet:N SSH:N HTTP:N` (e.g. `joesbbs.com, Telnet:23 SSH:22 HTTP:80`), using whichever of those services are actually enabled and their real configured ports. Combined with v1.0b2.139's per-network address fix, a full origin line now reads e.g. `joesbbs.com, Telnet:23 SSH:22 HTTP:80 (1:342/201)` — matching the standard FTN format.

5 new regression tests.

---

# ANetBBS v1.0b2.139 — Outbound echomail origin lines were missing the FTN address entirely (July 2026)

Reported live: outbound messages showed `* Origin: ANetBBS - A Modern BBS System` with no address at all, instead of the FTN convention `<text> (<our address>)` a peer's own reply routing depends on.

- FIX: the address-inclusive fallback only ran when the message's origin line was completely empty — but the web compose form always populates it from a single global tagline, so that fallback never actually fired for any real outbound message, on any network. The network's own address (already resolved per-network for multi-hub-identity installs) is now always appended, regardless of what origin text is configured.
- Fresh installs also no longer default to generic ANetBBS-branding text for this — the origin line now defaults to the sysop's own configured BBS name instead.

4 new regression tests, each verified to fail without the fix.

---

# ANetBBS v1.0b2.138 — Scheduled events: one hung handler could silently kill the entire scheduler forever (July 2026)

Reported live: a sysop's scheduled events — including the stock defaults, not just the one they'd added — simply stopped firing entirely, with no error anywhere. A 4-day log showed the scheduler's own startup line exactly once and nothing after.

- FIX: a handler that hung (a `shell` command waiting on input, a network call with no effective timeout, anything) froze the scheduler thread forever — silently, with no crash logged — taking down every other scheduled event with it, including ones that had nothing to do with whatever hung. This was a known, deliberate tradeoff ("a runaway is a sysop bug, not a security threat"), but the real-world consequence turned out to be worse than that framing accounted for. Every handler call is now bounded to a generous 5-minute ceiling — comfortably above what any of the built-in handlers actually need — so one hung handler can no longer take every other event down with it.

4 new regression tests, each verified to fail without the fix.

---

# ANetBBS v1.0b2.137 — BinkP inbound listener: stop making peers wait in silence (July 2026)

A peer sysop's own binkd log showed it delivering files to our inbound listener successfully — every one individually acknowledged — then sitting in total silence for over 2 minutes hearing nothing back from us, before giving up, closing the connection, and marking the whole transfer failed. It kept resending the same backlog on every subsequent connection as a result.

- FIX: the inbound listener used to fully drain everything a peer sent (waiting up to 120s for the *peer's own* end-of-batch signal) before ever sending our own outbound mail or announcing that we were done. If the peer's own mailer was itself waiting to hear from us first — which nothing in the protocol requires either side to do — neither side would say anything until somebody's timeout fired. We now send our own outbound mail (if any) and announce we're done immediately after authenticating, before waiting on the peer's stream at all.
- As a consequence of sending earlier, a peer may now interleave its own file delivery while we're still waiting on an acknowledgment for ours — those were previously logged and silently dropped; they're now received correctly, same as the outbound side already handled.

5 new/updated regression tests, each verified to fail without the fix.

---

# ANetBBS v1.0b2.136 — Docs/wiki full accuracy pass (July 2026)

First full docs+wiki pass since v1.0b2.45-46 — 5 parallel audit agents checked all 29 docs/*.md files and all 47 wiki pages against actual current code.

- Fixed stale references to gunicorn (dropped for eventlet's native WSGI server back at v1.0a2.67, several docs never caught up), a badly outdated Service Control Center wiki page, and a factually wrong claim about echomail duplicate handling.
- Documented several real, previously-undocumented features: a new Scheduled Events wiki page, ANetCRAFT (the bundled door game), the Ebook Reader, and the terminal Node Monitor's message capability.
- Found and fixed 43 broken internal wiki links, plus assorted smaller path/port/version-number corrections.

No code changes — docs and wiki content only.

---

# ANetBBS v1.0b2.135 — BinkP inbound listener: fix unbounded duplicate-message import (July 2026)

Reported live: a peer sysop's poll log showed the same ~570-message backlog "received" fresh on every inbound BinkP connection, a few minutes apart, with no sign of slowing down.

- FIX: the inbound BinkP listener (a peer connecting *into* this BBS to deliver mail) imported every message in a delivered packet unconditionally — no deduplication at all. For echomail it didn't even capture the MSGID kludge onto the row in the first place; for netmail it captured MSGID but never checked for an existing one before inserting. The outbound poller's own import path already did both correctly (`poller.py`) — this second, separate import path (`binkp_server.py`, used specifically when a peer dials in rather than when we dial out) never got the same treatment. Any redelivery of a backlog — a peer's own retry logic, a flaky link, anything — got re-imported as entirely new messages, unbounded, every time. Now deduplicates by MSGID the same way the outbound path already does.

3 new regression tests, built from a real FTS-0001 packet round-trip (not a hand-built dict), each verified to fail without the fix.

---

# ANetBBS v1.0b2.134 — BinkP GOT-ack fix, Service Control Center + scheduled-events reliability (July 2026)

Three unrelated reports: a peer sysop's poll log showing a real FidoNet hub rejecting our file acknowledgments mid-session, a Control Center screenshot showing a terminal-services crash with an alarming (but misleading) 99.3% CPU reading, and a peer sysop reporting his nightly/hourly scheduled events just weren't firing.

- FIX: receiving files during a poll we initiated ourselves (as opposed to a peer polling us) acknowledged each one with a bare `GOT: filename` — no size or timestamp. FTS-1026 defines `M_GOT` as `filename size time`, the same three fields as `M_FILE`; our own inbound listener already sent this correctly, but this second, separate code path never got the same fix. Tolerated by most peers, but confirmed live against a real, stricter binkd: the first two files' bare GOT acks were silently accepted, the third was rejected outright (`ERR: M_GOT: cannot parse args`) and the peer hung up — reproduced identically across two independent sessions.
- FIX: the Service Control Center's CPU%/RAM/thread pills kept showing a dead service's *last-known* reading indefinitely, with nothing marking it stale — a crashed process could look like it was still pinning the CPU for up to 5 minutes after it actually exited. Now blanked to "—" the moment the sampler's most recent tick comes back empty. Same bug also silently inflated the aggregate CPU/RAM totals at the top of the panel.
- FIX: the Control Center's own `/status.json` polling (systemctl state + journal reads, once per known unit, every 5s while the panel is open) ran raw `subprocess.run()` under gunicorn+eventlet — the same tight-loop shape already found crash-looping in the metrics sampler (v1.0b2.131, "Second simultaneous read on fileno N detected"). Routed through `eventlet.tpool.execute()` the same way.
- FIX: one scheduled event with an out-of-range time value (the `daily`/`weekly` schedule kinds were never format/range-validated, unlike hourly/weekly-day/interval) could crash the *entire* event scheduler sweep every single tick — silently blocking every other scheduled event (nightly maintenance, log rotation, VACUUM, custom events) from ever running again, with only one generic log line to go on. The scheduler now isolates each event so one bad row can't affect the others, and the form validates daily/weekly times the same way the other schedule kinds already were.

17 new regression tests across the three testable fixes (the stale-metrics display fix is front-end JS with no test harness in this project), each verified to fail without its fix.

---

# ANetBBS v1.0b2.133 — Terminal MRC: fix false "Rate limit" error on split messages (July 2026)

Reported live ("still getting this rate error when dming, even though it's not [too long]"). Not actually related to message length in the sense reported — the fix is real, but the mechanism is different from what it looked like.

- FIX: a message that needs to split into multiple wire chunks (this can happen well under the 140-char hub limit — a decorated display handle's prefix/suffix eats into that budget before your own text even starts) was sending every chunk back-to-back with zero delay. The bridge only allows one message every 0.5 seconds per connection, so any chunk after the first was always instantly rejected with "Rate limit: please slow down." Fixed by adding a small pause before each chunk after the first, in all four places a message can split: room chat, `/me`, `/broadcast`, and DMs (`/msg`, `/r`).

4 new regression tests, each verified to fail without the fix.

---

# ANetBBS v1.0b2.132 — Web: fix door game output silently stopping after the first idle pause (July 2026)

Root-caused the actual "dosemu2 door games show a black screen over the web UI" report — confirmed via a live log capture showing dosemu2 booting and rendering correctly server-side the entire time, while the browser never received any of it.

- FIX: `handle_start_game()`'s background output-draining task has no Flask app context of its own. Its idle-timeout check (does the queue sit empty for 5+ seconds? then verify the session is still active) needs a database query, which needs that context — and didn't have one. The very first time a door produced no output for 5+ seconds (a completely normal boot/loading pause — near-guaranteed for dosemu2 specifically, since it's much slower to boot than a native door), that query crashed with `RuntimeError: Working outside of application context`, which the surrounding code treated as "the session must be gone" and permanently stopped draining the queue for the rest of that browser session. The backend kept running the door correctly the whole time; nothing it rendered after that point ever reached the browser again — exactly a stuck black screen.

1 new regression test that reproduces the real 5-second idle gap directly (not simulated) and confirms output sent after it still reaches the browser — confirmed to fail with the exact live error message before the fix.

---

# ANetBBS v1.0b2.131 — Web: fix Service Control Center metrics sampler crash-loop (July 2026)

Found while investigating a separate live "dosemu2 door games show a black screen over the web UI" report — this was the actual thing crash-looping in the same process, every ~2 seconds, confirmed via `journalctl`.

- FIX: the Service Control Center's background per-PID metrics sampler called `subprocess.run()` once per known systemd unit in a tight sequential loop. Under gunicorn+eventlet, that call runs through eventlet's greened subprocess module; any transient hiccup could leave its fd-listener registered in the shared epoll hub past the point its fd number got recycled by the next call, colliding with it (`RuntimeError: Second simultaneous read on fileno N detected`) and crash-looping from that point on, indefinitely. Now dispatches the call to a real native OS thread via `eventlet.tpool.execute()`, bypassing eventlet's greened machinery entirely — the officially recommended pattern for exactly this.

4 new regression tests, each verified to fail without the fix.

---

# ANetBBS v1.0b2.130 — MRC: stop sending LOGOFF on individual leave — this is what was breaking trust (July 2026)

Root-caused from a real, complete packet transcript (`MRC_BRIDGE_LOG_LEVEL=DEBUG`) captured on the live server — the first fix in this saga backed by actual evidence instead of spec/source comparison.

- FIX: the bridge sent `LOGOFF` every time an individual caller left a room. The transcript showed this ends the hub's MRC Trust state for that handle **immediately** — the very next join got "Cannot join ROOM, please IDENTIFY to use this handle" even though the bridge's own connection to the hub never dropped in between. Since this bridge holds one persistent shared connection to the hub per BBS install across every local caller's join/leave, there's no need to tell the hub "this handle is logging off" the way a single-session client would. `LOGOFF` is no longer sent on an individual leave (neither an explicit `/quit` nor an abrupt disconnect) — `NOTME`'s "has left chat" message still covers the visible room-presence announcement other users see.
- Trade-off worth knowing: the hub's own `/who`/`CHATTERS` listing may show your handle lingering briefly after you leave, until your next reconnect's fresh join or the hub's own idle timeout cleans it up.

4 new regression tests, each verified to fail without the fix.

---

# ANetBBS v1.0b2.129 — MRC: fix debug-level packet tracing not actually activating (July 2026)

`.128`'s `MRC_BRIDGE_LOG_LEVEL=DEBUG` had no effect — confirmed live, zero `MRC RAW` lines after enabling it and restarting. `logging.basicConfig()` is a documented no-op if the root logger already has a handler attached before it runs (plausible under systemd, depending on import order) — it was silently doing nothing. Now sets the level directly on the `mrc_bridge` logger itself, which takes effect regardless.

2 new regression tests (one specifically simulating the pre-existing-root-handler scenario that caused this), verified to fail without the fix.

---

# ANetBBS v1.0b2.128 — MRC: add full raw packet tracing for diagnosing the identify-persistence bug (July 2026)

Three rounds of wire-format fixes verified against reference client source and the actual official protocol spec haven't resolved the live "still have to identify every time" report — rather than guess a fourth time, this adds a way to capture a complete real transcript for direct comparison.

- Every outgoing packet (`send_packet`) and every incoming line from the hub are now logged verbatim, tagged `MRC RAW OUT`/`MRC RAW IN`, at `DEBUG` level only — gated behind a new `MRC_BRIDGE_LOG_LEVEL` environment variable (defaults to the existing `INFO`, unchanged). Deliberately not on by default, since full tracing would otherwise mean every private chat message lands in plaintext in the server's own logs permanently.

1 new regression test, verified to fail without the fix.

---

# ANetBBS v1.0b2.127 — MRC: correct wire format against the actual official protocol spec (July 2026)

Direct follow-up to `.123`/`.124`, after obtaining the real MRC protocol developer documentation (not inferred from client source). Two important corrections:

- FIX: `.124`'s "empty `toRoom` for every generic command" change was based on one reference client's own implementation shortcut, not the actual spec — the documented template for most commands (`MOTD`, `WHOON`, `BANNERS`, etc.) is `user~bbs~room~SERVER~msgext~room~COMMAND~`, with `toRoom` populated. Reverted to populate it, keeping the empty-`toRoom` exception only for `IDENTIFY`/`REGISTER`/`UPDATE`, which the spec documents separately.
- FIX: same correction for `LOGOFF` (sent every time you leave) — the documented template has **both** `fromRoom` and `toRoom` populated with the room name; `.124` had emptied `toRoom` based on the same wrong assumption. Reverted to match spec.
- FIX: a genuinely new finding from the real spec — `USERIP` should send an empty `fromRoom` (`user~bbs~~SERVER~msgext~~USERIP:ipaddress~`), which the code never did correctly in any prior version. Fixed.
- Also confirmed from the spec: MRC Trust is keyed to (Handle, BBS Name, BBS's own IP address) with a 30-day window — not the individual caller's IP. Confirmed the BBS has a stable static IP, ruling that out as a contributing factor, which puts the weight back on these wire-format corrections actually being the fix.

10 new/updated regression tests, verified against the reference spec, each confirmed to fail without the fix.

---

# ANetBBS v1.0b2.126 — Terminal: bulletins now use the scrollable ANView reader (July 2026)

- FEATURE: reading a bulletin longer than a page used the old page-break `[MORE]` pager instead of the scrollable ANView reader already used for echo/private messages. Now uses ANView (Up/Dn/PgUp/PgDn to scroll, Q to back out) instead.
- Bulletins are authored via the web admin's plain-text form, not composed at a terminal, so this deliberately does *not* reuse the message reader's CP437-mojibake decode pipeline — that would have silently corrupted any non-ASCII bulletin text (curly quotes, em dashes, accented letters) into unrelated glyphs. Raw ANSI escapes in a bulletin still render correctly.

3 new regression tests, verified to fail without the fix.

---

# ANetBBS v1.0b2.125 — Terminal: file areas now lightbar-scrollable, matching message areas/RSS (July 2026)

- FEATURE: the terminal "File Library - Areas" screen used to dump every configured area top-to-bottom with a plain number-entry prompt — for any sysop with more file areas than fit one screen, this meant relying on the terminal client's own scrollback just to see the top entries. Now uses the same arrow-key lightbar selector already used for message areas and the RSS reader: Up/Dn/PgUp/PgDn to scroll, Enter to open, A for All Files, Q to back out.

3 new regression tests, verified to fail without the fix.

---

# ANetBBS v1.0b2.124 — MRC: fix real LOGOFF/command wire-format mismatch behind repeated forced re-identify (July 2026)

Direct follow-up to v1.0b2.123, after live testing showed identify was still required on every leave/rejoin. Root-caused by comparing exact packet fields against the reference client's C source, field by field.

- FIX: the `LOGOFF` packet sent every time you leave MRC populated the `toRoom` field with the room name; the reference client always sends it empty for `LOGOFF`. If the hub's trust/session tracking keys off that field, a populated one on logoff would plausibly end a handle's trusted session — a direct, verifiable candidate for "have to identify every time."
- FIX: the same mismatch existed on every other generic command (`MOTD`, `WHOON`, `CHATTERS`, `USERIP`, etc.) — previously only `IDENTIFY`/`REGISTER`/`UPDATE` sent an empty `toRoom`; the reference sends it empty for all of them.

7 new regression tests verified against the reference wire format, each confirmed to fail without the fix.

---

# ANetBBS v1.0b2.123 — MRC: fix "have to /identify every time" root cause + topic/userlist bugs from a full client review (July 2026)

Prompted by a report that MRC Trust never seemed to persist, plus two bugs found live while testing. Root-caused by comparing ANetBBS's terminal MRC client against the wire protocol line-by-line.

- FIX: the protocol expects a `USERIP:` packet on every room join, and the hub appears to use it to recognize a returning already-identified connection — but ANetBBS's bridge never sent one, for any user, on either the terminal or web client. This forced a fresh `/identify` on every single connect, no matter how recently the same handle had already identified. Now sent correctly: the web client's real address is captured server-side from the incoming connection (honoring `X-Forwarded-For` since the bridge sits behind nginx), and the terminal client — whose connection to the bridge always looks like `localhost` — now reports its own real caller address explicitly.
- FIX: the room topic line (`── Topic: ...`) was re-printed above the input line every time the hub re-sent a `ROOMTOPIC:` packet, even when the topic text hadn't actually changed — spamming the same line repeatedly. Now only announced when the topic genuinely changes.
- FIX: the sidebar user list only ever added names, never removed them — so if a single user's leave notice was ever missed, their name stuck around forever, since even a fresh full list refresh from the hub couldn't clean it up. Now treated as the authoritative snapshot it is: a fresh list fully replaces the known set.
- Reviewed CTCP handling, DM/reply, room switching, and away/back against the protocol's actual wire behavior — all already correct, no further changes needed there.

12 new regression tests, each verified to actually catch its bug (reverted and confirmed failing, then restored).

---

# ANetBBS v1.0b2.122 — ANetIRC: fix broken function/nav keys on SyncTerm + 4 more real bugs from a deep review (July 2026)

Prompted by a bug report ("F2 doesn't remove the user listing") from a SyncTerm/SSH user. Root cause was much bigger than F2 alone.

- FIX: the key parser was built entirely around xterm's keyboard conventions, but SyncTerm — likely the most common BBS terminal — uses a completely different, non-standard set of codes, confirmed directly against SyncTerm/CTerm's own official documentation. As a result **F1 through F12, PgUp, PgDn, End, Insert, and Back Tab all silently did nothing** for SyncTerm users, not just F2. Rewrote the parser to correctly handle SyncTerm's real sequences while keeping existing xterm/vt220 support working for other clients.
- FIX: CTCP requests other than ACTION (VERSION, PING, etc.) used to leak into the chat window as a blank ghost line from that user, and the requester never got a reply (real IRC etiquette expects one; some bots/clients flag nicks that never answer). VERSION and PING now get a real reply and don't show in chat; anything else is silently ignored instead of leaking a blank line.
- FIX: nick tab-complete could never actually cycle to a different match on repeated Tab presses — the second press looked for a match in the already-completed text, found none, and silently did nothing. Now correctly cycles through every match.
- FIX: a single malformed line from the IRC server could silently kill the background connection with zero visible error — chat would just stop receiving anything, indistinguishable from a hung connection. Now logs a visible error and keeps going.
- FIX: a literal `|` character typed into a bookmark field (most plausibly the label, e.g. "Home | Personal") silently corrupted and misaligned every field after it the next time bookmarks loaded, with no error. Fixed at the point bookmarks are saved.

31 new regression tests, each verified to actually catch its bug (reverted and confirmed failing, then restored) rather than just passing by coincidence.

---

# ANetBBS v1.0b2.121 — BinkP inbound transcripts; FileFix docs; Ask Anet troubleshooting content (July 2026)

- FIX: inbound BinkP sessions (a peer connecting TO this BBS) never saved a frame-by-frame transcript — only outbound polls did, since v1.0b2.47. This mattered most for exactly the direction this session's whole BinkP audit was chasing: a peer hub pushing mail in and then stalling, with no in-app log to look at afterward. Inbound sessions now save a transcript the same way outbound polls do, viewable in the same **Admin → Echomail Networks → Poll Logs** UI (no UI changes needed — it already just checks whether a log row has one).
- DOCS: FileFix (the file-echo counterpart to AreaFix) was never documented anywhere, despite being a fully-working bot — added a full section to `docs/07-file-areas.md`.
- FEATURE: Ask Anet's help search only ever indexed the wiki, not `docs/*.md` — and the wiki had zero troubleshooting content, so searching for help with a real problem always came back empty. Added a comprehensive Troubleshooting wiki page consolidating real symptom→fix entries from across the docs (web/nginx, FTPS, MRC, BinkP/echomail, doors, Docker, webhooks, scheduled events, MSP, Raspberry Pi, RSS), linked from Home and the Sysop Guide, and verified end-to-end that Ask Anet's search now actually finds it.

---

# ANetBBS v1.0b2.120 — BinkP/echomail: close 3 remaining alignment gaps found in follow-up spec research (July 2026)

Direct follow-up to v1.0b2.119's BinkP audit — closes three specific gaps identified after that release, each now verified against a real, fetched primary source rather than inference:

- FIX: the outbound BinkP client never cross-checked a peer's claimed address (M_ADR) against the hub address we actually dialed — a wrong host answering on the expected IP/port (stale DNS, misconfiguration) would sail through unnoticed as long as it also had our password. Now logs a clear warning on mismatch (the password remains the real auth gate, so this doesn't abort the session — a legitimate multi-AKA hub could otherwise trigger a false positive and break a working link).
- FIX: nodelist header parsing verified directly against the actual FTS-5000 spec text (fetched and confirmed, not guessed) — the real example given in the spec now round-trips correctly, in addition to this software's own generated format from v1.0b2.119.
- VERIFIED (no bug found): re-checked the QWK MESSAGES.DAT message-header field layout against the actual published QWK format spec, byte by byte. Everything already matched — added dedicated regression tests built independently from the spec's own offset table so this can't silently drift later.

---

# ANetBBS v1.0b2.119 — BinkP: fix a real mail-loop/mail-loss bug, full-subsystem audit (July 2026)

Prompted by a real peer sysop's report of repeated duplicate bundle deliveries and mid-session disconnects. Root-caused against the FTS-1026/FTS-1027 specs, binkd's own C source, and Synchronet's binkp.js reference implementation, then verified end-to-end with new regression tests (scripted real BinkP frames, not mocks).

- FIX: inbound listener could freeze **every other concurrent BinkP connection** while importing a large batch of mail — the listener runs one shared event loop for all sessions, and importing (DB writes, ZIP extraction, parsing) ran as blocking code directly on it. A big catch-up on one connection could stall a brand-new, unrelated connection for minutes, causing the peer to give up before ever getting an acknowledgement — the direct cause of the reported loop. Import now runs on a background thread so it can't block other sessions.
- FIX: the inbound listener could also delay closing its own session until after importing what it just received; a slow import meant the peer's connection timed out before the session formally ended, so the peer's own bookkeeping never marked those files delivered and resent them next time. The session now finishes (and the socket closes) immediately after files are received/acknowledged, before import runs.
- FIX: the outbound client used to mark a batch of messages "sent" regardless of whether the hub actually acknowledged it — a busy or unstable hub replying "skip" or "error" (both normal, spec-legal responses) silently and permanently discarded real outbound mail with no retry. It now only marks messages sent on actual acknowledgement; anything else is safely retried next poll.
- FIX: the outbound client didn't handle the hub closing the connection while waiting for that acknowledgement — it would crash instead of retrying gracefully.
- FIX: a path-traversal gap in both the inbound listener and outbound client — an inbound filename was never reduced to a safe basename before being used to write a local file.
- FIX: two silent-failure spots (a malformed FTN address, corrupted routing history on a message) now log a warning instead of failing invisibly, so a sysop debugging misrouted mail has a trail to follow.

Follow-up full audit of the rest of the echomail subsystem (AreaFix/FileFix, the hub tosser, QWK-hub FTP, InterBBS sync, nodelist import) surfaced several more real issues, now also fixed:

- FIX (security): inbound AreaFix/FileFix netmail was never checked against the AreaFix password — any netmail addressed to the areafix/filefix robot could subscribe/unsubscribe echo areas with no authentication at all. Now verified against the same password the outbound side already sends (FTS-0024), on both the leaf and hub paths.
- FIX: the hub's SEEN-BY loop-prevention check never actually worked (comparing a zone-qualified address against untokenized raw SEEN-BY lines, which could never match) — a hub could re-toss a message right back to the exact node that's also subscribed to receive it, a needless bounce and a contributor to loops.
- FIX: a single bad message in a multi-message QWK REP upload could silently discard earlier, already-successfully-imported messages from the same batch.
- FIX: InterBBS Wall/Last-Callers/Game-Scores sharing was re-sending a redundant AreaFix subscribe request on every single post/caller/score event instead of only when actually needed.
- FIX: nodelist header parsing (day-of-year, release date) never worked against this software's own generated nodelists.

---

# ANetBBS v1.0b2.118 — MRC bridge crash fix + protocol correctness; new terminal/web MRC features (July 2026)

- FIX: MRC bridge crash on a WebSocket-disconnect race (16 unguarded response sends) — confirmed via a live production crash-loop; very likely the actual cause of MRC trust unexpectedly lapsing.
- FIX: MRC bridge now handles the hub's version-enforcement messages instead of silently reconnect-looping or leaking them into chat, plus a few other protocol-correctness fixes (graceful shutdown notice, accurate capability list).
- NEW: MRC terminal client — `/set defaultroom`, `/set twitfilter`, `/set clockformat`, `/welcome`, `/changes`, plus `/q` `/b` `/cls` aliases.
- NEW: MRC web client — scrolling ticker/banner, latency display, clock, and timezone-offset setting (all previously missing entirely from the web client).

---

# ANetBBS v1.0b2.117 — Full docs/wiki accuracy pass ahead of Aug 1; exec-door drop-file fix; new terminal gallery viewer (July 2026)

- DOCS: Full docs/wiki accuracy pass ahead of the Aug 1 release — every claim checked against the actual code; fixed dozens of stale, wrong, or fabricated entries across `docs/` and the wiki (including invented MRC hub details, dead admin links, and wrong dosemu2/DOSBox drive-letter guidance).
- FIX: Exec-door drop-file generation (the `exec` menu action) was silently broken since it shipped — a wrong function name meant every configured drop file failed quietly. Now actually works.
- NEW: Terminal image gallery viewer (`anet-gallery.sh`) — documented for a while but never built. Now generated automatically by install.sh/update.sh.
- FIX: RSS feed access-level restrictions weren't enforced on two of four web routes — a restricted feed was directly reachable even though hidden from the river/index.

---

# ANetBBS v1.0b2.116 — install.sh: FTPS cert-renewal fix; expanded troubleshooting docs (July 2026)

- Fixed a gap where FTPS certificate permissions could silently break after the first Let's Encrypt renewal on a fresh install (only `update.sh` had the fix before).
- Added several new troubleshooting entries to `docs/INSTALL.md`/`docs/INSTALL-PI.md`/`docs/06-echomail.md` covering common newer-sysop pain points (SELinux blocking nginx, MRC config path overrides, Pi `/tmp` confusion, FTPS renewal, BinkP poll intervals).

---

# ANetBBS v1.0b2.115 — install.sh/update.sh: better distro detection, dependency update checks, extra verification (July 2026)

- Improved Linux distro detection and package handling in `install.sh`/`update.sh` (openSUSE, dosemu2 support, clearer firewall guidance where automation isn't available).
- Added automated dependency update/security checks (`pip-audit` + Dependabot) so outdated or vulnerable packages get flagged instead of going unnoticed.

---

# ANetBBS v1.0b2.114 — Echomail poller: back off after a failed poll instead of retrying every tick (July 2026)

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

---

# ANetBBS v1.0b2.113 — Multi-hub-identity: join form, nodelist, QWK hub, and BinkP auth all made identity-aware (July 2026)

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

---

# ANetBBS v1.0b2.112 — MRC bridge: root cause was a config-path mismatch, not code — plus pipe colors restored (July 2026)

- RESOLVED: the BBS-info-fields saga (v1.0b2.107-111) is now fully confirmed working live, and the real root cause turns out to have never been a code bug at all. One sysop's install had its bridge service pointed at a non-default `MRC_BRIDGE_CONFIG` path (in a `config/` subdirectory) via an environment variable — a leftover from running his own MRC bridge for two years before ANetBBS was built around it. He'd been editing the default `mrc/bridge/config.json` path the entire time, a file his own service never actually reads. Every fix shipped in v1.0b2.107-111 (the `send_info_fields()`/`BBSMETA` additions, the field-order correction, the stale-session dedup) was correct and necessary — none of them had anything to verify against until this was found via the raw packet logging added in v1.0b2.111 finally showing default/empty values despite a correctly-edited file. No install/deploy tooling changes needed — this is a one-off historical artifact of that specific install, not something fresh installs or the Pi3 hit.
- FIX: reverted the pipe-color-code stripping added for `send_info_fields()`/`send_bbsmeta()` in v1.0b2.109/.110. That stripping was reasoned only from the reference client's own plain-text `MRCBBS.DAT` example having no color codes in it — the sysop pushed back with two years of hands-on experience with this exact protocol confirming pipe colors in the BBS name/telnet/ssh/website/sysop/description fields are expected and supported, same as the BBS name field itself (which was never stripped). These values now go out exactly as configured, colors included. The *unrelated* pipe-code fix in `_extract_identified_handle` (comparing the hub's identify-success reply against a plain-text stored handle, not anything ever displayed) is untouched and still correct.
- 739 passed total (2 existing tests updated to assert colors are preserved, not stripped).

---

# ANetBBS v1.0b2.111 — MRC bridge: fixed duplicate self-heal on stale sessions + raw packet logging for BBS-info (July 2026)

- FIX: once v1.0b2.109's pipe-code fix made `/identify` actually get recognized, a single `/identify` started replaying the join once per matching *database* session record for that handle, not once per live connection — and a hard `systemctl restart` (used repeatedly during this same live-troubleshooting session) doesn't run the graceful WebSocket-close cleanup path, so stale records for the same handle piled up across restarts. Reported live as MOTD/CHATTERS showing up 4 times after a single `/identify`. Fixed: the self-heal now only acts on sessions with a genuinely live WebSocket connection right now, skipping any DB record with no matching live connection.
- DIAGNOSTIC: v1.0b2.110's field-order fix + `BBSMETA` addition, while verified byte-for-byte against the reference client, still didn't make the sysop's BBS-directory entry populate live — the real root cause remains unconfirmed after two careful, reference-checked attempts. Rather than guess a third time, added raw outgoing-packet logging (`logger.info`) for both `send_bbsmeta()` and `send_info_fields()` so the exact bytes leaving the bridge can be inspected directly.
- 1 new test (739 passed total).

---

# ANetBBS v1.0b2.110 — MRC bridge: fixed the BBS-info packet field order + added missing BBSMETA (July 2026)

- FIX: v1.0b2.107/.109's pipe-code stripping alone still wasn't enough — the sysop's directory entry stayed blank even with a fully clean, correctly-configured `config.json`. Re-checking the reference client's `mrc_send_info_fields()` byte-for-byte against its own `create_packet`-equivalent call turned up a genuine field-order bug: the reference client sends these specific "broadcast to everyone" packets with the literal `"ALL"` and the epoch timestamp in the **opposite** wire positions from every other message in this protocol (room-scoped sends put the timestamp before the room; this one puts `"ALL"` before the timestamp) — a quirk (possibly an accidental one even in the reference client itself, inconsistent with its own `BBSMETA` call a few lines above) that real hubs apparently expect literally. ANetBBS's `send_info_fields()` used the "logically correct" order instead, which put a garbage timestamp string where the hub expects `"ALL"` — with nothing valid to route the broadcast by, the hub had no reason to store it, blank directory entry despite zero errors anywhere in the pipeline. Field order now matches the reference client's actual sent bytes exactly.
- FEATURE: also added `send_bbsmeta()` (`BBSMETA: SecLevel(100) Sysop(...)`), sent once right before the info fields — matching the reference client's own connect sequence exactly. ANetBBS never sent this at all; added alongside the field-order fix since both were live-suspected contributors and couldn't be isolated without another full live round-trip.
- 4 new tests (738 passed total), plus an existing field-order test rewritten to check the raw wire bytes positionally instead of through the higher-level parser, since this packet type's field order doesn't match the parser's normal semantic labels.

---

# ANetBBS v1.0b2.109 — MRC bridge: real root cause found — pipe color codes broke identify detection (July 2026)

- FIX (significant): the v1.0b2.108 diagnostic logging paid off immediately — live capture on the production server showed the hub's actual identify-success reply is `"...Successfully identified, welcome back |10StingRay|07"`. `_extract_identified_handle` (pre-dating this session, never previously verified against a real hub reply) never stripped the `|10`/`|07` pipe-color codes wrapped around the handle, so it returned the literal string `"|10StingRay|07"` instead of `"StingRay"` — which then never matched any real session's plain-text handle. This silently broke *both* the pre-existing strict-mode auto-join-after-identify and the newer v1.0b2.106 default-mode self-heal, on every install, the entire time — it just never surfaced before because every prior test (including the ones written for the v1.0b2.106 self-heal fix) used a fabricated clean message with no pipe codes at all. Added `_strip_pipe_codes()` and applied it in `_extract_identified_handle` before searching.
- FIX: applied the same stripping defensively to `send_info_fields()` (v1.0b2.107) — a sysop's `bbs_telnet`/`bbs_ssh`/`bbs_website`/`bbs_sysop` config values copy-pasted from a colorized source (e.g. `"|15bbs.example.com:2233"`, a pipe-colored sysop name) now get sent as clean plain text, matching what the reference client actually sends (no color codes at all, sourced straight from `MRCBBS.DAT`) and avoiding the same class of hub-side parsing risk just confirmed for the identify reply. (Note: this specific behavior was reverted in v1.0b2.112 — see above — once it turned out pipe colors in these fields are actually expected/supported, contrary to the assumption made here.)
- 7 new tests (734 passed total), including two that reproduce the exact real captured wire text end-to-end and confirm the self-heal now actually fires against it.

---

# ANetBBS v1.0b2.108 — MRC bridge: diagnostic logging for identify/join-rejection wire text (July 2026)

- DIAGNOSTIC: live testing on the production server (`bbs.a-net.fyi`) after v1.0b2.106/.107 found the identify self-heal still not taking effect there, despite the deployed code, config, and service all being confirmed correct with zero errors in the logs — pointing at the detection logic itself rather than the deployment. The `"successfully identified"` / `"welcome back "` substring match this relies on (`_on_upstream_packet`, `_extract_identified_handle`) predates this session with no confirmed verification against a real hub reply. Added a temporary log line (`logger.info`) that captures the raw text of any incoming SERVER message shaped like an identify or join-rejection notice, so the actual wording can be confirmed against a live capture instead of assumed. No behavior change; safe to remove once the real wording is confirmed and, if needed, the detection updated to match.

---

# ANetBBS v1.0b2.107 — MRC bridge: broadcast our own BBS info (description/telnet/ssh/website/sysop) (July 2026)

- FIX: reported by the sysop — "the BBS info that is not shown with ANetBBS but is with the mystic mrc client... this is where you look at the bbs info and see their web, telnet, ssh, description." Verified against the real reference C client (`anetmrc_v1.3.9/src/helper_protocol.c`'s `mrc_send_info_fields`): every MRC client is expected to broadcast its own `INFODSC`/`INFOTEL`/`INFOSSH`/`INFOWEB`/`INFOSYS` fields once per connect (sourced there from `MRCBBS.DAT`), which the hub aggregates into a per-BBS directory other callers browse via `/bbses` (to find a BBS's list number) then `/info <n>` (to view that entry). ANetBBS's bridge never sent any of these fields at all, so ANetBBS's own entry was always blank to anyone looking it up from another client — including ones (like the Mystic Python client) that actually surface this. Added `send_info_fields()`, called once right after the existing capabilities announcement on every connect; five new optional config keys (`bbs_description`, `bbs_telnet`, `bbs_ssh`, `bbs_website`, `bbs_sysop`) in the bridge's `config.json`, each field only sent if actually configured (matching the reference client's own per-field guards — an unset field is omitted, not sent blank). The *reverse* direction — ANetBBS's own callers looking up *other* BBSes via `/bbses` + `/info <n>` — already worked with no changes needed: both are, and always were, generic passthrough commands on both clients (confirmed by inspecting the existing terminal `/info`/`/bbses` handling and web's catch-all slash-command forwarding).
- 5 new tests, including one that parses the actual generated packet and checks its wire fields against the reference client's own field order.

---

# ANetBBS v1.0b2.106 — MRC bridge: registered handles now self-heal after /identify (July 2026)

- FIX (significant): found live on the Pi immediately after deploying v1.0b2.105 (a registered handle, "StingRay") — the v1.0b2.103 default-join fix optimistically marks a session `in_room=True` the moment it connects, but for a handle that's actually *registered and not yet identified*, the real hub silently rejects that join ("Cannot join ROOM, please IDENTIFY to use this handle"). The bridge kept believing it was joined and went on forwarding chat sends anyway, which the hub then bounced back with "No route to a room from your user, /join a room first." — the caller looked joined locally but couldn't actually chat, with no way out except realizing they had to `/identify` *and separately* `/join` again by hand. Fixed: a successful `/identify` now always re-sends the room join (previously this only happened in the opt-in strict `identify_required_mode=True` path), so registered handles self-heal automatically the moment they identify — no follow-up `/join` needed. Unregistered/casual handles are unaffected (their optimistic join already succeeds at the hub and was never broken).
- 2 new tests, including one that captures the exact reported sequence (join → hub-side rejection scenario → identify → confirms the join packets actually get re-sent, not just a local flag flip).

---

# ANetBBS v1.0b2.105 — MRC Phase G: full regression + cross-client parity audit (July 2026)

Closes out the MRC feature-parity rework (Phases A-G, v1.0b2.94-105). Full automated regression suite re-run clean (720 passed, 2 skipped, 0 failures) plus a systematic code-level audit comparing every feature added in Phases A-F against both clients, specifically looking for the kind of one-sided fix that's already happened twice this rework (a bug found and fixed on one client without checking whether the same bug existed on the other). Found and fixed two real gaps, both in `anetbbs/templates/mrc/index.html`, both additive/non-breaking:

- FIX: the web client never got the STATS: noise fix from v1.0b2.102 (terminal-only at the time) — raw `STATS:175 15 39 2 170 34.0`-shaped lines were still showing up inline on web with nothing to silence them. Added the same suppression terminal already has (`SUPPRESS_STATS_LINES`, defaulting on, matching the existing `SUPPRESS_USERLIST_LINES`/`SUPPRESS_ROOMTOPIC_LINES` pattern).
- FIX: the structured `userlist` WS event added in Phase A (`mrc/bridge/main.py`'s `_send_userlist_control`) was specifically built so both clients could stop depending on regex-parsing free-text chat lines for the nick-list sidebar — the terminal client migrated to it in Phase B, but web was never updated to listen for it at all, silently left on the older, more fragile `tryParseUserListFromServerMessage` regex path the whole time. Web now also consumes the structured event directly (`case 'userlist':`) as a more robust source layered on top of the existing regex fallback, which stays in place unchanged.
- No JS test framework exists in this repo (consistent with every prior web-side change in this rework) — both script blocks syntax-checked clean with `node --check`.

---

# ANetBBS v1.0b2.104 — MRC: restored the /identify "MRC Trust" notice (non-blocking) on both clients (July 2026)

- FIX: follow-up to v1.0b2.103. Correction from the sysop: the reference client doesn't hide `/identify` entirely — it shows a permanent, non-blocking "Use /identify password for MRC Trust" notice on every connect (confirmed in `helper_protocol.c`), and a registered handle's trust status genuinely does lapse after a stretch of weeks on the real network, so periodically re-running `/identify` still matters even though chat is never gated on it. v1.0b2.103's fix correctly removed the *blocking*, but its plain "Joined #room as handle." message dropped the FYI along with it. Restored: the bridge's immediate-join message now reads "Joined #room as handle. Use /identify \<pass\> for MRC Trust." (surfaced automatically on web via its existing system-message display), and the terminal client's own local connect greeting — which never read the bridge's message field at all — gained a matching tip line, so both clients now show the same non-blocking reminder.
- 1 test updated to match the restored notice (720 passed total).

---

# ANetBBS v1.0b2.103 — MRC bridge: fixed a hard identify-wall blocking all chat by default (July 2026)

- FIX (significant): reported live as "I still have to identify every single time" / "blocked from chatting entirely until identified" — every MRC caller, on every install, was silently blocked from sending or (in some paths) seeing chat at all until they ran `/identify <pass>` against a *registered* account on the upstream hub, every single connect. Root-caused against the real reference C client (`anetmrc_v1.3.9/src/helper_protocol.c`): it joins the room unconditionally right after the handshake and never waits on identify — `/identify` is purely optional "MRC Trust" for a registered handle, never a requirement to participate, and casual/unregistered handles chat freely on the real network. The bridge's own `identify_required_mode` gate defaulted to `True` with no documented way to discover or disable it (not even in `config.example.json`), and even flipping it off alone didn't help — a second, equally undocumented flag (`post_identify_auto_join`, also defaulting off) additionally had to be set before a caller was ever actually placed in the room. Default changed to match the reference client's real behavior: `identify_required_mode` now defaults to `False`, and joining a room completes immediately, no `/identify` needed, for every existing install without any config change. An admin who deliberately wants the old strict, identify-required behavior can still opt in via `config.json`; that path is unchanged and fully covered by tests.
- 9 new tests, including one that builds a real `BridgeApp` from a config file with no `identify_required_mode` key (matching every existing install's actual config.json) and asserts the new default takes effect.

---

# ANetBBS v1.0b2.102 — MRC Phase F follow-up #3: BEL character threw off border alignment (July 2026)

- FIX: found live on the Pi — one specific row's `│` border still landed one column early even after the sidebar/status-bar/ticker border fixes (v1.0b2.99-101). Root cause: mention/DM alerts prepend a bare BEL (`\x07`, rings the terminal bell) ahead of the highlighted message text — zero-width on a real terminal, but `_visible_len` counted it as one visible column since `_ANSI_SEQ_RE` only matched ESC-prefixed sequences, not a bare BEL. Every message that happened to mention the caller's own handle (or arrive as a DM) was one column short on padding, throwing its row's border out of alignment by exactly one column. `_ANSI_SEQ_RE` now also matches BEL.
- 3 new tests (711 passed total), including an end-to-end reproduction (a mention message's row lands at the same border column as every other row).

---

# ANetBBS v1.0b2.101 — MRC Phase F follow-up #2: ticker border fix + STATS: inline noise silenced (July 2026)

- FIX: found live on the Pi as "border still has one spot" even after v1.0b2.100's status-bar fix — the scrolling ticker row (between the status bar and chat area) had no border/sidebar treatment at all, scrolling its text against the full terminal width with nothing lining up against the `│` every other split-screen row draws. Fixed the same way as the status bar: measured/padded against `chat_width` and followed by a matching border + blank nick-column-width gap when the sidebar is enabled.
- FIX: found live on the Pi — raw `STATS:175 15 39 2 170 34.0`-shaped lines were popping up inline mid-chat. This was deliberate, documented, unchanged-on-purpose behavior from when the ticker was first built (STATS: predated the ticker and had nowhere else to go), but now that the ticker pool already captures it, the inline copy is just noise. Silenced the same way `BANNER:` already is.
- 3 new tests (708 passed total).

---

# ANetBBS v1.0b2.100 — MRC Phase F follow-up: named /set tz zones + status-bar border fix (July 2026)

- FEATURE: `/set tz` now accepts common named zone abbreviations (`EST`, `EDT`, `CST`, `CDT`, `MST`, `MDT`, `PST`, `PDT`, `AKST`, `AKDT`, `HST`, `AST`, `ADT`, `UTC`, `GMT`, `BST`, `CET`, `CEST`, `EET`, `EEST`, `IST`, `JST`, `AWST`, `ACST`, `ACDT`, `AEST`, `AEDT`, `NZST`, `NZDT`) in addition to raw `±H[:MM]` offsets — reported live on the Pi as the raw-offset-only form being confusing. Fixed-offset aliases, not DST-aware, matching the feature's existing plain-UTC-offset design (a caller currently observing daylight time picks the `*DT` name, not `*ST`).
- FIX: found live on the Pi as "the border still messed up in one spot" after the v1.0b2.99 sidebar fix — the status bar (row 1) was drawn full terminal-width with no `│` border of its own whenever the nick-list sidebar was enabled, so its right-aligned badges (mention count, latency, clock) landed past the column where every chat row's border sits, with nothing to match it. Now measured/padded against `chat_width` like every other split-screen row and followed by the same border + a blank nick-column-width gap, so the status bar's right edge lines up with the sidebar below it.
- 5 new tests (705 passed total).

---

# ANetBBS v1.0b2.99 — MRC feature-parity rework, Phase F: quality-of-life additions (July 2026)

- FEATURE: sixth phase of the MRC feature-parity rework (see v1.0b2.94-98). Terminal MRC gains `/r <text>` (reply to your last received DM), input-history recall on Ctrl+Up/Down (plain Up/Down stay bound to chat-scroll, the terminal's existing convention — Ctrl+arrow required actually preserving the CSI modifier byte in `_read_escape_seq`, which previously truncated every modified cursor sequence the same way legacy PgUp/PgDn does), `/dlchatlog` (download the session's scrollback as a text file via whatever ZMODEM-family protocol is available, same tempfile-then-send_file pattern already used by the ebook reader's download command), and a light `/set palette <name>` chrome color picker (default/green/amber/cyan/mono) covering the status-bar room tag, nick-list sidebar, and ticker line — cosmetic-only scope mirroring the web client's 5 CSS themes, not a full re-theme of every message color. Web MRC gets a matching client-side "Download chat log" button in the Chat Prefs panel. Also reviewed whether either client needed a dedicated welcome screen (a locked-in Phase F item) and found both already surface the bridge's real `welcome` event on connect — terminal via its generic unknown-event fallback, web via an existing explicit handler — so nothing further was added there.
- FIX: found live on the Pi — the MRC clock widget and message timestamps used the *server's* system clock, not the caller's own timezone, silently correct only for a caller who happens to share the server's TZ (server on UTC, sysop several hours off in this case). Added `/set tz <offset>` (e.g. `-5`, `+5:30`, `utc`), persisted per-handle via the same `set_prefs` mechanism as the other Phase E settings; terminal timestamps now compute from UTC + this offset instead of the server's local clock. Web was already correct (browser-local time), no change needed there.
- FIX: found live on the Pi — the nick-list sidebar's `│` border column could drift out of alignment when a buffered chat line had been word-wrapped against a wider chat width before the sidebar narrowed it (e.g. sidebar enabled mid-session, or a stale line surviving a resize). The redraw only ever padded short lines to the border column, never truncated long ones, so an over-width line pushed the border past its column and could auto-wrap the physical terminal row, corrupting the next row's redraw too. Now defensively truncated to the current chat width before the border is drawn.
- 53 new tests (700 passed total, Python side; web JS syntax-validated).

---

# ANetBBS v1.0b2.98 — MRC feature-parity rework, Phase E: /set command + remaining settings (July 2026)

- FEATURE: fifth phase of the MRC feature-parity rework (see v1.0b2.94-97). Terminal MRC gets a real `/set <field> <value>` command (plus `/set list` and `/set help`) covering nick prefix/suffix/color, custom enter/leave/quit chat messages, and the scrolling ticker toggle — the terminal's `/set` for prefix/suffix/color goes through `set_style`, same wire message the web client's style panel already used, and now `/set` is the terminal's first way to change those at all (previously arrow-key cycling was the only style control terminal had). Web gets equivalent fields added to the "Chat Prefs" panel (ticker toggle, enter/leave/quit messages) for the same shared, server-persisted settings. Toggling the ticker via `/set ticker` or the web checkbox now actually re-lays-out the terminal screen live instead of only taking effect on next reconnect.
- FIX: found while building the above — arrow-key outgoing-color cycling (`_cycle_color`) sent only `{type: set_style, typing_color: ...}` to the bridge, but `_handle_set_style` (`mrc/bridge/main.py`) hard-defaults `prefix`/`suffix` to empty string when the field is simply absent from the request (unlike every other style field, which correctly falls back to the existing session value). This silently wiped any prefix/suffix decoration a user had set via the web style panel every single time they cycled their outgoing color with the arrow keys — a real, previously-shipped bug, not something introduced by this phase. Fixed by always sending the full last-known style with just the changed field(s) overridden, via a new `_style_payload()` helper now shared by both `/set` and arrow-key cycling.
- 20 new tests (Python side; web JS syntax-validated).

---

# ANetBBS v1.0b2.97 — MRC feature-parity rework, Phase D: twit list + broadcast shield (July 2026)

- FEATURE: fourth phase of the MRC feature-parity rework (see v1.0b2.94-96). Both terminal and web MRC clients gain a twit/ignore list and a broadcast shield, persisted server-side per handle via a new `set_prefs`/`prefs_updated` round-trip and shared between both clients automatically. Filtering happens client-side by design (the bridge fans one event stream to potentially many local clients, so per-viewer muting can't be centrally enforced) — messages from a twitted sender are dropped and counted, not silently invisible. Terminal gets `/twit add|del|list|clear` and `/shield on|off`; web gets a new "Chat Prefs" panel alongside the existing style settings. `/broadcast` (terminal) and the equivalent web slash command are now refused locally when the shield is on. 19 new tests (Python side — bridge protocol + terminal filtering logic; web JS syntax-validated, UI verified by hand).

---

# ANetBBS v1.0b2.96 — MRC feature-parity rework, Phase C: terminal scrolling ticker (July 2026)

- FEATURE: third phase of the MRC feature-parity rework (see v1.0b2.94/95). Terminal MRC gets a scrolling ticker row between the status bar and chat area, rotating through static tips and — for the first time — real hub-pushed `BANNER:` text, which was previously discarded entirely with no ticker to feed. `STATS:` text (already shown inline, unchanged) now also feeds the ticker rather than going unused after being displayed once. Short items dwell briefly before rotating; items too long for the terminal width scroll a few characters per tick instead. 12 new tests.

---

# ANetBBS v1.0b2.95 — MRC feature-parity rework, Phase B: terminal nick-list sidebar + clock (July 2026)

- FEATURE: second phase of the MRC feature-parity rework (see v1.0b2.94). Terminal MRC (`anetbbs/features/mrc_chat.py`) gains a live nick-list sidebar on wide-enough terminals (132+ columns) — the DECSTBM split-screen primitive only constrains vertical scrolling, so the sidebar rides along on the same row writes as the chat text rather than being a separate scroll region (no left/right sidebar rendering precedent existed anywhere in ANetBBS's terminal UI before this). Also adds a clock widget to the status bar, refreshed every 30s so it doesn't go stale during a quiet room. Both purely additive UI — no settings/persistence yet (that's Phase E). 10 new tests.

---

# ANetBBS v1.0b2.94 — MRC feature-parity rework, Phase A: bridge protocol + prefs (July 2026)

- FEATURE: first phase of a multi-phase MRC (Multi Relay Chat) rework bringing ANetBBS's terminal and web clients toward full feature parity with the wider MRC ecosystem. This phase is bridge-only (`mrc/bridge/main.py`, `mrc/bridge/db.py`) and lays the foundation the rest builds on: a new structured `userlist` WebSocket event (sent alongside the existing raw-text relay, real independently-verified wire format); a periodic `STATS` request feeding future ticker/banner work as opaque display text (deliberately not parsed into fabricated structured fields — an earlier draft assumed a format based on the Mystic BBS multiplexer's own internal file-protocol, corrected after checking the actual reference C client that talks to a real hub showed no evidence of structured STATS fields); a new `set_prefs`/`prefs_updated` request/response pair persisting twit/ignore list, broadcast shield, ticker toggle, and custom enter/leave/quit message templates per MRC handle; per-user enter/leave templates now actually applied instead of one global template for everyone; and explicit `/quit <message>` now threaded through end-to-end instead of being silently discarded. 19 new tests.

---

# ANetBBS v1.0b2.93 — Hub identity seeded active by default (July 2026)

- FIX: the default `HubIdentity` row (see v1.0b2.92's multi-hub-identity foundation) was seeded with `is_active=True`, so simply turning on `REGISTRY_MODE_ENABLED` made ANotherNetwork look like a live, fully-configured hub — before the sysop had touched anything. Doesn't match the existing convention: the seeded ANotherNetwork `EchomailNetwork` rows (both BinkP and QWK) have always started `is_active=False`, requiring the sysop to fill in real node address/packet-id/password before flipping them on. Fixed the seed to match (`is_active=False`), and changed the admin "Add Hub Identity" form's default to unchecked for the same reason — a freshly created identity shouldn't look live until deliberately activated.

---

# ANetBBS v1.0b2.92 — Terminal MRC never actually read its configured bridge port (July 2026)

- FIX: a sysop reported MRC chat working fine in the web UI but never connecting over the terminal (SSH/telnet) client, with only a brief, unreadable error flash on screen. Root cause: `MRCChat.show_menu()` (`anetbbs/features/mrc_chat.py`) read `current_app.config` with no Flask app context active — unlike `_chat_flags()` a few lines above it in `chat.py`, which correctly scopes its own DB read in one. Every config lookup therefore raised `RuntimeError: Working outside of application context`, silently swallowed by a bare `except: pass`, so terminal MRC always fell back to the hardcoded `DEFAULT_BRIDGE_URL` (port 8080) — regardless of the real bridge port (`WEB_PORT+1`, 5001 by default). This wasn't specific to one install; it silently broke terminal MRC on every install, unnoticed until now. Fixed by wrapping the lookup in `with _app().app_context():`, the same pattern already used elsewhere in the file. Also added `logger.warning(...)` to the connect-failure path, which previously only ever wrote to the failing user's own terminal screen and vanished on redraw — the exact reason the error was unreadable and left no trace to investigate afterward. 1 new regression test.

---

# ANetBBS v1.0b2.91 — BinkP: unhandled send-side disconnect during EOB/GOT handshake (July 2026)

- FIX: a sysop reported a BinkP poll to one particular hub failing and repeating on every scheduled poll, while two other hubs on the same install worked fine. Root cause: `_receive_messages()` already treats the hub closing the connection during our *receive* calls as a clean, expected end of session (added in the v1.0b2.57-60 two-round-EOB work) — but the two acknowledgement sends right next to it, the second `M_EOB` and the per-file `M_GOT`, had no equivalent guard. If the hub closes its side of the socket in the narrow window before either of those sends goes out, `sendall()` raises an uncaught `OSError`, which unwinds out of `poll()` and gets logged as a genuine poll failure even though the file transfer itself completed successfully. Wrapped both sends in the same log-and-break pattern already used for the receive side. Separately: polling that one hub on a 1-minute interval is a very plausible reason only it triggers this — if its mailer has any connection-rate throttling, frequent polling could be causing it to abort sessions mid-handshake, which is exactly the trigger condition here. Slower polling (30-60 min, standard FTN practice) should reduce how often this is hit regardless of this fix.

---

# ANetBBS v1.0b2.90 — Configurable Last Callers row count + screen clear (July 2026)

- FEATURE: the terminal Last Callers screen always fetched and paginated 200 rows, which made for a long scroll on a busy install. Added a sysop-configurable row count (`/admin/lastcallers/` — 5/10/15/20/25/30/50/100, default 20) that controls how many entries the terminal screen actually fetches; the admin audit list and the web one-liners page's fixed "Last 10" preview are unaffected. Also fixed the terminal screen not clearing before drawing — it now clears first like every other full-screen terminal view in the app. 3 new tests.

---

# ANetBBS v1.0b2.89 — Move pre-update backups off /tmp onto persistent disk (July 2026)

- FIX: a real Pi install had 108GB free on its actual disk, but `update.sh`'s new (v1.0b2.87) disk-space check still refused to proceed with "only 442MB free" — because the pre-update backup was written to `/tmp`, and on this system `/tmp` is a RAM-backed tmpfs, completely separate from and much smaller than the real disk. Moved backups from `/tmp/anetbbs-backup-*` to `INSTALL_DIR/data/backups/anetbbs-backup-*` — real, persistent disk, already excluded from the update's own file-sync step, and immune to distros that clear `/tmp` on reboot. Updated the admin Backups page (`anetbbs/web/backups_admin.py`) to browse the new location, and carefully re-anchored the privileged restore helper's (`deploy/run_restore.sh`) security path-allowlist to match — verified the path-traversal rejection still works exactly as before, just pointed at the new trusted location instead of the old one. The disk-space pre-flight check itself no longer needs to check `/tmp` separately, since the backup and the update both now live on the same filesystem the check already verifies.

- FIX: found while investigating a real disk-full incident — every launch of a Synchronet-JS door game (the Node.js compat fallback path, used when no real Synchronet jsexec binary is installed) created two temp files that were never cleaned up, ever. `_cleanup_session()`, the one place a door session's resources get released regardless of how the session ended, had no knowledge these files existed — they were local variables inside a completely different function, never tracked anywhere. A live install had accumulated well over a hundred orphaned files in `/tmp` from this alone, directly contributing to the disk-space exhaustion that corrupted an install during an update. A second, separate call site (a "validate the command before launching" dry run, used to show a clear error message from a terminal session) doubled the leak rate further — it built the command purely to check for exceptions and then discarded it entirely, abandoning its temp files immediately. Fixed by having the command-builder accept an optional list to record these paths in, and cleaning them up both when a door session closes (mirroring how the existing door_dosemu pts-symlink cleanup already works) and immediately after the validation dry run finishes. 5 new tests, run against the real Node.js code path (not mocked).

- FIX/FEATURE: a real sysop's disk hit zero during an update and corrupted the install past recovery — a dozen old pre-update backups had silently accumulated in `/tmp` over time (nothing ever pruned them), and there was no check for available disk space before the update started writing. Three real gaps fixed: (1) `update.sh` now requires at least 500MB free on both the backup filesystem and the install filesystem before proceeding at all, refusing to start rather than risking a mid-update disk-full corruption. (2) Pre-update backups are now automatically pruned to the 3 most recent after each successful backup — this was previously a deliberate "never auto-delete" design choice that, in practice, is exactly how unbounded accumulation silently ate a real sysop's disk space. (3) Found and fixed a real bug while auditing the backup steps: the `.env` backup printed "✅ Backed up .env" unconditionally, even if the `cp` itself had failed — meaning the one thing standing between a bad update and data loss could silently not exist while claiming to. All backup steps (`.env`, both SQLite databases) now check their actual result and abort the entire update if a backup genuinely can't be created, rather than proceeding with no real safety net.

- FIX: on a fresh Fedora install, nginx couldn't reach *any* backend it was supposed to proxy to — not just the MRC bridge, the main web app too. Confirmed directly from nginx's own error log: `connect() to 127.0.0.1:5000 failed (13: Permission denied)`. Errno 13 on an outbound connect() from nginx is the signature of SELinux blocking it — Fedora/RHEL/CentOS ship SELinux enforcing by default, and nginx isn't allowed to proxy to arbitrary backend ports unless the `httpd_can_network_connect` boolean is explicitly enabled. `install.sh` never touched SELinux at all. This is why accessing the web UI required going straight to the app's own port instead of through nginx, and why web MRC looked like it was randomly connecting and dropping (SELinux denials can be inconsistent under load, briefly succeeding before failing again). Fixed by having `install.sh` detect SELinux enforcing mode and enable the boolean automatically (persistently, via `setsebool -P`) right after generating the nginx config. Systems without SELinux (Debian/Ubuntu) are completely unaffected — the check itself doesn't exist there, so nothing runs. Extended `update.sh` with the matching self-heal for installs that predate this fix. Found and fixed the same day as, and independently of, a separate MRC-specific nginx port-mismatch bug (v1.0b2.85) — both were real, but this one is more fundamental on any SELinux-enforcing system since it blocks nginx's reverse proxy globally, not just MRC.

- FIX: web MRC chat consistently failed to connect on fresh installs, reproduced independently on multiple installs including Fedora. Root cause: nginx's generated `/mrcws` location hardcoded `proxy_pass http://127.0.0.1:8080/ws;`, but the MRC bridge's actual listen port is derived as `WEB_PORT+1` (5001 on the default production `WEB_PORT=5000`) — a mismatch introduced by an earlier, unrelated fix (v1.0b2.35) that updated the bridge's own port derivation but never touched the nginx proxy target to match. The bridge itself ran with zero errors the whole time, since it was never told about the mismatch — nginx was just silently proxying to a port nothing listens on. Fixed in `install.sh` (now uses the same derived port everywhere), the static `deploy/anetbbs-nginx.conf.template` reference file, and `.env.example`. Also extended `update.sh`'s existing nginx auto-repair logic to detect and fix this exact mismatch on already-affected installs, so existing sysops get this fixed automatically on next update, not just future fresh installs. Added clear install-time warnings for `test`/`behind` modes, where web MRC has no working path at all without nginx (confirmed: `/mrcws` is handled exclusively by nginx's proxy, Flask has no route for it) — terminal MRC over SSH/telnet is unaffected either way, since it connects directly to the bridge server-side using the correct port from `.env`, bypassing nginx entirely.

- FIX: a real downstream FidoNet peer reported "CRAM-MD5 is not supported by remote" when polling IN to an ANetBBS install. Root cause: the inbound BinkP listener (`binkp_server.py`) never implemented CRAM-MD5 as the answering side at all — it never sent the `OPT CRAM-MD5-<challenge>` line an answering side is supposed to offer, and had no code path to verify a CRAM-MD5 response even if one arrived. Only the outbound client (`binkp.py`) had CRAM-MD5, and only for the calling role — the two sides had simply never been given matching implementations. Fixed by generating a random challenge in the listener's handshake preamble, advertising it, and verifying incoming CRAM-MD5 responses against it (via HMAC-MD5 per FTS-1027) — plain-text passwords are still accepted for peers that don't support CRAM-MD5, so nothing existing breaks. 13 new tests.
- FIX: found while investigating a report that echomail from tqwnet and sp00knet was arriving (confirmed — real messages landing in message areas) but showing 0 in the Poll Log every time. Root cause: `EchomailPollLog` was only ever written by the outbound poller (`poller.py`, when ANetBBS dials out) — a session where a remote hub calls IN and delivers mail (exactly how tqwnet/sp00knet actually operate: they push to us) never wrote a poll log row at all. The 0s weren't wrong data, they were the outbound poller honestly reporting that a *later* outbound dial to the same network found nothing left to pull, since the hub had already delivered it via the inbound path moments earlier. This was a known, documented gap (noted as a follow-up back in v1.0b2.47's poll-log transcript work) rather than a new regression. Now the inbound listener also writes a poll log entry after each authenticated session, with real send/receive counts.

- FEATURE: fixed three real gaps found while processing the first live "apply to join this network" application. (1) The admin review screen only allowed Approve/Deny off a truncated list row — added a full application detail view (`/admin/echomail/hub/join/requests/<id>`) showing every submitted field, with Approve/Deny available directly from it. (2) BinkP node addresses were whatever the applicant typed in, used verbatim, with no sequential numbering — added optional hub-wide BinkP Zone/Net config; when set, approval auto-assigns the next sequential `{zone}:{net}/N` address (node 1 reserved for the hub), reusing the codebase's existing FTN address parser/formatter (`anetbbs/echomail/routing.py`). QWK packet IDs are explicitly untouched — stay fully sysop/applicant-chosen, per design. Leaving zone/net unset preserves the old verbatim-address behavior. (3) The generated node password was already being correctly generated and persisted (confirmed live via direct DB query — not a data-loss bug) but was never displayed anywhere in the admin UI; now shown on both node detail pages and inline in the review list. Also added a one-click "Archive Application to Disk" action that snapshots the full application to a JSON file under `data/network_join_archive/` — a directory with no web route pointing at it, confirmed unreachable from outside. 8 new tests.

- CLEANUP: two prior passes (v1.0b2.12, v1.0b2.23) removed the old full-rectangle `╔═╗║╚═╝` box-drawing border from most stock screens in favor of the borderless block-shaded bar style, but never touched the plain 80-column `.ans` files for `newuser`, `main`, `chat`, and `game_center` — those four still had the old box border. Rebuilt them to match the already-correct style (`welcome.ans`, `goodbye.ans`, `newuser132.ans`, and the 132-col menu screens). `newuser.ans` intentionally has no `@PAUSE@` (matches `newuser132.ans`'s existing behavior — registration continues immediately into security-question prompts, no separate pause). All 12 stock `.ans` screens confirmed to have zero box-drawing bytes.
- FIX: `menus/main.ans` (80-col) and `main.asc` were also missing 6 hotkeys that had been added to the live menu system over past releases but never back-ported into these static art files — `[J] Send InterBBS IM`, `[K] Ebook Reader`, `[A] Page Sysop`, `[D] Dial Out`, `[W] Change Password` — and `main132.ans` was missing `[K]`. All three now show the complete, current 22-item hotkey list (including the new `[L] Ask Anet`), laid out in properly aligned columns (2 columns for 80-col, 5 for 132-col) generated programmatically rather than hand-typed, to guarantee consistent spacing.

- FIX: found live on the Pi3 terminal (web UI was unaffected) — search result snippets showed garbled "?" characters and raw markdown syntax instead of clean text, e.g. `# TIC Processor` and `***«BinkP»` instead of `TIC Processor` and `**BinkP**`. Two compounding causes: (1) the snippet highlight/ellipsis markers were Unicode guillemets (`«`/`»`) and an ellipsis (`…`), which render fine in a browser (UTF-8) but as mojibake on a real terminal session (CP437, not UTF-8); (2) `snippet()` extracts from the raw markdown source stored in the FTS5 index (kept as markdown deliberately, for accurate word matching), so literal `#` headings and `**bold**` markup leaked straight into the displayed snippet unprocessed. Fixed by switching to ASCII-only markers (`>>`/`<<`/`...`) and running each returned snippet through the same markdown-to-plain-text stripper already used for the terminal's full-page reader. Also improved the terminal results list to truncate snippets on a word boundary instead of an arbitrary character cut, and made its columns responsive to terminal width (was a fixed 40/30 split) so results also use the extra room on a 132-col widescreen session instead of looking sparse.

- FIX: found live on the Pi3 test server immediately after v1.0b2.79's first deploy — every search returned "nothing matched", even for exact words like "netmail" that are definitely in the wiki. Root cause: `wiki_pages_fts` is an *external content* FTS5 table (it stores no copy of title/body, just the search index, reading the real text from `wiki_pages`) — and `count(*)` against an external-content FTS5 table is a passthrough to the content table's own row count, regardless of whether the actual search index has ever been populated. `ensure_fts_index()`'s "does this need a one-time backfill rebuild" check compared that count against `wiki_pages`'s count, which is always equal by construction — so on any install where `wiki_pages` already had rows before this feature ever shipped (i.e. every real ANetBBS install, Pi3 and live both), the real rebuild command never ran, permanently. Direct non-MATCH reads of the table (`SELECT body FROM wiki_pages_fts WHERE rowid=...`) looked completely normal throughout, which is what made this non-obvious — only `MATCH` queries were silently broken. Fixed by checking `sqlite_master` for whether the table existed *before* this call instead of comparing row counts; that's only ever true on the one process, one time, that actually creates it. Also wrapped the whole DDL+rebuild sequence in a real `BEGIN IMMEDIATE` write lock via the raw sqlite3 driver (SQLAlchemy's `engine.begin()` is a deferred transaction that doesn't lock until the first write) so the several separate processes that independently call this at their own startup (gunicorn web workers, the telnet/SSH terminal service, binkp, etc.) can't race on the one-time rebuild. Verified with a 6-process concurrency stress test in addition to the direct bug repro. 1 new regression test.

---

# ANetBBS v1.0b2.79 — "Ask Anet" help guru door, terminal + web (July 2026)

- FEATURE: a retro-styled in-BBS help assistant, callback to the old "Lisa AI" chat door. Ask a plain-language question ("where can I view netmail", "how do I chat") and it searches the wiki's 45 help pages for a match, in both the terminal (new `L` hotkey, "Ask Anet") and the web UI (`/guru/`). Fully self-contained — no external or local LLM, no new dependencies: built on SQLite FTS5 (bundled with Python) with a small hand-maintained alias table for common phrasings, ranked with bm25. A persistent, always-visible disclosure explains in plain language that this is a search tool with a friendly wrapper, not a live AI chatbot — shown before the first prompt in the terminal door and permanently at the top of the web page. New `anetbbs/guru/` package (fts.py, aliases.py, search.py, render_plain.py, personality.py). FTS5 index is created and kept in sync via SQL triggers scoped to title/body changes only (not view_count) inside the existing idempotent schema-migration path, so it self-heals on both fresh installs and upgrades of already-populated servers. 15 new tests.

---

# ANetBBS v1.0b2.78 — Terminal uploads into disk-backed file areas never showed on the web (July 2026)

- FIX: a terminal ZMODEM upload into a file area with a configured storage_path saved into a generic fallback directory instead of the area's own disk path — the web file-area page (and the terminal's own primary listing) only ever scans the area's real storage_path, so the uploaded file was invisible everywhere except the terminal's DB-fallback listing. Now saves directly under the area's storage_path with no DB row, matching the web upload route exactly. 2 new tests.

---

# ANetBBS v1.0b2.77 — Fix ZMODEM upload handshake failure with SyncTERM (July 2026)

- FIX: every SSH ZMODEM upload from SyncTERM failed with a broken handshake (`UNEXPECTED ZRPOS received instead of ZRINIT`). Same root cause class as an already-fixed send-side bug in this file: `rz --escape` triggers the same SyncTERM negotiation failure that `sz --escape` did. Removed `--escape` from ZMODEM's receive flags to match. 3 new tests.

---

# ANetBBS v1.0b2.76 — Diagnostics missed the inbound-listener path entirely (July 2026)

- DIAGNOSTIC: found the real reason the manifest stayed empty — `binkp_server.py` (the separate `anetbbs-binkp` inbound-listener service, for when a hub calls *us*) has its own independent packet-receive chain that never touched any of the diagnostics added so far. The actual parsing fixes already covered it (shared `_parse_ftn_packet()`), but the capture didn't. Now instrumented identically, prefixed `SRV:` to distinguish it from the outbound-poll path's captures.

---

# ANetBBS v1.0b2.75 — Manifest diagnostic: log-based capture confirmed unreliable in production (July 2026)

- DIAGNOSTIC: a different article still corrupts on rescan, and the existing packet-dump diagnostic isn't catching the actual bulk delivery. Found that production's `LOG_LEVEL` silently swallows every log line from this module — confirmed zero `"BinkP"` matches in the entire gunicorn error log despite live traffic. Added a plain-file-I/O manifest (bypasses `logging` entirely) recording every file handed to the packet dispatcher, so nothing can hide from it. No behavior change unless `BINKP_DEBUG_DUMP_DIR` is set.

---

# ANetBBS v1.0b2.74 — Chain-validate packet boundaries; fix a second desync vector found while testing (July 2026)

- FIX: v1.0b2.73's marker+date check could still be fooled by a real message body containing quoted old FTN content that happened to form a fully well-formed date+time+null field. Now validates a short chain of subsequent messages (rejecting if any header field along the way contains a raw control character) before trusting a boundary.
- FIX: also closed a related gap found while building the above — a rejected candidate's own routing-header bytes (attr=0/cost=0, very common) could produce a coincidental zero-byte pair mistaken for the packet's real end-of-data marker, silently dropping everything after it. Now requires that marker to actually be at the buffer's tail. 2 new tests, both reproducing the real structures found live.

---

# ANetBBS v1.0b2.73 — Close the remaining inbound packet-record parser desync (July 2026)

- FIX: the v1.0b2.70 desync fix only checked 2 bytes after a candidate null for the message-type marker. A real captured packet (via the v1.0b2.71/72 diagnostic capture) showed a message body containing that exact 2-byte sequence by coincidence, fooling the check and corrupting that message and the next. Now also requires a date-shaped string right where the packed header's date field belongs — a real message boundary always has one, a coincidental byte match essentially never does. Traced byte-for-byte against the actual production packet, not inferred from DB fallout. 1 new test, confirmed to fail against v1.0b2.70-72 and pass against this fix.

---

# ANetBBS v1.0b2.72 — Widen diagnostic capture to cover arcmail-bundled packets (July 2026)

- DIAGNOSTIC: the bulk rescan traffic actually arrives as ZIP-compressed arcmail bundles (`.fr7`/`.fr8`), not bare `.pkt` files — v1.0b2.71's capture missed that entirely. Now covers both paths. No behavior change unless `BINKP_DEBUG_DUMP_DIR` is set.

---

# ANetBBS v1.0b2.71 — Diagnostic capture for the still-open inbound packet desync (July 2026)

- DIAGNOSTIC: v1.0b2.70's embedded-null fix reduced but didn't eliminate the inbound packet desync — confirmed live after deploying it and re-running `%RESCAN`. Set `BINKP_DEBUG_DUMP_DIR` on the server to capture raw inbound `.pkt` bytes to disk before parsing, for byte-level root-causing instead of guessing from already-corrupted DB rows. No effect unless the env var is set.

---

# ANetBBS v1.0b2.70 — Fix inbound FTS-0001 packet-record parser desync on embedded null bytes (July 2026)

- FIX: the inbound packet parser located each message's end by scanning for the first `0x00` byte after its header fields. A real `[ANSI]`-tagged post's raw art body contained an embedded null partway through, which truncated that message and desynced the parser for everything after it in the same packet — producing fabricated "messages" with header fields built from fragments of real body text (confirmed live: raw `\r` bytes inside From/Subject columns). This surfaced only now because the v1.0b2.69 capValidate fix let a hub's `%RESCAN` reply — thousands of backlogged messages — actually land for the first time. Fix: only treat a candidate null as the real terminator if what follows it looks like a genuine message/packet boundary. 3 new tests, one confirmed to fail against the pre-fix parser.

---

# ANetBBS v1.0b2.69 — Fix malformed FTS-0001 packet header rejected by strict tossers (July 2026)

- FIX: every outbound FTN packet (netmail and echomail, all paths — one shared packet writer) had a malformed Type-2+ header field. `capValidate` must hold `capWord`'s value byte-swapped, not a plain copy — confirmed against the `hpt` tosser's own source after a real external sysop reported their tosser rejecting our packets outright. This likely also explains inbound mail silently stopping from any hub whose tosser validates the same way but doesn't surface as clear an error. 5 new tests.

---

# ANetBBS v1.0b2.68 — Close the confirmed gaps from the Synchronet/Mystic comparison (July 2026)

Follow-up to a competitive gap analysis against Synchronet and Mystic BBS. Ships the 5 tactical gaps plus a unified access-control helper.

- FEATURE: unified read-access check (`evaluate_access()`) replacing scattered per-feature comparisons — fixed two real gaps along the way: the web file-area browser never checked an area's access level at all, and `/search` returned sysop-only boards and access-gated echomail to any user.
- FEATURE: consolidated 21 duplicated admin-only route gates into one shared helper, standardized on `abort(403)` everywhere (a deliberate UX change for `admin.py`'s routes, which used to flash + redirect).
- FEATURE: file search added to `/search`.
- FEATURE: duplicate-file detection on upload (notice, not a hard block).
- FEATURE: archive integrity testing on upload, all four upload routes — fails open on anything untestable.
- FEATURE: FileFix bot — file-echo counterpart to AreaFix, lets downstream peers self-service file-echo subscriptions via netmail.
- FEATURE: menu translation wiring — `User.language`/`MenuTranslation` existed as unused schema, now actually consulted for the terminal menu system.
- 51 new tests.

---

# ANetBBS v1.0b2.67 — Fix netmail import crash on duplicate-check (CI-caught) (July 2026)

- FIX: inbound netmail import (`_import_netmail`, poll-response path) crashed with `AttributeError` on any message carrying a MSGID kludge — virtually all real FTN netmail — due to a duplicate-check query using the wrong column name (`msg_id` vs the model's actual `msgid`). Pre-existing bug, caught by GitHub Actions CI after the new netmail-notification tests actually exercised this path. Full local test suite (434 tests) now verified green in a real environment, not just syntax-checked.

---

# ANetBBS v1.0b2.66 — ClamAV scan timeout now sysop-configurable (July 2026)

- FEATURE: the ClamAV scan timeout was a hardcoded 30 seconds. New `CLAMSCAN_TIMEOUT` field at Admin → Settings (default 60s), same pattern as the existing idle/bot-gate timeouts. Takes effect immediately, no restart needed. 7 new tests.

---

# ANetBBS v1.0b2.65 — Fix missing notification for inbound netmail (July 2026)

- FIX: new inbound netmail never triggered a notification (web bell or terminal "You have new mail" banner). Two gaps: the poll-response import path resolved a recipient but never notified them, and the real-time BinkP listener path never resolved a recipient at all, so live-received netmail wasn't linked to a user account. Both paths now share one recipient-resolution helper and both notify correctly. 8 new tests.

---

# ANetBBS v1.0b2.64 — Terminal sysop tools: Node Monitor, broadened Sysop menu, MSP picker, profile redesign (July 2026)

- FEATURE: new terminal **Node Monitor** (Sysop Tools → Node Monitor) — scrollable live node list (slot/user/protocol/page/action/idle), with Kick and Message actions. Same live data source as the web NodeSpy panel.
- FEATURE: the terminal **Sysop Tools menu** grew from 3 items to 14 scrollable categories, bringing most of the terminal-feasible web admin UI into telnet/SSH/rlogin: Users, Boards/Bulletins, Echomail/Hub, Games, Wall, File Upload Queue, Scheduled Events, RSS Feeds, Login/Logoff Modules, Notifications, Registry/Peers, Caller Log, Node Monitor, Server Status.
- FEATURE: sending an Inter-BBS Instant Message from the terminal no longer requires knowing the exact `user@host` — browse a BBS directory, live-probe who's online there, and pick a name. Manual entry still available as a fallback.
- FEATURE: **Edit Profile** in the terminal is now one summary screen of all current settings plus a picker for which to change, instead of a blind sequential walk. Added tagline, show-email, and date-of-birth (previously web-only), plus codepage and language (previously unreachable anywhere). Password still never shown.
- FIX: AreaFix log Tags column no longer overflows its table cell on long area lists.
- 30+ new tests.

---

# ANetBBS v1.0b2.63 — Option to hide sysop from Last Callers (July 2026)

- FEATURE: new "Hide sysop logins from the Last Callers displays" toggle at Admin → Last Callers, off by default. Keeps a sysop who logs in several times a day from flooding the user-facing Last Callers list with themselves instead of real users. The admin audit list always shows everyone; InterBBS-shared entries from other BBSes are never affected. 7 new tests.

---

# ANetBBS v1.0b2.62 — InterBBS Game Score Sharing (July 2026)

- FEATURE: door/web game high scores can now be shared with other ANetBBS installs, the same pattern as the Graffiti Wall and Last Callers. Only new personal bests relay, not every submission. Per-game opt-in (Admin → Games, defaults on) plus an install-wide switch at Admin → Games → InterBBS Scores. Casino games are included and shown as dollar amounts; enabling the feature locks the four casino starting balances to a shared standard so scores are comparable across installs, and changing any of them afterward automatically disables sharing again. 20 new tests.

---

# ANetBBS v1.0b2.61 — QWK packet format cleanup for better reader compatibility (July 2026)

- FIX: cleaned up a few spots in the QWK packet writers/readers where fields weren't laid out quite right, which could affect compatibility with some third-party QWK reader software. No changes needed on the sysop side. 4 new tests.

---

# ANetBBS v1.0b2.60 — Updated handshake/CMD_EOB for older bink clients (July 2026)

- FIX: BinkP session close-out could end a bit too early for some strict FTN mailers, causing a successful file transfer to still be reported as failed on the other end. Adjusted the M_EOB handshake and connection close-out on both the client and server side to match. Also switched outbound packet filenames to the conventional FTN naming style. 6 new tests.

---

# ANetBBS v1.0b2.59 — Fix BinkP handshake announcing a hardcoded, stale version (July 2026)

- FIX: the BinkP handshake's `VER` line was a hardcoded literal (`ANetBBS/1.0a binkp/1.1`) in both the client and server handshake code, unrelated to the actual running version — it's said `1.0a` since this code was first written, on every release since. Found while investigating a real FTN interop report. Now pulls from `anetbbs.__version__`. 2 new tests.

---

# ANetBBS v1.0b2.58 — Fix session disconnect right after a telnet transfer completes (July 2026)

- FIX (live-caught testing v1.0b2.57): a telnet ZMODEM download worked correctly but the session disconnected to the goodbye screen the instant it finished. The transfer's own reader (which consumes the client's telnet-negotiation replies) was cancelled before the post-transfer "turn BINARY back off" command was sent, so the client's reply to that sat unconsumed in the socket buffer and got misread as a disconnect by the next normal prompt. Fixed by not sending that command — leaving a session in BINARY mode for the rest of the connection is standard, harmless practice. Same feature as v1.0b2.57; see that entry for credit to andy5995's original PR #6 diagnosis.

---

# ANetBBS v1.0b2.57 — Fix ZMODEM/XMODEM/YMODEM corruption on RFC-compliant telnet clients (July 2026)

- FIX: the terminal file-transfer bridge never undid a compliant telnet client's IAC doubling, so every `0xFF` byte in a transfer corrupted the ZMODEM/XMODEM/YMODEM stream — lenient clients that skip telnet processing happened to work by accident, RFC-compliant ones didn't. Root-caused and originally patched by andy5995 (GitHub PR #6, with Claude Opus 4.8's assistance) — the diagnosis and telnet-side fix were correct; landed here with a protocol gate added on top, since the transfer bridge is shared with SSH/rlogin sessions (which have no IAC concept and would have been corrupted instead by an unconditional fix). 21 new tests — this module had none before.

---

# ANetBBS v1.0b2.56 — Wire up the remaining 7 webhook event types (July 2026)

- FEATURE: the webhooks admin form has always offered 8 event types, but only `shout` actually fired anything — the other 7 (`post`, `bulletin`, `login`, `achievement`, `broadcast`, `sysop_page`, `echomail`) were selectable but never called. All 7 are now wired to their real trigger points, including logins across all four services (web, telnet, SSH, rlogin), not just web. New doc `docs/23-webhooks.md` covers all 8 events' payload formats and gotchas. 8 new tests.

---

# ANetBBS v1.0b2.55 — Fix QWK REP uploads never actually importing (July 2026)

- FIX (live-caught verifying the v1.0b2.54 fix end to end): a reply uploaded from a QWK node showed "sent" but never appeared on the hub. Root cause: the hub's REP importer read the parsed message using the wrong dict keys (`conference`/`from`/`to` instead of the parser's real `conf_num`/`from_name`/`to_name`), silently falling back to defaults instead of erroring. `conference` defaulting to `0` meant every uploaded reply, from every QWK node, always resolved to conference 0 (private mail) and was silently dropped — this was never node-specific, inbound REP processing has been completely broken since this function was written. Fixed by reading the correct keys. Also fixed a second, unrelated crash found in the same investigation: a trailing log statement outside the app-context block raised `DetachedInstanceError` after a successful import, misleadingly logging "REP processing failed" for uploads that had actually already succeeded. 1 new regression test.
- FIX: "Pending Outbound" (dashboard + terminal sysop status) counted QWK messages that can never clear, since QWK never sets `sent_at` at all — the number could only climb, never reflect reality. Scoped to BinkP only. 1 new test.

---

# ANetBBS v1.0b2.54 — Fix QWK areas silently dropping messages (July 2026)

- FIX (live-caught): a message posted from a QWK-connected node vanished with no error — reported "sent" locally, never arrived at the hub. QWK's wire format only carries a numeric conference number, but the ANotherNetwork seeder created the QWK side of all 26 built-in areas with the same symbolic tag as the BinkP side (`ANN.LINUX`, etc.), so any post into one silently fell back to conference 0 and was dropped on import — every install that had activated the QWK side of this network was affected, not one area. Conference numbers were also being assigned per-node-subscription instead of as a fixed property of the area, so two nodes could get different numbers for the same area. Fixed with a stable per-area conference number, a self-healing migration for already-seeded installs (renumbers areas, fixes existing subscriptions), and validation blocking non-numeric QWK area tags going forward. 10 new tests.
- FIX: InterBBS Wall/Last Callers restricted to BinkP networks only — QWK's numeric-only wire format can never carry the special area tag these features need.
- FIX: enabling InterBBS Wall/Last Callers created nothing until someone posted new content — now creates the area immediately on enable.
- FEATURE: QWK "Subscribe to All" is now scoped per-network (checkboxes) instead of sweeping every QWK network; BinkP got the same bulk-subscribe feature, built from scratch.
- New `tools/manage_qwk_requests.py` for clearing stuck pending QWK node requests.
- 31 more new tests total. Full suite: 316/316 passing.

---

# ANetBBS v1.0b2.53 — InterBBS Wall + InterBBS Last Callers (July 2026)

- FEATURE: opt-in InterBBS Graffiti Wall — share Wall posts with other ANetBBS installs over a dedicated `ANET_WALL` echomail area, riding the existing QWK/BinkP transport (real per-node auth, FTN dedup/threading fields, hub subscribe/approve UI) instead of a bespoke sync protocol, matching how fsxnet's own wall echo works. Toggle + network picker on the Wall admin page; a scheduled job (auto-created on enable, every 15 min) imports inbound posts. Remote posts are tagged with their origin BBS in the moderation view. Loop-prevention: an imported post is tagged `origin_bbs`, and the relay hook refuses to ever re-relay a tagged post — the only thing standing between this design and an infinite bounce between two hubs, since a re-composed message would get a brand-new msg_id no downstream dedup could catch.
- FEATURE: Last Callers — new paginated terminal screen (mirrors the existing "Last 10 Callers" style already on the one-liners screen) plus a same-shape opt-in InterBBS sharing toggle. Fixed a real gap found while building this: `CallerLog` (the "last callers" table) was only ever written from the web login route — telnet/SSH/rlogin logins never recorded one, so "Last Callers" only ever showed web users on an otherwise telnet-first BBS. Only `service` and login time are ever shared over InterBBS — IP addresses are never relayed across BBS boundaries.
- FIX: BinkP handshakes advertised each address in two forms at once (`addr@domain` and bare `addr`) in the same `M_ADR` line. Real binkd treats each form as a separate token and self-collides on its own busy-lock for the second one, dropping the session with `Secure AKA busy` before password checking ever runs — happens both polling out to a real binkd hub and when a real binkd hub polls in. Fixed on both the outbound and inbound sides to send one address form only.
- 21 new tests total (`tests/test_interbbs_sync.py`, `tests/test_binkp_dual_adr_fix.py`, `tests/test_binkp_server_single_adr_fix.py`). Full suite: 291/291 passing.

---

# ANetBBS v1.0b2.52 — Fix .env never actually being loaded (July 2026)

- FIX: `python-dotenv` was a declared dependency (requirements.txt, setup.py) but `load_dotenv()` was never actually called anywhere in the codebase. The real systemd services worked anyway because they set `EnvironmentFile=/opt/anetbbs/.env`, injecting those key=value pairs as real process environment variables before Python even starts — but any script run manually (a one-shot `tools/*.py` maintenance script, a bare `python -m ...`, an interactive shell) never saw `.env` at all and silently fell back to `DevelopmentConfig`'s `anetbbs_dev.db` instead of the real `anetbbs.db`, no error or warning either way. Live-caught running `tools/dedupe_qwk_messages.py` (new in v1.0b2.51) by hand on a real install: it reported "nothing to clean up" against an empty database while the real one had hundreds of duplicate rows sitting in it. Fixed by calling `load_dotenv(BASE_DIR / '.env')` once at import time in `anetbbs/config.py`, before any `os.environ.get(...)` reads happen; `load_dotenv()`'s default `override=False` means it can never clobber a value systemd already set. 3 new tests in `tests/test_dotenv_loading.py`.

---

# ANetBBS v1.0b2.51 — Fix QWK message duplication, CP437 body corruption, and node-edit lockout (July 2026)

- FIX (live-caught on the Pi3 test install polling the real ANotherNetwork QWK hub): inbound QWK messages from a hub that doesn't tunnel FTN `@MSGID:` kludges (the normal case for a vanilla QWK hub) got no `msg_id` at all, so the dedup check in the poller silently skipped itself — any poll with overlapping content re-imported every message as brand new, uncapped, inflating area message counts (220 in an area that should have ~22). Fixed by synthesizing a deterministic content-hash `msg_id` whenever no real one is present, mirroring the fallback the outbound REP-writer already had. New `tools/dedupe_qwk_messages.py` one-shot cleanup script wipes affected QWK areas so the next poll repopulates them cleanly. 5 new tests in `tests/test_qwk_inbound_msgid_dedup.py`.
- FIX: `_build_messages_dat()` (the QWK hub's outbound packet writer) built the QWK paragraph-separator by replacing `\n` with the Python string `'\xe3'` (Unicode U+00E3, "ã") *before* CP437-encoding the body — CP437 has no mapping for that character, so `encode(errors='replace')` silently turned every line break into a literal `?` instead of the real separator byte, corrupting every multi-paragraph outbound QWK message. Fixed by doing the substitution at the byte level, after encoding. 2 new tests in `tests/test_qwk_packet_roundtrip.py`.
- FIX: editing an existing BinkP or QWK hub node (e.g. to fix a typo'd tag/name) was silently impossible without also retyping a brand-new password — `DataRequired()` on the password field rejected the whole submission whenever it was left blank, even though the route logic and the form's own help text both already said "leave blank to keep current password." A typo could only be fixed by resetting credentials the node's sysop might not know about, or deleting and re-registering the node from scratch. Fixed by making the field optional on edit while still requiring a password when creating a brand-new node. 4 new tests in `tests/test_hub_node_edit_password.py`.

---

# ANetBBS v1.0b2.50 — Fix SyncTerm sixel auto-detect + terminal sixel profile option (July 2026)

- FIX: sixel auto-detect (`sixel_mode` = Automatic) never worked on SyncTerm specifically, even though forcing it on/off always worked and auto-detect worked fine on other sixel-capable clients. Root cause: SyncTerm's DA1 reply doesn't use the standard `?`-prefixed flag list at all — it spells "CTerm" out in decimal ASCII (`CSI = 67;84;101;114;109;rev c`) and never reports sixel support there regardless of whether it has it. Per SyncTerm's own CTerm manual, sixel support is only exposed via a second, CTerm-specific extended-DA query (`CSI < 0 c` → `CSI < 0 ; Ps... c`, flag 4 = pixel/sixel graphics). `_detect_sixel_support()` now recognizes the CTerm signature and follows up with that query; other terminals are unaffected (still a single round-trip). 3 new regression tests in `tests/test_sixel_detection.py`.
- FEATURE: the terminal "Edit Profile" menu (telnet/ssh) had no way to set the `sixel_mode` preference at all — only the web `/profile/edit` page did, since it shipped in v1.0b2.48. Added a matching Automatic/Always On/Always Off prompt to the terminal menu.
- FIX: `tests/test_mrc_integration.py` uses real `@pytest.fixture`s, so it was never importable by `unittest discover` without pytest installed, contradicting the dev docs' claim that running via `unittest` needed no pytest install. Added `requirements-dev.txt` (pulls in `requirements.txt` + `pytest`) and corrected `docs/17-development.md`'s Testing section.

---

# ANetBBS v1.0b2.49 — Three real Docker bugs found testing against an actual daemon (July 2026)

- FIX: single-container quick-start's documented `docker run` command referenced an entrypoint script the Dockerfile never actually copied there — every quick-start attempt failed instantly.
- FIX: the terminal service (single-container and docker-compose) invoked a broken console-script shim, failing with `ModuleNotFoundError: No module named 'anetbbs'`. Now invokes `python -m anetbbs.main` directly, matching every other service.
- FIX: MRC web chat 404'd in single-container mode (terminal MRC worked fine). The real WebSocket URL is built client-side and was hardcoded to the page's own host — now respects a server-rendered override for Docker single-container mode and the documented explicit-host docker-compose pattern. 5 new tests.
- Docs: single-container Docker path is now confirmed working end-to-end against a real Docker daemon, not just mocked unit tests.

---

# ANetBBS v1.0b2.48 — Sixel capability preference + door-game output queue fix (July 2026)

- FEATURE: new `sixel_mode` profile preference (Automatic/Always On/Always Off) — fixes a real, previously-dead-code bug where sixel auto-detection never actually ran because a manual prompt always short-circuited it first.
- FIX: door-game output now queue-marshaled through a proper background task instead of emitting directly from the PTY-reader thread, matching documented Flask-SocketIO practice. Added diagnostic logging to help confirm a known sixel-rendering dead end for Synchronet-compatible doors on a future real test session. 9 new tests.

---

# ANetBBS v1.0b2.47 — Full BinkP session transcripts for failed polls (July 2026)

- FEATURE: every BinkP poll now captures a full timestamped transcript of everything sent/received on the wire, viewable from a new "Transcript" link on the Poll Logs admin page — no more guessing what broke or needing server log access. Also fixed poll failure messages to always include the exception type, not just str(exc) which can be empty for some failure types. 14 new tests.

---

# ANetBBS v1.0b2.46 — Fill the two docs gaps flagged after v1.0b2.45 (July 2026)

- WIKI: new dedicated Notifications page (closes a long-dangling `[[Notifications]]` link) and a Hub Management writeup update on the Sysop Control Panel page.

---

# ANetBBS v1.0b2.45 — Document today's QWK/notification/join-form changes (July 2026)

- DOCS: updated docs/06-echomail.md, docs/20-federation.md, docs/02-sysop-daily-ops.md, and the qwk/anothernetwork wiki pages to cover everything shipped today — the corrected self-referential-poll-skip behavior, QWK "Subscribe to All", admin notifications, and the new public join form end to end.

---

# ANetBBS v1.0b2.44 — Make the network join form discoverable (July 2026)

- FIX: the /join/ page from v1.0b2.43 wasn't linked from anywhere a real visitor would find it. Added a "Join Our Network" link to the Tools nav menu, logged-in-users-only, shown only when the feature is actually enabled.

---

# ANetBBS v1.0b2.43 — Public "apply to join this network" form (July 2026)

- FEATURE: any hub sysop can now enable a public page where anyone can read the rules, download the infopack, confirm they read the rules, and apply to join — one form covering both BinkP and QWK. Configured from Hub Management's new "Join Form" tab: upload one zip, the system finds the rules text automatically. Applications go to a review queue, notify every admin, and on approval auto-create the right node type(s) with hub-generated credentials — emailed to the applicant if SMTP relay is set up. 29 new tests.

---

# ANetBBS v1.0b2.42 — Admin notifications for things needing sysop review (July 2026)

- FEATURE: the sysop no longer has to go looking for MSP federation join requests, QWK node applications, users pending NUV approval, or newly-discovered bad echomail areas — each now sends an in-app notification to every admin account, honoring existing per-kind notification preferences. New toggles appear in Notification Settings for admin accounts. 7 new tests.

---

# ANetBBS v1.0b2.41 — Stop self-referential polls from flooding the poll log (July 2026)

- FIX (live-caught): a hub's own network row pointing at itself was correctly getting skipped every minute by the poller, but logged a full poll-log row each time — dozens of identical entries drowned out real activity within 20 minutes. No longer creates a log row at all for this case.

---

# ANetBBS v1.0b2.40 — Fix total QWK message loss + Subscribe to All (July 2026)

- FIX (live-caught, critical): every QWK message to every node was silently vanishing on the wire — the hub's outbound packet writer had a byte-offset bug that made the conference number unreadable on the client side, so every message got dropped during parsing with zero error reported anywhere. A poll always showed clean "success, 0 received" no matter how many real messages existed. Fixed; 3 new round-trip tests verified to reproduce total message loss before the fix and pass after.
- FEATURE: "Subscribe to All" button on a QWK node's detail page — one click instead of one area at a time. 3 new tests.

---

# ANetBBS v1.0b2.39 — Fix QWK node FTP home directory path-doubling (July 2026)

- FIX (live-caught, real bug, affects every QWK node): a QWK node's FTP home directory got computed with a doubled `qwk-hub/qwk-hub` path segment, landing the client's session one directory away from where the packet actually gets generated. Login always succeeded and packet generation always "succeeded" silently, but `RETR` always failed with `550 No such file or directory` regardless of correct credentials or config. Found live testing the real ANotherNetwork QWK flow end-to-end, after ruling out network topology, Python version, eventlet, and stored credentials one at a time. 2 new tests.

---

# ANetBBS v1.0b2.38 — Better diagnostics for QWK download failures (July 2026)

- FIX: QWK download errors with an empty exception message (e.g. a bare connection timeout) used to show up in the poll log as an uninformative "QWK: failed to download packet:" with nothing after the colon. Now always includes the exception type name too, so the actual failure is visible instead of a bare colon. 2 new tests.

---

# ANetBBS v1.0b2.37 — Fix QWK node application crash + FTP login confusion (July 2026)

- FIX (live-caught, real bug): re-checking QWK node application status crashed with `DetachedInstanceError` — an ORM row was queried inside one `app_context()` block, then used after that context (and its DB session) had closed. Fixed by doing all DB work inside a single, consistent context and extracting plain values before it closes. 3 new tests.
- FIX (live-caught, real bug): the QWK poller only used `qwk_username` for the FTP login, with no fallback — but QNET-FTP-style hubs authenticate using the Packet ID as the username. A sysop who filled in Packet ID but left Username blank got a silent, always-failing login. Fixed with a fallback to `qwk_packet_id`, plus clearer field labels and applicant-facing instructions. 2 new tests.

---

# ANetBBS v1.0b2.36 — Fix unreachable QWK node application (July 2026)

- FIX (live-caught, real bug): a fresh install's terminal "Echomail Networks" screen bailed out with "No echomail areas configured" before ever reaching the "Apply for ANotherNetwork QWK node" option — making it unreachable for exactly the sysop it's meant for, since every fresh install starts with zero active networks/subscribed areas. Found live testing a real Pi peer install. Fixed; 2 new tests.

---

# ANetBBS v1.0b2.35 — Full documentation + wiki accuracy pass (July 2026)

- FIX (real bug): a fresh Pi/hobbyist-mode ("test"/"behind") install could have the web app and MRC bridge both trying to bind `127.0.0.1:8080` — `install.sh` hardcoded the MRC bridge's default port regardless of `WEB_PORT`. Fixed by deriving it as `WEB_PORT+1`.
- FIX (real bug): the preflight check meant to catch that exact collision after the fact never actually fired — it tried to parse a port number out of a systemd unit line that never contains one. Fixed to read the MRC bridge's own `config.json` directly. 4 new tests.
- FIX (docs + wiki, dozens of concrete mistakes): wrong config vars, wrong admin paths, wrong port numbers on the two most-viewed wiki pages, a fabricated protocol claim, a wrong bulletins field list, a retired systemd unit name, a fictional migration workflow contradicting the wiki's own Architecture page, and a wiki page describing an admin feature that doesn't exist (rewritten to describe what's actually there).
- NEW CONTENT: substantial documentation added across docs and wiki for ANotherNetwork (26 message + 9 file echo areas, new dedicated wiki page), `ftn_domain`, AreaFix, netmail, the Hub Management panel, auto-hatch-on-upload, achievements, the casino/wallet economy, the ebook reader, the separate Python installer toolchain (documented as a lightweight alternative), a new wiki page for the real per-user IRC client, and a Docker verification-status disclaimer.
- Full details in `docs/CHANGELOG.md`.

---

# ANetBBS v1.0b2.34 — Hub "Generation & Distribution" admin UI (July 2026)

- FEATURE: Hub Management gets a new "Generation & Distribution" panel with three tabs: **Nodelist** (manual "Generate Now" + weekly schedule, publishes directly into the `ANN.FILES.NODELIST` file area so peers pull it like any other file), **QWK Packets** (a "Preview" button per node to build/download a test packet without touching the real high-water mark), and **TIC / File Distribution** (status dashboard for a new real fix — files uploaded to any network-attached file area now auto-queue for TIC distribution to subscribed peers, across all 4 upload code paths).
- Built on the existing generic `ScheduledEvent` scheduler (already used for log rotation, security checks, etc.) rather than a new scheduling mechanism — sysops get the same "Run now" + flexible daily/weekly/interval schedule UI they already know.
- 6 new tests in `tests/test_hub_generation_features.py`, including a real route-level test that POSTs an actual file upload and confirms it auto-hatches to a subscribed peer end-to-end.

---

# ANetBBS v1.0b2.33 — ANotherNetwork file areas + real infopack (July 2026)

- FEATURE: ANotherNetwork gets 9 new TIC file-echo areas (`ANN.FILES.NODELIST`, `.INFOPACK`, `.BBSSOFT`, `.DOORS`, `.EBOOKS`, `.LINUX`, `.RETRO`, `.ANSIART`, `.TEST`), auto-seeded the same idempotent way the 26 message areas already are — safe to deploy to an existing install, it only adds what's missing. `ANN.FILES.NODELIST` is flagged as the network's nodelist-distribution area.
- Real bug caught while wiring this up: `FileArea.tag` has a database-level `UNIQUE` constraint (unlike `EchoArea.tag`), so file areas can't be duplicated across both the BinkP and QWK network entries the way message areas are — they now attach to the BinkP entry only, which matches how TIC file distribution actually works anyway (it's a BinkP-native mechanism, not really a QWK one).
- New distributable infopack, built from the project's actual seed data (not placeholder text), using a real third-party network's infopack (`tqwinfo.zip`) as the format reference: `annetinfo.zip` (goals/rules/full area list/application form, plus a new CP437 ANSI banner — verified by rendering it through a small PIL-based CP437 renderer using the project's own bundled VGA font, rather than guessing the escape codes blind) and `annetareas.zip` (machine-readable `.na` files for both message and file areas).
- FIX (docs): README's ANotherNetwork description had a stale category count (said 8, actual is 9) and didn't mention the new file areas.
- FEATURE: `EchomailNetwork.ftn_domain` — a new optional field overriding the qualified BinkP address's domain suffix (`addr@domain`), which previously always derived from the network's display name truncated to 8 chars (producing an awkward `anothern` for "ANotherNetwork"). Seed data now sets it to `anet`; exposed as an editable field in Admin → Echomail Networks for any network. Blank/NULL preserves the old behavior for existing networks.
- FIX (live-caught, real bug): activating the seeded "ANotherNetwork"/"ANotherNetwork (QWK)" rows *on the hub itself* made the poller dial its own address every 60 seconds and fail. Simply deactivating those rows wasn't safe either — the terminal echomail menu also filters areas by the network's `is_active` flag, so that would have hidden the areas from the hub's own local users too. Fixed properly: the poller now detects a network configured to dial its own address (BinkP: `our_address == hub_address`; QWK: `qwk_host` matches `BBS_PUBLIC_HOST`/`BBS_DOMAIN`) and skips it with a clear "skipped" log entry instead of attempting and failing — areas stay visible either way. 3 new tests, verified to reproduce the exact live error before the fix and pass after.
---

# ANetBBS v1.0b2.32 — Sysop-specific paths scrubbed from shared code (July 2026)

- FIX (real bugs, not just cosmetic): several hardcoded fallback paths/usernames from the maintainer's own install had leaked into shared code, meaning they'd silently misconfigure anyone else's install if the normal config lookup ever failed:
  - `anetbbs/games/dropfile.py` — drop-file output path fallback now derives from the file's own location instead of a hardcoded absolute path.
  - `anetbbs/web/gallery.py` — default seed galleries pointed at content that isn't part of the shipped tarball at all (the maintainer's own personal collections); every fresh install was silently getting broken gallery entries. Default is now empty — sysops add their own via `/admin/galleries/`.
  - `anetbbs/installer/symlinks.py`, `anetbbs/installer/upgrade.py`, `anetbbs/installer/wizard.py` — install-dir and service-user fallbacks that would have misconfigured (or, in one case, `chown`'d files to a nonexistent user on) any other sysop's install if triggered.
  - `anetbbs/web/irc_web.py` — the IRC bot's CTCP `SOURCE` reply pointed at a broken GitHub URL; fixed to the real repo.
  - `deploy/sudoers.anetbbs` / `update.sh` — the sudoers template used the maintainer's own username as a literal placeholder token that `sed` substitutes on every update. Renamed to `__SERVICE_USER__`, which also removes any chance of colliding with a real sysop who happens to share that username.
- FIX (public-facing content): the in-app sysop tutorial (`anetbbs/templates/main/tutorial.html`), ~10 spots in the seeded wiki content (`anetbbs/wiki/seed.py` — ships to every fresh install), the GitHub issue template, and two form placeholder fields all had the same maintainer-specific path/username baked in as if it were a generic example. Genericized throughout.
- FIX (follow-up audit): a broader sweep after the above turned up more of the same, and a couple of unrelated real bugs:
  - 3 systemd unit files (`anetbbs-binkp.service`, `anetbbs-finger.service`, `anetbbs-mrc-irc-bridge@.service`) still had the maintainer's own `User=`/path hardcoded — missed in the first pass since they're `.service` files, not `.py`/`.md`. Fixed to match every other unit's generic convention.
  - A third-party email address (copied from wherever `tools/bbs-files-tool.py` was originally adapted from) in its help text pointed sysops at an unrelated stranger; now points at this project's GitHub issues.
  - `tests/msp_loopback_test.py` — a manual diagnostic script, not a real test — matched pytest's default test-file naming pattern with no `if __name__ == '__main__':` guard, meaning every `pytest tests/` run this whole session was silently starting real MSP/SYSTAT network listeners and mutating process-wide env vars as a collection-time side effect. Renamed (to `msp_loopback_check.py`, no longer matches pytest's pattern) and properly guarded. Also had its own unrelated hardcoded absolute path from a different machine's mount point, also fixed.
  - Two more test files (`test_ebook_terminal_menu.py`, `test_qwk_hub_gating.py`) had the same unrestored-`os.environ['FLASK_ENV']` leak pattern as the `TestingConfig.SQLALCHEMY_DATABASE_URI` bug fixed alongside the Docker/CI work — fixed both.
  - `anetbbs/web/healthz.py`/`preflight.py` checked a `FINGER_PORT` config key that was never actually set by anything (the real one is `FINGER_LISTEN_PORT`) — the finger service's health/preflight check silently ignored any custom port a sysop configured. Added the missing `Config` attribute and fixed both call sites.
  - Docs: `docs/00-overview.md`'s table of contents was missing 2 existing doc files; README's wiki page count was stale (41 → 43); README's Doors list didn't mention the bundled A-Net Online game server; a few wiki pages used dead internal-wiki-link syntax for external tools instead of the real links used elsewhere in the same content.
- FIX (full documentation accuracy pass): every doc file, the in-app sysop tutorial, and the seeded wiki cross-checked against the actual current code. The two biggest: `docs/12-upgrading.md` and `docs/01-installing.md` had drifted badly from reality — wrong backup path (claimed next to the install; it's actually `/tmp/anetbbs-backup-*`), described the legacy split `anetbbs-telnet`/`anetbbs-ssh` services instead of the unified `anetbbs.service` that replaced them, a stale wizard-prompt list missing roughly half the real prompts, and automatic rollback described as an interactive prompt. Also: the sudoers install instructions broke in three more places (a doc, the tutorial, two wiki pages) when this same version's `__SERVICE_USER__` placeholder rename shipped — all fixed to show the required `sed` substitution instead of a bare `cp`; the wiki's Themes page listed five theme names that don't exist anywhere in the codebase; the wiki's Doors page and `docs/17-development.md` undercounted door types (7 vs the real 10); a wiki page referenced a nonexistent `tools/db_backup.sh`; `docs/INSTALL.md` was missing the `anetbbs` system-user creation step its own systemd instructions depend on, plus a wrong `apt` package list. One more real (non-doc) bug surfaced along the way: `healthz.py`/`preflight.py` checked a `FINGER_PORT` config key that was never actually set anywhere — fixed by adding the real `FINGER_LISTEN_PORT`/`FINGER_LISTEN_HOST` to `Config`.
- No behavior change for the maintainer's own existing install — these only affect what a *different* sysop's fresh install would have seen, plus some quiet test-suite/CI correctness fixes.

---

# ANetBBS v1.0b2.31 — Docker support (July 2026)

- FEATURE: **Docker deployment**, two ways to run ANetBBS in containers, sitting alongside (not replacing) the traditional `install.sh`/systemd path:
  - **Single-container quick start** — one image, all 5 services (web, terminal, MRC bridge, finger, binkp) managed inside by `supervisord`. Fastest way to try it.
  - **docker-compose (recommended)** — one container per service, matching the 5 systemd units a traditional install runs. Proper per-service health checks, logs, and restart control.
  - Both share one image (`docker/Dockerfile`), built multi-arch (amd64 + arm64, so it runs on a Raspberry Pi the same as bare metal).
  - Safe with the existing SQLite setup — no forced Postgres migration. Every container runs as the same fixed UID sharing one local volume, the same property that already makes 5 separate bare-metal processes sharing one SQLite file safe today.
  - The Sysop Control Panel's restart/upgrade buttons work under `docker-compose` too, via a Docker-socket-backed backend instead of `systemctl` — see `docs/22-containers.md` for the trust-boundary tradeoff this represents (opt-out available).
  - One-click self-upgrade works under `docker-compose` (pulls a new image tag, recreates affected containers). Single-container mode has no in-place upgrade button — documented manual steps instead.
  - See [`docs/22-containers.md`](docs/22-containers.md) for the full guide, including what's not yet supported in containers (DOSBox/Mystic/Node door games, ClamAV, FTP passive-mode NAT traversal).
- FIX (hardening, benefits bare-metal installs too): SQLite now runs in WAL mode with a real busy-timeout (`anetbbs/models.py`) — previously zero tuning existed, relying entirely on the 5 systemd processes sharing one OS user/filesystem to avoid lock contention. Also made `MSP_PORT`/`SYSTAT_PORT` environment-overridable, matching every other port in `anetbbs/config.py`.
- FIX (found while double-checking the above didn't affect bare-metal backups): the admin panel's on-demand **DB Backup download button** (`/admin/db-backup`) streams the SQLite file with a plain file copy, no checkpoint — in WAL mode, recently committed writes can sit only in the separate `-wal` file until checkpointed, so that download could have silently missed recent data. `update.sh`'s own automated pre-update backup was already safe (uses `sqlite3 .backup`, unaffected either way) — this was specifically the manual download button. Now forces a checkpoint (`PRAGMA wal_checkpoint(TRUNCATE)`) before streaming the file.
- 27 new tests across `tests/test_control_runtime_dispatch.py` and `tests/test_container_self_upgrade.py`. The Docker builds/runs themselves need real Docker access to verify end-to-end (not available in the sandbox this was built in) — verified via full syntax checks and YAML validation instead; needs a real `docker build`/`docker compose up` pass before shipping.
- FIX (critical, live-caught on bbs.a-net.fyi): reading **any echomail message via terminal** (SSH/telnet/rlogin) failed with "Menu action failed (see server log)". The read-only message viewer's own screen class (`_ViewerScreen.draw_text()` in `anetbbs/features/anedit.py`) was missing the `misspell` parameter added to the base editor screen class when spell-check shipped (v1.0b2.20) — the shared redraw logic the viewer inherits always passes it, so every read attempt crashed with a `TypeError`. This had been broken since v1.0b2.20; nobody had tried reading an echomail message via terminal since. Fixed, and added `tests/test_aneview_draw_text.py` (verified it reproduces the exact production traceback before the fix and passes after).
- FIX (release hygiene, unrelated to Docker but shipping alongside it): `build-release.sh` rewritten from a manually maintained `--exclude` denylist to build from git's own tracked/untracked-but-not-ignored file list instead. The old approach had let `bbs.log` (containing a plaintext admin password from a local test boot) leak into 7 prior release tarballs, plus assorted stray test-scratch files. See `.gitignore` for the accompanying new rules (`.claude/`, `door32.sys`, botwars runtime files, a `tests/.*.db*` pattern).

---

# ANetBBS v1.0b2.30 — Federation "Register with Hub" nav fix (July 2026)

- FIX: the "Register this BBS with a federation hub" admin page (`/admin/registry/self`) existed and worked, but had no link anywhere in the admin UI — genuinely undiscoverable unless you already knew the exact URL. Added a **Register with Hub** card to Admin → Network.
- FIX (found while adding the above): the v1.0b2.29 hub-gating fix made the entire `hub_admin_bp` blueprint 404 on non-hub installs, but the **Hub Management** card in Admin → Network still unconditionally linked to it — a dead link on every install except the actual hub. Now hidden unless `REGISTRY_MODE_ENABLED=true`.
- 2 new tests in `tests/test_qwk_hub_gating.py` covering both card-visibility cases.

---

# ANetBBS v1.0b2.29 — Local chat, terminal colors, QWK/federation hub gating (July 2026)

- FIX (critical): the terminal **Local Chat** menu option (Chat → 1) was a stub that only ever echoed your own message back to yourself — it never broadcast to any other node. Reported by the sysop: "I logged on 2 nodes and could not see what the other 'person' is typing." A fully working real-time broadcast/queue system already existed in `anetbbs/features/multinode.py` (used by a *different*, unreachable `multinode` menu action that was never wired into any default menu) — Local Chat now delegates to it, so it actually works: real-time broadcast, `/list` to see who's online, `/w <slot> <msg>` to whisper.
- FIX (critical, found while testing the above): even after wiring Local Chat to the real broadcast system, testing with the same account logged into two nodes at once (a natural way to quickly test this) still failed silently — `broadcast()`'s "don't echo back to the sender" check compared by **username**, not by which specific node sent the message. Two nodes sharing a username matched the check on *both* sides, so the message got dropped for everyone, not just the real sender. Fixed by identifying the sending node specifically (its node slot) instead of by username.
- FIX (critical): several terminal clients — MagiTerm, NetRunner, and PuTTY (in ANSI-BBS mode) — showed no color at all on many screens (graffiti wall, file areas, message boards, PMs, bulletins, MRC chat, the pre-login welcome screen, the games list) while the main menu rendered fine. Root cause: the main menu is drawn from a static `.ans` art file using the classic ANSI.SYS bright-color convention (bold + base color, e.g. `\x1b[1;36m`), but nearly every other screen was hand-coded in Python using the newer "aixterm" 90-97 SGR range directly (e.g. `\x1b[96m`), with no bold attribute. SyncTerm happens to support both conventions; the other three clients only recognize the classic one, so those bright-color codes were silently dropped. Converted every affected color constant/table across 10 files to the classic convention. New regression tests guard against reintroducing bare 90-97 codes.
- FIX (critical): the terminal **"Apply for ANotherNetwork QWK node"** wizard wrote its application straight into whichever BBS install happened to run it, instead of reaching the real hub (bbs.a-net.fyi) — and the hub admin's "Node Requests" review queue (Admin → Echomail → Hub) was exposed on *every* install with no gate at all, so any sysop's own admin panel showed the same review queue as if they were the hub. Reported by the sysop: "all the sysops try to put in for a node and it goes to their system." Fixed with a real hub-side API (`POST /qwkhub/apply`, `GET /qwkhub/status/<token>`) so the wizard on a peer install actually reaches the hub over HTTP, and gated the entire hub-admin blueprint behind the existing `REGISTRY_MODE_ENABLED` flag ("this install IS the hub") so it only appears on the real hub. **Sysops running the actual hub must set `REGISTRY_MODE_ENABLED=true` in their `.env`** for the hub-admin UI and the new API to work on their install — see `.env.example`.
- FIX: the federation/MSP self-registration wizard showed a flat "success" message ("check your email for the verification email from the hub") even when nothing was actually sent — `REGISTRY_URL` defaulted to blank, and the registration tick silently no-op'd without telling the caller. `REGISTRY_URL` now defaults to `https://bbs.a-net.fyi` (matching what the docs already described), and the tick function reports back whether it actually succeeded so the wizard and the "register now" admin button show the real outcome.
- 9 new tests in `tests/test_multinode_chat.py`, including a same-username-two-nodes regression test — the existing test suite used different usernames per node and would never have caught this. Also verified by hand against two real, concurrently connected telnet sessions (both logged in as the same account) before and after the fix.
- 4 new tests in `tests/test_ansi_color_compat.py` for the color-compat fix, plus manual verification against a live telnet session showing the corrected SGR codes.
- 10 new tests in `tests/test_qwk_hub_gating.py` (blueprint gating + the new apply/status API) and 4 in `tests/test_registry_tick_status.py` (the false-success fix).

---

# ANetBBS v1.0b2.28 — A-Net Game Server bundled by default + Ebook Reader (July 2026)

- FEATURE: A-Net Online's own rlogin game server (450+ door games) now ships pre-installed and active, the same way LORD does — no more manual setup required. Each install gets its own **randomly generated password and BBS tag** for it, created once on first boot and never touched again, so they're private to your BBS and never appear anywhere in the public source. The remote server doesn't validate the password against anything in particular — it just needs to be present and hard to guess, since every user on a given BBS shares it; the tag just needs to be short and distinct so the remote server can tell your BBS's users apart from everyone else's. Sysops are free to change either at Admin → Door Games → A-Net Game Server.
- FEATURE: new door_rlogin field, **BBS Tag** — previously the only way to set a BBS tag was hyphen-joining it directly into the user-template text (`@USER@-ANET`). It now has its own dedicated admin-form field instead, purely for clarity — the wire format itself (`username-ANET`, hyphen-joined, no space) is unchanged.
- FEATURE: new built-in web game, **Ebook Reader** — search and read free public-domain classics from Project Gutenberg (curated classics shelf + full-text search), in a clean, book-styled reading view with serif typography and page-turn navigation, not just a wall of scrolling text. Bookmarks, a "continue reading" history list, and one-click download to a plain `.txt` file. Admin → Games → Game Center → Ebook Reader.
- FEATURE: the Ebook Reader now also has a **terminal version** (telnet/SSH/rlogin) — same classics/search/history/bookmarks, reading with chapter navigation, scrolling, and download over ZMODEM (if `lrzsz` is installed). New main-menu hotkey `K`.
- FEATURE: **per-front-end enable/disable** — every game now has independent `Web` and `Terminal` toggles in Admin → Games (only meaningful for a game with a real implementation on both sides, currently just the Ebook Reader — turn either off without affecting the other).
- FIX: the bundled-door seeding logic hardcoded `max_nodes=1` for every stock door — harmless for the existing single-player DOSBox/JS doors, but wrong for a 20-slot remote multiplayer game server. Now respects a per-door override.
- FIX: `lrzsz` (provides the `sz`/`rz` ZMODEM binaries used for every terminal file transfer — file areas and now the ebook reader's download) was never in `install.sh`'s required-packages list, so fresh installs silently had no working terminal downloads until a sysop happened to install it manually. Now installed automatically on fresh installs. **Existing installs upgrading via `update.sh` need to install it once by hand** — `update.sh` only manages Python deps, not system packages: `sudo apt-get install -y lrzsz` (or your distro's equivalent).
- 25 new tests: `tests/test_anet_game_server_seed.py` (random password/tag generation, persistence across reboots, per-install uniqueness, the max_nodes fix), `tests/test_rlogin_client_user.py` (the BBS-tag wire format), `tests/test_ebooks.py` (chapter-splitting and text-cleaning against real Gutenberg formatting quirks), `tests/test_ebook_terminal_menu.py` (the web/terminal toggle defaults and menu-hotkey backfill). The terminal reading UI itself was verified by hand against a live telnet session end-to-end (menu navigation, real book fetch, chapter nav, bookmarks, history, CP437-safe text rendering).

---

# ANetBBS v1.0b2.27 — Telnet door game servers (July 2026)

- FEATURE: new `door_telnet` game type for external telnet-only game servers, like a TWGS (Trade Wars Game Server) instance — e.g. `game.a-net-online.lol:2002`, A-Net Online's own TWGS. Unlike the existing `door_rlogin` type, telnet has no pre-authentication handshake — connecting is just `host:port`, and the user logs in interactively on the remote side exactly like connecting with any telnet client directly. Works on both the web game center and terminal (telnet/SSH) doors, mirroring the existing rlogin door architecture. Includes a small RFC 854 option-negotiation filter so the remote server doesn't hang waiting for replies to features this client doesn't implement, and so negotiation control bytes never leak into what the player sees. Configure in Admin → Games: set Game Type to "Telnet Door Server", then just `host:port` in the Server field (default port 23). 13 new tests for the negotiation filter and the connection wrapper.

---

# ANetBBS v1.0b2.26 — MRC: terminal fixes + stale-session cleanup (July 2026)

- FIX: `/mentions` always showed 0 — mention detection was wired to event types the bridge never sends; moved it to the real `mrc_message` path. The status-bar `!N` indicator now works live too.
- FIX: messages up to 140 chars could get silently truncated to ~120 — the bridge prepends your display handle before its cutoff; terminal client now accounts for that overhead and splits long messages into `(1/2)`/`(2/2)` chunks instead of losing the tail.
- FIX: mention indicator was reverse-video red, illegible on some terminals — switched to explicit fg/bg colors.
- FIX: `/mentions` output misaligned on wrap — restructured into a header line + indented body line per mention.
- CHANGE: `/help` and `/helpserver` swapped — `/help` now asks the hub for its own help, `/helpserver` shows the client's local command list.
- FIX: outgoing text color (arrow keys) never persisted across reconnects — now restores on join and saves via `set_style` whenever you cycle color.
- FIX: Tab nick-completion gave no feedback on zero matches and could dump an unbounded candidate list — now gives explicit "no match" feedback and caps the list at 12 with a "+N more" hint. Also fixed a real bug where a visibly-present user couldn't be found: `/who`'s roster is a comma-separated wire format, but the parser was splitting on whitespace and never extracting anyone correctly. Bridge now also refreshes the roster whenever a client runs `/who`.
- FIX (critical): a mid-round version of the Tab-completion fix briefly deadlocked terminal MRC solid on the next Tab press (held a lock across a call that needed the same lock). Fixed and covered by a timeout-guarded test.
- FIX (bridge): a dropped connection (dead cable, force-killed client) left MRC sessions looking permanently logged in, causing "you can only be logged on once" from the upstream hub. The bridge's cleanup logic was already correct but never ran, since the WebSocket had no heartbeat. Added `heartbeat=30`; no client-side changes needed.
- 42 new tests across `tests/test_mrc_terminal_mentions.py` and `tests/test_mrc_bridge_userlist.py`.

---

# ANetBBS v1.0b2.25 — Paginate large file areas (July 2026)

- FEATURE: file area listings (**File Areas → any area**) are now paginated (50 files/page) instead of rendering every file in the area on one page — reported by the sysop after a door-games file area with 7,000+ files took a noticeable while to load, same complaint pattern as the CHANGELOG page before it was paginated (v1.0b2.22). Sorting (by name/size/date) still applies to the full list before slicing, so page contents are stable and correct regardless of which page you're on; pagination links preserve the active sort. New shared `ListPagination` helper (`anetbbs/web/list_pagination.py`) — same Flask-SQLAlchemy-compatible interface as the CHANGELOG's own pagination class, but with proper `…`-truncated page links for the hundreds of pages a large file area can span (the CHANGELOG's version didn't need that — it never gets anywhere near that many pages). Verified end-to-end against a 137-file scratch area: correct per-page counts including the partial last page, correct sort-then-slice ordering, out-of-range page numbers clamp instead of erroring, sort preserved across page links.

---

# ANetBBS v1.0b2.24 — Configurable login auto-ban (July 2026)

- FEATURE: the login auto-ban (triggered by repeated failed login attempts) is now fully sysop-configurable from **Admin → IP Bans**, instead of a hardcoded, permanent-only ban. Previously: any IP that failed to log in 10 times within 5 minutes got a **permanent** ban with no way to change the threshold or duration, and no way to turn it off — reported by Firehawke (feature request 2026-07-03) after it caught one of their own test users with no warning and no recourse short of manually removing the ban. Now configurable: number of failed attempts before a ban, the time window, the ban duration (in hours — 0 means permanent, still available as an explicit choice), and a full on/off switch. Defaults unchanged from before (10 attempts / 5 minutes) except the ban itself, which now defaults to **1 hour instead of permanent** — matching the resolution proposed in the request. Settings live in a new `AutoBanConfig` singleton table (`anetbbs/models.py`), editable via a new settings card at the top of the existing IP Bans admin page — no new nav entry needed. 8 new tests in `tests/test_auto_ban.py`, including one that exercises the real `/auth/login` route end-to-end to confirm the configured (not the old hardcoded) limit is what actually trips the ban.

---

# ANetBBS v1.0b2.23 — Multi-screen welcome/goodbye/newuser sequences (July 2026)

- FEATURE: sysops can now drop in more than one variant of any lifecycle ANSI screen (`welcome`, `goodbye`, `newuser`, or a custom slot) and ANetBBS shows **all of them, in order, every login** — the classic Synchronet `logon1.ans`/`logon2.ans`/`logon3.ans` multi-screen convention. Naming: `welcome132.ans` is shown first; add `welcome132_2.ans`, `welcome132_3.ans`, etc. alongside it and they display right after, in the same login (works the same way for `.ans`/`.asc`/non-widescreen variants, and for either the `data/text/<slot>.ans` file override or the bundled stock screens). Numbers don't need to be contiguous. Each variant is responsible for its own `@PAUSE@` if a pause is wanted before the next one loads — nothing is auto-inserted between them. A single file (the common case) behaves exactly as before, with zero extra overhead or setup — no persisted state of any kind is needed. Prefer a single random pick instead of the whole sequence? Name variants with `_ran` instead (`welcome_ran.ans`, `welcome_2_ran.ans`, ...) — that naming shows just ONE of the group, chosen at random, rather than the full sequence; `_ran` wins if both naming schemes exist for the same screen. See `docs/04-ansi-screens.md` for usage, and `tests/test_screen_rotation.py` for the regression suite (13 tests, all passing).
- FIX (pre-existing, unrelated to the feature above): found while testing on the Pi3 — a `BbsAnsiScreen` database row that's marked active but has an **empty** body (e.g. a blank draft saved and left active in the ANSI editor) was treated as "configured," which blocked `_show_ansi_screen`'s fallback to the bundled stock screen, and the screen silently showed nothing at all. This bug predates this release — an empty-but-active DB row now falls through to the bundled screen the same as if no row existed.
- FIX: the bundled `welcome132.ans` and `welcome.ans` were the last stock screens that never got the border-removal pass applied to `goodbye132.ans`/`newuser132.ans`/`main132.ans` — both still had box borders and (for the 132-col one) a duplicated Sysop/Version line. Rebuilt both borderless, matching the established style (132-col keeps the 120-visible-column footer safety margin; 80-col gets its own equivalent margin), with `@PAUSE@` added to all three welcome variants (`.ans`/`132.ans`/`.asc`) and to `goodbye.ans` — none of them had it, so with no custom screen configured the banner rendered and immediately fell straight through to the next prompt, easy to miss entirely at normal typing speed.

---

# ANetBBS v1.0b2.22 — Paginate the web Changelog page (July 2026)

- FEATURE: `/docs/CHANGELOG` was rendering the entire, ever-growing `docs/CHANGELOG.md` as one markdown pass on every page load, getting slower to load release after release. Now paginated at 15 version-entries per page (newest first, matching the file's existing order), with Prev/Next + page-number navigation styled to match the site's active theme. Other docs pages are unaffected — this is CHANGELOG-specific, since it's the only doc that grows unbounded over time. Verified with a direct test against the live route: page 1 shows the newest entry, page 2 differs from page 1, and an out-of-range page number clamps to the last valid page instead of erroring.
- Also fixed the CHANGELOG.md header's "Current release" line, which had been stuck at `v1.0b1.6` for a long time while every actual release moved past it — caught looking at the deployed Pi3 copy. Added a note to the version-bump checklist so this line gets updated alongside the changelog's new top entry going forward, not just occasionally.

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
