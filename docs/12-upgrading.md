# Upgrading

## Recommended path

```
tar xzf ANetBBS-vX.Y.Z.tar.gz
cd ANetBBS-vX.Y.Z
sudo bash update.sh
```

The update script:

1. Detects your install directory from the running `anetbbs-web` service.
2. Snapshots `data/`, `.env`, `logs/` to a timestamped backup
   directory next to your install.
3. Stops `anetbbs-web`, `anetbbs-telnet`, `anetbbs-ssh`,
   `anetbbs-rlogin`, `anetbbs-mrc-bridge`.
4. Rsync's the new code over (preserves `data/`, `.env`, `venv/`,
   `*.db`, `logs/`).
5. `pip install -e .` to pick up new modules.
6. Runs `create_app('production')` which fires
   `db.create_all()` plus `_lightweight_migrate()` to add new
   columns/tables.
7. Restarts services.
8. Probes `/healthz` for 10 seconds.
9. Refreshes `/usr/local/bin/` symlinks.

If the health check fails, the script offers to **roll back** —
restoring `.env` and `data/` from the snapshot, then restarting.

## Auto-update (Admin UI)

You can also trigger upgrades from **Admin → Upgrades** in the web
interface. The BBS checks for new releases against the configured
registry and offers a one-click install that runs `update.sh` in the
background. Progress is streamed to the browser log viewer.

## Manual rollback

If you need to undo a bad upgrade, the backup is left at
`/opt/anetbbs.backup-YYYYMMDD-HHMMSS/` (path printed during update).
Restore with:

```
sudo systemctl stop anetbbs-web anetbbs-telnet anetbbs-ssh anetbbs-rlogin
sudo cp -a /opt/anetbbs.backup-YYYYMMDD-HHMMSS/.env /opt/anetbbs/.env
sudo rsync -a --delete /opt/anetbbs.backup-YYYYMMDD-HHMMSS/data/ /opt/anetbbs/data/
sudo systemctl start anetbbs-web anetbbs-telnet anetbbs-ssh anetbbs-rlogin
```

## What's safe to skip

- `data/` — uploads, avatars, file queue, personal pages. Never overwritten.
- `.env` — your config and secrets. Never overwritten.
- `venv/` — kept; only `pip install -e` runs to pick up new modules.
- `logs/` — kept.
- `*.db` — kept. Schema migrated forward only.

## Upgrade frequency

Versions ship roughly weekly. Every release adds either:

- A bug fix (always safe to deploy)
- A new feature (additive, no migration of data)
- A schema change (handled by `_lightweight_migrate`)

Read the release notes if anything mentions a destructive migration
(rare).
