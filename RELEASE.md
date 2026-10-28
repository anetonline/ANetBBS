# ANetBBS v1.1.62 — Second auto-update outage, structural fix (October 2026)

**If your auto-update button fails with `sudo: a password is required`, apply this release manually over SSH once (`sudo bash update.sh` from inside the extracted tarball) — the button cannot fix this on its own, but this is also the last time it should ever need manual help for this specific reason.**

A second real outage, separate from the one v1.1.59 fixed: on a box where a past upgrade's sudoers refresh had silently failed while the rest of that upgrade still succeeded, the auto-update button was left permanently stuck — the running code built a command the stale sudoers grant no longer matched, and the button is the one feature that can't repair a sudoers problem by itself.

Root cause: v1.1.59's fix moved the mechanism for surviving `anetbbs-web`'s own restart (needed because the upgrade stops and restarts the web service partway through itself) into the sudo-gated command's exact flags. Every time that mechanism needed to change, every existing install's sudoers grant had to change with it, via a successful `update.sh` run — and when that one refresh step failed quietly on one box, the mismatch had no way to self-correct.

Fixed by moving that mechanism out of the sudo-gated command entirely. `sudoers` now only ever grants one permanently simple thing — the upgrade wrapper script and its plain arguments — and the wrapper itself, once it already has root, detaches into its own systemd unit internally with no further sudo call involved. This can't drift out of sync again regardless of how the detachment mechanism itself needs to evolve in the future, because changing it never again requires every existing install's sudoers file to be rewritten in lockstep.
