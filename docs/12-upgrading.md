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
   directory under `/tmp/anetbbs-backup-YYYYMMDDHHMMSS/`. This backup
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

You can also trigger upgrades from **Admin → Upgrades** in the web
interface. The BBS checks for new releases against the configured
registry and offers a one-click install that runs `update.sh` in the
background. Progress is streamed to the browser log viewer.

## Manual rollback

If you need to undo a bad upgrade yourself, the backup directory path
is printed during the update — it looks like
`/tmp/anetbbs-backup-YYYYMMDDHHMMSS/` (note: **`/tmp`, not next to your
install** — and it doesn't survive a reboot, since it's `/tmp`). Find
it with `ls -dt /tmp/anetbbs-backup-* | head -1` if you didn't note it
down. Restore with:

```
sudo systemctl stop anetbbs-web anetbbs anetbbs-mrc-bridge anetbbs-finger anetbbs-binkp
sudo cp /tmp/anetbbs-backup-YYYYMMDDHHMMSS/.env.bak /opt/anetbbs/.env
sudo cp /tmp/anetbbs-backup-YYYYMMDDHHMMSS/anetbbs.db.bak /opt/anetbbs/data/anetbbs.db
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
- `doors/` — vendor door-game trees; excluded from rsync so any local
  changes/saves survive.
- The SQLite database(s) — kept; schema is migrated forward only
  (new columns/tables added, nothing dropped).

## Upgrade frequency

Versions ship roughly weekly. Every release adds either:

- A bug fix (always safe to deploy)
- A new feature (additive, no migration of data)
- A schema change (handled automatically in Step 7)

Read the release notes if anything mentions a destructive migration
(rare).
