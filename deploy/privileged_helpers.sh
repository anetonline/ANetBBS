# shellcheck shell=bash
# Shared root-side helpers for install.sh and update.sh (audit H-1).
#
# Sourced, not executed. Every function takes explicit arguments so the
# test suite can exercise it against temporary paths.
#
# Rule these helpers enforce: root never executes, reads, or writes
# through anything the service user can modify. The service user owns
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

_anetbbs_env_value() {   # _anetbbs_env_value FILE KEY  -> value without quotes
    [[ -r "$1" ]] || return 0
    { grep -m1 "^$2=" "$1" || true; } | cut -d= -f2- | tr -d '"'"'"
}

# anetbbs_write_install_conf INSTALL_DIR SERVICE_USER [CONF] [ENV_FILE]
# Write the root-owned config the privileged helpers trust. Nothing in it
# comes from the service account's .env: that file is writable by the
# account the helpers protect against (ENV_FILE is accepted for backward
# compatibility and ignored).
#
# REQUIRE_SIGNED_UPGRADES: "yes" unless the operator, running as root, sets
#   ANETBBS_REQUIRE_SIGNED_UPGRADES=no. A "no" is kept on later runs only
#   when it was set that way by an operator (REQUIRE_SIGNED_SET_BY=operator).
# REGISTRY_HOST (the only host an UNSIGNED upgrade may come from, and only
#   when signatures are not required): the operator's ANETBBS_REGISTRY_HOST,
#   else the value already in CONF, else bbs.a-net.fyi.
anetbbs_write_install_conf() {
    local install_dir="$1" user="$2" conf="${3:-/etc/anetbbs.install}"
    local host="" req="" set_by="" tmp
    if anetbbs_root_file_ok "$conf"; then
        host=$(_anetbbs_conf_value "$conf" REGISTRY_HOST)
        req=$(_anetbbs_conf_value "$conf" REQUIRE_SIGNED_UPGRADES)
        set_by=$(_anetbbs_conf_value "$conf" REQUIRE_SIGNED_SET_BY)
    fi
    [[ -n "${ANETBBS_REGISTRY_HOST:-}" ]] && host="$ANETBBS_REGISTRY_HOST"
    [[ -n "$host" ]] || host="$ANETBBS_REGISTRY_HOST_DEFAULT"
    if ! [[ "$host" =~ ^[A-Za-z0-9.-]+(:[0-9]+)?$ ]]; then
        _anetbbs_warn "invalid REGISTRY_HOST '$host' -- using $ANETBBS_REGISTRY_HOST_DEFAULT"
        host="$ANETBBS_REGISTRY_HOST_DEFAULT"
    fi
    if [[ -n "${ANETBBS_REQUIRE_SIGNED_UPGRADES:-}" ]]; then
        if [[ "$ANETBBS_REQUIRE_SIGNED_UPGRADES" == no ]]; then req=no; else req=yes; fi
        set_by=operator
    elif [[ "$req" == no && "$set_by" == operator ]]; then
        :
    else
        req=yes; set_by=default
    fi
    tmp="$conf.tmp.$$"
    ( umask 022
      {
        echo "# Written by install.sh / update.sh (root). Trusted by the privileged"
        echo "# upgrade/restore helpers. Do not edit by hand; re-run update.sh."
        echo "INSTALL_DIR=$install_dir"
        echo "SERVICE_USER=$user"
        echo "REGISTRY_HOST=$host"
        echo "REQUIRE_SIGNED_UPGRADES=$req"
        echo "REQUIRE_SIGNED_SET_BY=$set_by"
      } > "$tmp" ) || return 1
    if [[ "$(id -u)" == 0 ]]; then chown root:root "$tmp" || return 1; fi
    chmod 0644 "$tmp" && mv -f "$tmp" "$conf"
}

_anetbbs_conf_value() {   # _anetbbs_conf_value FILE KEY
    { grep -m1 "^$2=" "$1" 2>/dev/null || true; } | cut -d= -f2- | tr -d '"'"'"
}

# anetbbs_root_file_ok FILE
# True when FILE is a regular file (not a symlink) owned by the current
# root identity (uid 0 in production) and not group/world writable.
anetbbs_root_file_ok() {
    local f="$1"
    [[ -f "$f" && ! -L "$f" ]] || return 1
    [[ "$(stat -c %u "$f")" == "$(id -u)" ]] || return 1
    (( (8#$(stat -c %a "$f") & 8#022) == 0 ))
}

# anetbbs_install_release_trust SRC_DIR [DEST_DIR]
# Install the release trust files from SRC_DIR/deploy, root-owned 0644:
#   release-signers       trusted signing keys (OpenSSH allowed_signers)
#   release-revoked-keys  revoked public keys (ssh-keygen -Y verify -r)
#   release-denylist      sha256 digests of release archives never to install
# A template with no content lines removes the installed copy, so a key
# can be withdrawn by a later release. Upgrades then fail closed (signatures
# are required unless an operator opted out).
anetbbs_install_release_trust() {
    local src="$1/deploy" dst="${2:-/etc/anetbbs}" f
    install -d -m 0755 "$dst" || return 1
    if [[ "$(id -u)" == 0 ]]; then chown root:root "$dst" || return 1; fi
    for f in release-signers release-revoked-keys release-denylist; do
        if [[ ! -f "$src/$f" || -L "$src/$f" ]]; then
            continue
        fi
        if ! grep -qv '^\s*\(#\|$\)' "$src/$f"; then
            rm -f "$dst/$f"
            continue
        fi
        install -m 0644 "$src/$f" "$dst/.$f.new" || return 1
        if [[ "$(id -u)" == 0 ]]; then chown root:root "$dst/.$f.new" || return 1; fi
        mv -f "$dst/.$f.new" "$dst/$f" || return 1
    done
}
# Older name, kept for callers and tests: installs only the signer file.
anetbbs_install_release_signers() {
    local src="$1/deploy/release-signers" dst="${2:-/etc/anetbbs/release-signers}"
    [[ -f "$src" && ! -L "$src" ]] || return 0
    if ! grep -qv '^\s*\(#\|$\)' "$src"; then
        rm -f "$dst"
        return 0
    fi
    install -d -m 0755 "$(dirname "$dst")" || return 1
    if [[ "$(id -u)" == 0 ]]; then chown root:root "$(dirname "$dst")"; fi
    install -m 0644 "$src" "$dst.new" || return 1
    if [[ "$(id -u)" == 0 ]]; then chown root:root "$dst.new"; fi
    mv -f "$dst.new" "$dst"
}

# ── Release identity (anti-rollback) ─────────────────────────────────────────
# The installed release is recorded root-owned in STATE, never read from the
# service-writable INSTALL_DIR/VERSION. The web upgrade wrapper only installs
# a release newer than STATE and not older than its built-in floor.
ANETBBS_RELEASE_STATE_DEFAULT=/var/lib/anetbbs/release-state

# anetbbs_stable_version V  -> true for vX.Y.Z
anetbbs_stable_version() { [[ "$1" =~ ^v[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}$ ]]; }

# anetbbs_version_cmp A B  -> prints -1, 0 or 1 (both must be vX.Y.Z)
anetbbs_version_cmp() {
    local a="${1#v}" b="${2#v}" i x y
    local -a A B
    IFS=. read -r -a A <<<"$a"; IFS=. read -r -a B <<<"$b"
    for i in 0 1 2; do
        x=$((10#${A[$i]})); y=$((10#${B[$i]}))
        if (( x < y )); then echo -1; return 0; fi
        if (( x > y )); then echo 1; return 0; fi
    done
    echo 0
}

# anetbbs_release_state_read [STATE] -> prints INSTALLED_VERSION, or nothing
# when the file is missing or not root-trusted.
anetbbs_release_state_read() {
    local st="${1:-$ANETBBS_RELEASE_STATE_DEFAULT}" v
    anetbbs_root_file_ok "$st" || return 1
    v=$(_anetbbs_conf_value "$st" INSTALLED_VERSION)
    anetbbs_stable_version "$v" || return 1
    printf '%s\n' "$v"
}

# anetbbs_release_state_write VERSION SHA256 [STATE]
anetbbs_release_state_write() {
    local v="$1" sha="$2" st="${3:-$ANETBBS_RELEASE_STATE_DEFAULT}" d tmp
    anetbbs_stable_version "$v" || { _anetbbs_warn "refusing to record version '$v'"; return 1; }
    [[ -z "$sha" || "$sha" =~ ^[0-9a-f]{64}$ ]] || sha=""
    d=$(dirname "$st")
    install -d -m 0755 "$d" || return 1
    if [[ "$(id -u)" == 0 ]]; then chown root:root "$d" || return 1; fi
    tmp="$d/.release-state.$$"
    ( umask 022
      {
        echo "# Written by update.sh (root). Trusted by the upgrade wrapper."
        echo "INSTALLED_VERSION=$v"
        echo "INSTALLED_SHA256=$sha"
        echo "RECORDED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
      } > "$tmp" ) || return 1
    if [[ "$(id -u)" == 0 ]]; then chown root:root "$tmp" || return 1; fi
    chmod 0644 "$tmp" && mv -f "$tmp" "$st"
}

# ── Trust checks on paths root reads code or data from ───────────────────────
# anetbbs_path_not_service_writable PATH USER
# True when neither PATH nor any parent directory can be modified by USER:
# not owned by USER, and not writable through group (USER a member) or world
# (a sticky world-writable directory such as /tmp is accepted as a parent).
anetbbs_path_not_service_writable() {
    local p="$1" user="$2" uid mode grp groups
    uid=$(id -u "$user" 2>/dev/null) || return 0   # no such user: nothing to fear
    groups=" $(id -G "$user" 2>/dev/null) "
    p=$(realpath -e -- "$p" 2>/dev/null) || return 1
    local first="$p"
    while :; do
        [[ "$(stat -c %u "$p")" != "$uid" ]] || return 1
        mode=$((8#$(stat -c %a "$p")))
        grp=$(stat -c %g "$p")
        if (( mode & 8#020 )) && [[ "$groups" == *" $grp "* ]]; then return 1; fi
        if (( mode & 8#002 )); then
            [[ -d "$p" && "$p" != "$first" ]] && (( mode & 8#1000 )) || return 1
        fi
        [[ "$p" == / ]] && break
        p=$(dirname "$p")
    done
    return 0
}

# anetbbs_tree_not_service_writable DIR USER
# DIR and its parents pass the check above, and nothing inside DIR is owned
# by USER, group-writable by one of USER's groups, or world-writable.
anetbbs_tree_not_service_writable() {
    local d="$1" user="$2" uid g found gargs=()
    anetbbs_path_not_service_writable "$d" "$user" || return 1
    uid=$(id -u "$user" 2>/dev/null) || return 0
    for g in $(id -G "$user" 2>/dev/null); do gargs+=(-o \( -gid "$g" -perm -g+w \)); done
    found=$(find -P "$d" \( -uid "$uid" -o -perm -o+w ${gargs[@]+"${gargs[@]}"} \) \
            ! -type l -print -quit 2>/dev/null) || return 1
    [[ -z "$found" ]]
}

# ── Root-owned rollback snapshots ────────────────────────────────────────────
# Copies of the privileged files an update replaces (systemd units and their
# drop-ins, the nginx site). They live under a root-only directory, are
# created with mktemp, are never handed to the service user, and are verified
# against their own manifest before a rollback uses them.
ANETBBS_ROLLBACK_ROOT_DEFAULT=/var/lib/anetbbs/rollback

# anetbbs_snapshot_create ROOT FILE...   -> prints the snapshot directory
# Each FILE (absolute path; directories are copied recursively) is stored at
# ROOT/<id>/files/<absolute path>. Missing FILEs are recorded as absent.
anetbbs_snapshot_create() {
    local root="$1"; shift
    local snap f rel
    install -d -m 0700 "$root" || return 1
    if [[ "$(id -u)" == 0 ]]; then chown root:root "$root" || return 1; fi
    chmod 0700 "$root" || return 1
    anetbbs_root_dir_ok "$root" || { _anetbbs_warn "$root is not a root-only directory"; return 1; }
    snap=$(mktemp -d "$root/snap.$(date +%Y%m%d%H%M%S).XXXXXX") || return 1
    mkdir -m 0700 "$snap/files" || return 1
    : > "$snap/ABSENT"
    for f in "$@"; do
        rel="${f#/}"
        if [[ -e "$f" || -L "$f" ]]; then
            mkdir -p "$snap/files/$(dirname "$rel")" || return 1
            cp -a --no-dereference -- "$f" "$snap/files/$rel" || return 1
        else
            printf '%s\n' "$f" >> "$snap/ABSENT"
        fi
    done
    ( cd "$snap/files" && find . -type f -print0 | sort -z | xargs -0 -r sha256sum ) > "$snap/MANIFEST" || return 1
    ( cd "$snap/files" && find . \( -type f -o -type l \) | sort ) > "$snap/LIST" || return 1
    echo complete > "$snap/COMPLETE"
    printf '%s\n' "$snap"
}

# anetbbs_root_dir_ok DIR: directory, not a symlink, owned by the current
# root identity, mode without group/world access.
anetbbs_root_dir_ok() {
    local d="$1"
    [[ -d "$d" && ! -L "$d" ]] || return 1
    [[ "$(stat -c %u "$d")" == "$(id -u)" ]] || return 1
    (( (8#$(stat -c %a "$d") & 8#077) == 0 ))
}

# anetbbs_snapshot_verify SNAP: complete marker present, every manifest entry
# matches, and no file was added or removed since creation.
anetbbs_snapshot_verify() {
    local snap="$1"
    anetbbs_root_dir_ok "$snap" || return 1
    [[ -f "$snap/COMPLETE" && -f "$snap/MANIFEST" && -f "$snap/LIST" ]] || return 1
    ( cd "$snap/files" && sha256sum --quiet --strict -c "$snap/MANIFEST" ) >/dev/null 2>&1 || return 1
    diff -q "$snap/LIST" <(cd "$snap/files" && find . \( -type f -o -type l \) | sort) >/dev/null
}

# anetbbs_snapshot_restore SNAP FILE...
# Put each FILE back from SNAP (or delete it when it was absent before).
anetbbs_snapshot_restore() {
    local snap="$1"; shift
    local f rel rc=0
    anetbbs_snapshot_verify "$snap" || { _anetbbs_warn "rollback snapshot $snap failed verification -- not restoring privileged files"; return 1; }
    for f in "$@"; do
        rel="${f#/}"
        if [[ -e "$snap/files/$rel" || -L "$snap/files/$rel" ]]; then
            rm -rf -- "$f"
            cp -a --no-dereference -- "$snap/files/$rel" "$f" || rc=1
        elif grep -qxF -- "$f" "$snap/ABSENT"; then
            rm -rf -- "$f"
        fi
    done
    return $rc
}

# anetbbs_snapshot_prune ROOT KEEP: remove all but the newest KEEP snapshots.
anetbbs_snapshot_prune() {
    local root="$1" keep="$2" s
    local -a all
    anetbbs_root_dir_ok "$root" || return 0
    mapfile -t all < <(find "$root" -mindepth 1 -maxdepth 1 -type d -name 'snap.*' | sort)
    (( ${#all[@]} > keep )) || return 0
    for s in "${all[@]:0:${#all[@]}-keep}"; do rm -rf -- "$s"; done
}

# anetbbs_valid_port VALUE: 1-65535, digits only.
anetbbs_valid_port() {
    [[ "$1" =~ ^[0-9]{1,5}$ ]] && (( 10#$1 >= 1 && 10#$1 <= 65535 ))
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

# anetbbs_git_update DIR [RUNNER...]
# Update a git-clone install. RUNNER (e.g. as_service) prefixes every git
# command so it never runs as root. Besides a normal fast-forward, this
# handles an upstream history rewrite (a file removed from old commits):
# --ff-only refuses that even with no local changes, which used to leave
# git installs silently stuck. It moves to the new history only when the
# tree is clean and the current commit's content is identical to a commit
# on the new upstream branch. Returns 0 if the code is current.
anetbbs_git_update() {
    local dir=$1; shift
    if "$@" git -C "$dir" pull --ff-only --quiet 2>/dev/null; then
        ok "git pull succeeded"
        return 0
    fi
    if ! "$@" git -C "$dir" fetch --quiet 2>/dev/null; then
        warn "git fetch failed (network or remote problem); code not updated"
        return 1
    fi
    local upstream head_tree dirty match
    upstream=$("$@" git -C "$dir" rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || true)
    head_tree=$("$@" git -C "$dir" rev-parse 'HEAD^{tree}' 2>/dev/null || true)
    dirty=$("$@" git -C "$dir" status --porcelain --untracked-files=no 2>/dev/null || echo dirty)
    match=""
    if [[ -n "$upstream" && -n "$head_tree" && -z "$dirty" ]]; then
        match=$("$@" git -C "$dir" log --format='%H %T' "$upstream" 2>/dev/null \
                | awk -v t="$head_tree" '$2 == t { print $1; exit }')
    fi
    if [[ -n "$match" ]]; then
        info "Upstream history was rewritten; this checkout matches upstream commit ${match:0:12}"
        if "$@" git -C "$dir" reset --hard --quiet "$match" \
                && "$@" git -C "$dir" merge --ff-only --quiet "$upstream"; then
            ok "git checkout moved to the rewritten upstream history and updated"
            return 0
        fi
        warn "could not move to the rewritten upstream history; code not updated"
        return 1
    fi
    if [[ -n "$dirty" ]]; then
        warn "git pull refused: this checkout has local changes to tracked files; code not updated"
        warn "  review them with: git -C $dir status"
    elif [[ -z "$upstream" ]]; then
        warn "git pull refused: this checkout has no upstream branch; code not updated"
    else
        warn "git pull refused: local commits are not on $upstream; code not updated"
        warn "  inspect with: git -C $dir log --oneline $upstream..HEAD"
    fi
    return 1
}
