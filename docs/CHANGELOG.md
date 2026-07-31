# ANetBBS Changelog

Current release: **`v1.0.4`** (August 2026). This file covers `v1.0.0`
onward, which follows standard semantic versioning — patch releases are
`v1.0.1`, `v1.0.2`, and so on. The full internal beta build-number
history (`v1.0a1.1` through `v1.0b2.239`) that got the project to this
release is preserved in
[`CHANGELOG-beta.md`](CHANGELOG-beta.md).

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
