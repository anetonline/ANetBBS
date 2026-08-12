#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# ANetBBS — Safe Update Script
# https://github.com/anetonline/anetbbs
#
# Usage:
#   sudo bash update.sh                        # Auto-detect install dir
#   sudo bash update.sh --install-dir /opt/anetbbs
# ═══════════════════════════════════════════════════════════════════════════════

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'
BOLD='\033[1m'; DIM='\033[2m'; NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
fail()  { echo -e "${RED}[FAIL]${NC}  $*"; }
step()  { echo -e "\n${CYAN}${BOLD}── $* ──${NC}"; }
ok()    { echo -e "  ${GREEN}✅ $*${NC}"; }
skip()  { echo -e "  ${YELLOW}⏭  $*${NC}"; }
bad()   { echo -e "  ${RED}❌ $*${NC}"; }

# ─── Root check ────────────────────────────────────────────────────────────────
if [[ "$EUID" -ne 0 ]]; then
    fail "This script must be run as root."
    echo "  Run: sudo bash update.sh"
    exit 1
fi

# ─── Systemd check ─────────────────────────────────────────────────────────────
# This script restarts/patches ANetBBS's systemd units throughout. Without
# systemctl, those calls would fail piecemeal with "command not found"
# scattered across 8 steps rather than one clear signal up front.
if ! command -v systemctl >/dev/null 2>&1; then
    fail "systemctl not found — ANetBBS's install/update scripts require systemd."
    echo "  This system (or container) doesn't appear to run systemd."
    echo "  See docker/ for a container-based deployment that doesn't need it."
    exit 1
fi

# ─── Source directory (where update.sh lives) ──────────────────────────────────
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ─── Parse arguments ──────────────────────────────────────────────────────────
INSTALL_DIR=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --install-dir)
            INSTALL_DIR="$2"; shift 2 ;;
        *)
            fail "Unknown argument: $1"
            echo "Usage: sudo bash update.sh [--install-dir /opt/anetbbs]"
            exit 1 ;;
    esac
done

# ─── Step 1: Detect existing installation ──────────────────────────────────────
step "Step 1/8: Detecting existing installation"

if [[ -z "$INSTALL_DIR" ]]; then
    # Try to find from service file
    if [[ -f /etc/systemd/system/anetbbs-web.service ]]; then
        INSTALL_DIR=$(grep -oP 'WorkingDirectory=\K.*' /etc/systemd/system/anetbbs-web.service | head -1)
        info "Detected install dir from service: $INSTALL_DIR"
    else
        INSTALL_DIR="/opt/anetbbs"
        info "Using default install dir: $INSTALL_DIR"
    fi
fi

if [[ ! -d "$INSTALL_DIR" ]]; then
    fail "Install directory not found: $INSTALL_DIR"
    echo "  Run install.sh first, or specify --install-dir"
    exit 1
fi

ENV_FILE="$INSTALL_DIR/.env"
if [[ ! -f "$ENV_FILE" ]]; then
    fail "No .env found at $ENV_FILE — cannot update a non-existent installation."
    exit 1
fi

# Snapshot which optional services are CURRENTLY active before we stop anything.
# update.sh only restarts optional services (mrc-bridge, finger, binkp) if
# they were running before the update — this prevents force-starting a
# service that the sysop had stopped.
#
# SERVICES_PREVIOUSLY_EXISTED (separate from _ACTIVE above) records
# whether the unit *file* was present at all, active or not, before this
# run touches anything. This exists to fix a real bug: a unit that gets
# freshly created later in THIS SAME RUN (e.g. a sysop upgrading from a
# version that never wrote anetbbs-binkp.service) can never appear in
# SERVICES_PREVIOUSLY_ACTIVE — it didn't exist yet to test is-active
# against — so it was written to disk but never enabled/started, staying
# dormant until someone started it by hand. See the restart-decision
# logic near the end of this script for the actual fix.
SERVICES_PREVIOUSLY_ACTIVE=()
SERVICES_PREVIOUSLY_EXISTED=()
for svc in anetbbs-mrc-bridge anetbbs-finger anetbbs-binkp; do
    if [[ -f "/etc/systemd/system/${svc}.service" ]]; then
        SERVICES_PREVIOUSLY_EXISTED+=("$svc")
        if systemctl is-active --quiet "$svc" 2>/dev/null; then
            SERVICES_PREVIOUSLY_ACTIVE+=("$svc")
        fi
    fi
done

# Read existing .env into associative array
declare -A EXISTING_ENV
while IFS='=' read -r key value; do
    [[ "$key" =~ ^#.*$ || -z "$key" ]] && continue
    EXISTING_ENV["$key"]="$value"
done < <(grep -v '^\s*#' "$ENV_FILE" | grep '=')

# Detect the user under which the BBS actually runs. Prefer the running
# systemd unit's User=, which is authoritative once a service has been
# installed. Fall back to the install-directory owner only when no unit
# exists yet (fresh install scenario).
#
# Why: the old `stat -c '%U' "$INSTALL_DIR"` logic returned the OWNER of
# the install dir, which is fine on a stock install (install.sh chowns
# the tree to the service user). But many installs sit under a sysop
# home directory like /home/sysopname/anetbbs, so the dir owner is the
# human "sysopname" while the actual service user is "anetbbs". Using
# dir-owner would then write the systemd unit with `User=sysopname`
# AND `chown -R sysopname:sysopname data/` — which fights the live
# service every update, breaking writes from doors and the web app.
SERVICE_USER=""
if command -v systemctl >/dev/null 2>&1; then
    SERVICE_USER=$(systemctl show anetbbs-web -p User --value 2>/dev/null)
    # systemctl prints empty when the unit doesn't exist, but also
    # prints empty when User= is not set (defaults to root). Normalize.
    if [ "$SERVICE_USER" = "[not set]" ] || [ "$SERVICE_USER" = "root" ]; then
        SERVICE_USER=""
    fi
fi
if [ -z "$SERVICE_USER" ]; then
    SERVICE_USER=$(stat -c '%U' "$INSTALL_DIR" 2>/dev/null || echo "anetbbs")
fi
VENV_DIR="$INSTALL_DIR/venv"

ok "Installation found at $INSTALL_DIR (user: $SERVICE_USER)"

# ─── Step 2: Pre-update backup ────────────────────────────────────────────────
step "Step 2/8: Creating pre-update backup"

# Disk-space pre-flight check. Real-world incident: a sysop's disk hit
# zero mid-update and corrupted the install beyond recovery -- and the
# backup this step is about to create couldn't have protected against
# that anyway, since nothing checked available space before starting.
MIN_FREE_KB=512000  # 500MB
_check_free_space() {
    local path="$1" label="$2"
    local avail_kb
    avail_kb=$(df -Pk "$path" 2>/dev/null | tail -1 | awk '{print $4}')
    if [[ -z "$avail_kb" ]]; then
        warn "Could not determine free space on $label ($path) — proceeding without this check"
        return 0
    fi
    if (( avail_kb < MIN_FREE_KB )); then
        fail "Only $((avail_kb / 1024))MB free on $label ($path) — need at least $((MIN_FREE_KB / 1024))MB."
        echo "  Free up space first (check Admin → Backups for old snapshots to delete,"
        echo "  or clear old logs/uploads), then re-run update.sh. Refusing to proceed:"
        echo "  running out of space mid-update can corrupt the install past recovery."
        exit 1
    fi
    ok "$label: $((avail_kb / 1024))MB free"
}
_check_free_space "$INSTALL_DIR" "install filesystem"

# Backups live under INSTALL_DIR/data/backups/, NOT /tmp. Real-world
# incident: on a Pi where /tmp is a RAM-backed tmpfs (separate from,
# and much smaller than, the actual disk), the disk-space check above
# would pass fine (108GB free on the real filesystem) while backups
# still couldn't fit in /tmp's much smaller RAM allocation -- update.sh
# refused to proceed despite the disk itself being nowhere near full.
# INSTALL_DIR/data/ is already excluded from the file-sync step (Step 4
# below, --exclude='/data/'), so it's safe from being overwritten, and
# it's on the same filesystem the disk-space check above just verified
# has room. It also survives a reboot, unlike /tmp on distros that
# clear it at boot.
BACKUP_DIR="$INSTALL_DIR/data/backups/anetbbs-backup-$(date +%Y%m%d%H%M%S)"
# mkdir -p alone leaves this world-readable (default umask) until the
# chmod 0700 that used to run at the very end of this step -- a window
# during which .env.bak (SECRET_KEY, DB creds) sat readable by any local
# user while the rest of the backup (DB, code snapshot, units) was still
# being written. Lock it down at creation instead of after the fact.
mkdir -p -m 0700 "$BACKUP_DIR"
chmod 0700 "$BACKUP_DIR"

# Every backup step below is now checked -- a failure here (near-certainly
# meaning the disk filled up despite the check above, e.g. a concurrent
# process ate the space between the check and now) aborts the update
# rather than silently proceeding with no real safety net. Previously the
# .env backup in particular printed "Backed up .env" unconditionally, even
# if the cp itself had failed.
if cp "$ENV_FILE" "$BACKUP_DIR/.env.bak"; then
    ok "Backed up .env"
else
    fail "Could not back up .env to $BACKUP_DIR — aborting before touching anything live."
    exit 1
fi

# Database backup — use sqlite3's `.backup` rather than cp(1). The
# services aren't stopped yet (that's Step 3) so the DB might be
# mid-write; cp can produce a torn snapshot that fails to open with
# "database disk image is malformed", while .backup uses the WAL to
# produce a consistent snapshot under concurrent writes. Fall back to
# cp only if sqlite3(1) isn't installed.
#
# Returns 0 if the source doesn't exist (nothing to back up, not a
# failure) or the backup succeeded; 1 only when the source file exists
# but every backup method failed -- the caller aborts on that, since a
# DB that exists but couldn't be backed up means no real rollback
# point exists for it. Doesn't try to guess which of anetbbs.db /
# anetbbs_dev.db is the "real" one in use (some installs run against
# either via DATABASE_URL) -- both are treated as equally critical.
backup_sqlite() {
    local src="$1" dst="$2" label="$3"
    [[ -f "$src" ]] || return 0
    if command -v sqlite3 >/dev/null 2>&1; then
        if sqlite3 "$src" ".backup '$dst'" 2>/dev/null; then
            ok "Backed up $label (sqlite3 .backup, $(du -h "$dst" | cut -f1))"
            return 0
        fi
        warn "sqlite3 .backup of $label failed — falling back to cp"
    fi
    if cp "$src" "$dst"; then
        ok "Backed up $label (cp)"
        return 0
    fi
    return 1
}
if ! backup_sqlite "$INSTALL_DIR/data/anetbbs.db" "$BACKUP_DIR/anetbbs.db.bak" "production DB"; then
    fail "Could not back up data/anetbbs.db — aborting before touching anything live."
    exit 1
fi
if ! backup_sqlite "$INSTALL_DIR/data/anetbbs_dev.db" "$BACKUP_DIR/anetbbs_dev.db.bak" "dev DB"; then
    fail "Could not back up data/anetbbs_dev.db — aborting before touching anything live."
    exit 1
fi

DB_FILE="$INSTALL_DIR/data/anetbbs.db"  # kept for the failure-rollback block at end-of-script

for svc in anetbbs-web anetbbs anetbbs-telnet anetbbs-ssh anetbbs-mrc-bridge anetbbs-finger anetbbs-binkp; do
    [[ -f "/etc/systemd/system/${svc}.service" ]] && \
        cp "/etc/systemd/system/${svc}.service" "$BACKUP_DIR/${svc}.service.bak"
done
ok "Backed up systemd service files"

NGINX_AVAIL="/etc/nginx/sites-available/anetbbs"
[[ -f "$NGINX_AVAIL" ]] && cp "$NGINX_AVAIL" "$BACKUP_DIR/anetbbs-nginx.bak"

# Snapshot the current application code so a failed update can be fully
# rolled back, not just its .env/DB/systemd units. Previously "rollback"
# only restored config and left whatever broken code the failed update
# had already written in place -- restarting services just failed again
# against the same broken code. Mirrors Step 4's rsync excludes (the
# user data/config Step 4 never touches doesn't need backing up here
# either), plus runtime-only artifacts (*.log, __pycache__, egg-info,
# pytest cache) that would just waste backup space/time -- excluding
# those keeps this snapshot ~28MB rather than pulling in bbs.log, which
# can grow into the GBs on a long-running install.
CODE_BACKUP_DIR="$BACKUP_DIR/code"
mkdir -p "$CODE_BACKUP_DIR"
info "Snapshotting current application code for rollback..."
if rsync -a \
    --exclude='.git' --exclude='venv' --exclude='__pycache__' --exclude='*.pyc' \
    --exclude='*.egg-info' --exclude='.pytest_cache' \
    --exclude='*.log' --exclude='bbs.log*' \
    --exclude='/.env' --exclude='/data/' --exclude='/logs/' --exclude='/doors/' \
    --exclude='/gallery-config.json' --exclude='/mrc/bridge/config.json' \
    --exclude='/mrc/bridge/config/' --exclude='/mrc/bridge/data/' \
    --exclude='/mrc/bridge/data-*/' --exclude='/mrc/bridge/logs/' \
    --exclude='/mrc/bridge/*.db' --exclude='/mrc/bridge/*.db-shm' --exclude='/mrc/bridge/*.db-wal' \
    "$INSTALL_DIR/" "$CODE_BACKUP_DIR/"; then
    ok "Code snapshot stored ($(du -sh "$CODE_BACKUP_DIR" 2>/dev/null | cut -f1))"
else
    fail "Could not snapshot application code — aborting before touching anything live."
    exit 1
fi

# Manifest so rollback can identify what's inside without guessing.
OLD_VERSION=$(cat "$INSTALL_DIR/VERSION" 2>/dev/null || echo unknown)
NEW_VERSION=$(cat "$SOURCE_DIR/VERSION" 2>/dev/null || echo unknown)
cat > "$BACKUP_DIR/MANIFEST" <<MEOF
created_at = $(date -u +%Y-%m-%dT%H:%M:%SZ)
from_version = $OLD_VERSION
to_version = $NEW_VERSION
service_user = $SERVICE_USER
install_dir = $INSTALL_DIR
MEOF
# Backup dir + everything inside is owned by root at this point (we
# ran via sudo). The /admin/backups/ UI runs as the service user and
# needs to be able to delete these without invoking a helper. Chown
# the whole tree so plain os.unlink() / shutil.rmtree() from gunicorn
# works.
chown -R "$SERVICE_USER":"$SERVICE_USER" "$BACKUP_DIR" 2>/dev/null || true
chmod 0700 "$BACKUP_DIR" 2>/dev/null || true

ok "Backup stored at $BACKUP_DIR (was $OLD_VERSION → $NEW_VERSION)"

# Prune old backups -- keep the 3 most recent (including the one just
# created). Real-world incident: backups were never auto-deleted by
# design ("we don't trust ourselves to GC them automatically"), and a
# sysop who wasn't watching accumulated a dozen of them, silently
# eating disk space until an update ran the filesystem to zero and
# corrupted the install. Unbounded accumulation is a worse default than
# bounded retention -- 3 covers "I need to roll back one or two
# versions" without growing forever. Sorted by name, which sorts
# chronologically since the timestamp format is YYYYMMDDHHMMSS.
mapfile -t _old_backups < <(ls -1d "$INSTALL_DIR/data/backups/anetbbs-backup-"* 2>/dev/null | sort | head -n -3)
for _old in "${_old_backups[@]:-}"; do
    [[ -n "$_old" && -d "$_old" ]] || continue
    rm -rf "$_old" && info "Pruned old backup: $_old"
done

# ─── Step 3: Stop services ─────────────────────────────────────────────────────
step "Step 3/8: Stopping services"

# Only stop units that actually exist on this host. The legacy split
# anetbbs-telnet / anetbbs-ssh services were merged into anetbbs.service
# during v1.0a2.10; iterating over them on a current install just prints
# "was not running" lines that look like errors in screenshots.
for svc in anetbbs-web anetbbs anetbbs-telnet anetbbs-ssh anetbbs-mrc-bridge anetbbs-finger anetbbs-binkp; do
    if [[ ! -f "/etc/systemd/system/${svc}.service" ]]; then
        continue
    fi
    if systemctl is-active --quiet "$svc" 2>/dev/null; then
        systemctl stop "$svc" 2>/dev/null && ok "Stopped $svc" || warn "Could not stop $svc"
    else
        skip "$svc was not running"
    fi
done
sleep 2

# Migration: nuke the legacy split anetbbs-telnet + anetbbs-ssh services.
# They've been replaced by a single anetbbs.service. If the install still
# has the old units enabled they'll fight each other for ports on next
# boot. Remove them here so the auto-install block below can write the
# new unified unit cleanly.
for legacy in anetbbs-telnet anetbbs-ssh; do
    if [[ -f "/etc/systemd/system/${legacy}.service" ]]; then
        systemctl disable "$legacy" 2>/dev/null || true
        rm -f "/etc/systemd/system/${legacy}.service"
        ok "Removed legacy $legacy.service (replaced by unified anetbbs.service)"
    fi
done
systemctl daemon-reload 2>/dev/null || true

# ─── Step 4: Update application files ─────────────────────────────────────────
step "Step 4/8: Updating application files"

# Remove legacy paths from the install dir BEFORE syncing the new tree.
# The cleanup-rebuild dropped: top-level core/, features/, services/, main.py,
# mrc_client.*, mrc_config.py, the anetbbs/core + anetbbs/features symlinks,
# and the dynamic-shim anetbbs/main.py written by the original install.sh.
# If we leave any of those behind, Python may still import the old copies.
info "Removing legacy paths from previous install (if present)..."
LEGACY_PATHS=(
    "$INSTALL_DIR/core"
    "$INSTALL_DIR/features"
    "$INSTALL_DIR/services"
    "$INSTALL_DIR/main.py"
    "$INSTALL_DIR/mrc_client.py"
    "$INSTALL_DIR/mrc_client.mps"
    "$INSTALL_DIR/mrc_config.py"
    "$INSTALL_DIR/install"
    "$INSTALL_DIR/__init__.py"
    "$INSTALL_DIR/directory_structure.txt"
)
for p in "${LEGACY_PATHS[@]}"; do
    if [[ -e "$p" || -L "$p" ]]; then
        rm -rf "$p" && info "  removed $p"
    fi
done
# anetbbs/core and anetbbs/features were symlinks in the broken install — replace with real dirs
for p in "$INSTALL_DIR/anetbbs/core" "$INSTALL_DIR/anetbbs/features"; do
    if [[ -L "$p" ]]; then
        rm -f "$p" && info "  removed symlink $p"
    fi
done
# The old anetbbs/main.py was a shim that re-imported the top-level main.py via importlib.
# Detect it by signature and remove so the new real file from the source can take its place.
if [[ -f "$INSTALL_DIR/anetbbs/main.py" ]] && \
   grep -q "Entry point shim for the 'anetbbs' console_scripts" "$INSTALL_DIR/anetbbs/main.py" 2>/dev/null; then
    rm -f "$INSTALL_DIR/anetbbs/main.py" && info "  removed legacy main.py shim"
fi
ok "Legacy paths cleared"

# ── Safety: refuse to run if the source dir contains live user data ──────────
# Only applies when SOURCE_DIR != INSTALL_DIR (i.e. rsync path).  When
# update.sh is run from the install dir itself (SOURCE_DIR == INSTALL_DIR,
# e.g. after extracting a tarball directly into the live tree), data/ is
# supposed to be there — checking for it would always fire a false alarm.
#
# A raw "rsync --delete" from a source that includes data/anetbbs.db will
# silently wipe the live database.  update.sh's rsync excludes /data/, but if
# someone bypasses this script that protection is gone.  Guard here so that
# even running update.sh from a polluted extract can't do damage:
#   • If source data/anetbbs.db is larger than 500 KB it is almost certainly a
#     live or dev database (the seeded-only DB is ~100 KB).  Abort.
#   • If source data/uploads/ or data/avatars/ exist they are user-uploaded
#     files that must never be rsync'd over the install.  Abort.
if [[ "$SOURCE_DIR" != "$INSTALL_DIR" ]]; then
for _danger in \
        "$SOURCE_DIR/data/anetbbs_dev.db" \
        "$SOURCE_DIR/data/uploads" \
        "$SOURCE_DIR/data/avatars" \
        "$SOURCE_DIR/data/echomail" \
        "$SOURCE_DIR/data/personal_pages" \
        "$SOURCE_DIR/data/ftp_root"; do
    if [[ -e "$_danger" ]]; then
        echo ""
        echo "  ╔══════════════════════════════════════════════════════════════╗"
        echo "  ║  SAFETY ABORT: source dir contains live user data            ║"
        echo "  ║                                                              ║"
        echo "  ║  Found: $_danger"
        echo "  ║                                                              ║"
        echo "  ║  The release tarball should never include data/, uploads,    ║"
        echo "  ║  or user databases.  Rebuild the tarball with the correct    ║"
        echo "  ║  build command (see docs/DEPLOY.md) then re-run update.sh.  ║"
        echo "  ╚══════════════════════════════════════════════════════════════╝"
        echo ""
        exit 1
    fi
done
_src_db="$SOURCE_DIR/data/anetbbs.db"
if [[ -f "$_src_db" ]]; then
    _src_db_kb=$(du -k "$_src_db" 2>/dev/null | cut -f1)
    if [[ "${_src_db_kb:-0}" -gt 500 ]]; then
        echo ""
        echo "  ╔══════════════════════════════════════════════════════════════╗"
        echo "  ║  SAFETY ABORT: source data/anetbbs.db looks like a live DB  ║"
        echo "  ║  (${_src_db_kb} KB > 500 KB threshold)                      ║"
        echo "  ║                                                              ║"
        echo "  ║  The release tarball must not include the production DB.     ║"
        echo "  ║  Rebuild with the correct tarball build command.             ║"
        echo "  ╚══════════════════════════════════════════════════════════════╝"
        echo ""
        exit 1
    fi
fi
fi  # SOURCE_DIR != INSTALL_DIR

if [[ "$SOURCE_DIR" == "$INSTALL_DIR" ]]; then
    # Running from the install dir — try git pull
    if [[ -d "$INSTALL_DIR/.git" ]]; then
        info "Updating via git pull..."
        cd "$INSTALL_DIR"
        if sudo -u "$SERVICE_USER" git pull --ff-only 2>/dev/null || git pull --ff-only 2>/dev/null; then
            ok "git pull succeeded"
        else
            warn "git pull failed — files may already be up to date or have local changes"
        fi
    else
        skip "No .git directory; files are already in place"
    fi
else
    # Running from a different source dir — rsync, preserving user data and configs.
    # Excludes:
    #   .env, data/, logs/                  — user data / state
    #   /doors/                             — sysop's customized door installs
    #                                         (DSR, BotWars, RDQ3, GIF library, etc.)
    #                                         NEVER overwrite — the v195 incident
    #                                         wiped a production install via this path
    #   /gallery-config.json                — sysop's gallery list (auto-seeded
    #                                         on first run; never re-overwrite)
    #   mrc/bridge/config.json              — primary user MRC config
    #   mrc/bridge/config/                  — multi-network configs
    #   mrc/bridge/data*/                   — per-network state dirs
    #   mrc/bridge/logs/                    — bridge runtime logs
    #   mrc/bridge/*.db / *.db-shm / *.db-wal — bridge sqlite state
    info "Syncing files from $SOURCE_DIR to $INSTALL_DIR ..."
    rsync -a \
        --exclude='.git' \
        --exclude='venv' \
        --exclude='__pycache__' \
        --exclude='*.pyc' \
        --exclude='/.env' \
        --exclude='/data/' \
        --exclude='/logs/' \
        --exclude='/doors/' \
        --exclude='/gallery-config.json' \
        --exclude='/mrc/bridge/config.json' \
        --exclude='/mrc/bridge/config/' \
        --exclude='/mrc/bridge/data/' \
        --exclude='/mrc/bridge/data-*/' \
        --exclude='/mrc/bridge/logs/' \
        --exclude='/mrc/bridge/*.db' \
        --exclude='/mrc/bridge/*.db-shm' \
        --exclude='/mrc/bridge/*.db-wal' \
        "$SOURCE_DIR/" "$INSTALL_DIR/"
    ok "Files synced (user data + configs preserved)"

    # Sync bundled door subdirectories from the release tarball.
    # /doors/ is excluded from the main rsync above to protect sysop-customized
    # installs (DSR, BotWars, etc.) from being overwritten.  For doors that ARE
    # bundled in the release (currently only anetirc), we do a file-by-file
    # comparison and update anything that changed — so new binaries, build
    # scripts, and config templates always arrive on update.
    #
    # Binary arch-mismatch (Pi running ARM, tarball ships x86-64): the binary
    # is skipped and a recompile message is printed; build.sh is still updated.
    if [[ -d "$SOURCE_DIR/doors" ]]; then
        mkdir -p "$INSTALL_DIR/doors"
        for src_door in "$SOURCE_DIR/doors"/*/; do
            [[ -d "$src_door" ]] || continue
            door_name=$(basename "$src_door")
            dst_door="$INSTALL_DIR/doors/$door_name"

            if [[ ! -d "$dst_door" ]]; then
                # Brand-new door — install everything.
                cp -a "$src_door" "$dst_door"
                ok "Installed new door: doors/$door_name"
                find "$dst_door" -maxdepth 1 -type f | while IFS= read -r f; do
                    file "$f" 2>/dev/null | grep -qiE 'ELF|executable' && chmod 755 "$f" 2>/dev/null || true
                done
                find "$dst_door" -maxdepth 2 -name "*.sh" -exec chmod 755 {} \; 2>/dev/null || true
            else
                # Door already installed — check each bundled file for changes.
                door_changed=0
                needs_rebuild=0
                while IFS= read -r src_file; do
                    [[ -f "$src_file" ]] || continue
                    fname=$(basename "$src_file")
                    dst_file="$dst_door/$fname"

                    # Skip files that are identical to what's installed.
                    [[ -f "$dst_file" ]] && cmp -s "$src_file" "$dst_file" && continue

                    if file "$src_file" 2>/dev/null | grep -qiE 'ELF|Mach-O|PE32'; then
                        # Binary file — only overwrite if arch matches.
                        src_arch=$(file "$src_file" 2>/dev/null | grep -oE 'x86-64|aarch64|ARM|i386' | head -1)
                        if [[ -f "$dst_file" ]]; then
                            dst_arch=$(file "$dst_file" 2>/dev/null | grep -oE 'x86-64|aarch64|ARM|i386' | head -1)
                        else
                            dst_arch="$src_arch"
                        fi
                        if [[ -z "$src_arch" || "$src_arch" == "$dst_arch" ]]; then
                            cp "$src_file" "$dst_file"
                            chmod 755 "$dst_file" 2>/dev/null || true
                            ok "Updated doors/$door_name/$fname"
                            door_changed=1
                        else
                            info "doors/$door_name/$fname: bundled binary is $src_arch, installed is $dst_arch — skipping binary"
                            needs_rebuild=1
                        fi
                    else
                        # Non-binary (scripts, configs, etc.) — always update.
                        cp "$src_file" "$dst_file"
                        [[ "$fname" == *.sh ]] && chmod 755 "$dst_file" 2>/dev/null || true
                        ok "Updated doors/$door_name/$fname"
                        door_changed=1
                    fi
                done < <(find "$src_door" -maxdepth 1 -type f)

                if [[ $needs_rebuild -eq 1 ]]; then
                    warn "doors/$door_name binary needs recompile for this CPU arch:"
                    warn "  cd \"$dst_door\" && bash build.sh"
                fi
                if [[ $door_changed -eq 0 && $needs_rebuild -eq 0 ]]; then
                    skip "doors/$door_name — no updates"
                fi
            fi
        done
    fi

    # Copy pre-bundled game ZIPs — these are static assets, not user data,
    # so they're safe to update alongside the code even though /data/ is
    # otherwise excluded.
    if [[ -d "$SOURCE_DIR/data/dos-games" ]]; then
        mkdir -p "$INSTALL_DIR/data/dos-games"
        rsync -a "$SOURCE_DIR/data/dos-games/" "$INSTALL_DIR/data/dos-games/"
        ok "DOS game bundles updated"
    fi
fi

# ─── Step 5: Update Python dependencies ───────────────────────────────────────
step "Step 5/8: Updating Python dependencies"

if [[ ! -d "$VENV_DIR" ]]; then
    warn "Virtual environment not found at $VENV_DIR — skipping pip update"
else
    info "Running pip install -e ..."
    cd "$INSTALL_DIR"
    if sudo -u "$SERVICE_USER" "$VENV_DIR/bin/pip" install -e "$INSTALL_DIR" --quiet 2>/dev/null || \
       "$VENV_DIR/bin/pip" install -e "$INSTALL_DIR" --quiet 2>/dev/null; then
        ok "Python dependencies updated (aiohttp + others pulled in via setup.py)"
    else
        warn "pip install failed — dependencies may be out of date"
    fi

    # ── Refresh /usr/local/bin/ console-script shortcuts ────────────────────
    # pip install -e above regenerates venv/bin/anetbbs-* for any console
    # script currently in setup.py, including brand-new ones (e.g.
    # anetbbs-cfg, added in v1.0.20) that didn't exist at original install
    # time -- but a new script has no /usr/local/bin/ shortcut until
    # ensure_symlinks() runs again. Self-heals every update instead of only
    # on a fresh install or an explicit `anetbbs-upgrade` run, so a sysop
    # never hits "command not found" for a script that pip clearly just
    # installed. Idempotent + silent on installs with nothing new to add.
    if [[ -x "$VENV_DIR/bin/anetbbs-symlinks" ]]; then
        "$VENV_DIR/bin/anetbbs-symlinks" "$INSTALL_DIR" >/dev/null 2>&1 && \
            ok "Refreshed /usr/local/bin/ command shortcuts" || \
            warn "Could not refresh /usr/local/bin/ shortcuts (non-fatal — use \$VENV_DIR/bin/<command> directly)"
    fi

    # ── Python 3.13 eventlet safety fix ──────────────────────────────────────
    # eventlet < 0.38.0 crashes on Python 3.13 with:
    #   AttributeError: module 'eventlet.green.thread' has no attribute
    #   'start_joinable_thread'
    # Python 3.13's threading.py requires this attribute at import time.
    #
    # IMPORTANT: version number alone is NOT sufficient. Prebuilt wheels
    # from piwheels (Raspberry Pi) ship eventlet 0.37.0 without this fix
    # because the wheel was compiled before the patch merged.  We grep the
    # installed file directly — if the attribute is absent we force a source
    # rebuild (--no-binary eventlet) which always has the fix.
    EVT_THREAD_PY=$(find "$VENV_DIR/lib" -maxdepth 5 -path "*/eventlet/green/thread.py" 2>/dev/null | head -1)
    if [[ -z "$EVT_THREAD_PY" ]]; then
        info "eventlet not yet installed (pip install above will pull in >=0.37.0)"
    elif grep -q "start_joinable_thread" "$EVT_THREAD_PY"; then
        ok "eventlet green/thread.py has start_joinable_thread — Python 3.13 compatible"
    else
        info "eventlet wheel missing start_joinable_thread (common piwheels aarch64 issue) — rebuilding from source..."
        _evt_fixed=false
        if sudo -u "$SERVICE_USER" "$VENV_DIR/bin/pip" install --force-reinstall --no-cache-dir --no-binary eventlet "eventlet>=0.38.0" --quiet 2>/dev/null; then
            _evt_fixed=true
        elif "$VENV_DIR/bin/pip" install --force-reinstall --no-cache-dir --no-binary eventlet "eventlet>=0.38.0" --quiet 2>/dev/null; then
            _evt_fixed=true
        fi
        if [[ "$_evt_fixed" == "true" ]]; then
            ok "eventlet rebuilt from source — Python 3.13 compatible"
        else
            warn "eventlet source rebuild failed — trying relaxed version constraint..."
            if "$VENV_DIR/bin/pip" install --force-reinstall --no-cache-dir --no-binary eventlet "eventlet>=0.38.0" --quiet 2>/dev/null; then
                ok "eventlet rebuilt (newer version) — Python 3.13 compatible"
            else
                warn "eventlet fix failed — web service will crash on Python 3.13"
                warn "Manual fix: sudo $VENV_DIR/bin/pip install --force-reinstall --no-cache-dir --no-binary eventlet 'eventlet>=0.38.0'"
            fi
        fi
    fi
    # ─────────────────────────────────────────────────────────────────────────

    # bcrypt / cryptography fast path: only nuke-and-reinstall if the
    # current install is ACTUALLY broken. The old logic ran the slow
    # rebuild on every update, adding 5-10 minutes per upgrade on
    # boxes without prebuilt wheels for the running Python — and the
    # web service is stopped for that entire window. Quick import
    # probes detect a working install in <100 ms and skip the rebuild.
    if "$VENV_DIR/bin/python" -c "import bcrypt; bcrypt.hashpw(b'x', bcrypt.gensalt())" >/dev/null 2>&1; then
        ok "bcrypt imports + hashes cleanly — skipping reinstall"
    else
        info "bcrypt broken — running the nuke-and-reinstall recovery path..."
        # The historical breakage: pip --force-reinstall LEAVES the old
        # .so FILE if a differently-named package (e.g. python-bcrypt
        # 0.3.2, which the original setup.py wrongly listed) put it
        # there. Leftover _bcrypt.so → "cannot import name '__author__'
        # from 'bcrypt._bcrypt'". Nuke explicitly first.
        SITE_PKGS=$("$VENV_DIR/bin/python" -c "import sys; print([p for p in sys.path if 'site-packages' in p][0])")
        sudo -u "$SERVICE_USER" "$VENV_DIR/bin/pip" uninstall -y bcrypt python-bcrypt 2>/dev/null || \
        "$VENV_DIR/bin/pip" uninstall -y bcrypt python-bcrypt 2>/dev/null || true
        rm -rf "$SITE_PKGS/bcrypt" "$SITE_PKGS/bcrypt-"*.dist-info "$SITE_PKGS/python_bcrypt"* "$SITE_PKGS/_bcrypt"*
        sudo -u "$SERVICE_USER" "$VENV_DIR/bin/pip" install --no-cache-dir --quiet bcrypt 2>/dev/null || \
        "$VENV_DIR/bin/pip" install --no-cache-dir --quiet bcrypt 2>/dev/null || \
        warn "bcrypt reinstall failed"
        ok "bcrypt reinstalled clean"
    fi

    if "$VENV_DIR/bin/python" -c "from cryptography.fernet import Fernet; Fernet.generate_key()" >/dev/null 2>&1; then
        ok "cryptography imports cleanly — skipping reinstall"
    else
        info "cryptography broken — force-reinstalling..."
        sudo -u "$SERVICE_USER" "$VENV_DIR/bin/pip" install \
            --force-reinstall --no-deps --no-cache-dir --quiet cryptography 2>/dev/null || \
        "$VENV_DIR/bin/pip" install \
            --force-reinstall --no-deps --no-cache-dir --quiet cryptography 2>/dev/null || true
    fi

    # Wipe any __pycache__ that pip-as-root may have written so the service user
    # can regenerate them on first import.
    find "$INSTALL_DIR" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
    chown -R "$SERVICE_USER":"$SERVICE_USER" "$INSTALL_DIR" 2>/dev/null || true

    # Verify the new package imports cleanly — catches stray legacy paths shadowing it.
    # Show the actual error if it fails so we can fix the root cause.
    info "Verifying anetbbs package imports..."
    IMPORT_ERR=$(cd /tmp && "$VENV_DIR/bin/python" -c "from anetbbs.main import main; from anetbbs.web_app import create_app; from anetbbs.core.ssh_server import start_ssh_server; print('OK')" 2>&1)
    if echo "$IMPORT_ERR" | grep -q "^OK$"; then
        ok "Package imports verified"
    else
        warn "Package import check failed:"
        echo "$IMPORT_ERR" | sed 's/^/    /'
        warn "Service start will likely fail. Investigate before restarting services."
    fi
fi

# ─── Step 6: Merge .env (only add missing keys) ───────────────────────────────
step "Step 6/8: Merging configuration (.env)"

NEW_ENV_KEYS_ADDED=0
if [[ -f "$SOURCE_DIR/.env.example" ]]; then
    while IFS='=' read -r key value; do
        [[ "$key" =~ ^#.*$ || -z "$key" ]] && continue
        if [[ -z "${EXISTING_ENV[$key]+x}" ]]; then
            echo "${key}=${value}" >> "$ENV_FILE"
            info "Added new config key: $key"
            NEW_ENV_KEYS_ADDED=$(( NEW_ENV_KEYS_ADDED + 1 ))
        fi
    done < <(grep -v '^\s*#' "$SOURCE_DIR/.env.example" | grep '=')
fi
if [[ $NEW_ENV_KEYS_ADDED -gt 0 ]]; then
    ok "$NEW_ENV_KEYS_ADDED new config key(s) added to .env"
else
    ok ".env is up to date — no new keys needed"
fi

# TRUST_PROXY_HEADERS is deliberately NOT in .env.example -- it's not
# in "add the default" here alongside everything else above:
# whether it's safe depends on whether a reverse proxy genuinely sits
# in front of this specific install, which this script can't reliably
# detect (nginx configs are sometimes at a custom path/filename, not
# the one install.sh generates -- confirmed live on a real install).
# Blindly backfilling a value here would either wrongly enable
# header-trust on an install with no proxy (letting any direct
# connection spoof its IP) or leave it wrongly disabled behind a real
# proxy (every visitor logs as the proxy's own address, useless for
# banning). install.sh writes this correctly for fresh installs; an
# existing install upgrading through this path has always been silent
# about it until now -- loud and manual beats silent and wrong here.
if ! grep -q '^TRUST_PROXY_HEADERS=' "$ENV_FILE" 2>/dev/null; then
    warn "TRUST_PROXY_HEADERS is not set in .env. If this install sits behind nginx or another reverse proxy, visitor IPs (bans, audit logs) will show as the proxy's own address until you add TRUST_PROXY_HEADERS=true and restart. If Flask is exposed directly with no proxy in front, leave this unset."
fi

# ─── Step 7: Update database schema (non-destructive) ─────────────────────────
step "Step 7/8: Updating database schema"

if [[ ! -d "$VENV_DIR" ]]; then
    warn "Virtual environment not found — skipping DB schema update"
else
    info "Migrating DB: adding missing columns to existing tables, then create_all() for new tables..."
    cd "$INSTALL_DIR"
    # Pull existing SECRET_KEY from .env. If missing OR set to a known
    # insecure default (older installer wrote 'changeme' or the dev
    # fallback), generate a real one and persist it. Web app since v146
    # refuses to boot in production with the dev default — auto-healing
    # this here makes the upgrade idempotent without sysop intervention.
    DB_SECRET_KEY="${EXISTING_ENV[SECRET_KEY]:-}"
    if [[ -z "$DB_SECRET_KEY" \
          || "$DB_SECRET_KEY" == "changeme" \
          || "$DB_SECRET_KEY" == "dev-secret-key-change-in-production" \
          || "$DB_SECRET_KEY" == "your-secret-key-here" ]]; then
        info "  No real SECRET_KEY in .env — generating one and writing it"
        DB_SECRET_KEY=$("$VENV_DIR/bin/python" -c \
            'import secrets; print(secrets.token_urlsafe(48))')
        # Strip any prior SECRET_KEY line, then append the new one.
        if grep -q '^SECRET_KEY=' "$ENV_FILE"; then
            sed -i 's|^SECRET_KEY=.*|SECRET_KEY='"$DB_SECRET_KEY"'|' "$ENV_FILE"
        else
            echo "SECRET_KEY=$DB_SECRET_KEY" >> "$ENV_FILE"
        fi
        ok "  SECRET_KEY written to $ENV_FILE (mode 0600)"
        chmod 600 "$ENV_FILE"
    fi
    DB_URL="${EXISTING_ENV[DATABASE_URL]:-sqlite:///${INSTALL_DIR}/data/anetbbs.db}"
    DB_UPDATED=false
    if sudo -u "$SERVICE_USER" "$VENV_DIR/bin/python" << DBEOF 2>/dev/null; then
import os, sys, sqlite3
sys.path.insert(0, '$INSTALL_DIR')
os.chdir('$INSTALL_DIR')
os.environ['FLASK_ENV'] = 'production'
os.environ['DATABASE_URL'] = '$DB_URL'
os.environ['SECRET_KEY'] = '$DB_SECRET_KEY'

from dotenv import load_dotenv
load_dotenv('$ENV_FILE')

# Build minimal app context — avoid create_app() which calls _create_default_data()
# and would crash if old tables are missing new columns.
from flask import Flask
from anetbbs.models import db
from anetbbs.config import get_config

app = Flask(__name__)
app.config.from_object(get_config('production'))
db.init_app(app)

def sqlite_type(col):
    from sqlalchemy.types import Integer, String, Text, Boolean, DateTime, Date, Float, LargeBinary
    t = col.type
    if isinstance(t, Integer): return "INTEGER"
    if isinstance(t, Boolean): return "BOOLEAN"
    if isinstance(t, (DateTime, Date)): return "DATETIME"
    if isinstance(t, Float): return "FLOAT"
    if isinstance(t, LargeBinary): return "BLOB"
    return "TEXT"

with app.app_context():
    db_url = app.config['SQLALCHEMY_DATABASE_URI']
    db_path = db_url.replace('sqlite:///', '').replace('sqlite:////', '/')
    if not db_path.startswith('/'):
        db_path = '/' + db_path
    if os.path.exists(db_path):
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        existing_tables = {row[0] for row in cur.fetchall()}
        for table_name, table in db.metadata.tables.items():
            if table_name not in existing_tables:
                continue
            cur.execute(f"PRAGMA table_info({table_name})")
            existing_cols = {row[1] for row in cur.fetchall()}
            for col in table.columns:
                if col.name in existing_cols:
                    continue
                sql_type = sqlite_type(col)
                null_clause = "" if col.nullable else " NOT NULL"
                default_clause = ""
                if col.default is not None and getattr(col.default, 'arg', None) is not None:
                    d = col.default.arg
                    if isinstance(d, bool):
                        default_clause = f" DEFAULT {1 if d else 0}"
                    elif isinstance(d, (int, float)):
                        default_clause = f" DEFAULT {d}"
                    elif isinstance(d, str):
                        default_clause = f" DEFAULT '{d}'"
                try:
                    cur.execute(f"ALTER TABLE {table_name} ADD COLUMN {col.name} {sql_type}{null_clause}{default_clause}")
                    print(f"  + {table_name}.{col.name}")
                except sqlite3.OperationalError:
                    pass
        conn.commit()
        conn.close()
    db.create_all()
    print('SCHEMA_OK')
DBEOF
        DB_UPDATED=true
    elif "$VENV_DIR/bin/python" << DBEOF2 2>/dev/null; then
import os, sys
sys.path.insert(0, '$INSTALL_DIR')
os.chdir('$INSTALL_DIR')
os.environ['FLASK_ENV'] = 'production'
os.environ['DATABASE_URL'] = '$DB_URL'
os.environ['SECRET_KEY'] = '$DB_SECRET_KEY'
os.environ['ANETBBS_SCHEMA_MIGRATE_ONLY'] = '1'

from dotenv import load_dotenv
load_dotenv('$ENV_FILE')

from anetbbs.web_app import create_app
from anetbbs.models import db, User, EchomailNetwork, EchoArea, EchomailMessage, EchomailReadStatus, EchomailPollLog

app = create_app('production')
with app.app_context():
    db.create_all()
    print('SCHEMA_OK')
DBEOF2
        DB_UPDATED=true
    fi
    if $DB_UPDATED; then
        ok "Database schema updated"
    else
        warn "Could not update database schema"
    fi
    chown -R "$SERVICE_USER":"$SERVICE_USER" "$INSTALL_DIR/data" 2>/dev/null || true
fi

# ─── Update systemd services (add new ones) ────────────────────────────────────
info "Checking for new systemd service files..."
MRC_BRIDGE_CONFIG="$INSTALL_DIR/mrc/bridge/config.json"

# Auto-install anetbbs-web.service if missing (e.g. updating from a
# pre-systemd install or one that used a different unit naming scheme).
#
# Port-selection logic — critical for not breaking existing installs:
#
# 1. If .env already sets WEB_PORT, use that (sysop has made an
#    explicit choice; respect it even if it conflicts with something).
# 2. Otherwise, probe what's currently bound. If the sysop was running
#    gunicorn manually on, say, :8080 and we wrote a fresh unit
#    hard-coded to :5000, their bookmarked URL would land on the MRC
#    bridge instead. Look for a running gunicorn / python pointing at
#    the install dir and inherit its bind port.
# 3. If step 2 finds nothing, default to 5000 — but FIRST verify that
#    port isn't already held by another anetbbs service (mrc-bridge
#    defaults to :8080 — overlap is unlikely but possible on weird
#    configs). If the default is taken, walk up to the next free
#    port and write THAT value back into .env so the choice survives.
if [[ ! -f /etc/systemd/system/anetbbs-web.service ]]; then
    info "Installing anetbbs-web.service ..."
    WEB_PORT_VAL=""

    # Step 1: explicit env value wins.
    if [[ -n "${EXISTING_ENV[WEB_PORT]:-}" ]]; then
        WEB_PORT_VAL="${EXISTING_ENV[WEB_PORT]}"
        info "  WEB_PORT=$WEB_PORT_VAL (from .env)"
    fi

    # Step 2: discover existing gunicorn binding for the install.
    if [[ -z "$WEB_PORT_VAL" ]] && command -v ss >/dev/null 2>&1; then
        # Walk every listening TCP port, look for one whose owning
        # process is a gunicorn from this install's venv. ss output:
        #   LISTEN 0 50 0.0.0.0:8080 0.0.0.0:* users:(("gunicorn",pid=...))
        DISCOVERED=$(ss -tlnp 2>/dev/null \
            | grep -E "gunicorn|$INSTALL_DIR" \
            | grep -oE '0\.0\.0\.0:[0-9]+|127\.0\.0\.1:[0-9]+|\*:[0-9]+|\[::\]:[0-9]+' \
            | grep -oE '[0-9]+$' \
            | sort -u | head -1)
        if [[ -n "$DISCOVERED" ]]; then
            WEB_PORT_VAL="$DISCOVERED"
            warn "  WEB_PORT auto-detected from running gunicorn: $WEB_PORT_VAL"
            warn "  (a manual install was running here; preserving its choice)"
        fi
    fi

    # Step 3: fall back to 5000, walking up if taken by something else.
    if [[ -z "$WEB_PORT_VAL" ]]; then
        WEB_PORT_VAL=5000
        if command -v ss >/dev/null 2>&1; then
            while ss -tlnH 2>/dev/null | grep -qE ":${WEB_PORT_VAL}\b"; do
                warn "  port $WEB_PORT_VAL is already bound; trying $((WEB_PORT_VAL + 1))"
                WEB_PORT_VAL=$((WEB_PORT_VAL + 1))
                [[ "$WEB_PORT_VAL" -gt 5050 ]] && break
            done
        fi
        info "  WEB_PORT=$WEB_PORT_VAL (chosen default; will write to .env)"
        # Persist so subsequent upgrades don't repeat the discovery dance.
        if ! grep -qE '^WEB_PORT=' "$ENV_FILE" 2>/dev/null; then
            echo "WEB_PORT=$WEB_PORT_VAL" >> "$ENV_FILE"
        fi
    fi

    mkdir -p "$INSTALL_DIR/logs"
    chown -R "$SERVICE_USER":"$SERVICE_USER" "$INSTALL_DIR/logs" 2>/dev/null || true
    cat > /etc/systemd/system/anetbbs-web.service << SVCEOF
[Unit]
Description=ANetBBS Web Application (eventlet WSGI)
After=network.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$INSTALL_DIR
EnvironmentFile=$INSTALL_DIR/.env
# CAP_NET_BIND_SERVICE lets the unprivileged service user bind to the
# privileged ports MSP (TCP/18) and SYSTAT/ActiveUser (UDP/11).
# NOTE: We intentionally do NOT set CapabilityBoundingSet here so that
# sudo (used by the Service Control Center) can still escalate.
AmbientCapabilities=CAP_NET_BIND_SERVICE
ExecStart=$VENV_DIR/bin/python $INSTALL_DIR/deploy/serve.py
Restart=always
RestartSec=5
KillMode=mixed
TimeoutStopSec=45

[Install]
WantedBy=multi-user.target
SVCEOF
    ok "anetbbs-web.service installed"
fi

# Auto-install the unified anetbbs.service (telnet+ssh+rlogin in one
# process, gated by .env flags) if missing. Generates an SSH host key
# first if one doesn't exist; SSH is opportunistic-on by default.
if [[ ! -f /etc/systemd/system/anetbbs.service ]]; then
    info "Installing anetbbs.service (unified terminal protocols) ..."
    SSH_KEY="$INSTALL_DIR/data/ssh_host_key"
    if [[ ! -f "$SSH_KEY" ]]; then
        info "Generating SSH host key..."
        sudo -u "$SERVICE_USER" ssh-keygen -t rsa -b 2048 -f "$SSH_KEY" -N '' -q 2>/dev/null || \
        ssh-keygen -t rsa -b 2048 -f "$SSH_KEY" -N '' -q 2>/dev/null || \
        warn "Could not generate SSH host key — SSH may fail until you create $SSH_KEY"
        chown "$SERVICE_USER":"$SERVICE_USER" "$SSH_KEY" "$SSH_KEY.pub" 2>/dev/null || true
    fi
    cat > /etc/systemd/system/anetbbs.service << SVCEOF
[Unit]
Description=ANetBBS terminal protocols (telnet / ssh / rlogin / FTP / LMTP)
After=network.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$INSTALL_DIR
EnvironmentFile=$INSTALL_DIR/.env
# FTP on port 21 needs CAP_NET_BIND_SERVICE since the unit doesn't run
# as root. Without this, the FTP listener silently fails to bind and
# /admin/control/ flags it as a listener problem.
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
ExecStart=$VENV_DIR/bin/anetbbs
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SVCEOF
    ok "anetbbs.service installed"
fi

# Patch existing anetbbs-web.service to REMOVE CapabilityBoundingSet=.
# Past releases shipped this set to CAP_NET_BIND_SERVICE only, which
# prevents sudo from re-acquiring CAP_SETUID/SETGID/AUDIT_WRITE — the
# SCC's Restart buttons then fail with "sudo: unable to change to
# root gid". AmbientCapabilities= still lets MSP/SYSTAT bind to their
# privileged ports; the bounding set was overcautious.
WEB_UNIT=/etc/systemd/system/anetbbs-web.service
if [[ -f "$WEB_UNIT" ]] && grep -q '^CapabilityBoundingSet=' "$WEB_UNIT"; then
    info "Patching $WEB_UNIT — removing CapabilityBoundingSet so sudo can elevate..."
    sed -i.bak '/^CapabilityBoundingSet=/d' "$WEB_UNIT"
    systemctl daemon-reload 2>/dev/null || true
    NEEDS_WEB_RESTART=true
    ok "anetbbs-web.service patched (Restart buttons in the SCC will work)"
fi

# Ensure data/ftp_root is owned by the service user. Leftover root-
# owned symlinks here would crash the FTP listener thread during the
# nightly tree rebuild (the symlink-tree builder couldn't unlink them).
# v1.0a2.32 also added a soft-skip-on-PermissionError path, so this
# is belt-and-braces.
FTP_ROOT_DIR_GUESS="$INSTALL_DIR/data/ftp_root"
if [[ -d "$FTP_ROOT_DIR_GUESS" ]]; then
    info "Ensuring $FTP_ROOT_DIR_GUESS is owned by $SERVICE_USER..."
    chown -R "$SERVICE_USER":"$SERVICE_USER" "$FTP_ROOT_DIR_GUESS" 2>/dev/null && \
        ok "ftp_root ownership normalised" || \
        warn "ftp_root chown failed — FTP listener may stay down"
fi

# ─── FTPS auto-enable ─────────────────────────────────────────────────────────
# If FTP_TLS_CERTFILE in .env points at /etc/letsencrypt (the common
# case), get the service into a state where it can actually read the
# cert + key. Three things have to line up:
#   1. The ssl-cert group exists and the service user belongs to it.
#   2. /etc/letsencrypt/{live,archive} are group-readable by ssl-cert,
#      with privkey*.pem at 0640 (group-readable, not world).
#   3. anetbbs.service has SupplementaryGroups=ssl-cert so systemd
#      actually grants the supplementary group at process start.
# All three are idempotent: re-running update.sh on a healthy install
# is a no-op.
#
# Plus we install a certbot renewal-hook so this doesn't drift on
# auto-renewal — certbot resets archive/ perms to 0700 root:root on
# every renewal, which would otherwise break FTPS overnight.
FTP_CERT="${EXISTING_ENV[FTP_TLS_CERTFILE]:-}"
if [[ -n "$FTP_CERT" && "$FTP_CERT" =~ ^/etc/letsencrypt/ ]]; then
    info "FTP_TLS_CERTFILE references /etc/letsencrypt — wiring FTPS access..."

    # 1. Ensure ssl-cert group exists (Debian/Ubuntu usually ship it;
    #    fallback creates a system group with a stable GID).
    if ! getent group ssl-cert >/dev/null 2>&1; then
        groupadd --system ssl-cert 2>/dev/null && \
            info "  created ssl-cert system group" || \
            warn "  could not create ssl-cert group"
    fi

    # 2. Add the service user to ssl-cert if not already a member.
    if ! id -nG "$SERVICE_USER" 2>/dev/null | tr ' ' '\n' | grep -qx ssl-cert; then
        if usermod -aG ssl-cert "$SERVICE_USER" 2>/dev/null; then
            ok "  added $SERVICE_USER to ssl-cert group"
            NEEDS_ANETBBS_RESTART=true
        else
            warn "  could not add $SERVICE_USER to ssl-cert"
        fi
    fi

    # 3. Normalise letsencrypt perms so ssl-cert members can read them.
    #    Done once now; the renewal hook below maintains them.
    if [[ -d /etc/letsencrypt/archive ]]; then
        chgrp -R ssl-cert /etc/letsencrypt/archive /etc/letsencrypt/live 2>/dev/null || true
        chmod g+rX /etc/letsencrypt/archive /etc/letsencrypt/live 2>/dev/null || true
        find /etc/letsencrypt/archive -name 'privkey*.pem' -exec chmod 640 {} \; 2>/dev/null || true
        find /etc/letsencrypt/archive -name '*.pem' ! -name 'privkey*' -exec chmod 644 {} \; 2>/dev/null || true
        ok "  /etc/letsencrypt perms set for ssl-cert group access"
    fi

    # 4. Install the renewal-hooks/deploy/ shim so certbot doesn't
    #    revert the perms on every renewal.
    HOOK_DIR=/etc/letsencrypt/renewal-hooks/deploy
    HOOK=$HOOK_DIR/anetbbs-ssl-cert-perms.sh
    if [[ -d /etc/letsencrypt ]]; then
        mkdir -p "$HOOK_DIR" 2>/dev/null || true
        cat > "$HOOK" << 'HOOKEOF'
#!/bin/bash
# Installed by anetbbs update.sh.
#
# Certbot resets /etc/letsencrypt/archive perms to 0700 root:root on
# every cert renewal, which silently breaks anetbbs FTPS (and any
# other non-root service reading the cert). This deploy-hook fires
# after each successful renewal and restores ssl-cert group access.
chgrp -R ssl-cert /etc/letsencrypt/archive /etc/letsencrypt/live 2>/dev/null || true
chmod g+rX /etc/letsencrypt/archive /etc/letsencrypt/live 2>/dev/null || true
find /etc/letsencrypt/archive -name 'privkey*.pem' -exec chmod 640 {} \; 2>/dev/null || true
find /etc/letsencrypt/archive -name '*.pem' ! -name 'privkey*' -exec chmod 644 {} \; 2>/dev/null || true
HOOKEOF
        chmod 0755 "$HOOK"
        ok "  certbot renewal hook installed ($HOOK)"
    fi

    # 5. Add SupplementaryGroups=ssl-cert to anetbbs.service if missing.
    #    Without this systemd's User=<service-user> doesn't pick up the
    #    new group membership — systemd doesn't call initgroups() on its
    #    own; you have to declare supplementary groups explicitly.
    ANETBBS_UNIT_FOR_SSL=/etc/systemd/system/anetbbs.service
    if [[ -f "$ANETBBS_UNIT_FOR_SSL" ]] && \
       ! grep -q '^SupplementaryGroups=.*ssl-cert' "$ANETBBS_UNIT_FOR_SSL"; then
        info "  patching $ANETBBS_UNIT_FOR_SSL — adding SupplementaryGroups=ssl-cert..."
        if grep -q '^EnvironmentFile=' "$ANETBBS_UNIT_FOR_SSL"; then
            sed -i.bak '/^EnvironmentFile=/a SupplementaryGroups=ssl-cert' "$ANETBBS_UNIT_FOR_SSL"
        else
            sed -i.bak '/^ExecStart=/i SupplementaryGroups=ssl-cert' "$ANETBBS_UNIT_FOR_SSL"
        fi
        systemctl daemon-reload 2>/dev/null || true
        NEEDS_ANETBBS_RESTART=true
        ok "  anetbbs.service patched (FTPS cert will be readable on next restart)"
    fi
fi
# ──────────────────────────────────────────────────────────────────────────────

# Patch existing anetbbs.service if it's missing CAP_NET_BIND_SERVICE.
# Releases before v1.0a2.29 shipped a unit without the cap, so the FTP
# listener could not bind to port 21 — the only listener that needed
# the cap and the only one that failed. We add the two lines
# (AmbientCapabilities + CapabilityBoundingSet) in place rather than
# rewriting the file so any sysop customizations survive.
ANETBBS_UNIT=/etc/systemd/system/anetbbs.service
if [[ -f "$ANETBBS_UNIT" ]] && ! grep -q '^AmbientCapabilities=CAP_NET_BIND_SERVICE' "$ANETBBS_UNIT"; then
    info "Patching $ANETBBS_UNIT — adding CAP_NET_BIND_SERVICE for FTP:21..."
    # Inject the two Capability lines right after the EnvironmentFile= line.
    # Fall back to before ExecStart= if EnvironmentFile= is missing for any
    # reason. sed -i creates a backup at .bak for safety.
    if grep -q '^EnvironmentFile=' "$ANETBBS_UNIT"; then
        sed -i.bak '/^EnvironmentFile=/a AmbientCapabilities=CAP_NET_BIND_SERVICE\nCapabilityBoundingSet=CAP_NET_BIND_SERVICE' "$ANETBBS_UNIT"
    else
        sed -i.bak '/^ExecStart=/i AmbientCapabilities=CAP_NET_BIND_SERVICE\nCapabilityBoundingSet=CAP_NET_BIND_SERVICE' "$ANETBBS_UNIT"
    fi
    systemctl daemon-reload 2>/dev/null || true
    # Mark for restart after Step 8 picks up the new caps.
    NEEDS_ANETBBS_RESTART=true
    ok "anetbbs.service patched (FTP listener will bind on next restart)"
fi

if [[ ! -f /etc/systemd/system/anetbbs-mrc-bridge.service ]]; then
    info "Installing anetbbs-mrc-bridge.service ..."
    cat > /etc/systemd/system/anetbbs-mrc-bridge.service << SVCEOF
[Unit]
Description=ANetBBS MRC Bridge Service
After=network.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$INSTALL_DIR
Environment=MRC_BRIDGE_CONFIG=$MRC_BRIDGE_CONFIG
ExecStart=$VENV_DIR/bin/python -m mrc.bridge.main
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
SVCEOF
    ok "anetbbs-mrc-bridge.service installed"
fi

# Migrate the old manually-installed unit name → the canonical one.
# Some installs have anetbbs-binkp-listener.service (the original hand-crafted
# name). The canonical unit is anetbbs-binkp.service. Stop and remove the old
# one so the new install block below can take over without a port conflict.
if [[ -f /etc/systemd/system/anetbbs-binkp-listener.service ]]; then
    info "Migrating anetbbs-binkp-listener → anetbbs-binkp ..."
    systemctl stop anetbbs-binkp-listener 2>/dev/null || true
    systemctl disable anetbbs-binkp-listener 2>/dev/null || true
    rm -f /etc/systemd/system/anetbbs-binkp-listener.service \
          /etc/systemd/system/multi-user.target.wants/anetbbs-binkp-listener.service
    systemctl daemon-reload 2>/dev/null || true
    ok "anetbbs-binkp-listener removed (replaced by anetbbs-binkp)"
fi

if [[ ! -f /etc/systemd/system/anetbbs-binkp.service ]]; then
    info "Installing anetbbs-binkp.service ..."
    cat > /etc/systemd/system/anetbbs-binkp.service << SVCEOF
[Unit]
Description=ANetBBS BinkP Inbound Listener
After=network.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$INSTALL_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$VENV_DIR/bin/python -m anetbbs.echomail.binkp_server
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SVCEOF
    ok "anetbbs-binkp.service installed"
fi

# Finger had a creation block in install.sh but never got one here, so an
# existing install missing anetbbs-finger.service (e.g. upgrading from a
# version that predates Finger support, or one where the install-time
# step failed) had no self-heal path at all. Mirrors
# deploy/anetbbs-finger.service / install.sh's own Finger block exactly.
if [[ ! -f /etc/systemd/system/anetbbs-finger.service ]]; then
    info "Installing anetbbs-finger.service ..."
    cat > /etc/systemd/system/anetbbs-finger.service << SVCEOF
[Unit]
Description=ANetBBS Finger Server (RFC 1288)
After=network.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$INSTALL_DIR
EnvironmentFile=$ENV_FILE
AmbientCapabilities=CAP_NET_BIND_SERVICE
ExecStart=$VENV_DIR/bin/python -m anetbbs.core.finger_server
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
SVCEOF
    ok "anetbbs-finger.service installed"
fi

# Ensure MRC bridge config.json exists
if [[ ! -f "$MRC_BRIDGE_CONFIG" ]]; then
    info "Generating MRC bridge config.json ..."
    mkdir -p "$INSTALL_DIR/mrc/bridge"
    BBS_NAME="${EXISTING_ENV[BBS_NAME]:-ANetBBS}"
    # web_listen_port must match the bridge's actual listen port
    # (install.sh derives it as WEB_PORT+1, written to .env as
    # MRC_BRIDGE_PORT) -- a hardcoded 8080 here would silently mismatch
    # on any install using a non-default WEB_PORT, the same class of bug
    # already fixed for the nginx proxy_pass patch a bit further down.
    MRC_LISTEN_PORT="${EXISTING_ENV[MRC_BRIDGE_PORT]:-8080}"
    # platform_info's version component is MRC client/protocol
    # compatibility as understood by the upstream hub, NOT ANetBBS's
    # own release version -- this used to be $NEW_VERSION, which meant
    # every fresh config.json generated this way was guaranteed to be
    # rejected by the hub's own OLDVERSION check (a real hub minimum
    # observed live: 1.2.9, in a numbering scheme unrelated to
    # ANetBBS's v1.0.x series). See install.sh's own longer comment on
    # MRC_CLIENT_COMPAT_VERSION for the full story -- keep this in
    # sync with that value if it's ever bumped.
    cat > "$MRC_BRIDGE_CONFIG" << MRCEOF
{
  "mrc_host": "mrc.bottomlessabyss.net",
  "mrc_port": 5001,
  "use_ssl": true,
  "bridge_bbs": "$BBS_NAME",
  "platform_info": "ANETBBS/Linux.$(uname -m)/1.3.9",
  "capabilities": ["MCI", "MSGEXT", "CTCP"],
  "web_listen_host": "127.0.0.1",
  "web_listen_port": $MRC_LISTEN_PORT,
  "message_rate_seconds": 0.5,
  "iamhere_interval_seconds": 60,
  "log_level": "INFO",
  "data_dir": "$INSTALL_DIR/data/mrc"
}
MRCEOF
    chown "$SERVICE_USER":"$SERVICE_USER" "$MRC_BRIDGE_CONFIG"
    chmod 640 "$MRC_BRIDGE_CONFIG"
    ok "MRC bridge config.json generated"
fi

# Ensure data/mrc directory exists
mkdir -p "$INSTALL_DIR/data/mrc"
chown -R "$SERVICE_USER":"$SERVICE_USER" "$INSTALL_DIR/data/mrc" 2>/dev/null || true

# Ensure data/text/ and data/text/menus/ exist for file-based ANSI overrides
mkdir -p "$INSTALL_DIR/data/text/menus"

# Seed stock slot screens (welcome/newuser/goodbye) and menu art from the
# bundled anetbbs/screens/ directory.  Uses cp -n (no-overwrite) so any
# sysop-customised files already in data/text/ are never touched.
_SCREENS_SRC="$SOURCE_DIR/anetbbs/screens"
if [[ -d "$_SCREENS_SRC" ]]; then
    for _f in "$_SCREENS_SRC"/*.ans "$_SCREENS_SRC"/*.asc; do
        [[ -f "$_f" ]] || continue
        _dest="$INSTALL_DIR/data/text/$(basename "$_f")"
        if [[ ! -f "$_dest" ]]; then
            cp "$_f" "$_dest"
            info "  seeded data/text/$(basename "$_f")"
        fi
    done
    if [[ -d "$_SCREENS_SRC/menus" ]]; then
        for _f in "$_SCREENS_SRC/menus"/*.ans "$_SCREENS_SRC/menus"/*.asc; do
            [[ -f "$_f" ]] || continue
            _dest="$INSTALL_DIR/data/text/menus/$(basename "$_f")"
            if [[ ! -f "$_dest" ]]; then
                cp "$_f" "$_dest"
                info "  seeded data/text/menus/$(basename "$_f")"
            fi
        done
    fi
fi

chown -R "$SERVICE_USER":"$SERVICE_USER" "$INSTALL_DIR/data/text" 2>/dev/null || true

# Ensure data/mods/ (and its text/, text/menus/, and core/
# subdirectories) exist -- the sysop's own file-override tree,
# matching Synchronet's real /sbbs/mods/ convention (mods/<script>.js
# overrides exec/, mods/text/ overrides text/, mods/text/menu/
# overrides text/menu/ -- see wiki.synchro.net/dir:mods). Covers three
# categories: Synchronet-compat door scripts/stubs
# (synchronet_compat.py's js.mods_dir, door_runner.py's
# _apply_mods_override()), ANSI/menu screens (session.py's
# _show_ansi_screen(), ansi_ui.py's load_menu_ansi()), and ANetBBS's
# own native core Python screens (core/mods_override.py's
# call_core_override() -- e.g. data/mods/core/login_menu.py overrides
# the built-in ANSI login menu, the same idea as customizing
# Synchronet's own login.js/logon.js in its mods/). Deliberately NOT
# seeded with any stock content the way data/text/ is above -- mods/
# starts empty and stays empty until a sysop deliberately drops in a
# same-named replacement for something they want to override; copying
# stock content into it here would make every file "overridden" by
# default, which is the opposite of the intent.
mkdir -p "$INSTALL_DIR/data/mods/text/menus"
mkdir -p "$INSTALL_DIR/data/mods/core"
chown -R "$SERVICE_USER":"$SERVICE_USER" "$INSTALL_DIR/data/mods" 2>/dev/null || true

# Refresh /etc/sudoers.d/anetbbs so the Service Control Center can run
# systemctl + journalctl against the current unit names. We rewrite it
# every update because past releases shipped a stale list (anetbbs-telnet,
# anetbbs-ssh, anetbbs-rlogin — none of which exist post-merge), which
# left the panel showing "permission denied" on Restart buttons.
SUDOERS_SRC="$SOURCE_DIR/deploy/sudoers.anetbbs"
SUDOERS_DST="/etc/sudoers.d/anetbbs"
if [[ -f "$SUDOERS_SRC" ]]; then
    if [[ -z "$SERVICE_USER" ]]; then
        # Real report: on a fresh install where anetbbs-web.service hasn't
        # been created yet AND stat on INSTALL_DIR somehow comes back
        # empty, the sed below would substitute __SERVICE_USER__ with
        # nothing, producing a line that starts with a bare space
        # (" ALL=(root) NOPASSWD: ...") -- invalid sudoers syntax, and
        # previously just an opaque "syntax check failed" with no clue
        # why. Skip outright with a clear reason instead of attempting a
        # write we already know will fail.
        warn "SERVICE_USER resolved empty — skipping sudoers refresh " \
             "(would produce invalid syntax). Re-run update.sh once " \
             "anetbbs-web.service exists, or check INSTALL_DIR ownership."
    else
        info "Refreshing $SUDOERS_DST from deploy/sudoers.anetbbs (user: $SERVICE_USER)..."
        # Substitute placeholders so the sudoers grants:
        #   - run as the actual SERVICE_USER (not the template's '__SERVICE_USER__')
        #   - reference the real upgrade wrapper path ($INSTALL_DIR/deploy/run_upgrade.sh,
        #     not the template's /opt/anetbbs/ placeholder)
        UPGRADE_WRAPPER="$INSTALL_DIR/deploy/run_upgrade.sh"
        RESTORE_WRAPPER="$INSTALL_DIR/deploy/run_restore.sh"
        # Substitute both deploy paths plus the user placeholder. Without
        # the -g flag each sed only hits its first match; we need every
        # /opt/anetbbs/deploy/* line to land on the real install path.
        sed -e "s/^__SERVICE_USER__ /$SERVICE_USER /" \
            -e "s|/opt/anetbbs/deploy/run_upgrade.sh|$UPGRADE_WRAPPER|g" \
            -e "s|/opt/anetbbs/deploy/run_restore.sh|$RESTORE_WRAPPER|g" \
            "$SUDOERS_SRC" > "$SUDOERS_DST.tmp"
        # Capture visudo's actual error instead of discarding it -- the
        # previous ">/dev/null 2>&1" left this a total black box: a real
        # user hit "syntax check failed" and neither they nor we could
        # tell why. `2>&1 >/dev/null` (order matters) sends only stderr
        # into the captured variable, discarding visudo's own stdout.
        VISUDO_ERR=$(visudo -cf "$SUDOERS_DST.tmp" 2>&1 >/dev/null)
        if [[ $? -eq 0 ]]; then
            mv "$SUDOERS_DST.tmp" "$SUDOERS_DST"
            chmod 0440 "$SUDOERS_DST"
            ok "sudoers refreshed (SCC restart + Check-for-Updates auto-install will work)"
        else
            rm -f "$SUDOERS_DST.tmp"
            warn "sudoers syntax check failed — leaving $SUDOERS_DST unchanged"
            warn "  visudo says: ${VISUDO_ERR:-<no output>}"
        fi
    fi
fi

# Self-heal the terminal gallery viewer for installs from before it
# existed. Only created if missing -- never overwritten once present,
# same as gallery-config.json, so a sysop's own edits to it survive
# future updates.
GALLERY_SCRIPT_SRC="$SOURCE_DIR/deploy/anet-gallery.sh"
GALLERY_SCRIPT_DST="$INSTALL_DIR/anet-gallery.sh"
if [[ -f "$GALLERY_SCRIPT_SRC" && ! -f "$GALLERY_SCRIPT_DST" ]]; then
    sed "s|__INSTALL_DIR__|$INSTALL_DIR|g" "$GALLERY_SCRIPT_SRC" > "$GALLERY_SCRIPT_DST"
    chmod 0755 "$GALLERY_SCRIPT_DST"
    chown "$SERVICE_USER":"$SERVICE_USER" "$GALLERY_SCRIPT_DST" 2>/dev/null || true
    ok "Terminal gallery viewer installed: $GALLERY_SCRIPT_DST"
fi

# Drop a sentinel /etc/anetbbs.install so the privileged upgrade wrapper
# (and any other future root helper) can discover the install root
# without hardcoding a path. Sources cleanly with `. /etc/anetbbs.install`.
cat > /etc/anetbbs.install <<EOF
# Written by update.sh — used by deploy/run_upgrade.sh to locate the
# install root when invoked under sudo. Do not edit by hand; rerun
# install.sh or update.sh to refresh.
INSTALL_DIR=$INSTALL_DIR
SERVICE_USER=$SERVICE_USER
EOF
chmod 0644 /etc/anetbbs.install

# Ensure deploy/ wrappers are executable. Tarballs built from FAT
# source trees lose the +x bit, so we set it explicitly post-extract.
for w in run_upgrade.sh run_restore.sh; do
    if [[ -f "$INSTALL_DIR/deploy/$w" ]]; then
        chmod 0755 "$INSTALL_DIR/deploy/$w"
    fi
done

systemctl daemon-reload

# ─── Patch nginx config (known bug fixes from past releases) ──────────────────
# Never rewrite the whole config — sysops may have customized it.
# Only surgically fix known bad values from old install.sh templates.
NGINX_AVAIL="/etc/nginx/sites-available/anetbbs"
if [[ -f "$NGINX_AVAIL" ]]; then
    NGINX_CHANGED=false

    # Fix: MRC bridge proxy_pass used /mrcws path; bridge only listens on /ws.
    # Old: proxy_pass http://127.0.0.1:8080/mrcws;
    # New: proxy_pass http://127.0.0.1:8080/ws;
    if grep -q '8080/mrcws' "$NGINX_AVAIL" 2>/dev/null; then
        info "Patching nginx: MRC bridge proxy path /mrcws → /ws"
        sed -i 's|8080/mrcws;|8080/ws;|g' "$NGINX_AVAIL"
        NGINX_CHANGED=true
        ok "nginx: MRC bridge proxy path fixed"
    fi

    # Fix: MRC bridge proxy_pass port didn't match the bridge's actual
    # listen port. install.sh derives the bridge's real port as
    # WEB_PORT+1 (MRC_BRIDGE_PORT_DEFAULT) and writes it correctly to
    # .env and the bridge's own config.json, but the nginx /mrcws
    # location was a hardcoded literal 8080 until this was caught — on
    # any install where the real bridge port differs (e.g. production
    # mode's default WEB_PORT=5000 -> bridge on 5001), nginx silently
    # proxied MRC traffic to a port nothing was listening on. The
    # bridge itself ran with zero errors, so this was invisible outside
    # of the browser's own WebSocket ever actually connecting — no
    # installer or health check caught it. Read the real port from
    # .env (which install.sh already writes correctly) and patch nginx
    # to match, so an install that's carrying this bug self-heals here
    # without needing a fresh reinstall.
    REAL_MRC_PORT="$(grep -E '^MRC_BRIDGE_PORT=' "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2 | tr -d '[:space:]')"
    # Extract just the port digits after the colon immediately before
    # /ws -- grep -oE '[0-9]+' alone would match "127" from the IP's
    # own octets first, since head -1 takes whichever numeric group
    # comes first in the string, not the actual port.
    CURRENT_MRC_PORT="$(grep -oE '127\.0\.0\.1:[0-9]+/ws;' "$NGINX_AVAIL" 2>/dev/null | head -1 | grep -oE ':[0-9]+/ws;' | grep -oE '[0-9]+')"
    if [[ -n "$REAL_MRC_PORT" && -n "$CURRENT_MRC_PORT" && "$CURRENT_MRC_PORT" != "$REAL_MRC_PORT" ]]; then
        info "Patching nginx: MRC bridge proxy port $CURRENT_MRC_PORT → $REAL_MRC_PORT (matching the bridge's actual listen port)"
        sed -i "s|127.0.0.1:${CURRENT_MRC_PORT}/ws;|127.0.0.1:${REAL_MRC_PORT}/ws;|g" "$NGINX_AVAIL"
        NGINX_CHANGED=true
        ok "nginx: MRC bridge proxy port fixed"
    fi

    # Fix: 'immutable' Cache-Control caused browsers to permanently cache
    # 404 responses from old installs (before anetbbs/static/mrc/ existed).
    # Replace with a 1-day max-age without immutable so forced-refresh works.
    if grep -q 'Cache-Control.*immutable' "$NGINX_AVAIL" 2>/dev/null; then
        info "Patching nginx: removing 'immutable' from static file cache headers"
        sed -i 's|add_header Cache-Control "public, immutable";|add_header Cache-Control "public, max-age=86400";|g' "$NGINX_AVAIL"
        # Also remove the now-redundant 'expires 7d' line if it's still there
        sed -i '/^\s*expires 7d;$/d' "$NGINX_AVAIL"
        NGINX_CHANGED=true
        ok "nginx: static cache headers updated (no longer immutable)"
    fi

    # Fix: Add /static/ location if the config pre-dates it entirely.
    # Without it, static files fall through to Flask — still works but slower.
    if ! grep -q 'location /static/' "$NGINX_AVAIL" 2>/dev/null; then
        info "Adding /static/ location to nginx config (missing from old install)..."
        STATIC_BLOCK="
    # Static files served directly by nginx for performance.
    location /static/ {
        alias ${INSTALL_DIR}/anetbbs/static/;
        add_header Cache-Control \"public, max-age=86400\";
    }"
        # Insert before the last closing brace in the last server{} block
        python3 -c "
import re, sys
txt = open('$NGINX_AVAIL').read()
# Insert before final closing brace
idx = txt.rfind('}')
if idx != -1:
    txt = txt[:idx] + '''$STATIC_BLOCK
}'''
    open('$NGINX_AVAIL', 'w').write(txt)
    print('inserted')
" 2>/dev/null && { NGINX_CHANGED=true; ok "nginx: /static/ location added"; } || \
        warn "nginx: could not auto-add /static/ location — add it manually from deploy/anetbbs-nginx.conf.template"
    fi

    # Fix: Add /mrcws and /mrc-auth-check locations if entirely absent.
    # Without them requests fall through to gunicorn (404) instead of the
    # MRC bridge.  Sysops who installed before MRC web was added
    # (pre-v1.0a2.76) or whose config was generated from an old template never
    # got these blocks. Read the real web/bridge ports from .env instead of
    # hardcoding 5000/8080 -- those were stale literals that caused the
    # exact "MRC bridge proxy port" bug fixed just above; falls back to the
    # install.sh defaults only if .env is somehow missing the keys.
    if ! grep -q 'location /mrcws' "$NGINX_AVAIL" 2>/dev/null; then
        info "Adding /mrcws location to nginx config (missing from this install)..."
        REAL_WEB_PORT="$(grep -E '^WEB_PORT=' "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2 | tr -d '[:space:]')"
        REAL_WEB_PORT="${REAL_WEB_PORT:-5000}"
        REAL_MRC_PORT="${REAL_MRC_PORT:-$(grep -E '^MRC_BRIDGE_PORT=' "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2 | tr -d '[:space:]')}"
        REAL_MRC_PORT="${REAL_MRC_PORT:-8080}"
        MRC_BLOCK="
    location = /mrc-auth-check {
        internal;
        proxy_pass http://127.0.0.1:${REAL_WEB_PORT}/mrc/auth-check;
        proxy_pass_request_body off;
        proxy_set_header Content-Length \"\";
        proxy_set_header X-Original-URI \$request_uri;
        proxy_set_header Cookie \$http_cookie;
    }

    location /mrcws {
        auth_request /mrc-auth-check;
        proxy_pass         http://127.0.0.1:${REAL_MRC_PORT}/ws;
        proxy_http_version 1.1;
        proxy_set_header   Upgrade    \$http_upgrade;
        proxy_set_header   Connection \"upgrade\";
        proxy_read_timeout 86400s;
    }"
        python3 -c "
import sys
txt = open('$NGINX_AVAIL').read()
idx = txt.rfind('}')
if idx != -1:
    txt = txt[:idx] + '''$MRC_BLOCK
}'''
    open('$NGINX_AVAIL', 'w').write(txt)
    print('inserted')
" 2>/dev/null && { NGINX_CHANGED=true; ok "nginx: /mrcws location added"; } || \
        warn "nginx: could not auto-add /mrcws location — add it manually from deploy/anetbbs-nginx.conf.template"
    fi

    # SELinux (Fedora/RHEL/CentOS default): nginx's proxy_pass to backend
    # ports is blocked by policy unless httpd_can_network_connect is on --
    # confirmed live on a fresh Fedora install (nginx's own error log:
    # "connect() to 127.0.0.1:5000 failed (13: Permission denied)", for
    # every proxied backend, main app AND the MRC bridge alike). install.sh
    # now sets this boolean on fresh installs; this is the matching
    # self-heal for installs that predate that fix. Debian/Ubuntu don't
    # have SELinux at all, so getenforce simply won't exist there and this
    # is a no-op.
    if command -v getenforce &>/dev/null && [[ "$(getenforce 2>/dev/null)" == "Enforcing" ]]; then
        if [[ "$(getsebool httpd_can_network_connect 2>/dev/null)" != *"--> on"* ]] && \
           command -v setsebool &>/dev/null; then
            if setsebool -P httpd_can_network_connect 1 2>/dev/null; then
                ok "SELinux: httpd_can_network_connect enabled (nginx can now reach the app/MRC bridge)"
            else
                warn "SELinux is enforcing but 'setsebool -P httpd_can_network_connect 1' failed — nginx cannot reach the app. Run it manually (as root)."
            fi
        fi
    fi

    if [[ "$NGINX_CHANGED" == "true" ]]; then
        if nginx -t 2>/dev/null; then
            if nginx -s reload 2>/dev/null || systemctl reload nginx 2>/dev/null; then
                ok "nginx config patched and reloaded"
            else
                warn "nginx config patched but reload failed — run: sudo nginx -s reload"
            fi
        else
            warn "nginx config test failed after patching — reverting nginx to backup"
            [[ -f "$BACKUP_DIR/anetbbs-nginx.bak" ]] && cp "$BACKUP_DIR/anetbbs-nginx.bak" "$NGINX_AVAIL"
            nginx -s reload 2>/dev/null || systemctl reload nginx 2>/dev/null || true
        fi
    else
        ok "nginx config looks good — no patches needed"
    fi
else
    skip "No nginx config at $NGINX_AVAIL — skipping nginx patch step"
fi

# ─── Step 8: Restart services ──────────────────────────────────────────────────
step "Step 8/8: Restarting services"

# Build the restart list:
#   anetbbs-web and anetbbs are always restarted (core services).
#   anetbbs-mrc-bridge, anetbbs-finger, and anetbbs-binkp are optional,
#   decided per service by a three-way check:
#     - previously ACTIVE            -> restart (proven-correct, unchanged)
#     - previously EXISTED but stopped -> skip (respects a sysop's
#       deliberate stop; don't second-guess it just because we can)
#     - freshly created THIS RUN, and the sysop's own .env says this
#       feature should be on (*_ENABLED=true) -> enable + start now.
#       Fixes a real bug: SERVICES_PREVIOUSLY_ACTIVE is snapshotted
#       before this run creates the unit, so a brand-new unit could
#       never appear in it — it was written to disk and daemon-reloaded
#       but never actually enabled/started, staying dormant until a
#       sysop noticed and started it by hand.
#     - freshly created THIS RUN, flag absent (a legacy .env from before
#       the *_ENABLED keys existed, hitting this self-heal for the very
#       first time) -> do NOT auto-start (never guess and force-open a
#       live listener on production without the sysop's own opt-in);
#       print exact remediation instead. Step 6 already backfilled the
#       flag as "false" onto disk by now, so the *next* run of update.sh
#       resolves deterministically and silently either way.
SERVICES_TO_START=()
for svc in anetbbs-web anetbbs; do
    [[ -f "/etc/systemd/system/${svc}.service" ]] && SERVICES_TO_START+=("$svc")
done
for svc in anetbbs-mrc-bridge anetbbs-finger anetbbs-binkp; do
    [[ -f "/etc/systemd/system/${svc}.service" ]] || continue

    if printf '%s\n' "${SERVICES_PREVIOUSLY_ACTIVE[@]}" | grep -qx "$svc" 2>/dev/null; then
        SERVICES_TO_START+=("$svc")
        continue
    fi

    if printf '%s\n' "${SERVICES_PREVIOUSLY_EXISTED[@]}" | grep -qx "$svc" 2>/dev/null; then
        skip "$svc was not active before the update — skipping restart (start it manually if needed)"
        continue
    fi

    # Freshly created this run -- map unit name to its .env flag.
    case "$svc" in
        anetbbs-mrc-bridge) _enabled_flag="MRC_BRIDGE_ENABLED" ;;
        anetbbs-finger)     _enabled_flag="FINGER_ENABLED" ;;
        anetbbs-binkp)      _enabled_flag="BINKP_ENABLED" ;;
    esac
    if [[ "${EXISTING_ENV[$_enabled_flag]:-}" == "true" ]]; then
        info "$svc was just created and $_enabled_flag=true in .env — enabling and starting it now"
        SERVICES_TO_START+=("$svc")
    else
        skip "$svc was just created but $_enabled_flag is not set to true in .env yet — not auto-starting." \
             "Set $_enabled_flag=true in $ENV_FILE and re-run update.sh, or: systemctl enable --now $svc"
    fi
done

if [[ ${#SERVICES_TO_START[@]} -eq 0 ]]; then
    warn "No anetbbs systemd units found — nothing to restart."
else
    for svc in "${SERVICES_TO_START[@]}"; do
        systemctl enable "$svc" >/dev/null 2>&1 || true
        # Clear start-limit-hit so a previously failed service can restart cleanly
        systemctl reset-failed "$svc" 2>/dev/null || true
        # Capture restart errors so we can surface them
        RESTART_ERR=$(systemctl restart "$svc" 2>&1)
        [[ -n "$RESTART_ERR" ]] && warn "$svc restart returned: $RESTART_ERR"
    done

    sleep 5

    CRITICAL_FAILED=false
    for svc in "${SERVICES_TO_START[@]}"; do
        if systemctl is-active --quiet "$svc" 2>/dev/null; then
            ok "$svc is running"
        else
            bad "$svc failed to start"
            # Only treat web as critical
            [[ "$svc" == "anetbbs-web" ]] && CRITICAL_FAILED=true
            warn "systemctl status $svc:"
            systemctl status "$svc" --no-pager -n 5 2>&1 | sed 's/^/    /' | head -15
            JOURNAL=$(journalctl -u "$svc" --no-pager -n 20 2>&1)
            if [[ -n "$JOURNAL" && "$JOURNAL" != "-- No entries --" ]]; then
                warn "journalctl -u $svc -n 20:"
                echo "$JOURNAL" | sed 's/^/    /'
            fi
        fi
    done

    # HTTP-level health check: systemctl is-active only says the process
    # is up, not that the web app can serve requests. A bad migration or
    # bad import would still leave gunicorn alive but every request
    # 500s. Poll /healthz until it returns 200 OR we time out, then
    # flag as a critical failure so the rollback block below kicks in.
    if [[ " ${SERVICES_TO_START[*]} " == *" anetbbs-web "* ]]; then
        WEB_PORT_PROBE="${EXISTING_ENV[WEB_PORT]:-5000}"
        HEALTH_URL="http://127.0.0.1:$WEB_PORT_PROBE/healthz"
        info "Probing $HEALTH_URL (up to 30s)..."
        HEALTH_OK=false
        for i in $(seq 1 15); do
            HEALTH_BODY=$(curl --fail --silent --max-time 3 "$HEALTH_URL" 2>/dev/null || true)
            if [[ -n "$HEALTH_BODY" ]]; then
                HEALTH_OK=true
                ok "Web health OK: ${HEALTH_BODY:0:120}"
                break
            fi
            sleep 2
        done
        if [[ "$HEALTH_OK" != true ]]; then
            # /healthz route is in v1.0a2.43+; an older deployed version
            # that lacks it will 404, which curl --fail treats as failure.
            # Fall back to a probe of the login page so an upgrade from
            # a pre-/healthz version doesn't spuriously fail.
            LOGIN_URL="http://127.0.0.1:$WEB_PORT_PROBE/auth/login"
            if curl --fail --silent --max-time 3 "$LOGIN_URL" >/dev/null 2>&1; then
                ok "Web responding at $LOGIN_URL (no /healthz route yet — old version?)"
            else
                bad "Web service is up but not responding to HTTP probes"
                CRITICAL_FAILED=true
            fi
        fi
    fi
fi

# ─── MRC web-proxy verification ────────────────────────────────────────────
# Real gap found live: a sysop kept getting reports from new admins that
# web MRC chat didn't work (terminal MRC over SSH/telnet was fine — that's
# a separate direct connection, not proxied through nginx at all). Root
# cause class: "behind" mode installs (sysop already runs their own
# nginx/apache and ANetBBS never touches it — install.sh only prints
# one-time setup instructions for this, easy to miss in the final wall of
# text) have zero ongoing verification that /mrcws is actually being
# proxied correctly, and drift (WEB_PORT changed later, config hand-edited)
# was previously only ever caught for the auto-managed nginx path above.
# DOMAIN is never persisted to .env (install.sh only uses it transiently
# to write server_name) so an externally-managed nginx config, wherever it
# lives, can't be located or inspected from here — this check verifies
# everything that IS actually knowable: the bridge's own local listener,
# and (when we manage nginx) that its /mrcws block points at the right
# port. Runs on every update, not just fresh installs, so drift gets
# caught going forward instead of only at initial setup.
#
# Gate on whether the service is ACTUALLY running, not just the .env
# flag -- confirmed live on a real long-lived install: MRC_BRIDGE_ENABLED
# was stuck at "false" in .env (evidently gone stale at some point after
# initial install) while anetbbs-mrc-bridge.service was demonstrably
# active and serving real traffic. Trusting the flag alone silently
# skipped this entire check on exactly the kind of hand-evolved install
# where catching drift matters most.
MRC_ACTUALLY_RUNNING=false
if [[ "${EXISTING_ENV[MRC_BRIDGE_ENABLED]:-}" == "true" ]] || \
   systemctl is-active --quiet anetbbs-mrc-bridge 2>/dev/null; then
    MRC_ACTUALLY_RUNNING=true
fi
if [[ "$MRC_ACTUALLY_RUNNING" == "true" ]]; then
    MRC_PORT_CHECK="${EXISTING_ENV[MRC_BRIDGE_PORT]:-8080}"
    MRC_WS_LOCAL="http://127.0.0.1:${MRC_PORT_CHECK}/ws"
    # Confirmed live (twice): (1) a probe right after the restart loop
    # can race the bridge's own startup -- "active" only means the
    # process started, not that it's finished binding its socket -- so
    # this retries briefly, same reasoning as the existing /healthz
    # probe. (2) A real WS-upgrade attempt is the WRONG probe shape: once
    # the server answers 101, curl has no reason to close the now-raw
    # duplexed connection on its own, so it just sits until --max-time
    # kills it -- meaning curl's own exit code comes back as a timeout
    # (28) on every success, not just failures. Confirmed via the
    # bridge's own access log: five separate probes, five real 101
    # responses, and this script reported "not answering" every time
    # regardless, because it was checking curl's exit code instead of
    # what curl actually received. Fixed: no Upgrade headers (a genuine
    # handshake was never actually needed -- any HTTP response at all,
    # even a 400 rejecting a non-upgrade GET to a WS-only route, proves
    # the bridge's HTTP layer is alive) and read the captured status
    # code directly rather than trusting curl's process exit status.
    MRC_WS_OK=false
    for i in 1 2 3 4 5; do
        MRC_HTTP_CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 \
            "$MRC_WS_LOCAL" 2>/dev/null || echo "000")
        if [[ "$MRC_HTTP_CODE" != "000" ]]; then
            MRC_WS_OK=true
            break
        fi
        sleep 2
    done
    if [[ "$MRC_WS_OK" == "true" ]]; then
        ok "MRC bridge answering locally on port $MRC_PORT_CHECK (HTTP $MRC_HTTP_CODE)"
    else
        warn "MRC bridge is NOT answering on 127.0.0.1:$MRC_PORT_CHECK/ws (after 5 retries over ~15s) — check 'systemctl status anetbbs-mrc-bridge' and its journalctl output. Both web AND terminal MRC need this running."
    fi

    if [[ -f "$NGINX_AVAIL" ]]; then
        if grep -q "location /mrcws" "$NGINX_AVAIL" 2>/dev/null; then
            NGINX_MRC_PORT=$(grep -oE '127\.0\.0\.1:[0-9]+/ws;' "$NGINX_AVAIL" 2>/dev/null | head -1 | sed -E 's/.*:([0-9]+)\/ws;.*/\1/')
            if [[ "$NGINX_MRC_PORT" == "$MRC_PORT_CHECK" ]]; then
                ok "nginx /mrcws proxy correctly points at port $MRC_PORT_CHECK"
            else
                warn "nginx /mrcws proxy points at port ${NGINX_MRC_PORT:-<none found>}, but the bridge is actually listening on $MRC_PORT_CHECK — web MRC chat will not connect. (The auto-patch above should have fixed this already; if you're seeing this warning, please report it.)"
            fi
        else
            warn "MRC_BRIDGE_ENABLED=true but nginx config ($NGINX_AVAIL) has no /mrcws location block — web MRC chat will not work until you add one. See deploy/anetbbs-nginx.conf.template for the exact block (needs /mrc-auth-check too)."
        fi
    else
        warn "MRC_BRIDGE_ENABLED=true but ANetBBS isn't managing nginx on this box ($NGINX_AVAIL not found) — expected for 'behind' mode installs, but it means we CANNOT verify your own nginx/reverse-proxy correctly proxies /mrcws to 127.0.0.1:$MRC_PORT_CHECK/ws. Test it yourself: open the web MRC page while logged in and check the browser console for WebSocket errors. Terminal MRC (SSH/telnet) doesn't depend on this and is unaffected either way."
    fi
fi

if [[ "${CRITICAL_FAILED:-false}" == "true" ]]; then
    echo ""
    # Detect if the failure is the Python 3.13 eventlet wheel issue.
    # Rolling back application files does not fix a pip package problem —
    # it just reverts good code while leaving the broken venv in place.
    # Instead: auto-fix eventlet, retry the web service, and skip the
    # file rollback entirely for this class of failure.
    WEB_JOURNAL=$(journalctl -u anetbbs-web -n 10 --no-pager 2>/dev/null || true)
    if echo "$WEB_JOURNAL" | grep -q "start_joinable_thread"; then
        warn "anetbbs-web crashed: eventlet wheel missing start_joinable_thread (piwheels aarch64 issue)"
        info "Attempting auto-fix: rebuilding eventlet from source..."
        _evt_retry=false
        if sudo -u "$SERVICE_USER" "$VENV_DIR/bin/pip" install --force-reinstall --no-cache-dir --no-binary eventlet "eventlet>=0.38.0" --quiet 2>/dev/null || \
           "$VENV_DIR/bin/pip" install --force-reinstall --no-cache-dir --no-binary eventlet "eventlet>=0.38.0" --quiet 2>/dev/null; then
            _evt_retry=true
        fi
        if [[ "$_evt_retry" == "true" ]]; then
            info "eventlet rebuilt — retrying anetbbs-web..."
            systemctl restart anetbbs-web 2>/dev/null || true
            sleep 4
            if systemctl is-active --quiet anetbbs-web; then
                ok "anetbbs-web is now running after eventlet fix"
                CRITICAL_FAILED=false
            else
                warn "anetbbs-web still failing after eventlet fix — check journalctl -u anetbbs-web -n 30"
            fi
        fi
        if [[ "${CRITICAL_FAILED:-false}" == "true" ]]; then
            warn "Application files were updated successfully."
            warn "Only anetbbs-web is affected. Fix manually:"
            warn "  sudo $VENV_DIR/bin/pip install --force-reinstall --no-cache-dir --no-binary eventlet 'eventlet>=0.38.0'"
            warn "  sudo systemctl restart anetbbs-web"
            fail "Web service failed to start — see fix above (files NOT rolled back)"
        fi
    else
        warn "Critical service failed to start. Rolling back from backup..."
        cp "$BACKUP_DIR/.env.bak" "$ENV_FILE"
        # Restore both DBs, same "don't try to guess which is real"
        # reasoning as the backup step above -- DATABASE_URL decides
        # which file Step 7's migration actually touched, and restoring
        # the untouched one is a harmless no-op. Restoring only
        # anetbbs.db.bak here left anetbbs_dev.db (when that's the one
        # DATABASE_URL actually pointed at) stuck in its post-migration
        # state with no rollback -- a real gap found in a pre-release audit.
        [[ -f "$BACKUP_DIR/anetbbs.db.bak" ]] && cp "$BACKUP_DIR/anetbbs.db.bak" "$DB_FILE"
        [[ -f "$BACKUP_DIR/anetbbs_dev.db.bak" ]] && cp "$BACKUP_DIR/anetbbs_dev.db.bak" "$INSTALL_DIR/data/anetbbs_dev.db"
        if [[ -d "$BACKUP_DIR/code" ]]; then
            info "Restoring application code from pre-update snapshot..."
            # --delete removes anything the failed update added that wasn't
            # in the old version (new modules, etc.) -- same exclude list as
            # the snapshot step so user data/config already on disk is left
            # alone rather than being deleted for "not being in the backup".
            if rsync -a --delete \
                --exclude='.git' --exclude='venv' --exclude='__pycache__' --exclude='*.pyc' \
                --exclude='*.egg-info' --exclude='.pytest_cache' \
                --exclude='*.log' --exclude='bbs.log*' \
                --exclude='/.env' --exclude='/data/' --exclude='/logs/' --exclude='/doors/' \
                --exclude='/gallery-config.json' --exclude='/mrc/bridge/config.json' \
                --exclude='/mrc/bridge/config/' --exclude='/mrc/bridge/data/' \
                --exclude='/mrc/bridge/data-*/' --exclude='/mrc/bridge/logs/' \
                --exclude='/mrc/bridge/*.db' --exclude='/mrc/bridge/*.db-shm' --exclude='/mrc/bridge/*.db-wal' \
                "$BACKUP_DIR/code/" "$INSTALL_DIR/"; then
                ok "Application code rolled back"
            else
                warn "Code rollback rsync reported errors — check $BACKUP_DIR/code manually"
            fi
        else
            warn "No code snapshot in $BACKUP_DIR (backup predates this safety feature) — code NOT rolled back"
        fi
        for svc in anetbbs-web anetbbs anetbbs-telnet anetbbs-ssh anetbbs-mrc-bridge anetbbs-finger anetbbs-binkp; do
            [[ -f "$BACKUP_DIR/${svc}.service.bak" ]] && \
                cp "$BACKUP_DIR/${svc}.service.bak" "/etc/systemd/system/${svc}.service"
        done
        systemctl daemon-reload
        for svc in "${SERVICES_TO_START[@]}"; do
            systemctl restart "$svc" 2>/dev/null || true
        done
        fail "Rollback complete. Check logs with: journalctl -u anetbbs-web -n 50"
    fi
    exit 1
fi

echo ""
echo -e "${BOLD}${CYAN}  Update Complete!${NC}"
echo ""
echo -e "  Backup stored at: ${DIM}$BACKUP_DIR${NC}"
echo -e "  ${BOLD}Useful Commands:${NC}"
echo -e "  ${DIM}sudo systemctl status anetbbs-web anetbbs anetbbs-mrc-bridge${NC}"
echo -e "  ${DIM}sudo journalctl -u anetbbs-web -f${NC}"
echo ""
