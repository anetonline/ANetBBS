# ANetBBS v1.0a2.81 — Fix: DOSBox-X detection + web auto-update tar failure on some VPS hosts

## What's fixed

### DOSBox-X (and dosbox-staging) not detected — revised fix

v1.0a2.80 attempted to fix DOSBox-X detection by setting `SDL_VIDEODRIVER=dummy`
before the version probe. This was not enough: dosbox-x on headless Debian/Ubuntu
servers still hangs during the probe (audio subsystem, not just display), causing
the 5-second timeout to fire and marking the binary as unusable even when
correctly installed via `apt install dosbox-x`.

The detection logic now simply checks that the file exists and is executable.
A subprocess version probe is unnecessary — if `apt` installed the binary, the
arch is correct. Any real execution failure will produce a clear error when the
door actually launches.

### Web auto-update failing with "tar extract failed" on some VPS hosts

On VPS hosts with user-namespace restrictions (common on LXC containers,
some cloud providers), `tar` running as root cannot `chown` files to the
uid/gid baked into the tarball (uid 1000 = the developer's local account).
This caused `run_upgrade.sh` to abort at the extract step with:

```
tar: ...: Cannot change ownership to uid 1000, gid 1000: Operation not permitted
tar: Exiting with failure status due to previous errors
[upgrade] FAIL: tar extract failed
```

Fixed: `--no-same-owner` added to the tar extract in `run_upgrade.sh` so the
extracted files take the ownership of the extracting process rather than the
original build machine's uid/gid.
