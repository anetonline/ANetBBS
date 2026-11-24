#!/usr/bin/env bash
# Privileged restore helper for ANetBBS pre-update snapshots.
#
# Invoked by the admin "Restore" buttons in /admin/backups/:
#   sudo -n /usr/local/libexec/anetbbs/run_restore.sh env    INSTALL_DIR/data/backups/anetbbs-backup-YYYYMMDDHHMMSS
#   sudo -n /usr/local/libexec/anetbbs/run_restore.sh db     INSTALL_DIR/data/backups/anetbbs-backup-YYYYMMDDHHMMSS
#   sudo -n /usr/local/libexec/anetbbs/run_restore.sh delete INSTALL_DIR/data/backups/anetbbs-backup-YYYYMMDDHHMMSS
#
# Installed root:root 0755 in a root-owned directory (audit H-1). Every
# file operation below runs AS THE SERVICE USER: the backups, .env and
# the database all belong to that user anyway, and doing the copy/delete
# as root on paths that user controls would let a swapped-in symlink
# redirect root's rm/cp. Only `systemctl` needs root. Backups left
# root-owned by very old releases can no longer be restored or deleted
# from the web UI; remove those by hand as root.
#
# Restores `.env` or the SQLite production DB from the snapshot,
# preserving ownership. Refuses to operate on anything outside the
# expected INSTALL_DIR/data/backups/anetbbs-backup-* path so a
# malicious caller can't point us at arbitrary disk locations.
#
# Backups moved here from /tmp after a real incident: on a Pi where
# /tmp is a RAM-backed tmpfs (separate from, and much smaller than,
# the actual disk), backups routinely couldn't fit even with the disk
# itself nowhere near full. INSTALL_DIR/data/ is real, persistent disk.

set -eu -o pipefail
log()  { printf '[restore] %s\n' "$*"; }
fail() { printf '[restore] FAIL: %s\n' "$*" >&2; exit 1; }

[ $# -eq 2 ] || fail "usage: $0 <env|db> <backup-dir>"
KIND="$1"
BACKUP="$2"

# Resolve install dir from the sentinel update.sh drops. Must happen
# BEFORE path validation below, since the allowlisted prefix is now
# anchored to INSTALL_DIR rather than a fixed literal like /tmp was.
CONF=/etc/anetbbs.install
[ -f "$CONF" ] && [ ! -L "$CONF" ] || fail "$CONF missing -- re-run update.sh as root"
[ "$(stat -c %u "$CONF")" = 0 ] || fail "$CONF is not owned by root"
[ $(( 0$(stat -c %a "$CONF") & 022 )) -eq 0 ] || fail "$CONF is group/world writable"
_conf() { grep -m1 "^$1=" "$CONF" | cut -d= -f2- | tr -d '"'"'"; }
INSTALL_DIR=$(_conf INSTALL_DIR)
SERVICE_USER=$(_conf SERVICE_USER)
[[ "$INSTALL_DIR" =~ ^/[A-Za-z0-9._/-]+$ ]] || fail "INSTALL_DIR not in $CONF — re-run update.sh"
[[ "${SERVICE_USER:-}" =~ ^[a-z_][a-z0-9_-]*$ ]] || fail "SERVICE_USER missing or invalid in $CONF"
[ "$SERVICE_USER" != root ] || fail "SERVICE_USER may not be root"
as_service() {
    if command -v runuser >/dev/null 2>&1; then
        runuser -u "$SERVICE_USER" -- "$@"
    else
        sudo -n -u "$SERVICE_USER" -- "$@"
    fi
}
log "install dir: $INSTALL_DIR  service user: $SERVICE_USER"

# Path validation. INSTALL_DIR itself comes from a root-written sentinel
# file, not caller input, so anchoring the allowlist to it doesn't
# weaken this check -- a malicious BACKUP argument still can't point
# outside INSTALL_DIR/data/backups/ no matter what INSTALL_DIR resolves to.
case "$BACKUP" in
    "$INSTALL_DIR"/data/backups/anetbbs-backup-*) ;;
    *) fail "backup path must be $INSTALL_DIR/data/backups/anetbbs-backup-… (got $BACKUP)" ;;
esac
case "$BACKUP" in
    *..*|*';'*|*'&'*|*'|'*) fail "backup path contains forbidden chars" ;;
esac
[ -d "$BACKUP" ] || fail "not a directory: $BACKUP"

# Real gap found in a security/performance audit: SERVICE_USER owns
# every file/dir under INSTALL_DIR (both install.sh and update.sh
# chown -R the whole tree to it), including this backups directory and
# the live .env/DB this script goes on to overwrite -- but this script
# itself runs as ROOT via a passwordless sudoers grant
# (deploy/sudoers.anetbbs). The string-pattern check above only
# validates the literal path text, not that it actually resolves
# inside the trusted tree -- SERVICE_USER could replace any path
# component (e.g. the whole backup dir, or a subdirectory inside it)
# with a symlink pointing elsewhere and the pattern match would still
# pass, since it never touches the filesystem. Resolving here and
# re-checking closes that: a directory-level symlink trick now fails
# this check outright instead of silently being followed once root
# starts reading/writing through it below.
REAL_BACKUPS_ROOT="$(realpath "$INSTALL_DIR/data/backups" 2>/dev/null)" \
    || fail "cannot resolve $INSTALL_DIR/data/backups"
REAL_BACKUP="$(realpath "$BACKUP" 2>/dev/null)" \
    || fail "cannot resolve backup path: $BACKUP"
case "$REAL_BACKUP" in
    "$REAL_BACKUPS_ROOT"/*) ;;
    *) fail "resolved backup path escapes the trusted backups directory: $REAL_BACKUP" ;;
esac
BACKUP="$REAL_BACKUP"

case "$KIND" in
    delete)
        # Wipe a backup dir wholesale. Used by /admin/backups/ when the
        # dir is root-owned (created before update.sh started chowning
        # backups to the service user). Strict path check is already
        # above — only accepts INSTALL_DIR/data/backups/anetbbs-backup-*.
        log "rm -rf $BACKUP (as $SERVICE_USER)"
        as_service rm -rf -- "$BACKUP" \
            || fail "could not delete $BACKUP as $SERVICE_USER (root-owned backup from an old release? remove it by hand as root)"
        log "delete: ok"
        ;;
    env)
        SRC="$BACKUP/.env.bak"
        DST="$INSTALL_DIR/.env"
        # Real gap found in a security/performance audit: neither a
        # real backup's .env.bak nor the live .env is EVER legitimately
        # a symlink -- both are written directly by cp/heredocs in
        # install.sh/update.sh. SERVICE_USER owns both paths, so
        # without this check they could replace .env.bak with a
        # symlink to an arbitrary file (e.g. /etc/shadow) to have root
        # read it via the cp below, or replace the live .env with a
        # symlink to a root-writable file (e.g. a sudoers drop-in) to
        # have root overwrite it -- a full SERVICE_USER -> root file
        # read/write primitive via this passwordless-sudo script.
        [ -L "$SRC" ] && fail "refusing: $SRC is a symlink"
        [ -L "$DST" ] && fail "refusing: $DST is a symlink"
        [ -f "$SRC" ] || fail "no .env.bak in $BACKUP"
        log "copying $SRC -> $DST"
        # --remove-destination: unlink() the destination PATH itself
        # (never follows a symlink) before writing, so even a symlink
        # planted between the check above and this line gets removed
        # rather than written through -- defense in depth against the
        # same class of race, not just the already-checked case.
        as_service cp -f --remove-destination -- "$SRC" "$DST" \
            || fail "copy failed as $SERVICE_USER"
        as_service chmod 600 -- "$DST"
        log "restore env: ok"
        ;;
    db)
        SRC="$BACKUP/anetbbs.db.bak"
        DST="$INSTALL_DIR/data/anetbbs.db"
        # Same reasoning as the env case above.
        [ -L "$SRC" ] && fail "refusing: $SRC is a symlink"
        [ -L "$DST" ] && fail "refusing: $DST is a symlink"
        [ -f "$SRC" ] || fail "no anetbbs.db.bak in $BACKUP"
        # Stop the web service so SQLite write handles aren't dangling.
        log "stopping anetbbs-web before DB swap..."
        systemctl stop anetbbs-web 2>/dev/null || true
        log "copying $SRC -> $DST"
        if ! as_service cp -f --remove-destination -- "$SRC" "$DST"; then
            systemctl start anetbbs-web 2>/dev/null || true
            fail "copy failed as $SERVICE_USER"
        fi
        # Real gap found in a security/performance audit: the DB holds
        # password hashes, session tokens, and private messages -- see
        # install.sh's matching fix on the fresh-install path for why
        # 600 (not 644) is correct here.
        as_service chmod 600 -- "$DST"
        log "starting anetbbs-web back up..."
        systemctl start anetbbs-web 2>/dev/null || true
        log "restore db: ok (anetbbs-web restarted)"
        ;;
    *)
        fail "unknown kind: $KIND (use env or db)"
        ;;
esac
