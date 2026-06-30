#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# ANetBBS — All-in-One Install Wizard
# https://github.com/anetonline/anetbbs
#
# Usage:
#   sudo bash install.sh              # Interactive install
#   sudo bash install.sh --uninstall  # Remove ANetBBS
#   sudo bash install.sh --defaults   # Install with all defaults (non-interactive)
#   sudo bash install.sh --force      # Overwrite existing .env and database
# ═══════════════════════════════════════════════════════════════════════════════

# ─── Color helpers ─────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'
BOLD='\033[1m'; DIM='\033[2m'; NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
fail()  { echo -e "${RED}[FAIL]${NC}  $*"; }
step()  { echo -e "\n${CYAN}${BOLD}── $* ──${NC}"; }
ok()    { echo -e "  ${GREEN}✅ $*${NC}"; }
skip()  { echo -e "  ${YELLOW}⏭  $*${NC}"; }
bad()   { echo -e "  ${RED}❌ $*${NC}"; }

# Track what succeeded for final summary
declare -A STATUS

# ─── Defaults ──────────────────────────────────────────────────────────────────
DEF_BBS_NAME="ANetBBS"
DEF_BBS_DESC="A Modern BBS System"
DEF_SERVICE_USER="anetbbs"
DEF_INSTALL_DIR="/opt/anetbbs"
DEF_ADMIN_USER="admin"
DEF_DOMAIN="localhost"
DEF_WEB_PORT=5000
DEF_TELNET_PORT=2233
DEF_SSH_PORT=2234
DEF_ENABLE_TELNET="y"
DEF_ENABLE_SSH="y"
DEF_ENABLE_NGINX="y"
DEF_ENABLE_SSL="n"
BBS_VERSION="1.3.7"

# ─── Source directory (where install.sh lives) ─────────────────────────────────
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ═══════════════════════════════════════════════════════════════════════════════
# UNINSTALL
# ═══════════════════════════════════════════════════════════════════════════════
if [[ "${1:-}" == "--uninstall" ]]; then
    echo -e "${BOLD}${RED}"
    echo "╔══════════════════════════════════════════════╗"
    echo "║         ANetBBS — UNINSTALL                  ║"
    echo "╚══════════════════════════════════════════════╝"
    echo -e "${NC}"

    read -rp "Service user [anetbbs]: " UNI_USER
    UNI_USER="${UNI_USER:-anetbbs}"
    read -rp "Install directory [/opt/anetbbs]: " UNI_DIR
    UNI_DIR="${UNI_DIR:-/opt/anetbbs}"

    read -rp "Are you sure you want to completely remove ANetBBS? [y/N]: " CONFIRM
    [[ "${CONFIRM,,}" != "y" ]] && { echo "Aborted."; exit 0; }

    info "Stopping and removing services..."
    for svc in anetbbs-web anetbbs anetbbs-telnet anetbbs-ssh anetbbs-mrc-bridge anetbbs-finger; do
        systemctl stop    "$svc" 2>/dev/null || true
        systemctl disable "$svc" 2>/dev/null || true
        rm -f "/etc/systemd/system/${svc}.service"
    done
    systemctl daemon-reload

    info "Removing nginx config..."
    rm -f /etc/nginx/sites-enabled/anetbbs
    rm -f /etc/nginx/sites-available/anetbbs
    systemctl reload nginx 2>/dev/null || true

    info "Removing install directory..."
    rm -rf "$UNI_DIR"

    info "Removing system user..."
    id "$UNI_USER" &>/dev/null && userdel "$UNI_USER" 2>/dev/null || true

    info "ANetBBS has been uninstalled."
    exit 0
fi

# ═══════════════════════════════════════════════════════════════════════════════
# ROOT CHECK
# ═══════════════════════════════════════════════════════════════════════════════
if [[ "$EUID" -ne 0 ]]; then
    fail "This installer must be run as root."
    echo "  Run: sudo bash install.sh"
    exit 1
fi

# ═══════════════════════════════════════════════════════════════════════════════
# OS DETECTION
# ═══════════════════════════════════════════════════════════════════════════════
step "Detecting operating system"

OS_ID="unknown"
OS_VERSION=""
OS_PRETTY=""
PKG_MANAGER="unknown"

if [[ -f /etc/os-release ]]; then
    . /etc/os-release
    OS_ID="${ID,,}"
    OS_VERSION="${VERSION_ID:-}"
    OS_PRETTY="${PRETTY_NAME:-$OS_ID $OS_VERSION}"
fi

case "$OS_ID" in
    ubuntu|debian|linuxmint|pop|elementary|zorin|kali)
        PKG_MANAGER="apt"
        ;;
    rhel|centos|rocky|almalinux|ol)
        if command -v dnf &>/dev/null; then
            PKG_MANAGER="dnf"
        elif command -v yum &>/dev/null; then
            PKG_MANAGER="yum"
        fi
        ;;
    fedora)
        PKG_MANAGER="dnf"
        ;;
    arch|manjaro|endeavouros)
        PKG_MANAGER="pacman"
        ;;
    opensuse*|sles)
        PKG_MANAGER="zypper"
        ;;
    *)
        PKG_MANAGER="unknown"
        ;;
esac

info "Detected: ${BOLD}$OS_PRETTY${NC}"
info "Package manager: ${BOLD}$PKG_MANAGER${NC}"

if [[ "$PKG_MANAGER" == "unknown" ]]; then
    warn "Could not detect your package manager."
    warn "You may need to install dependencies manually."
    read -rp "Continue anyway? [y/N]: " CONT
    [[ "${CONT,,}" != "y" ]] && { echo "Aborted."; exit 1; }
fi

# ═══════════════════════════════════════════════════════════════════════════════
# PYTHON VERSION CHECK
# ═══════════════════════════════════════════════════════════════════════════════
PYTHON_CMD=""
for cmd in python3.12 python3.11 python3.10 python3; do
    if command -v "$cmd" &>/dev/null; then
        PY_VER=$("$cmd" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null)
        PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
        PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
        if [[ "$PY_MAJOR" -ge 3 ]] && [[ "$PY_MINOR" -ge 9 ]]; then
            PYTHON_CMD="$cmd"
            break
        fi
    fi
done

if [[ -n "$PYTHON_CMD" ]]; then
    info "Found Python: ${BOLD}$PYTHON_CMD ($PY_VER)${NC}"
else
    info "Python 3.9+ not yet installed — will install during setup."
fi

# ═══════════════════════════════════════════════════════════════════════════════
# INTERACTIVE WIZARD
# ═══════════════════════════════════════════════════════════════════════════════
USE_DEFAULTS=false
FORCE_OVERWRITE=false
[[ "${1:-}" == "--defaults" ]] && USE_DEFAULTS=true
[[ "${1:-}" == "--force" || "${2:-}" == "--force" ]] && FORCE_OVERWRITE=true

echo ""
echo -e "${BOLD}${CYAN}"
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║                                                              ║"
echo "║              █████╗ ███╗   ██╗███████╗████████╗              ║"
echo "║             ██╔══██╗████╗  ██║██╔════╝╚══██╔══╝              ║"
echo "║             ███████║██╔██╗ ██║█████╗     ██║                 ║"
echo "║             ██╔══██║██║╚██╗██║██╔══╝     ██║                 ║"
echo "║             ██║  ██║██║ ╚████║███████╗   ██║                 ║"
echo "║             ╚═╝  ╚═╝╚═╝  ╚═══╝╚══════╝   ╚═╝                 ║"
echo "║                                                              ║"
echo "║              ANetBBS — Installation Wizard                   ║"
echo "║                                                              ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo -e "${NC}"
echo -e "${DIM}  This wizard will set up your BBS from scratch.${NC}"
echo -e "${DIM}  Press Enter to accept [defaults] shown in brackets.${NC}"
echo ""

ask() {
    local var_name="$1" prompt="$2" default="$3"
    if $USE_DEFAULTS; then
        eval "$var_name=\"$default\""
    else
        read -rp "  $prompt [$default]: " input
        eval "$var_name=\"\${input:-$default}\""
    fi
}

ask_yn() {
    local var_name="$1" prompt="$2" default="$3"
    if $USE_DEFAULTS; then
        eval "$var_name=\"$default\""
    else
        read -rp "  $prompt [$default]: " input
        input="${input:-$default}"
        eval "$var_name=\"\${input,,}\""
    fi
}

ask_secret() {
    local var_name="$1" prompt="$2"
    if $USE_DEFAULTS; then
        eval "$var_name=\"\""
        return
    fi
    local input confirm
    while true; do
        echo -en "  $prompt (leave blank to auto-generate): "
        read -rs input
        echo ""
        if [[ -z "$input" ]]; then
            # Blank = auto-generate, no confirmation needed.
            eval "$var_name=\"\""
            return
        fi
        echo -en "  Confirm password: "
        read -rs confirm
        echo ""
        if [[ "$input" == "$confirm" ]]; then
            eval "$var_name=\"$input\""
            return
        fi
        warn "Passwords didn't match. Try again."
    done
}

echo -e "${BOLD}  ── Install Mode ──${NC}"
echo "  production  = Full public BBS with HTTPS. We install nginx + a"
echo "                Let's Encrypt cert on YOUR domain. Needs ports"
echo "                80/443 reachable from the internet. Pick this if"
echo "                ANetBBS is the only web server on this box."
echo ""
echo "  behind      = You already run another BBS / web server that owns"
echo "                ports 80/443 (Synchronet, Mystic, etc.). ANetBBS"
echo "                gunicorn binds 0.0.0.0:8080 — reverse-proxy to it"
echo "                from your existing nginx/apache. We don't touch"
echo "                your web-server config."
echo ""
echo "  test        = Local sandbox. gunicorn binds 127.0.0.1:8080 — only"
echo "                reachable from this box itself (or via SSH tunnel)."
echo "                No port exposed to the LAN, no public web at all."
echo "                Telnet/SSH/MRC still work normally for trying"
echo "                ANetBBS out."
echo ""
ask INSTALL_MODE    "Install mode (production/behind/test)" "production"
INSTALL_MODE="${INSTALL_MODE,,}"
if [[ "$INSTALL_MODE" != "production" && "$INSTALL_MODE" != "behind" && "$INSTALL_MODE" != "test" ]]; then
    warn "Unrecognized mode '$INSTALL_MODE' — defaulting to production"
    INSTALL_MODE="production"
fi
# Apply mode-derived defaults to the prompts below. Sysop can still
# override anything explicitly later in the wizard.
case "$INSTALL_MODE" in
    test)
        DEF_DOMAIN="localhost"
        DEF_ENABLE_NGINX="n"
        DEF_ENABLE_SSL="n"
        DEF_ENABLE_MSP="n"
        DEF_ENABLE_FINGER="n"
        DEF_ENABLE_BINKP="n"
        DEF_WEB_PORT=8080
        WEB_BIND="127.0.0.1"   # localhost-only, invisible to LAN
        ;;
    behind)
        DEF_DOMAIN="localhost"
        DEF_ENABLE_NGINX="n"
        DEF_ENABLE_SSL="n"
        # Privileged-port services + BinkP can still run alongside the
        # other BBS as long as ports aren't already taken; sysop opts in.
        DEF_ENABLE_MSP="n"
        DEF_ENABLE_FINGER="n"
        DEF_ENABLE_BINKP="y"
        DEF_WEB_PORT=8080
        WEB_BIND="0.0.0.0"     # reachable so sysop's other nginx can proxy in
        ;;
    *)
        DEF_ENABLE_NGINX="y"
        DEF_ENABLE_SSL="y"
        DEF_ENABLE_MSP="y"
        DEF_ENABLE_FINGER="y"
        DEF_ENABLE_BINKP="y"
        DEF_WEB_PORT=5000
        WEB_BIND="0.0.0.0"
        ;;
esac
echo ""

echo -e "${BOLD}  ── BBS Settings ──${NC}"
ask BBS_NAME        "BBS Name"              "$DEF_BBS_NAME"
ask BBS_DESC        "BBS Description"       "$DEF_BBS_DESC"
echo ""

echo -e "${BOLD}  ── System Settings ──${NC}"
ask SERVICE_USER    "System service user"    "$DEF_SERVICE_USER"
ask INSTALL_DIR     "Install directory"      "$DEF_INSTALL_DIR"
echo ""

echo -e "${BOLD}  ── Sysop Admin Account ──${NC}"
ask SYSOP_NAME      "Sysop username (your handle / login)"   "$BBS_NAME Sysop"
ADMIN_USER="$SYSOP_NAME"
ask_secret ADMIN_PASS "Sysop password"

# Auto-generate password if blank
if [[ -z "$ADMIN_PASS" ]]; then
    ADMIN_PASS=$(openssl rand -base64 16 2>/dev/null || python3 -c "import secrets; print(secrets.token_urlsafe(16))" 2>/dev/null || head -c 24 /dev/urandom | base64 | tr -d '/+=' | head -c 16)
    ADMIN_PASS_GENERATED=true
    info "  Auto-generated admin password."
else
    ADMIN_PASS_GENERATED=false
fi

echo ""
echo -e "${BOLD}  ── Network Settings ──${NC}"
ask DOMAIN          "Domain / hostname"      "$DEF_DOMAIN"
ask WEB_PORT        "Web port"               "$DEF_WEB_PORT"
ask TELNET_PORT     "Telnet port"            "$DEF_TELNET_PORT"
ask SSH_PORT        "SSH port"               "$DEF_SSH_PORT"
echo ""

echo -e "${BOLD}  ── Services to Enable ──${NC}"
ask_yn ENABLE_TELNET "Enable Telnet server? (y/n)"   "$DEF_ENABLE_TELNET"
ask_yn ENABLE_SSH    "Enable SSH server? (y/n)"       "$DEF_ENABLE_SSH"
ask_yn ENABLE_NGINX  "Enable nginx reverse proxy? (y/n)" "$DEF_ENABLE_NGINX"
ask_yn ENABLE_MRC    "Install MRC bridge service (inter-BBS chat)? (y/n)" "y"
ask_yn ENABLE_MSP    "Enable inter-BBS Instant Messaging (MSP/SYSTAT, privileged ports 18/11)? (y/n)" "$DEF_ENABLE_MSP"
ask_yn ENABLE_FINGER "Enable Finger service (RFC 1288, privileged port 79)? (y/n)" "$DEF_ENABLE_FINGER"
ask_yn ENABLE_BINKP  "Enable BinkP listener for FidoNet inbound mail (port 24554)? (y/n)" "$DEF_ENABLE_BINKP"

ENABLE_SSL="n"
if [[ "$DOMAIN" != "localhost" ]] && [[ "$ENABLE_NGINX" == "y" ]]; then
    ask_yn ENABLE_SSL "Enable SSL via Let's Encrypt? (y/n)" "$DEF_ENABLE_SSL"
fi

# In test or behind-another-server mode, refuse SSL even if user said
# yes — there's no public domain to certify against (test), or the
# certbot challenge would conflict with the sysop's existing web server
# on port 80 (behind). Sysop can run certbot manually later if they
# want HTTPS on the front-facing nginx instead.
if [[ "$INSTALL_MODE" == "test" || "$INSTALL_MODE" == "behind" ]]; then
    if [[ "$ENABLE_SSL" == "y" ]]; then
        warn "$INSTALL_MODE mode: forcing ENABLE_SSL=n."
        ENABLE_SSL="n"
    fi
fi

echo ""
echo -e "${BOLD}  ── Optional Components ──${NC}"
ask_yn INSTALL_DOSBOX "Install DOSBox-staging for DOS door games? (y/n)"      "n"
ask_yn INSTALL_CLAMAV "Install ClamAV for upload virus scanning? (y/n)"        "n"
ask_yn INSTALL_LHASA  "Install lhasa for .lzh archive description extraction? (y/n)" "n"
ask_yn INSTALL_SIXEL  "Install libsixel-bin for sixel images in terminal RSS reader? (y/n)" "n"
ask_yn INSTALL_MYSTIC "Install Mystic BBS runtime + mplc compiler (.mps door support)? (y/n)" "n"
# UFW: only meaningful in production mode (test mode is local-only)
if [[ "$INSTALL_MODE" == "production" ]]; then
    ask_yn CONFIGURE_UFW  "Configure UFW firewall (open BBS ports)? (y/n)"     "n"
else
    CONFIGURE_UFW="n"
fi

# ─── Generate SECRET_KEY ───────────────────────────────────────────────────────
SECRET_KEY=$(openssl rand -hex 32 2>/dev/null || python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || head -c 64 /dev/urandom | xxd -p | tr -d '\n' | head -c 64)

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIRMATION
# ═══════════════════════════════════════════════════════════════════════════════
echo ""
echo -e "${BOLD}${CYAN}┌──────────────────────────────────────────────────┐${NC}"
echo -e "${BOLD}${CYAN}│         Installation Summary                     │${NC}"
echo -e "${BOLD}${CYAN}└──────────────────────────────────────────────────┘${NC}"
echo -e "  Mode:            ${BOLD}${INSTALL_MODE}${NC}"
echo -e "  BBS Name:        ${BOLD}$BBS_NAME${NC}"
echo -e "  Description:     ${BOLD}$BBS_DESC${NC}"
echo -e "  System User:     ${BOLD}$SERVICE_USER${NC}"
echo -e "  Install Dir:     ${BOLD}$INSTALL_DIR${NC}"
echo -e "  Admin User:      ${BOLD}$ADMIN_USER${NC}"
echo -e "  Admin Password:  ${BOLD}$(if $ADMIN_PASS_GENERATED; then echo "$ADMIN_PASS (auto-generated)"; else echo "********"; fi)${NC}"
echo -e "  Domain:          ${BOLD}$DOMAIN${NC}"
echo -e "  Web Port:        ${BOLD}$WEB_PORT${NC} (bind ${WEB_BIND})"
echo -e "  Telnet:          ${BOLD}$([ "$ENABLE_TELNET" = "y" ] && echo "Enabled (port $TELNET_PORT)" || echo "Disabled")${NC}"
echo -e "  SSH:             ${BOLD}$([ "$ENABLE_SSH" = "y" ] && echo "Enabled (port $SSH_PORT)" || echo "Disabled")${NC}"
echo -e "  nginx:           ${BOLD}$([ "$ENABLE_NGINX" = "y" ] && echo "Enabled" || echo "Disabled")${NC}"
echo -e "  SSL:             ${BOLD}$([ "$ENABLE_SSL" = "y" ] && echo "Enabled" || echo "Disabled")${NC}"
echo -e "  Inter-BBS IM:    ${BOLD}$([ "$ENABLE_MSP" = "y" ] && echo "Enabled (MSP/18, SYSTAT/11)" || echo "Disabled")${NC}"
echo -e "  Finger:          ${BOLD}$([ "$ENABLE_FINGER" = "y" ] && echo "Enabled (port 79)" || echo "Disabled")${NC}"
echo -e "  BinkP:           ${BOLD}$([ "$ENABLE_BINKP" = "y" ] && echo "Enabled (port 24554)" || echo "Disabled")${NC}"
echo -e "  Mystic .mps:     ${BOLD}$([ "$INSTALL_MYSTIC" = "y" ] && echo "Will download" || echo "Skipped")${NC}"
echo -e "  OS:              ${BOLD}$OS_PRETTY${NC}"
echo ""

if ! $USE_DEFAULTS; then
    read -rp "  Proceed with installation? [Y/n]: " GO
    [[ "${GO,,}" == "n" ]] && { echo "Aborted."; exit 0; }
fi

echo ""

# ═══════════════════════════════════════════════════════════════════════════════
# PACKAGE INSTALLATION HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

heal_apt() {
    info "Healing package manager state..."
    dpkg --configure -a 2>/dev/null || true
    apt-get --fix-broken install -y 2>/dev/null || true
    apt-get update -qq 2>/dev/null || true
}

install_one_pkg() {
    local pkg="$1"
    case "$PKG_MANAGER" in
        apt)
            DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$pkg" 2>/dev/null
            ;;
        dnf)
            dnf install -y -q "$pkg" 2>/dev/null
            ;;
        yum)
            yum install -y -q "$pkg" 2>/dev/null
            ;;
        pacman)
            pacman -S --noconfirm --needed "$pkg" 2>/dev/null
            ;;
        zypper)
            zypper install -y -n "$pkg" 2>/dev/null
            ;;
        *)
            return 1
            ;;
    esac
}

pkg_name() {
    local generic="$1"
    case "$PKG_MANAGER" in
        apt)
            case "$generic" in
                python3)         echo "python3" ;;
                python3-venv)    echo "python3-venv" ;;
                python3-pip)     echo "python3-pip" ;;
                python3-dev)     echo "python3-dev" ;;
                build-essential) echo "build-essential" ;;
                rsync)           echo "rsync" ;;
                git)             echo "git" ;;
                curl)            echo "curl" ;;
                nodejs)          echo "nodejs" ;;
                npm)             echo "npm" ;;
                nginx)           echo "nginx" ;;
                certbot)         echo "certbot" ;;
                certbot-nginx)   echo "python3-certbot-nginx" ;;
                dosbox)          echo "dosbox" ;;
                openssh-client)  echo "openssh-client" ;;
                libffi-dev)      echo "libffi-dev" ;;
                libsixel-bin)    echo "libsixel-bin" ;;
                *)               echo "$generic" ;;
            esac
            ;;
        dnf|yum)
            case "$generic" in
                python3)         echo "python3" ;;
                python3-venv)    echo "python3" ;;
                python3-pip)     echo "python3-pip" ;;
                python3-dev)     echo "python3-devel" ;;
                build-essential) echo "gcc gcc-c++ make" ;;
                rsync)           echo "rsync" ;;
                git)             echo "git" ;;
                curl)            echo "curl" ;;
                nodejs)          echo "nodejs" ;;
                npm)             echo "npm" ;;
                nginx)           echo "nginx" ;;
                certbot)         echo "certbot" ;;
                certbot-nginx)   echo "python3-certbot-nginx" ;;
                dosbox)          echo "dosbox" ;;
                openssh-client)  echo "openssh-clients" ;;
                libffi-dev)      echo "libffi-devel" ;;
                libsixel-bin)    echo "libsixel" ;;
                *)               echo "$generic" ;;
            esac
            ;;
        pacman)
            case "$generic" in
                python3)         echo "python" ;;
                python3-venv)    echo "python" ;;
                python3-pip)     echo "python-pip" ;;
                python3-dev)     echo "python" ;;
                build-essential) echo "base-devel" ;;
                rsync)           echo "rsync" ;;
                git)             echo "git" ;;
                curl)            echo "curl" ;;
                nodejs)          echo "nodejs" ;;
                npm)             echo "npm" ;;
                nginx)           echo "nginx" ;;
                certbot)         echo "certbot" ;;
                certbot-nginx)   echo "certbot-nginx" ;;
                dosbox)          echo "dosbox" ;;
                openssh-client)  echo "openssh" ;;
                libffi-dev)      echo "libffi" ;;
                libsixel-bin)    echo "libsixel" ;;
                *)               echo "$generic" ;;
            esac
            ;;
        *)
            echo "$generic"
            ;;
    esac
}

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 1: HEAL & UPDATE PACKAGE MANAGER
# ═══════════════════════════════════════════════════════════════════════════════
step "Step 1/9: Preparing package manager"

case "$PKG_MANAGER" in
    apt)
        heal_apt
        info "Updating package lists..."
        apt-get update -qq 2>/dev/null || warn "apt-get update had warnings (continuing)"
        ok "APT ready"
        ;;
    dnf)
        info "Refreshing dnf cache..."
        dnf makecache -q 2>/dev/null || true
        ok "DNF ready"
        ;;
    yum)
        info "Refreshing yum cache..."
        yum makecache -q 2>/dev/null || true
        ok "YUM ready"
        ;;
    pacman)
        info "Refreshing pacman databases..."
        pacman -Sy --noconfirm 2>/dev/null || true
        ok "Pacman ready"
        ;;
esac

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 2: INSTALL REQUIRED PACKAGES
# ═══════════════════════════════════════════════════════════════════════════════
step "Step 2/9: Installing required system packages"

REQUIRED_PKGS=(python3 python3-venv python3-pip python3-dev build-essential libffi-dev rsync git curl openssh-client)
REQUIRED_FAILED=()

for generic in "${REQUIRED_PKGS[@]}"; do
    actual=$(pkg_name "$generic")
    for p in $actual; do
        if install_one_pkg "$p"; then
            ok "$p"
        else
            if [[ "$PKG_MANAGER" == "apt" ]]; then
                heal_apt
                if install_one_pkg "$p"; then
                    ok "$p (installed after repair)"
                else
                    REQUIRED_FAILED+=("$p")
                    bad "$p — FAILED"
                fi
            else
                REQUIRED_FAILED+=("$p")
                bad "$p — FAILED"
            fi
        fi
    done
done

if [[ ${#REQUIRED_FAILED[@]} -gt 0 ]]; then
    echo ""
    fail "The following REQUIRED packages could not be installed:"
    for p in "${REQUIRED_FAILED[@]}"; do
        echo -e "    ${RED}• $p${NC}"
    done
    echo ""
    fail "Please fix your system package manager and try again."
    echo -e "  On Ubuntu/Debian try:"
    echo -e "    ${CYAN}sudo dpkg --configure -a${NC}"
    echo -e "    ${CYAN}sudo apt --fix-broken install${NC}"
    echo -e "    ${CYAN}sudo apt update${NC}"
    echo -e "  Then re-run: ${CYAN}sudo bash install.sh${NC}"
    exit 1
fi

STATUS[required_pkgs]="ok"

# Re-detect Python after install
if [[ -z "$PYTHON_CMD" ]]; then
    for cmd in python3.12 python3.11 python3.10 python3; do
        if command -v "$cmd" &>/dev/null; then
            PYTHON_CMD="$cmd"
            break
        fi
    done
fi

if [[ -z "$PYTHON_CMD" ]]; then
    fail "Python3 is still not available after installation. Cannot continue."
    exit 1
fi

info "Using Python: $($PYTHON_CMD --version)"

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 3: INSTALL OPTIONAL PACKAGES
# ═══════════════════════════════════════════════════════════════════════════════
step "Step 3/9: Installing optional packages"

for generic in nodejs npm; do
    actual=$(pkg_name "$generic")
    if install_one_pkg "$actual"; then
        ok "$actual"
    else
        skip "$actual — not available (games requiring Node.js won't work, but BBS will run fine)"
    fi
done

if [[ "$ENABLE_NGINX" == "y" ]]; then
    actual=$(pkg_name "nginx")
    if install_one_pkg "$actual"; then
        ok "$actual"
        STATUS[nginx_pkg]="ok"
    else
        warn "nginx failed to install. You can set up a reverse proxy manually later."
        STATUS[nginx_pkg]="fail"
        ENABLE_NGINX="n"
    fi
fi

if [[ "$ENABLE_SSL" == "y" ]]; then
    actual_certbot=$(pkg_name "certbot")
    actual_plugin=$(pkg_name "certbot-nginx")
    CERTBOT_OK=false

    if install_one_pkg "$actual_certbot" && install_one_pkg "$actual_plugin"; then
        ok "certbot (via $PKG_MANAGER)"
        CERTBOT_OK=true
    else
        warn "certbot failed via $PKG_MANAGER, trying snap..."
        if command -v snap &>/dev/null || install_one_pkg "snapd"; then
            snap install core 2>/dev/null || true
            if snap install --classic certbot 2>/dev/null; then
                ln -sf /snap/bin/certbot /usr/bin/certbot 2>/dev/null || true
                ok "certbot (via snap)"
                CERTBOT_OK=true
            fi
        fi
    fi

    if ! $CERTBOT_OK; then
        warn "certbot could not be installed. SSL will be skipped."
        warn "You can set up SSL manually later with: certbot --nginx -d $DOMAIN"
        ENABLE_SSL="n"
    fi
fi

if [[ "$INSTALL_DOSBOX" == "y" ]]; then
    # Prefer dosbox-staging (better TCP nullmodem support); fall back to vanilla.
    if install_one_pkg "dosbox-staging" 2>/dev/null; then
        ok "dosbox-staging"
    elif install_one_pkg "$(pkg_name "dosbox")"; then
        ok "dosbox (vanilla)"
    else
        skip "DOSBox — door games using DOSBox won't work"
    fi
fi

if [[ "$INSTALL_CLAMAV" == "y" ]]; then
    if install_one_pkg "clamav" && install_one_pkg "clamav-daemon"; then
        ok "clamav + clamav-daemon"
        # First freshclam can take a minute; do it in the background so install
        # doesn't block. Service starts when ready.
        systemctl enable --now clamav-freshclam 2>/dev/null || true
        systemctl enable --now clamav-daemon 2>/dev/null || true
    else
        skip "ClamAV — virus scan on uploads will be silently disabled"
    fi
fi

if [[ "$INSTALL_LHASA" == "y" ]]; then
    if install_one_pkg "lhasa"; then
        ok "lhasa (.lzh archive support)"
    else
        skip "lhasa — FILE_ID.DIZ extraction from .lzh archives won't work"
    fi
fi

if [[ "$INSTALL_SIXEL" == "y" ]]; then
    actual=$(pkg_name "libsixel-bin")
    if install_one_pkg "$actual"; then
        ok "$actual (img2sixel — sixel images in terminal RSS reader)"
    else
        skip "libsixel — terminal RSS images will be skipped (text-only mode)"
    fi
fi

# ─── Optional: Mystic BBS runtime + mplc compiler ────────────────────────────
# Mystic isn't apt-packaged. We download the official freeware tarball
# from mysticbbs.com and extract just the two binaries we need:
#   /opt/mystic/mystic   — runs compiled .mpx Pascal scripts
#   /opt/mystic/mplc     — compiles .mps source -> .mpx
# Soft-fails on download issues (no network, URL changed, etc.) so a
# headless install doesn't abort just because we couldn't fetch Mystic.
if [[ "$INSTALL_MYSTIC" == "y" ]]; then
    MYSTIC_DIR="/opt/mystic"
    BUNDLED_MYSTIC="$SOURCE_DIR/vendor/mystic"

    if [[ -x "$BUNDLED_MYSTIC/mystic" && -x "$BUNDLED_MYSTIC/mplc" ]]; then
        # Bundled inside the alpha tarball — copy the whole tree.
        # Mystic at runtime expects its data/menus/themes dirs next to the
        # binary, so we ship the full freeware install (~5 MB) rather than
        # just the two ELFs.
        info "Installing bundled Mystic BBS runtime + mplc compiler..."
        mkdir -p "$MYSTIC_DIR"
        cp -a "$BUNDLED_MYSTIC/." "$MYSTIC_DIR/"
        chmod +x "$MYSTIC_DIR/mystic" "$MYSTIC_DIR/mplc" 2>/dev/null || true
        ln -sf "$MYSTIC_DIR/mplc"   /usr/local/bin/mplc   2>/dev/null || true
        ln -sf "$MYSTIC_DIR/mystic" /usr/local/bin/mystic 2>/dev/null || true
        ok "mystic + mplc installed to $MYSTIC_DIR (from bundled copy)"
        STATUS[mystic]="ok"
        MYSTIC_INSTALLED="y"
    else
        # Fallback path — vendor/mystic missing (e.g. running from a slim
        # checkout). Pull the installer from mysticbbs.com.
        info "Bundled mystic not found — downloading Mystic BBS installer..."
        MYSTIC_URL_X64="https://mysticbbs.com/downloads/mys112a48_l64.rar"
        TMP_RAR="/tmp/mystic-linux64.rar"
        TMP_STAGE="/tmp/mystic-installer-$$"

        if ! command -v unrar &>/dev/null; then
            info "Installing unrar (needed to extract Mystic archive)..."
            install_one_pkg unrar 2>/dev/null || install_one_pkg unrar-free 2>/dev/null || true
        fi

        mkdir -p "$MYSTIC_DIR" "$TMP_STAGE"
        if curl -fsSL --connect-timeout 15 --max-time 180 \
                -o "$TMP_RAR" "$MYSTIC_URL_X64" 2>/dev/null; then
            EXTRACT_OK=""
            if command -v unrar &>/dev/null; then
                (cd "$TMP_STAGE" && unrar x -y "$TMP_RAR" >/dev/null 2>&1) && EXTRACT_OK=1
            elif command -v 7z &>/dev/null; then
                7z x -y -o"$TMP_STAGE" "$TMP_RAR" >/dev/null 2>&1 && EXTRACT_OK=1
            fi
            # The rar contains a Mystic *installer* binary (./install), not the
            # runtime. Run it in unattended auto mode to extract everything.
            if [[ -n "$EXTRACT_OK" && -x "$TMP_STAGE/install" ]]; then
                if (cd "$TMP_STAGE" && ./install auto "$MYSTIC_DIR" overwrite >/dev/null 2>&1); then
                    chmod +x "$MYSTIC_DIR/mystic" "$MYSTIC_DIR/mplc" 2>/dev/null || true
                    ln -sf "$MYSTIC_DIR/mplc"   /usr/local/bin/mplc   2>/dev/null || true
                    ln -sf "$MYSTIC_DIR/mystic" /usr/local/bin/mystic 2>/dev/null || true
                    if [[ -x "$MYSTIC_DIR/mystic" && -x "$MYSTIC_DIR/mplc" ]]; then
                        ok "mystic + mplc installed to $MYSTIC_DIR"
                        STATUS[mystic]="ok"
                        MYSTIC_INSTALLED="y"
                    else
                        skip "Mystic — installer ran but mystic/mplc missing in $MYSTIC_DIR"
                    fi
                else
                    skip "Mystic — installer auto-run failed"
                fi
            elif [[ -n "$EXTRACT_OK" ]]; then
                skip "Mystic — no ./install binary inside the archive"
            else
                skip "Mystic — archive extract failed (need unrar or 7z)"
            fi
            rm -f "$TMP_RAR" 2>/dev/null || true
            rm -rf "$TMP_STAGE" 2>/dev/null || true
        else
            warn "Mystic — download from $MYSTIC_URL_X64 failed."
            warn "       You can install manually later: drop mystic + mplc"
            warn "       binaries in $MYSTIC_DIR/ and re-run with MYSTIC_BBS_PATH set."
            STATUS[mystic]="fail"
        fi
    fi
fi

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 4: CREATE SYSTEM USER & DIRECTORIES
# ═══════════════════════════════════════════════════════════════════════════════
step "Step 4/9: Creating system user and directories"

if id "$SERVICE_USER" &>/dev/null; then
    info "User '$SERVICE_USER' already exists."
else
    useradd -r -d "$INSTALL_DIR" -s /usr/sbin/nologin "$SERVICE_USER" 2>/dev/null || \
    useradd -r -d "$INSTALL_DIR" -s /bin/false "$SERVICE_USER" 2>/dev/null || \
    { fail "Could not create user '$SERVICE_USER'"; exit 1; }
    ok "Created system user: $SERVICE_USER"
fi

mkdir -p "$INSTALL_DIR"
ok "Install directory: $INSTALL_DIR"

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 5: COPY APPLICATION FILES
# ═══════════════════════════════════════════════════════════════════════════════
step "Step 5/9: Deploying application files"

if [[ "$SOURCE_DIR" != "$INSTALL_DIR" ]]; then
    info "Copying files from $SOURCE_DIR to $INSTALL_DIR ..."
    # MRC bridge config + per-network configs + per-network data dirs are
    # intentionally excluded: they hold the *previous* sysop's BBS handle,
    # MRC server, and chat-profile state from whatever box built the
    # tarball. config.json is regenerated in step 7 from the wizard
    # inputs; per-network configs / data dirs are sysop-created at
    # runtime via the admin UI.
    rsync -a --exclude='.git' --exclude='venv' --exclude='__pycache__' \
          --exclude='*.pyc' --exclude='data/*.db' \
          --exclude='/mrc/bridge/config.json' \
          --exclude='/mrc/bridge/config/' \
          --exclude='/mrc/bridge/data-*/' \
          "$SOURCE_DIR/" "$INSTALL_DIR/"
    ok "Files deployed"
else
    info "Already running from install directory."
    ok "Files in place"
fi

# Create all data directories
for d in data data/uploads data/avatars data/games data/dos-games data/echomail data/mrc data/text data/text/menus logs anetbbs/logs; do
    mkdir -p "$INSTALL_DIR/$d"
done
ok "Data directories created"

# Set ownership with proper permissions — service user must be able
# to write to data/, logs/, and the entire install tree.
# Note: anetbbs/core, anetbbs/features, and anetbbs/main.py are real files in the
# repo now — no symlink or shim hacks needed.
chown -R "$SERVICE_USER":"$SERVICE_USER" "$INSTALL_DIR"
chmod -R u+rwX "$INSTALL_DIR"
chmod 755 "$INSTALL_DIR/data" "$INSTALL_DIR/logs"
# sbbs_stubs ships from a Synchronet install with mode 700 — anetbbs.js
# door scripts read these files at runtime, so they need to be readable
# by the service user even when the install dir is owned by the sysop.
if [[ -d "$INSTALL_DIR/anetbbs/games/sbbs_stubs" ]]; then
    chmod -R g+rX,o+rX "$INSTALL_DIR/anetbbs/games/sbbs_stubs"
fi
# Door save dirs: doors run as $SERVICE_USER and need to write here even
# if the door tree was dropped in by a sysop's regular account.
if [[ -d "$INSTALL_DIR/doors" ]]; then
    chmod -R g+rwX "$INSTALL_DIR/doors"
fi
ok "Ownership and permissions set to $SERVICE_USER"

STATUS[files]="ok"

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 6: PYTHON VIRTUAL ENVIRONMENT & DEPENDENCIES
# ═══════════════════════════════════════════════════════════════════════════════
step "Step 6/9: Setting up Python environment"

VENV_DIR="$INSTALL_DIR/venv"

if [[ ! -d "$VENV_DIR" ]]; then
    info "Creating virtual environment..."
    sudo -u "$SERVICE_USER" "$PYTHON_CMD" -m venv "$VENV_DIR" 2>/dev/null || \
    "$PYTHON_CMD" -m venv "$VENV_DIR" 2>/dev/null || \
    { fail "Could not create Python virtual environment."; exit 1; }
    ok "Virtual environment created"
else
    info "Virtual environment already exists."
fi

info "Upgrading pip..."
sudo -u "$SERVICE_USER" "$VENV_DIR/bin/pip" install --upgrade pip --quiet 2>/dev/null || \
"$VENV_DIR/bin/pip" install --upgrade pip --quiet 2>/dev/null || true

info "Installing ANetBBS Python dependencies (this may take a minute)..."
cd "$INSTALL_DIR"
PIP_LOG=$(mktemp)
# --prefer-binary: use pre-built wheels when available (avoids source-compile
# failures on ARM64 / Pi where Rust/C toolchains may be incomplete).
if sudo -u "$SERVICE_USER" "$VENV_DIR/bin/pip" install --prefer-binary -e "$INSTALL_DIR" 2>"$PIP_LOG" || \
   "$VENV_DIR/bin/pip" install --prefer-binary -e "$INSTALL_DIR" 2>"$PIP_LOG"; then
    ok "Python dependencies installed"
    STATUS[python]="ok"
else
    if sudo -u "$SERVICE_USER" "$VENV_DIR/bin/pip" install --prefer-binary "$INSTALL_DIR" 2>"$PIP_LOG" || \
       "$VENV_DIR/bin/pip" install --prefer-binary "$INSTALL_DIR" 2>"$PIP_LOG"; then
        ok "Python dependencies installed (non-editable)"
        STATUS[python]="ok"
    else
        fail "pip install failed — output below:"
        cat "$PIP_LOG" | while read -r line; do echo -e "    ${DIM}$line${NC}"; done
        fail "Fix the dependency error above, then re-run install.sh."
        STATUS[python]="fail"
    fi
fi
rm -f "$PIP_LOG"

# Verify that the package actually imports cleanly. setup.py declares aiohttp
# as a dependency now, so a successful pip install above already pulled it in.
info "Verifying package imports..."
if "$VENV_DIR/bin/python" -c "from anetbbs.main import main; from anetbbs.web_app import create_app; print('OK')" 2>/dev/null; then
    ok "anetbbs package verified (telnet, web entry points OK)"
else
    fail "anetbbs package failed to import — check logs above for ModuleNotFoundError"
    STATUS[python]="fail"
fi

# Fix ownership again after pip install (pip may have created files as root)
chown -R "$SERVICE_USER":"$SERVICE_USER" "$INSTALL_DIR"

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 7: GENERATE CONFIGURATION
# ═══════════════════════════════════════════════════════���═══════════════════════
step "Step 7/9: Generating configuration"

ENV_FILE="$INSTALL_DIR/.env"

if [[ -f "$ENV_FILE" ]] && ! $FORCE_OVERWRITE; then
    info ".env already exists — skipping (use --force to overwrite)"
    skip ".env preserved"
else
# ★ FIX 4: Use ABSOLUTE path for DATABASE_URL so gunicorn can always find
# the SQLite file regardless of its WorkingDirectory.
# Also set DATA_DIR explicitly so config.py resolves correctly.
cat > "$ENV_FILE" << ENVEOF
# ANetBBS Configuration
# Generated by install.sh on $(date)

# Environment
FLASK_ENV=production

# Security
SECRET_KEY=$SECRET_KEY
# Set to true only when the web interface is served over HTTPS (e.g. via nginx
# with TLS). Leaving false lets HTTP-only installs work; with true the browser
# silently drops the session cookie on plain HTTP, breaking login (CSRF error).
SESSION_COOKIE_SECURE=$( [[ "$ENABLE_SSL" == "y" ]] && echo "true" || echo "false" )

# Database — ABSOLUTE path so it works from any working directory
DATABASE_URL=sqlite:///${INSTALL_DIR}/data/anetbbs.db

# Telnet Server
TELNET_ENABLED=$( [ "$ENABLE_TELNET" = "y" ] && echo "true" || echo "false" )
TELNET_HOST=0.0.0.0
TELNET_PORT=$TELNET_PORT

# SSH Server
SSH_ENABLED=$( [ "$ENABLE_SSH" = "y" ] && echo "true" || echo "false" )
SSH_HOST=0.0.0.0
SSH_PORT=$SSH_PORT
SSH_HOST_KEY_FILE=${INSTALL_DIR}/data/ssh_host_key

# rlogin (disabled)
RLOGIN_ENABLED=false
RLOGIN_HOST=0.0.0.0
RLOGIN_PORT=513

# Web Server
WEB_HOST=0.0.0.0
WEB_PORT=$WEB_PORT

# Application
BBS_NAME=$BBS_NAME
BBS_DESCRIPTION=$BBS_DESC
SYSOP_NAME=$SYSOP_NAME

# Inter-BBS Instant Messaging (MSP / RFC 1312, ports 18 TCP + 11 UDP).
# Disabled here means the listener threads don't start; outbound /imsg/send
# from the web UI still works.
MSP_ENABLED=$([[ "$ENABLE_MSP" == "y" ]] && echo true || echo false)
SYSTAT_ENABLED=$([[ "$ENABLE_MSP" == "y" ]] && echo true || echo false)

# Logging
LOG_LEVEL=INFO

# Games
GAMES_ENABLED=true
GAMES_MAX_NODES=10
GAMES_SESSION_TIMEOUT=3600
DOSBOX_PATH=/usr/bin/dosbox
DOSEMU_PATH=/usr/bin/dosemu
NODEJS_PATH=$(command -v node 2>/dev/null || echo "/usr/bin/node")
MYSTIC_PYTHON_PATH=
MYSTIC_BBS_PATH=$([[ "${MYSTIC_INSTALLED:-n}" == "y" ]] && echo "/opt/mystic/mystic" || echo "/usr/local/bin/mystic")
MYSTIC_MPLC_PATH=$([[ "${MYSTIC_INSTALLED:-n}" == "y" ]] && echo "/opt/mystic/mplc" || echo "")

# Echomail
ECHOMAIL_ENABLED=true
ECHOMAIL_POLL_ENABLED=true
ECHOMAIL_ORIGIN_LINE=$BBS_NAME - $BBS_DESC
ECHOMAIL_TEAR_LINE=--- $BBS_NAME

# MRC Bridge
MRC_BRIDGE_HOST=localhost
MRC_BRIDGE_PORT=8080
MRC_BRIDGE_USE_SSL=false
MRC_BRIDGE_WS_PATH=/mrcws
ENVEOF

chown "$SERVICE_USER":"$SERVICE_USER" "$ENV_FILE"
chmod 600 "$ENV_FILE"
ok ".env generated"
fi

# ─── SSH Host Key ──────────────────────────────────────────────────────────────
SSH_KEY_FILE="$INSTALL_DIR/data/ssh_host_key"
if [[ ! -f "$SSH_KEY_FILE" ]] && [[ "$ENABLE_SSH" == "y" ]]; then
    info "Generating SSH host key..."
    sudo -u "$SERVICE_USER" ssh-keygen -t rsa -b 2048 -f "$SSH_KEY_FILE" -N "" -q 2>/dev/null || \
    ssh-keygen -t rsa -b 2048 -f "$SSH_KEY_FILE" -N "" -q 2>/dev/null || true
    ok "SSH host key generated"
fi
# Always reassert ownership/mode — covers re-runs where the key was
# generated by an earlier shell user (e.g. sysop ran ssh-keygen by hand)
# and the service account can't read it.
if [[ -f "$SSH_KEY_FILE" ]]; then
    chown "$SERVICE_USER":"$SERVICE_USER" "$SSH_KEY_FILE" "$SSH_KEY_FILE.pub" 2>/dev/null || true
    chmod 600 "$SSH_KEY_FILE" 2>/dev/null || true
    chmod 644 "$SSH_KEY_FILE.pub" 2>/dev/null || true
fi

# ─── Pre-create application log so the service user owns it from the start ────
APP_LOG_FILE="$INSTALL_DIR/logs/anetbbs.log"
touch "$APP_LOG_FILE" 2>/dev/null || true
chown "$SERVICE_USER":"$SERVICE_USER" "$APP_LOG_FILE" 2>/dev/null || true
chmod 664 "$APP_LOG_FILE" 2>/dev/null || true

# ─── MRC Bridge config.json ────────────────────────────────────────────────────
# Always generated from the install wizard inputs — never copied from the
# source tarball (Step 5's rsync excludes mrc/bridge/config.json) so that
# the sysop who built the tarball can't leak their own BBS info to a
# fresh installer. On re-runs the existing file is preserved.
MRC_CONFIG_FILE="$INSTALL_DIR/mrc/bridge/config.json"
if [[ ! -f "$MRC_CONFIG_FILE" ]]; then
    info "Generating MRC bridge config from wizard inputs..."
    mkdir -p "$INSTALL_DIR/mrc/bridge"
    # Build info_web: prefer https when SSL is on, http otherwise, and skip
    # the field entirely when the install is local-only (test mode).
    if [[ "$DOMAIN" == "localhost" ]]; then
        INFO_WEB=""
    elif [[ "$ENABLE_SSL" == "y" ]]; then
        INFO_WEB="https://$DOMAIN"
    else
        INFO_WEB="http://$DOMAIN"
    fi
    cat > "$MRC_CONFIG_FILE" << MRCEOF
{
  "mrc_host": "mrc.bottomlessabyss.net",
  "mrc_port": 5001,
  "use_ssl": true,
  "bridge_bbs": "$BBS_NAME",
  "platform_info": "ANETBBS/Linux.$(uname -m)/$BBS_VERSION",
  "info_web": "$INFO_WEB",
  "info_sysop": "$ADMIN_USER",
  "info_desc": "$BBS_DESC",
  "capabilities": ["MCI", "MSGEXT", "CTCP"],
  "web_listen_host": "127.0.0.1",
  "web_listen_port": 8080,
  "message_rate_seconds": 0.5,
  "iamhere_interval_seconds": 60,
  "log_level": "INFO",
  "data_dir": "$INSTALL_DIR/data/mrc"
}
MRCEOF
    chown "$SERVICE_USER":"$SERVICE_USER" "$MRC_CONFIG_FILE"
    chmod 640 "$MRC_CONFIG_FILE"
    ok "MRC bridge config.json generated (sysop: $ADMIN_USER, BBS: $BBS_NAME)"
else
    info "MRC bridge config.json already exists — preserving"
    skip "mrc/bridge/config.json preserved (delete it and re-run to regenerate)"
fi

# ─── Database setup: preserve existing DB; only create fresh if none exists ───
DB_FILE="$INSTALL_DIR/data/anetbbs.db"
if [[ -f "$DB_FILE" ]] && ! $FORCE_OVERWRITE; then
    info "Existing database found — preserving data (db.create_all() will add any missing tables)"
    skip "Database preserved"
elif [[ -f "$DB_FILE" ]] && $FORCE_OVERWRITE; then
    DB_BAK="${DB_FILE}.bak.$(date +%Y%m%d%H%M%S)"
    info "Existing database found — backing up to $(basename "$DB_BAK") (--force)"
    cp "$DB_FILE" "$DB_BAK"
    rm -f "$DB_FILE" "${DB_FILE}-journal" "${DB_FILE}-wal" "${DB_FILE}-shm"
    ok "Old database backed up; a fresh database will be created"
fi

# ─── Set admin password via Python ─────────────────────────────────────────────
if [[ "${STATUS[python]:-}" != "ok" ]]; then
    warn "Skipping admin account setup — Python environment failed to install."
    warn "Fix the pip error above, then re-run: sudo bash $INSTALL_DIR/install.sh"
else
# ★ FIX 5: Ensure the data directory is writable by service user BEFORE we
# create the database, and run the admin setup as the SERVICE_USER so the
# SQLite file is created with correct ownership from the start.
info "Configuring sysop admin account..."
chown -R "$SERVICE_USER":"$SERVICE_USER" "$INSTALL_DIR/data"
chmod 755 "$INSTALL_DIR/data"

cd "$INSTALL_DIR"
if sudo -u "$SERVICE_USER" "$VENV_DIR/bin/python" << ADMINEOF
import os, sys
sys.path.insert(0, '$INSTALL_DIR')
os.chdir('$INSTALL_DIR')
os.environ['FLASK_ENV'] = 'production'
os.environ['DATABASE_URL'] = 'sqlite:///${INSTALL_DIR}/data/anetbbs.db'
os.environ['SECRET_KEY'] = '$SECRET_KEY'

from dotenv import load_dotenv
load_dotenv('$ENV_FILE')

from anetbbs.web_app import create_app
from anetbbs.models import db, User, EchomailNetwork, EchoArea, EchomailMessage, EchomailReadStatus, EchomailPollLog

app = create_app('production')
with app.app_context():
    db.create_all()
    user = User.query.filter_by(username='$ADMIN_USER').first()
    if user is None:
        user = User(username='$ADMIN_USER', email='${ADMIN_USER}@${DOMAIN}', is_admin=True)
        user.set_password('$ADMIN_PASS')
        db.session.add(user)
        db.session.commit()
        print('CREATED')
    else:
        user.set_password('$ADMIN_PASS')
        user.is_admin = True
        db.session.commit()
        print('UPDATED')
ADMINEOF
then
    ok "Admin account configured ($ADMIN_USER)"
else
    # ★ FIX 5b: If running as service user fails, try as root then fix ownership
    warn "Retrying admin setup as root..."
    if "$VENV_DIR/bin/python" << ADMINEOF2
import os, sys
sys.path.insert(0, '$INSTALL_DIR')
os.chdir('$INSTALL_DIR')
os.environ['FLASK_ENV'] = 'production'
os.environ['DATABASE_URL'] = 'sqlite:///${INSTALL_DIR}/data/anetbbs.db'
os.environ['SECRET_KEY'] = '$SECRET_KEY'

from dotenv import load_dotenv
load_dotenv('$ENV_FILE')

from anetbbs.web_app import create_app
from anetbbs.models import db, User, EchomailNetwork, EchoArea, EchomailMessage, EchomailReadStatus, EchomailPollLog

app = create_app('production')
with app.app_context():
    db.create_all()
    user = User.query.filter_by(username='$ADMIN_USER').first()
    if user is None:
        user = User(username='$ADMIN_USER', email='${ADMIN_USER}@${DOMAIN}', is_admin=True)
        user.set_password('$ADMIN_PASS')
        db.session.add(user)
        db.session.commit()
        print('CREATED')
    else:
        user.set_password('$ADMIN_PASS')
        user.is_admin = True
        db.session.commit()
        print('UPDATED')
ADMINEOF2
    then
        ok "Admin account configured ($ADMIN_USER)"
    else
        warn "Could not configure admin account (you can use default: admin / admin123)"
    fi
    # Fix ownership after root created the db
    chown -R "$SERVICE_USER":"$SERVICE_USER" "$INSTALL_DIR/data"
fi
fi  # end: STATUS[python] == ok guard

# ★ FIX 6: Final ownership + permissions sweep on data directory
# This catches both cases: db created as service user or as root
chown -R "$SERVICE_USER":"$SERVICE_USER" "$INSTALL_DIR/data"
chmod 755 "$INSTALL_DIR/data"
chmod 644 "$INSTALL_DIR/data"/*.db 2>/dev/null || true

STATUS[config]="ok"

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 8: SYSTEMD SERVICES
# ═══════════════════════════════════════════════════════════════════════════════
step "Step 8/9: Installing systemd services"

# ★ FIX 7: Remove any stale service files from previous installs before writing
# fresh ones. This prevents old service files (e.g. from deploy/ templates that
# reference /opt/anetbbs/venv/bin/anetbbs console script) from persisting.
info "Cleaning up stale service files..."
# anetbbs-telnet + anetbbs-ssh are legacy split-protocol services that
# fought each other for ports because Ubuntu systemd's EnvironmentFile
# wins over Environment= (opposite of documented behavior). Replaced by
# a single anetbbs.service that reads .env and starts whichever of
# telnet/ssh/rlogin are enabled. Stop+disable+remove the legacy units
# on every install so the migration is clean.
for svc in anetbbs-web anetbbs anetbbs-telnet anetbbs-ssh anetbbs-mrc-bridge anetbbs-finger; do
    systemctl stop "$svc" 2>/dev/null || true
    systemctl disable "$svc" 2>/dev/null || true
    rm -f "/etc/systemd/system/${svc}.service"
done
systemctl daemon-reload 2>/dev/null || true
# and all paths are absolute so services work regardless of cwd.
# The web service uses gunicorn with deploy.wsgi_wrapper:app
# The telnet/SSH services use $VENV_DIR/bin/python $INSTALL_DIR/main.py
# (NOT the console script, which has historically broken)

# ─── Web service ────────────────────────────────────────────────────────────��──
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
# privileged ports MSP (TCP/18) and SYSTAT/ActiveUser (UDP/11). Without
# this, those listeners fail to bind and inter-BBS IM is broken silently.
# NOTE: We intentionally do NOT set CapabilityBoundingSet here so that
# sudo (used by the Service Control Center) can still escalate.
AmbientCapabilities=CAP_NET_BIND_SERVICE
ExecStart=$VENV_DIR/bin/python $INSTALL_DIR/deploy/serve.py
Restart=always
RestartSec=5
TimeoutStopSec=45
KillMode=mixed

[Install]
WantedBy=multi-user.target
SVCEOF
ok "anetbbs-web.service"

# ─── Terminal-protocol service (telnet/ssh/rlogin in one process) ──────────────
# A single anetbbs.service owns telnet (2233), ssh (2234), and rlogin
# (5132). Which protocols actually start is driven by the *_ENABLED
# flags in $INSTALL_DIR/.env — no Environment= directives in this unit
# because Ubuntu systemd treats EnvironmentFile as winning over them
# (opposite of documented behavior), so the unit had no way to override
# .env on the v1.0a release line. The collapsed design just trusts .env.
# This replaces the legacy anetbbs-telnet.service + anetbbs-ssh.service
# pair, which silently fought each other for ports.
if [[ "$ENABLE_TELNET" == "y" || "$ENABLE_SSH" == "y" ]]; then
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
ok "anetbbs.service"
fi

# ─── MRC Bridge service ────────────────────────────────────────────────────────
if [[ "$ENABLE_MRC" == "y" ]]; then
cat > /etc/systemd/system/anetbbs-mrc-bridge.service << SVCEOF
[Unit]
Description=ANetBBS MRC Bridge Service
After=network.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$INSTALL_DIR
Environment=MRC_BRIDGE_CONFIG=$INSTALL_DIR/mrc/bridge/config.json
ExecStart=$VENV_DIR/bin/python -m mrc.bridge.main
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
SVCEOF
ok "anetbbs-mrc-bridge.service"
fi

# ─── Finger service ───────────────────────────────────────────────────────────
# Privileged port 79. Production mode adds CAP_NET_BIND_SERVICE so the
# unprivileged service user can bind it. Test mode skips this entirely.
if [[ "$ENABLE_FINGER" == "y" ]]; then
cat > /etc/systemd/system/anetbbs-finger.service << SVCEOF
[Unit]
Description=ANetBBS Finger Server (RFC 1288)
After=network.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$INSTALL_DIR
EnvironmentFile=$INSTALL_DIR/.env
AmbientCapabilities=CAP_NET_BIND_SERVICE
ExecStart=$VENV_DIR/bin/python -m anetbbs.core.finger_server
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
SVCEOF
ok "anetbbs-finger.service"
fi

# ─── Reload and start ─────────────────────────────────────────────────────────
systemctl daemon-reload

# Clean up stale /tmp log files that may be owned by root from a previous run
info "Cleaning up stale log files in /tmp..."
rm -f /tmp/mrc_*.log 2>/dev/null || true

if [[ "${STATUS[python]:-}" != "ok" ]]; then
    warn "Skipping service starts — Python environment is not installed."
    warn "Fix the pip error shown above, then re-run: sudo bash $INSTALL_DIR/install.sh"
else

SERVICES_TO_START=(anetbbs-web)
# One service for all terminal protocols — telnet, ssh, rlogin live in
# the same process driven by .env flags. Start it if any one is on.
[[ "$ENABLE_TELNET" == "y" || "$ENABLE_SSH" == "y" ]] && SERVICES_TO_START+=(anetbbs)
[[ "$ENABLE_MRC" == "y" ]]    && SERVICES_TO_START+=(anetbbs-mrc-bridge)
[[ "$ENABLE_FINGER" == "y" ]] && SERVICES_TO_START+=(anetbbs-finger)

for svc in "${SERVICES_TO_START[@]}"; do
    systemctl enable "$svc" 2>/dev/null || true
    systemctl restart "$svc" 2>/dev/null || true
done

# ★ FIX 10: Give services more time to start (especially gunicorn which loads
# Flask + SQLAlchemy + creates tables on first boot)
sleep 5

for svc in "${SERVICES_TO_START[@]}"; do
    if systemctl is-active --quiet "$svc"; then
        ok "$svc is running"
        STATUS[$svc]="ok"
    else
        bad "$svc failed to start"
        STATUS[$svc]="fail"
        warn "Last log lines:"
        journalctl -u "$svc" --no-pager -n 10 2>/dev/null | while read -r line; do
            echo -e "    ${DIM}$line${NC}"
        done
    fi
done

fi  # end: STATUS[python] == ok guard

# ─── Optional: UFW firewall rules ─────────────────────────────────────────────
if [[ "$CONFIGURE_UFW" == "y" ]]; then
    if command -v ufw &>/dev/null; then
        info "Opening BBS ports in UFW..."
        if [[ "$ENABLE_NGINX" == "y" ]]; then
            ufw allow 80/tcp  >/dev/null 2>&1 && ok "  80/tcp (HTTP)"
            ufw allow 443/tcp >/dev/null 2>&1 && ok "  443/tcp (HTTPS)"
        else
            ufw allow "$WEB_PORT"/tcp >/dev/null 2>&1 && ok "  $WEB_PORT/tcp (web)"
        fi
        [[ "$ENABLE_TELNET" == "y" ]] && ufw allow "$TELNET_PORT"/tcp >/dev/null 2>&1 && ok "  $TELNET_PORT/tcp (telnet)"
        [[ "$ENABLE_SSH" == "y" ]]    && ufw allow "$SSH_PORT"/tcp    >/dev/null 2>&1 && ok "  $SSH_PORT/tcp (ssh)"
        if [[ "$ENABLE_MSP" == "y" ]]; then
            ufw allow 18/tcp >/dev/null 2>&1 && ok "  18/tcp (MSP)"
            ufw allow 11/udp >/dev/null 2>&1 && ok "  11/udp (SYSTAT)"
        fi
        [[ "$ENABLE_FINGER" == "y" ]] && ufw allow 79/tcp >/dev/null 2>&1 && ok "  79/tcp (Finger)"
        [[ "$ENABLE_BINKP" == "y" ]]  && ufw allow 24554/tcp >/dev/null 2>&1 && ok "  24554/tcp (BinkP / FidoNet)"
        if ufw status | grep -q "Status: active"; then
            ufw reload >/dev/null 2>&1 && ok "  UFW reloaded"
        else
            warn "  UFW is inactive — rules added but firewall not enabled."
            warn "  Run 'sudo ufw enable' when ready (after verifying SSH access works)."
        fi
    else
        warn "UFW is not installed — skipping firewall config."
    fi
fi

# ═══════════════════════════════════════════════════════════════════════════��═══
# STEP 9: NGINX & SSL
# ═══════════════════════════════════════════════════════════════════════════════
step "Step 9/9: Configuring nginx & SSL"

if [[ "$ENABLE_NGINX" == "y" ]] && [[ "${STATUS[nginx_pkg]:-}" == "ok" || -x /usr/sbin/nginx ]]; then
    NGINX_AVAIL="/etc/nginx/sites-available/anetbbs"
    NGINX_ENABLED="/etc/nginx/sites-enabled/anetbbs"

    mkdir -p /etc/nginx/sites-available /etc/nginx/sites-enabled
    if ! grep -q "sites-enabled" /etc/nginx/nginx.conf 2>/dev/null; then
        sed -i '/http {/a \    include /etc/nginx/sites-enabled/*;' /etc/nginx/nginx.conf 2>/dev/null || true
    fi

    # Always start with an HTTP-only config — even if SSL is requested.
    # The deploy/anetbbs-nginx.conf.template references
    # /etc/letsencrypt/live/<domain>/fullchain.pem which doesn't exist
    # yet on a fresh install, so writing the SSL template first makes
    # `nginx -t` fail and certbot can't run because nginx is broken.
    # Write HTTP-only first, then `certbot --nginx` (below) auto-edits
    # this same file to add the HTTPS server block + redirect using the
    # cert it provisions.
    cat > "$NGINX_AVAIL" << NGINXEOF
# ANetBBS — nginx reverse proxy (HTTP)
# Generated by install.sh on $(date)

server {
    listen 80;
    server_name ${DOMAIN};

    # Accept uploads up to the BBS's UPLOAD_MAX_SIZE (100MB) plus headroom.
    client_max_body_size 110m;

    proxy_set_header Host              \$host;
    proxy_set_header X-Real-IP         \$remote_addr;
    proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \$scheme;

    location / {
        proxy_pass http://127.0.0.1:${WEB_PORT};
        proxy_read_timeout 120s;
    }

    location /socket.io/ {
        proxy_pass         http://127.0.0.1:${WEB_PORT}/socket.io/;
        proxy_http_version 1.1;
        proxy_set_header   Upgrade    \$http_upgrade;
        proxy_set_header   Connection "upgrade";
        proxy_read_timeout 86400s;
    }

    # MRC Bridge WebSocket — gated by Flask auth_request so unauthenticated
    # browsers can't open the upstream WS even if they know the URL.
    location = /mrc-auth-check {
        internal;
        proxy_pass http://127.0.0.1:${WEB_PORT}/mrc/auth-check;
        proxy_pass_request_body off;
        proxy_set_header Content-Length "";
        proxy_set_header X-Original-URI \$request_uri;
        proxy_set_header Cookie \$http_cookie;
    }

    location /mrcws {
        auth_request /mrc-auth-check;
        proxy_pass         http://127.0.0.1:8080/ws;
        proxy_http_version 1.1;
        proxy_set_header   Upgrade    \$http_upgrade;
        proxy_set_header   Connection "upgrade";
        proxy_read_timeout 86400s;
    }

    # Static files served directly by nginx for performance.
    # max-age=86400 (1 day) intentionally omits 'immutable': static files
    # change between BBS upgrades, so browsers must re-fetch after an update.
    location /static/ {
        alias ${INSTALL_DIR}/anetbbs/static/;
        add_header Cache-Control "public, max-age=86400";
    }
}
NGINXEOF

    ln -sf "$NGINX_AVAIL" "$NGINX_ENABLED" 2>/dev/null || true
    rm -f /etc/nginx/sites-enabled/default 2>/dev/null || true

    if nginx -t 2>/dev/null; then
        systemctl enable nginx 2>/dev/null || true
        systemctl restart nginx 2>/dev/null || true
        ok "nginx configured and running"
        STATUS[nginx]="ok"
    else
        bad "nginx config test failed — check $NGINX_AVAIL"
        STATUS[nginx]="fail"
        nginx -t 2>&1 | while read -r line; do
            echo -e "    ${DIM}$line${NC}"
        done
    fi

    if [[ "$ENABLE_SSL" == "y" ]] && command -v certbot &>/dev/null; then
        # Let's Encrypt verifies the domain by fetching
        #   http://$DOMAIN/.well-known/acme-challenge/<token>
        # which means port 80 (and 443 for the redirect) MUST be reachable
        # from the public internet. Behind a home router this almost always
        # means port-forwarding 80/443 → this box's LAN IP. Most first-time
        # certbot failures here are simply "ports not forwarded yet" — pause
        # and let the sysop confirm before we burn a Let's Encrypt rate-limit
        # slot on an attempt that's guaranteed to fail.
        if ! $USE_DEFAULTS; then
            echo ""
            echo -e "${BOLD}  ── Let's Encrypt readiness check ──${NC}"
            echo -e "  About to request an SSL cert for: ${BOLD}$DOMAIN${NC}"
            echo ""
            echo "  Let's Encrypt will connect from the public internet to:"
            echo "    http://$DOMAIN/.well-known/acme-challenge/..."
            echo ""
            echo "  For this to succeed, ALL of the following must be true:"
            echo "    1. DNS for $DOMAIN points at your public IP"
            echo -e "    2. Your router forwards ${BOLD}port 80${NC} → this box"
            echo -e "    3. Your router forwards ${BOLD}port 443${NC} → this box"
            echo "    4. No upstream firewall (ISP, cloud SG) blocks 80/443"
            echo ""
            echo "  Each failed cert request costs a Let's Encrypt rate-limit"
            echo "  slot (5/hour for a given domain). Verify your forwarding"
            echo "  is in place before continuing."
            echo ""
            read -rp "  Ports forwarded and ready? Press Enter to continue, or Ctrl+C to abort. "
        fi

        info "Obtaining SSL certificate for $DOMAIN ..."
        if certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos \
             -m "admin@${DOMAIN}" --redirect 2>/dev/null; then
            ok "SSL certificate obtained"
            STATUS[ssl]="ok"
        else
            warn "certbot failed. Common causes:"
            warn "  • Port 80 not forwarded from your router to this box"
            warn "  • DNS for $DOMAIN doesn't resolve to your public IP yet"
            warn "  • An ISP/cloud firewall is blocking inbound 80"
            warn "Once you've fixed the issue, retry with:"
            warn "  sudo certbot --nginx -d $DOMAIN"
            STATUS[ssl]="fail"
        fi
    fi
else
    skip "nginx not enabled or not installed"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# HEALTH CHECK
# ═══════════════════════════════════════════════════════════════════════════════
step "Running health checks"

sleep 2

HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "http://127.0.0.1:${WEB_PORT}/" 2>/dev/null || echo "000")
if [[ "$HTTP_CODE" -ge 200 ]] && [[ "$HTTP_CODE" -lt 500 ]]; then
    ok "Web server responding on port $WEB_PORT (HTTP $HTTP_CODE)"
    STATUS[web_health]="ok"
else
    bad "Web server not responding on port $WEB_PORT (HTTP $HTTP_CODE)"
    STATUS[web_health]="fail"
fi

# ─── nginx CVE check ──────────────────────────────────────────────────────────
# CVE-2026-42945 is a heap overflow affecting nginx 0.6.27 through 1.30.0
# when the `rewrite` and `set` directives interact. Warn the sysop if their
# installed nginx falls in the range, and check the live config for the
# vulnerable directive pair so we can flag actual exploitability vs latent risk.
if [[ "$ENABLE_NGINX" == "y" ]] && command -v nginx &>/dev/null; then
    NGINX_VER=$(nginx -v 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
    if [[ -n "$NGINX_VER" ]]; then
        # Decompose for numeric comparison
        IFS='.' read -r NG_MAJ NG_MIN NG_PAT <<<"$NGINX_VER"
        # In vulnerable range if (>0.6) OR (==0.6 AND patch>=27), AND (<1.30) OR (==1.30 AND patch==0)
        vulnerable=0
        if (( NG_MAJ == 0 && NG_MIN == 6 && NG_PAT >= 27 )); then vulnerable=1; fi
        if (( NG_MAJ == 0 && NG_MIN > 6 )); then vulnerable=1; fi
        if (( NG_MAJ == 1 && NG_MIN < 30 )); then vulnerable=1; fi
        if (( NG_MAJ == 1 && NG_MIN == 30 && NG_PAT == 0 )); then vulnerable=1; fi

        if (( vulnerable )); then
            # Check whether the active config actually uses the vulnerable
            # directive pair. The CVE only fires when both `rewrite` and `set`
            # appear in the loaded config.
            uses_rewrite=$(grep -rEhc '^\s*rewrite\s' /etc/nginx/sites-enabled/ /etc/nginx/conf.d/ /etc/nginx/snippets/ 2>/dev/null | awk '{s+=$1} END{print s+0}')
            uses_set=$(grep -rEhc '^\s*set\s' /etc/nginx/sites-enabled/ /etc/nginx/conf.d/ 2>/dev/null | awk '{s+=$1} END{print s+0}')
            if (( uses_rewrite > 0 && uses_set > 0 )); then
                bad "nginx $NGINX_VER is in the CVE-2026-42945 range AND your"
                bad "config uses both 'rewrite' and 'set' — you are exploitable."
                bad "Upgrade nginx immediately: sudo apt update && sudo apt"
                bad "upgrade nginx, OR remove the rewrite/set directive pair."
                STATUS[nginx_cve]="fail"
            else
                warn "nginx $NGINX_VER is in the CVE-2026-42945 vulnerable range"
                warn "(0.6.27 to 1.30.0) but your live config does not use both"
                warn "'rewrite' and 'set' directives, so you're not currently"
                warn "exploitable. Patch when your distro ships a fix:"
                warn "  sudo apt update && sudo apt list --upgradable | grep nginx"
                STATUS[nginx_cve]="warn"
            fi
        else
            ok "nginx $NGINX_VER is outside the CVE-2026-42945 range"
            STATUS[nginx_cve]="ok"
        fi
    fi
fi

# ═══════════════════════════════════════════════════════════════════════════════
# FINAL SUMMARY
# ═══════════════════════════════════════════════════════════════════════════════
echo ""
echo ""
echo -e "${BOLD}${CYAN}"
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║              Installation Complete!                          ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo -e "${NC}"

show_status() {
    local key="$1" label="$2"
    if [[ "${STATUS[$key]:-}" == "ok" ]]; then
        echo -e "  ${GREEN}✅${NC} $label"
    elif [[ "${STATUS[$key]:-}" == "fail" ]]; then
        echo -e "  ${RED}❌${NC} $label"
    else
        echo -e "  ${DIM}⏭  $label (skipped)${NC}"
    fi
}

echo -e "${BOLD}  Mode:${NC} ${INSTALL_MODE}"
echo -e "${BOLD}  Components:${NC}"
show_status "required_pkgs"   "System packages"
show_status "files"           "Application files"
show_status "python"          "Python environment"
show_status "config"          "Configuration (.env)"
show_status "anetbbs-web"       "Web server (gunicorn)"
if [[ "$ENABLE_TELNET" == "y" || "$ENABLE_SSH" == "y" ]]; then
    proto_label=""
    [[ "$ENABLE_TELNET" == "y" ]] && proto_label+="telnet "
    [[ "$ENABLE_SSH" == "y" ]]    && proto_label+="ssh "
    [[ "$RLOGIN_ENABLED" == "true" ]] && proto_label+="rlogin "
    show_status "anetbbs"        "Terminal protocols (${proto_label% })"
fi
[[ "$ENABLE_FINGER" == "y" ]] && show_status "anetbbs-finger" "Finger server"
show_status "anetbbs-mrc-bridge" "MRC bridge service"
[[ "$ENABLE_NGINX" == "y" ]]  && show_status "nginx"          "nginx reverse proxy"
[[ "$ENABLE_SSL" == "y" ]]    && show_status "ssl"            "SSL certificate"
show_status "web_health"      "Web health check"
if [[ "${STATUS[nginx_cve]:-}" == "warn" ]]; then
    echo -e "  ${YELLOW}⚠${NC}  nginx CVE-2026-42945 — version is in vulnerable range (latent, not exploitable in this config)"
elif [[ "${STATUS[nginx_cve]:-}" == "fail" ]]; then
    echo -e "  ${RED}❌${NC} nginx CVE-2026-42945 — version vulnerable AND config uses rewrite+set: patch nginx NOW"
elif [[ "${STATUS[nginx_cve]:-}" == "ok" ]]; then
    echo -e "  ${GREEN}✅${NC} nginx CVE-2026-42945 check (version not in vulnerable range)"
fi

if [[ "$INSTALL_MODE" == "test" ]]; then
    echo ""
    echo -e "${BOLD}${YELLOW}  Note: This is a TEST install (localhost-only web).${NC}"
    echo -e "  ${DIM}- gunicorn binds 127.0.0.1:${WEB_PORT} — not reachable from your LAN${NC}"
    echo -e "  ${DIM}- Access from another machine via SSH tunnel:${NC}"
    echo -e "  ${DIM}    ssh -L ${WEB_PORT}:localhost:${WEB_PORT} ${SERVICE_USER}@<this-box>${NC}"
    echo -e "  ${DIM}    then open http://localhost:${WEB_PORT}/ in your local browser${NC}"
    [[ "$ENABLE_MSP"    != "y" ]] && echo -e "  ${DIM}- Inter-BBS IM (MSP/SYSTAT) disabled — re-enable in .env once you have a public IP${NC}"
    [[ "$ENABLE_FINGER" != "y" ]] && echo -e "  ${DIM}- Finger disabled — re-enable in .env + restart anetbbs-finger when going public${NC}"
    [[ "$ENABLE_BINKP"  != "y" ]] && echo -e "  ${DIM}- BinkP listener disabled — outbound netmail/echomail still works, inbound from FidoNet doesn't${NC}"
    echo -e "  ${DIM}- To go production later: re-run install.sh with mode=production${NC}"
elif [[ "$INSTALL_MODE" == "behind" ]]; then
    echo ""
    echo -e "${BOLD}${YELLOW}  Note: Behind-another-server install.${NC}"
    echo -e "  ${DIM}- gunicorn binds 0.0.0.0:${WEB_PORT} — reachable from this box's IP${NC}"
    echo -e "  ${DIM}- Add a server block to your EXISTING nginx (or apache) pointing${NC}"
    echo -e "  ${DIM}  bbs.<your-domain> at http://127.0.0.1:${WEB_PORT}/ . Example nginx:${NC}"
    echo -e "  ${DIM}    server { server_name bbs.example.com; listen 80;${NC}"
    echo -e "  ${DIM}      location / { proxy_pass http://127.0.0.1:${WEB_PORT};${NC}"
    echo -e "  ${DIM}        proxy_http_version 1.1; proxy_set_header Upgrade \$http_upgrade;${NC}"
    echo -e "  ${DIM}        proxy_set_header Connection \"upgrade\"; proxy_read_timeout 86400; }${NC}"
    echo -e "  ${DIM}    }${NC}"
    echo -e "  ${DIM}- Then run certbot --nginx -d bbs.example.com on YOUR nginx for HTTPS${NC}"
fi

echo ""
echo -e "${BOLD}  Access URLs:${NC}"
if [[ "$ENABLE_NGINX" == "y" ]] && [[ "${STATUS[nginx]:-}" == "ok" ]]; then
    if [[ "$ENABLE_SSL" == "y" ]] && [[ "${STATUS[ssl]:-}" == "ok" ]]; then
        echo -e "  Web:     ${CYAN}https://${DOMAIN}${NC}"
    else
        echo -e "  Web:     ${CYAN}http://${DOMAIN}${NC}"
    fi
elif [[ "$INSTALL_MODE" == "test" ]]; then
    echo -e "  Web:     ${CYAN}http://localhost:${WEB_PORT}${NC} (from this box only — SSH tunnel for remote)"
else
    echo -e "  Web:     ${CYAN}http://${DOMAIN}:${WEB_PORT}${NC}"
fi
[[ "$ENABLE_TELNET" == "y" ]] && echo -e "  Telnet:  ${CYAN}telnet ${DOMAIN} ${TELNET_PORT}${NC}"
[[ "$ENABLE_SSH" == "y" ]]    && echo -e "  SSH:     ${CYAN}ssh -p ${SSH_PORT} ${DOMAIN}${NC}"

echo ""
echo -e "${BOLD}  Admin Login:${NC}"
echo -e "  Username:  ${CYAN}${ADMIN_USER}${NC}"
if $ADMIN_PASS_GENERATED; then
    echo -e "  Password:  ${CYAN}${ADMIN_PASS}${NC}  ${YELLOW}(save this — it won't be shown again!)${NC}"
else
    echo -e "  Password:  ${DIM}(the password you entered)${NC}"
fi

echo ""
echo -e "${BOLD}  File Locations:${NC}"
echo -e "  Install dir:    ${DIM}${INSTALL_DIR}${NC}"
echo -e "  Configuration:  ${DIM}${INSTALL_DIR}/.env${NC}"
echo -e "  Database:       ${DIM}${INSTALL_DIR}/data/anetbbs.db${NC}"
echo -e "  Logs:           ${DIM}${INSTALL_DIR}/logs/${NC}"

echo ""
echo -e "${BOLD}  Useful Commands:${NC}"
echo -e "  ${DIM}sudo systemctl status anetbbs-web anetbbs anetbbs-mrc-bridge${NC}"
echo -e "  ${DIM}sudo journalctl -u anetbbs-web -f${NC}"
echo -e "  ${DIM}sudo bash ${INSTALL_DIR}/install.sh --uninstall${NC}"

echo ""
echo -e "${GREEN}${BOLD}  Your BBS is ready! Log in to the web admin panel to configure${NC}"
echo -e "${GREEN}${BOLD}  message boards, games, echomail, themes, and more.${NC}"
echo ""
