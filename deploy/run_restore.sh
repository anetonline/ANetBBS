#!/usr/bin/env bash
# Privileged restore helper for ANetBBS pre-update snapshots.
#
# Invoked by the admin "Restore" buttons in /admin/backups/:
#   sudo -n /home/<bbs-user>/anetbbs/deploy/run_restore.sh env  /tmp/anetbbs-backup-YYYYMMDDHHMMSS
#   sudo -n /home/<bbs-user>/anetbbs/deploy/run_restore.sh db   /tmp/anetbbs-backup-YYYYMMDDHHMMSS
#
# Restores `.env` or the SQLite production DB from the snapshot,
# preserving ownership. Refuses to operate on anything outside the
# expected /tmp/anetbbs-backup-* path so a malicious caller can't
# point us at arbitrary disk locations.

set -eu -o pipefail
log()  { printf '[restore] %s\n' "$*"; }
fail() { printf '[restore] FAIL: %s\n' "$*" >&2; exit 1; }

[ $# -eq 2 ] || fail "usage: $0 <env|db> <backup-dir>"
KIND="$1"
BACKUP="$2"

# Path validation.
case "$BACKUP" in
    /tmp/anetbbs-backup-*) ;;
    *) fail "backup path must be /tmp/anetbbs-backup-… (got $BACKUP)" ;;
esac
case "$BACKUP" in
    *..*|*';'*|*'&'*|*'|'*) fail "backup path contains forbidden chars" ;;
esac
[ -d "$BACKUP" ] || fail "not a directory: $BACKUP"

# Resolve install dir from the sentinel update.sh drops.
INSTALL_DIR=""
SERVICE_USER=""
if [ -r /etc/anetbbs.install ]; then
    # shellcheck source=/dev/null
    . /etc/anetbbs.install
fi
[ -n "${INSTALL_DIR:-}" ] || fail "INSTALL_DIR not in /etc/anetbbs.install — re-run update.sh"
[ -n "${SERVICE_USER:-}" ] || SERVICE_USER=anetbbs
log "install dir: $INSTALL_DIR  service user: $SERVICE_USER"

case "$KIND" in
    delete)
        # Wipe a backup dir wholesale. Used by /admin/backups/ when the
        # dir is root-owned (created before update.sh started chowning
        # backups to the service user). Strict path check is already
        # above — only accepts /tmp/anetbbs-backup-*.
        log "rm -rf $BACKUP"
        rm -rf "$BACKUP"
        log "delete: ok"
        ;;
    env)
        SRC="$BACKUP/.env.bak"
        DST="$INSTALL_DIR/.env"
        [ -f "$SRC" ] || fail "no .env.bak in $BACKUP"
        log "copying $SRC -> $DST"
        cp -f "$SRC" "$DST"
        chown "$SERVICE_USER":"$SERVICE_USER" "$DST" 2>/dev/null || true
        chmod 600 "$DST"
        log "restore env: ok"
        ;;
    db)
        SRC="$BACKUP/anetbbs.db.bak"
        DST="$INSTALL_DIR/data/anetbbs.db"
        [ -f "$SRC" ] || fail "no anetbbs.db.bak in $BACKUP"
        # Stop the web service so SQLite write handles aren't dangling.
        log "stopping anetbbs-web before DB swap..."
        systemctl stop anetbbs-web 2>/dev/null || true
        log "copying $SRC -> $DST"
        cp -f "$SRC" "$DST"
        chown "$SERVICE_USER":"$SERVICE_USER" "$DST" 2>/dev/null || true
        chmod 644 "$DST"
        log "starting anetbbs-web back up..."
        systemctl start anetbbs-web 2>/dev/null || true
        log "restore db: ok (anetbbs-web restarted)"
        ;;
    *)
        fail "unknown kind: $KIND (use env or db)"
        ;;
esac
