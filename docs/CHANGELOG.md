# ANetBBS Changelog

Current release: **`v1.0.2`** (August 2026). This file covers `v1.0.0`
onward, which follows standard semantic versioning — patch releases are
`v1.0.1`, `v1.0.2`, and so on. The full internal beta build-number
history (`v1.0a1.1` through `v1.0b2.239`) that got the project to this
release is preserved in
[`CHANGELOG-beta.md`](CHANGELOG-beta.md).

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
