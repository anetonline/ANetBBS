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

BBS_VERSION="1.3.7"

# ─── Root check ────────────────────────────────────────────────────────────────
if [[ "$EUID" -ne 0 ]]; then
    fail "This script must be run as root."
    echo "  Run: sudo bash update.sh"
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

# Read existing .env into associative array
declare -A EXISTING_ENV
while IFS='=' read -r key value; do
    [[ "$key" =~ ^#.*$ || -z "$key" ]] && continue
    EXISTING_ENV["$key"]="$value"
done < <(grep -v '^\s*#' "$ENV_FILE" | grep '=')

SERVICE_USER=$(stat -c '%U' "$INSTALL_DIR" 2>/dev/null || echo "anetbbs")
VENV_DIR="$INSTALL_DIR/venv"

ok "Installation found at $INSTALL_DIR (user: $SERVICE_USER)"

# ─── Step 2: Pre-update backup ────────────────────────────────────────────────
step "Step 2/8: Creating pre-update backup"

BACKUP_DIR="/tmp/anetbbs-backup-$(date +%Y%m%d%H%M%S)"
mkdir -p "$BACKUP_DIR"

cp "$ENV_FILE" "$BACKUP_DIR/.env.bak"
ok "Backed up .env"

DB_FILE="$INSTALL_DIR/data/anetbbs.db"
if [[ -f "$DB_FILE" ]]; then
    cp "$DB_FILE" "$BACKUP_DIR/anetbbs.db.bak"
    ok "Backed up database"
fi

for svc in anetbbs-web anetbbs anetbbs-telnet anetbbs-ssh anetbbs-mrc-bridge anetbbs-finger; do
    [[ -f "/etc/systemd/system/${svc}.service" ]] && \
        cp "/etc/systemd/system/${svc}.service" "$BACKUP_DIR/${svc}.service.bak"
done
ok "Backed up systemd service files"

NGINX_AVAIL="/etc/nginx/sites-available/anetbbs"
[[ -f "$NGINX_AVAIL" ]] && cp "$NGINX_AVAIL" "$BACKUP_DIR/anetbbs-nginx.bak"
ok "Backup stored at $BACKUP_DIR"

# ─── Step 3: Stop services ─────────────────────────────────────────────────────
step "Step 3/8: Stopping services"

for svc in anetbbs-web anetbbs anetbbs-telnet anetbbs-ssh anetbbs-mrc-bridge anetbbs-finger; do
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

    # Nuke + reinstall bcrypt because pip --force-reinstall LEAVES THE OLD .so FILE
    # if it was put there by a different-named package (e.g. python-bcrypt 0.3.2,
    # which the original setup.py wrongly listed). The leftover _bcrypt.so causes:
    #   ImportError: cannot import name '__author__' from 'bcrypt._bcrypt'
    info "Nuking + reinstalling bcrypt (handles namespace collision with old python-bcrypt)..."
    SITE_PKGS=$("$VENV_DIR/bin/python" -c "import sys; print([p for p in sys.path if 'site-packages' in p][0])")
    sudo -u "$SERVICE_USER" "$VENV_DIR/bin/pip" uninstall -y bcrypt python-bcrypt 2>/dev/null || \
    "$VENV_DIR/bin/pip" uninstall -y bcrypt python-bcrypt 2>/dev/null || true
    rm -rf "$SITE_PKGS/bcrypt" "$SITE_PKGS/bcrypt-"*.dist-info "$SITE_PKGS/python_bcrypt"* "$SITE_PKGS/_bcrypt"*
    sudo -u "$SERVICE_USER" "$VENV_DIR/bin/pip" install --no-cache-dir --quiet bcrypt 2>/dev/null || \
    "$VENV_DIR/bin/pip" install --no-cache-dir --quiet bcrypt 2>/dev/null || \
    warn "bcrypt reinstall failed"
    ok "bcrypt reinstalled clean"

    # Cryptography rarely has this problem but doesn't hurt to refresh.
    sudo -u "$SERVICE_USER" "$VENV_DIR/bin/pip" install \
        --force-reinstall --no-deps --no-cache-dir --quiet cryptography 2>/dev/null || \
    "$VENV_DIR/bin/pip" install \
        --force-reinstall --no-deps --no-cache-dir --quiet cryptography 2>/dev/null || true

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

# Auto-install anetbbs-web.service if missing (e.g. updating from a pre-systemd
# install or one that used a different unit naming scheme).
if [[ ! -f /etc/systemd/system/anetbbs-web.service ]]; then
    info "Installing anetbbs-web.service ..."
    WEB_PORT_VAL="${EXISTING_ENV[WEB_PORT]:-5000}"
    mkdir -p "$INSTALL_DIR/logs"
    chown -R "$SERVICE_USER":"$SERVICE_USER" "$INSTALL_DIR/logs" 2>/dev/null || true
    cat > /etc/systemd/system/anetbbs-web.service << SVCEOF
[Unit]
Description=ANetBBS Web Application (Gunicorn + eventlet)
After=network.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$INSTALL_DIR
EnvironmentFile=$INSTALL_DIR/.env
# Privileged ports (MSP/18, SYSTAT/11) need this capability.
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
ExecStart=$VENV_DIR/bin/gunicorn \\
    --worker-class eventlet \\
    -w 1 \\
    -b 0.0.0.0:$WEB_PORT_VAL \\
    --timeout 120 \\
    --log-level info \\
    --access-logfile $INSTALL_DIR/logs/gunicorn-access.log \\
    --error-logfile $INSTALL_DIR/logs/gunicorn-error.log \\
    deploy.wsgi_wrapper:app
Restart=always
RestartSec=5

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
Description=ANetBBS terminal protocols (telnet / ssh / rlogin)
After=network.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$INSTALL_DIR
EnvironmentFile=$INSTALL_DIR/.env
ExecStart=$VENV_DIR/bin/anetbbs
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SVCEOF
    ok "anetbbs.service installed"
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

# Ensure MRC bridge config.json exists
if [[ ! -f "$MRC_BRIDGE_CONFIG" ]]; then
    info "Generating MRC bridge config.json ..."
    mkdir -p "$INSTALL_DIR/mrc/bridge"
    BBS_NAME="${EXISTING_ENV[BBS_NAME]:-ANetBBS}"
    cat > "$MRC_BRIDGE_CONFIG" << MRCEOF
{
  "mrc_host": "mrc.bottomlessabyss.net",
  "mrc_port": 5000,
  "use_ssl": false,
  "bridge_bbs": "$BBS_NAME",
  "platform_info": "ANETBBS/Linux.$(uname -m)/$BBS_VERSION",
  "capabilities": ["MCI", "MSGEXT", "CTCP"],
  "web_listen_host": "127.0.0.1",
  "web_listen_port": 8080,
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

systemctl daemon-reload

# ─── Step 8: Restart services ──────────────────────────────────────────────────
step "Step 8/8: Restarting services"

# Build list from units that ACTUALLY exist on disk after Step 7's auto-install pass.
SERVICES_TO_START=()
for svc in anetbbs-web anetbbs anetbbs-mrc-bridge anetbbs-finger; do
    [[ -f "/etc/systemd/system/${svc}.service" ]] && SERVICES_TO_START+=("$svc")
done

if [[ ${#SERVICES_TO_START[@]} -eq 0 ]]; then
    warn "No anetbbs systemd units found — nothing to restart."
else
    for svc in "${SERVICES_TO_START[@]}"; do
        systemctl enable "$svc" >/dev/null 2>&1 || true
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
fi

if [[ "${CRITICAL_FAILED:-false}" == "true" ]]; then
    echo ""
    warn "Critical service failed to start. Rolling back from backup..."
    cp "$BACKUP_DIR/.env.bak" "$ENV_FILE"
    [[ -f "$BACKUP_DIR/anetbbs.db.bak" ]] && cp "$BACKUP_DIR/anetbbs.db.bak" "$DB_FILE"
    for svc in anetbbs-web anetbbs anetbbs-telnet anetbbs-ssh anetbbs-mrc-bridge anetbbs-finger; do
        [[ -f "$BACKUP_DIR/${svc}.service.bak" ]] && \
            cp "$BACKUP_DIR/${svc}.service.bak" "/etc/systemd/system/${svc}.service"
    done
    systemctl daemon-reload
    for svc in "${SERVICES_TO_START[@]}"; do
        systemctl restart "$svc" 2>/dev/null || true
    done
    fail "Rollback complete. Check logs with: journalctl -u anetbbs-web -n 50"
    exit 1
fi

echo ""
echo -e "${BOLD}${CYAN}"
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║              Update Complete!                                ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo -e "${NC}"
echo -e "  Backup stored at: ${DIM}$BACKUP_DIR${NC}"
echo -e "  ${BOLD}Useful Commands:${NC}"
echo -e "  ${DIM}sudo systemctl status anetbbs-web anetbbs anetbbs-mrc-bridge${NC}"
echo -e "  ${DIM}sudo journalctl -u anetbbs-web -f${NC}"
echo ""
