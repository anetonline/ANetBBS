# Upgrading

## Recommended path

```
tar xzf ANetBBS-vX.Y.Z.tar.gz
cd ANetBBS-vX.Y.Z
sudo bash update.sh
```

The update script (8 steps):

1. Detects your install directory from the running `anetbbs-web`
   service's `WorkingDirectory=` (falls back to `/opt/anetbbs` if no
   service is installed yet).
2. Backs up `.env`, the SQLite database(s) (via `sqlite3 .backup`, a
   consistent snapshot even with the service still running), the
   systemd unit files, and the nginx site config to a timestamped
   directory under `data/backups/anetbbs-backup-YYYYMMDDHHMMSS/`
   (inside your install dir — moved off `/tmp` after a real incident
   where a Pi's `/tmp` turned out to be a small RAM-backed tmpfs and
   the backup didn't survive). This backup
   does **not** include `data/` or `logs/` — those are never touched
   by the update in the first place (see "What's safe to skip" below),
   so there's nothing to restore for them.
3. Stops `anetbbs-web`, `anetbbs` (the unified telnet/SSH/rlogin
   process), and any of `anetbbs-mrc-bridge`/`anetbbs-finger`/
   `anetbbs-binkp` that are currently running. Also removes the
   legacy split `anetbbs-telnet`/`anetbbs-ssh` units if a very old
   install still has them (replaced by the unified `anetbbs.service`
   back in v1.0a2.10).
4. Rsync's the new code over your install dir — excludes `data/`,
   `.env`, `venv/`, `logs/`, `doors/`, `gallery-config.json`, and the
   MRC bridge's own config/data/logs, so none of those are ever
   overwritten by an update.
5. Runs `pip install -e .` to pick up new/changed dependencies
   (includes an automatic Python 3.13 eventlet-on-ARM compatibility
   check/rebuild — see docs/INSTALL-PI.md).
6. Merges any new keys into your existing `.env` (never overwrites
   values you've already set) and auto-generates a real `SECRET_KEY`
   if yours is still a known insecure default.
7. Updates the database schema — adds missing columns/tables directly
   via a minimal script (deliberately avoids the normal app startup
   path, which would crash on an old schema missing new columns).
8. Restarts services (only restarts the optional ones —
   mrc-bridge/finger/binkp — if they were already running before the
   update) and probes `/healthz` for up to 30 seconds.

If the web service fails to start after restart, the script
**automatically rolls back** — no prompt — restoring `.env`, the DB
file, and the systemd units from the Step 2 backup, then restarting.
One exception: if the failure looks like the known Python 3.13
eventlet/ARM wheel issue, it tries rebuilding eventlet from source and
retrying instead of rolling back (rolling back application files
wouldn't fix a broken Python package anyway).

## Auto-update (Admin UI)

You can also upgrade from **Admin → Upgrades** in the web interface.
The BBS checks the configured registry (`REGISTRY_URL`) for a new
release and offers a one-click install. The click runs the root-owned
helper `/usr/local/libexec/anetbbs/run_upgrade.sh` through sudo, in a
detached systemd unit, so the upgrade survives the web service
restarting. The helper:

1. Takes its settings from root-owned files only (`/etc/anetbbs.install`,
   `/etc/anetbbs/`, `/var/lib/anetbbs/release-state`), never from `.env`
   or anything else in the install directory.
2. Only moves forward: the release must be a stable `vX.Y.Z` newer than
   the installed one. Reinstalls and downgrades are done as root.
3. Requires an https download (plain http only to this machine's loopback,
   for a hub whose `REGISTRY_URL` is `http://localhost:<port>/`).
4. Requires a release signed by a key in `/etc/anetbbs/release-signers`,
   unless an operator turned that off (see
   [02 — Sysop daily ops](02-sysop-daily-ops.md)). Revoked keys and
   denylisted archives are refused.
5. Checks the SHA-256 and the signature, and that the archive contains
   exactly `ANetBBS-<version>/` with a matching `VERSION` file.
6. Runs that release's `update.sh`, the same steps as above, including
   the automatic rollback, and checks that it recorded the new version.

Progress goes to `/var/log/anetbbs/upgrade.log` and is shown in the
browser log viewer. Each run starts the log fresh and ends with
`result=ok` or `result=fail` and its run id.

**Using a private release feed?** Nothing in `.env` is trusted for
upgrades any more. A feed that serves official, signed releases needs no
setup. A feed with its own unsigned releases has to be allowed by the
operator as root; see [02 — Sysop daily ops](02-sysop-daily-ops.md).

**Run `update.sh` from a root-owned copy.** `update.sh` refuses to run
from a directory the BBS service account can modify, such as the install
directory itself, because that would run code the account controls as
root. Extract the release somewhere root-owned (for example under
`/root`) and run it from there. Git-checkout installs can accept that
risk with `ANETBBS_ALLOW_SERVICE_OWNED_SOURCE=yes`.

**Docker installs** upgrade through the separate `control` service; see
[22 — Containers](22-containers.md#self-upgrade).

### Installs made with `git clone`

On an install that is a git checkout, `update.sh` runs `git pull` (as
the service account) instead of copying files. If the upstream history
was rewritten, for example to remove a file from old commits, a normal
pull refuses. `update.sh` then checks two things:
- that you have no local changes to tracked files;
- that your current commit has exactly the same content as a commit on
  the new upstream branch.

If both hold, it moves the checkout to the new history and updates.
Otherwise it leaves the checkout alone and tells you why: local
changes, or local commits that aren't upstream.

## Manual rollback

If you need to undo a bad upgrade yourself, the backup directory path
is printed during the update — it looks like
`/opt/anetbbs/data/backups/anetbbs-backup-YYYYMMDDHHMMSS/` (inside
your install dir, so it survives a reboot). Find it with
`ls -dt /opt/anetbbs/data/backups/anetbbs-backup-* | head -1` if you
didn't note it down. Restore with:

```
sudo systemctl stop anetbbs-web anetbbs anetbbs-mrc-bridge anetbbs-finger anetbbs-binkp
sudo cp /opt/anetbbs/data/backups/anetbbs-backup-YYYYMMDDHHMMSS/.env.bak /opt/anetbbs/.env
sudo cp /opt/anetbbs/data/backups/anetbbs-backup-YYYYMMDDHHMMSS/anetbbs.db.bak /opt/anetbbs/data/anetbbs.db
sudo systemctl start anetbbs-web anetbbs anetbbs-mrc-bridge anetbbs-finger anetbbs-binkp
```

Only start the optional services (mrc-bridge/finger/binkp) if you were
actually running them before.

## What's safe to skip

- `data/` — uploads, avatars, echomail, personal pages, galleries.
  Excluded from the update's rsync entirely; never touched.
- `.env` — your config and secrets. Existing values are never
  overwritten; only new keys get merged in.
- `venv/` — kept; only `pip install -e .` runs to pick up new/changed
  dependencies.
- `logs/` — excluded from rsync; never touched.
- `doors/` — local door-game trees (not part of the release archive);
  excluded from rsync so any local changes/saves survive.
- The SQLite database(s) — kept; schema is migrated forward only
  (new columns/tables added, nothing dropped).

## Upgrade frequency

Versions ship roughly weekly. Every release adds either:

- A bug fix (always safe to deploy)
- A new feature (additive, no migration of data)
- A schema change (handled automatically in Step 7)

Read the release notes if anything mentions a destructive migration
(rare).
