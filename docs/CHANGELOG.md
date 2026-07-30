# ANetBBS Changelog

Current release: **`v1.0.0`** (August 2026). This file covers `v1.0.0`
onward, which follows standard semantic versioning — patch releases are
`v1.0.1`, `v1.0.2`, and so on. The full internal beta build-number
history (`v1.0a1.1` through `v1.0b2.239`) that got the project to this
release is preserved in
[`CHANGELOG-beta.md`](CHANGELOG-beta.md).

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
