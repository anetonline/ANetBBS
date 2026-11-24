# shellcheck shell=bash
# Shared root-side helpers for install.sh and update.sh (audit H-1).
#
# Sourced, not executed. Every function takes explicit arguments so the
# test suite can exercise it against temporary paths.
#
# Rule these helpers enforce: root never executes, and never writes
# through, anything the service user can modify. The service user owns
# INSTALL_DIR (code, venv, .env, data), so:
#   - the sudo-granted helper scripts live in a root-owned directory
#     outside INSTALL_DIR (anetbbs_install_root_wrappers);
#   - the one config file those helpers trust is root-owned and lives
#     in /etc (anetbbs_write_install_conf);
#   - anything that runs venv code or touches the service user's tree
#     runs as that user (anetbbs_as_service).

ANETBBS_WRAPPER_DIR_DEFAULT=/usr/local/libexec/anetbbs
ANETBBS_REGISTRY_HOST_DEFAULT=bbs.a-net.fyi

_anetbbs_warn() {
    if declare -F warn >/dev/null 2>&1; then warn "$@"; else echo "[WARN] $*" >&2; fi
}

# anetbbs_as_service USER CMD [ARGS...]
# Run a command as the service user. Fails closed (never falls back to
# running it as root).
anetbbs_as_service() {
    local user="$1"; shift
    if [[ -z "$user" || "$user" == root ]]; then
        _anetbbs_warn "refusing to run '$1' as '${user:-<empty>}'"
        return 1
    fi
    if [[ "$(id -u)" != 0 && "$(id -un)" == "$user" ]]; then
        "$@"
    elif command -v runuser >/dev/null 2>&1; then
        # runuser keeps root's HOME; give the user its own (pip cache etc.).
        local home
        home=$(getent passwd "$user" 2>/dev/null | cut -d: -f6)
        runuser -u "$user" -- env HOME="${home:-/nonexistent}" "$@"
    elif command -v sudo >/dev/null 2>&1; then
        sudo -n -u "$user" -- "$@"
    elif command -v su >/dev/null 2>&1; then
        local q="" a
        for a in "$@"; do q+=" $(printf '%q' "$a")"; done
        su -s /bin/sh "$user" -c "exec$q"
    else
        _anetbbs_warn "no runuser/sudo/su available to drop to $user"
        return 1
    fi
}

# _anetbbs_dir_is_root_trusted DIR SERVICE_USER
# True when DIR is owned by uid 0, not world-writable, and -- if it is
# group-writable -- the service user is not a member of that group.
_anetbbs_dir_is_root_trusted() {
    local d="$1" user="$2" mode grp
    [[ -d "$d" && ! -L "$d" ]] || return 1
    [[ "$(stat -c %u "$d")" == 0 ]] || return 1
    mode=$(stat -c %a "$d")
    (( (8#$mode & 8#002) == 0 )) || return 1
    if (( (8#$mode & 8#020) != 0 )); then
        grp=$(stat -c %G "$d")
        if [[ -n "$user" ]] && id -nG "$user" 2>/dev/null | tr ' ' '\n' | grep -qx -- "$grp"; then
            return 1
        fi
    fi
    return 0
}

# anetbbs_install_root_wrappers SRC_DIR SERVICE_USER [WRAPPER_DIR]
# Copy deploy/run_upgrade.sh + deploy/run_restore.sh to WRAPPER_DIR as
# root:root 0755, after checking every parent directory is root-trusted.
anetbbs_install_root_wrappers() {
    local src="$1" user="$2" dst="${3:-$ANETBBS_WRAPPER_DIR_DEFAULT}" p w
    install -d -o root -g root -m 0755 "$dst" || return 1
    chown root:root "$dst" && chmod 0755 "$dst" || return 1
    p="$dst"
    while :; do
        if ! _anetbbs_dir_is_root_trusted "$p" "$user"; then
            _anetbbs_warn "$p is not root-owned/trusted -- privileged helpers NOT installed (web upgrade/restore stay disabled)"
            return 1
        fi
        [[ "$p" == / ]] && break
        p=$(dirname "$p")
    done
    for w in run_upgrade.sh run_restore.sh; do
        if [[ ! -f "$src/deploy/$w" || -L "$src/deploy/$w" ]]; then
            _anetbbs_warn "missing or symlinked $src/deploy/$w -- privileged helpers NOT installed"
            return 1
        fi
        install -o root -g root -m 0755 "$src/deploy/$w" "$dst/.$w.new" || return 1
        mv -f "$dst/.$w.new" "$dst/$w" || return 1
    done
    return 0
}

# anetbbs_write_install_conf INSTALL_DIR SERVICE_USER [CONF] [ENV_FILE]
# Write the root-owned config the privileged helpers trust. REGISTRY_HOST
# is preserved from an existing CONF, else taken from the operator's
# ANETBBS_REGISTRY_HOST, else the default. It is never copied from the
# service-user-owned .env; if .env names a different host, warn so the
# sysop can opt in explicitly.
anetbbs_write_install_conf() {
    local install_dir="$1" user="$2" conf="${3:-/etc/anetbbs.install}" envf="${4:-$1/.env}"
    local host="" env_host="" line tmp
    # Only trust a previous value from a file owned by us (root in real use).
    if [[ -f "$conf" && ! -L "$conf" && "$(stat -c %u "$conf")" == "$(id -u)" ]]; then
        host=$({ grep -m1 '^REGISTRY_HOST=' "$conf" || true; } | cut -d= -f2- | tr -d '"'"'")
    fi
    [[ -n "${ANETBBS_REGISTRY_HOST:-}" ]] && host="$ANETBBS_REGISTRY_HOST"
    [[ -n "$host" ]] || host="$ANETBBS_REGISTRY_HOST_DEFAULT"
    if ! [[ "$host" =~ ^[A-Za-z0-9.-]+(:[0-9]+)?$ ]]; then
        _anetbbs_warn "invalid REGISTRY_HOST '$host' -- using $ANETBBS_REGISTRY_HOST_DEFAULT"
        host="$ANETBBS_REGISTRY_HOST_DEFAULT"
    fi
    if [[ -r "$envf" ]]; then
        line=$({ grep -m1 '^REGISTRY_URL=' "$envf" || true; } | cut -d= -f2- | tr -d '"'"'")
        env_host="${line#http://}"; env_host="${env_host#https://}"; env_host="${env_host%%/*}"
        if [[ -n "$env_host" && "$env_host" != "$host" ]]; then
            _anetbbs_warn "REGISTRY_URL in .env points at '$env_host' but the trusted upgrade host is '$host'." \
                "Web upgrades will only download from '$host'. To trust '$env_host', re-run as root with" \
                "ANETBBS_REGISTRY_HOST=$env_host."
        fi
    fi
    tmp="$conf.tmp.$$"
    ( umask 022
      {
        echo "# Written by install.sh / update.sh (root). Trusted by the privileged"
        echo "# upgrade/restore helpers. Do not edit by hand; re-run update.sh."
        echo "INSTALL_DIR=$install_dir"
        echo "SERVICE_USER=$user"
        echo "REGISTRY_HOST=$host"
      } > "$tmp" ) || return 1
    if [[ "$(id -u)" == 0 ]]; then chown root:root "$tmp" || return 1; fi
    chmod 0644 "$tmp" && mv -f "$tmp" "$conf"
}

# anetbbs_link_console_scripts INSTALL_DIR [BIN_DIR]
# Bash replacement for the venv's anetbbs-symlinks console script, so
# root no longer executes venv code to create these links.
anetbbs_link_console_scripts() {
    local install_dir="$1" bin="${2:-/usr/local/bin}" n src
    [[ -d "$bin" ]] || return 1
    for n in anetbbs anetbbs-web anetbbs-install anetbbs-upgrade \
             anetbbs-symlinks anetbbs-cleanup anetbbs-cfg anetbbs-monitor; do
        src="$install_dir/venv/bin/$n"
        [[ -e "$src" ]] || continue
        ln -sfn "$src" "$bin/$n"
    done
}
