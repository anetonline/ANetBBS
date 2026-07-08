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
