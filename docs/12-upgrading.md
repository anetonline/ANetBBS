# Upgrading

## Recommended path

```
sudo anetbbs-upgrade /tmp/anetbbs-rebuilt-vXX.tar.gz
```

The wizard:

1. Extracts the tarball to a temp dir.
2. Asks "have you taken your own backup?" — answers yes to continue.
3. Snapshots `data/`, `.env`, `logs/` to a timestamped backup
   directory next to your install.
4. Stops `anetbbs-web`, `anetbbs-telnet`, `anetbbs-ssh`,
   `anetbbs-rlogin`, `anetbbs-mrc-bridge`.
5. Rsync's the new code over (preserves `data/`, `.env`, `venv/`,
   `*.db`, `logs/`).
6. `pip install -e .` to pick up new modules.
7. Runs `create_app('production')` which fires
   `db.create_all()` plus `_lightweight_migrate()` to add new
   columns/tables.
8. Restarts services.
9. Probes `/healthz` for 10 seconds.
10. Refreshes `/usr/local/bin/` symlinks.

If the health check fails, the wizard offers to **roll back** —
restoring `.env` and `data/` from the snapshot, then restarting.

## Manual rollback

If you need to undo a bad upgrade later, the wizard prints the exact
commands:

```
sudo systemctl stop anetbbs-web anetbbs-telnet anetbbs-ssh anetbbs-rlogin
sudo cp -a /home/stingray/anetbbs.backup-YYYYMMDD-HHMMSS/.env /home/stingray/anetbbs/.env
sudo rsync -a --delete /home/stingray/anetbbs.backup-YYYYMMDD-HHMMSS/data/ /home/stingray/anetbbs/data/
sudo systemctl start anetbbs-web anetbbs-telnet anetbbs-ssh anetbbs-rlogin
```

## Manual upgrade fallback

If the wizard breaks for any reason:

```
sudo systemctl stop anetbbs-web anetbbs-telnet anetbbs-ssh anetbbs-rlogin
sudo pkill -9 -f gunicorn
sleep 2
cd /tmp && rm -rf anetbbs-vNNN
tar xzf anetbbs-rebuilt-vNNN.tar.gz
mv anetbbs-rebuilt anetbbs-vNNN
sudo rsync -a \
    --exclude='data/' --exclude='.env' --exclude='venv/' \
    --exclude='*.db' --exclude='logs/' \
    /tmp/anetbbs-vNNN/ /home/stingray/anetbbs/
sudo -u stingray /home/stingray/anetbbs/venv/bin/pip install -e /home/stingray/anetbbs --quiet
sudo systemctl start anetbbs-web anetbbs-telnet anetbbs-ssh anetbbs-rlogin
```

## What's safe to skip

- `data/` — your uploads, avatars, file queue, personal pages.
  Never overwritten by an upgrade.
- `.env` — your config + secrets. Never overwritten.
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
