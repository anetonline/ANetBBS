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
