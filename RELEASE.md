# ANetBBS v1.1.63 — Third auto-update outage, plus a preflight false warning (October 2026)

A third real outage, found live while verifying v1.1.62's fix: with the sudoers problem genuinely resolved, clicking the auto-update button failed with a new error — systemd exit 209/STDOUT, "Failed to set up standard output: Permission denied."

Root cause: `anetbbs-web.service` runs with `PrivateTmp=yes`, which gives it its own private, isolated view of `/tmp`. The upgrade progress log used to live at a fixed `/tmp/anetbbs_upgrade.log` — a path the web process could create/truncate fine in its own private `/tmp`, but which the separately-launched, detached systemd unit that does the real upgrade work (see v1.1.62's fix) resolves against the REAL, system-wide `/tmp` instead. Same literal path, two different, inconsistent views of it depending on which process looked at it.

Fixed by moving the log to `<install dir>/data/anetbbs_upgrade.log` — a real, non-virtualized directory every process already agrees on identically, regardless of any unit's `PrivateTmp` setting, and already used for persistent app data.

Also found live immediately after applying v1.1.62, and fixed in this same release: the Preflight page's "sudoers grant present" check flagged `/etc/sudoers.d/anetbbs`'s own freshly-refreshed, correct `0440` permissions as "too open," then suggested fixing it by setting it to the exact same `0440` it had just rejected. Fixed by narrowing the check's permission mask to only flag group write/execute or any access at all for "other" — the actual security-relevant cases — leaving group-read (which `0440` itself sets, and which `install.sh`/`update.sh` always apply) alone.

This is the first release where the structural fix from v1.1.62 (sudoers escalation itself) and this release's fix (the detached unit's log path) have both actually been exercised by a real attempt — apply this one over SSH once more, same as the last two.
