#!/usr/bin/env bash
# Privileged in-place upgrade for ANetBBS.
#
# Invoked by the sysop "Check for Updates" UI as:
#   sudo -n /usr/local/libexec/anetbbs/run_upgrade.sh <version> <url> <sha256> [<sig-url>]
#
# Installed root:root 0755 into a root-owned directory by install.sh /
# update.sh (audit H-1). The copy under <INSTALL_DIR>/deploy/ is only the
# source for that install: sudoers never grants it, because the service
# user owns everything under INSTALL_DIR and could rewrite it.
#
# The sudoers grant is open to the service account, so every argument and
# every policy decision is checked here BEFORE anything is written,
# including the log. Policy, all from root-owned files:
#   - release identity: the target must be a stable vX.Y.Z, newer than the
#     installed release recorded in $STATE (never INSTALL_DIR/VERSION), and
#     not older than MIN_VERSION. Reinstalls and downgrades need root
#     (sudo bash update.sh from the extracted release).
#   - signatures: required unless an operator set REQUIRE_SIGNED_UPGRADES=no
#     in /etc/anetbbs.install. A required signature that is missing, made by
#     an unknown or revoked key, or over an archive on the denylist is a
#     refusal; there is no unsigned fallback.
#   - the extracted archive must contain exactly ANetBBS-<version>/ with a
#     VERSION file equal to <version> before its update.sh runs.
#
# Logging: after the checks pass, the wrapper takes a root lock, starts a
# fresh /var/log/anetbbs/upgrade.log with a run id, and re-execs itself in a
# detached systemd unit whose output goes to that log. The last line is
# always "result=ok run_id=..." or "result=fail run_id=...".

set -eu -o pipefail

CONF=/etc/anetbbs.install
TRUST_DIR=/etc/anetbbs
STATE=/var/lib/anetbbs/release-state
LOCK=/run/anetbbs-upgrade.lock
LOG_DIR=/var/log/anetbbs
ROOT_UID=0
# First release with the privilege-boundary fixes. Nothing older is ever
# installable through this wrapper.
MIN_VERSION=v1.1.74

LOG_PREFIX="[upgrade]"
RUN_ID="${ANETBBS_UPGRADE_RUN_ID:-}"
log()  { printf '%s %s\n' "$LOG_PREFIX" "$*"; }
fail() {
    printf '%s FAIL: %s\n' "$LOG_PREFIX" "$*" >&2
    [ -z "$RUN_ID" ] || printf '%s result=fail run_id=%s\n' "$LOG_PREFIX" "$RUN_ID" >&2
    exit 1
}

_root_file() {   # regular file, not a symlink, root-owned, not group/world writable
    [ -f "$1" ] && [ ! -L "$1" ] && [ "$(stat -c %u "$1")" = "$ROOT_UID" ] \
        && [ $(( 0$(stat -c %a "$1") & 022 )) -eq 0 ]
}
_kv() { { grep -m1 "^$2=" "$1" || true; } | cut -d= -f2- | tr -d '"'"'"; }
_stable() { [[ "$1" =~ ^v[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}$ ]]; }
_vcmp() {   # prints -1/0/1 for two stable versions
    local a b i x y
    IFS=. read -r -a a <<<"${1#v}"; IFS=. read -r -a b <<<"${2#v}"
    for i in 0 1 2; do
        x=$((10#${a[$i]})); y=$((10#${b[$i]}))
        if (( x < y )); then echo -1; return; fi
        if (( x > y )); then echo 1; return; fi
    done
    echo 0
}
_is_loopback_http() {
    # Plain http is allowed only to this machine's loopback: a registry
    # hub whose REGISTRY_URL is http://localhost:<port>/ fetches its own
    # releases that way. The shell-metacharacter check refuses '@', so
    # userinfo can't disguise the host.
    case "$1" in
        http://localhost/*|http://localhost:[0-9]*/*|http://127.0.0.1/*|http://127.0.0.1:[0-9]*/*|http://\[::1\]/*|http://\[::1\]:[0-9]*/*) return 0 ;;
    esac
    return 1
}
_url_scheme() {   # _url_scheme URL SUFFIX -> https|http, or fails
    case "$1" in
        *[\ \;\&\|\`\$\<\>\"\'\\@]*) return 1 ;;
    esac
    case "$1" in
        https://*"$2") echo https ;;
        http://*"$2") _is_loopback_http "$1" && echo http || return 1 ;;
        *) return 1 ;;
    esac
}
_url_host() { local s="${1#*://}"; printf '%s' "${s%%/*}"; }

# ── Trusted configuration ────────────────────────────────────────────────
_root_file "$CONF" || fail "$CONF missing, not root-owned, or writable by others -- re-run update.sh as root"
INSTALL_DIR=$(_kv "$CONF" INSTALL_DIR)
REGISTRY_HOST=$(_kv "$CONF" REGISTRY_HOST)
REQUIRE_SIGNED=$(_kv "$CONF" REQUIRE_SIGNED_UPGRADES)
[ "$REQUIRE_SIGNED" = no ] || REQUIRE_SIGNED=yes
[ -n "$REGISTRY_HOST" ] || REGISTRY_HOST="bbs.a-net.fyi"
[[ "$INSTALL_DIR" =~ ^/[A-Za-z0-9._/-]+$ ]] || fail "INSTALL_DIR in $CONF is missing or invalid"
[[ "$REGISTRY_HOST" =~ ^[A-Za-z0-9.-]+(:[0-9]+)?$ ]] || fail "REGISTRY_HOST in $CONF is invalid"
[ -d "$INSTALL_DIR" ] || fail "install dir $INSTALL_DIR does not exist"
SIGNERS="$TRUST_DIR/release-signers"
REVOKED="$TRUST_DIR/release-revoked-keys"
DENYLIST="$TRUST_DIR/release-denylist"

# ── Arguments ────────────────────────────────────────────────────────────
[ $# -eq 3 ] || [ $# -eq 4 ] || fail "usage: $0 <version> <url> <sha256> [<sig-url>]"
VERSION="$1"; URL="$2"; SHA="$3"; SIG_URL="${4:-}"
_stable "$VERSION" || fail "version $VERSION is not a stable vX.Y.Z release (pre-releases install only via sudo bash update.sh)"
URL_SCHEME=$(_url_scheme "$URL" .tar.gz) \
    || fail "url $URL must be a .tar.gz over https (plain http only to this machine's loopback; no shell metacharacters or '@')"
URL_HOST=$(_url_host "$URL")
[[ "$URL_HOST" =~ ^([A-Za-z0-9.-]+|\[::1\])(:[0-9]+)?$ ]] || fail "url host '$URL_HOST' is not a plain host[:port]"
[[ "$SHA" =~ ^[0-9a-fA-F]{64}$ ]] || fail "sha256 $SHA is not 64 hex chars"
SHA_LC=$(printf '%s' "$SHA" | tr '[:upper:]' '[:lower:]')
if [ -n "$SIG_URL" ]; then
    SIG_SCHEME=$(_url_scheme "$SIG_URL" .tar.gz.sig) \
        || fail "signature url $SIG_URL must be https (or loopback http), end in .tar.gz.sig, and contain no shell metacharacters or '@'"
fi
command -v curl >/dev/null 2>&1 || fail "curl is required for web upgrades"

# ── Release identity ─────────────────────────────────────────────────────
_root_file "$STATE" || fail "no trusted record of the installed release ($STATE) -- run the update once as root: sudo bash update.sh"
INSTALLED=$(_kv "$STATE" INSTALLED_VERSION)
_stable "$INSTALLED" || fail "$STATE has no valid INSTALLED_VERSION -- run the update once as root"
[ "$(_vcmp "$VERSION" "$MIN_VERSION")" != -1 ] || fail "version $VERSION is older than the minimum $MIN_VERSION"
[ "$(_vcmp "$VERSION" "$INSTALLED")" = 1 ] \
    || fail "version $VERSION is not newer than the installed $INSTALLED -- reinstalls and downgrades need root (sudo bash update.sh)"
if [ -f "$DENYLIST" ] || [ -L "$DENYLIST" ]; then
    _root_file "$DENYLIST" || fail "$DENYLIST is not root-owned -- refusing"
    ! grep -qix "$SHA_LC" <(grep -oE '^[0-9a-fA-F]{64}' "$DENYLIST") \
        || fail "release $SHA_LC is on the denylist"
fi

# ── Signature policy ─────────────────────────────────────────────────────
HAVE_SIGNERS=no
if _root_file "$SIGNERS" && grep -qv '^\s*\(#\|$\)' "$SIGNERS"; then HAVE_SIGNERS=yes; fi
HAVE_REVOKED=no
if [ -f "$REVOKED" ] || [ -L "$REVOKED" ]; then
    _root_file "$REVOKED" || fail "$REVOKED is not root-owned -- refusing"
    HAVE_REVOKED=yes
fi
if [ "$REQUIRE_SIGNED" = yes ]; then
    [ "$HAVE_SIGNERS" = yes ] || fail "signed upgrades are required but $SIGNERS lists no trusted key"
    [ -n "$SIG_URL" ] || fail "signed upgrades are required and no signature was offered"
    command -v ssh-keygen >/dev/null 2>&1 || fail "ssh-keygen is required to verify release signatures"
    MODE=signed
elif [ -n "$SIG_URL" ] && [ "$HAVE_SIGNERS" = yes ]; then
    MODE=signed
else
    [ "$URL_HOST" = "$REGISTRY_HOST" ] \
        || fail "url host '$URL_HOST' does not match REGISTRY_HOST '$REGISTRY_HOST' -- refusing an unsigned download from an unexpected origin"
    MODE=unsigned
fi

# ── Detach ───────────────────────────────────────────────────────────────
# Everything above is read-only. From here on this run owns the log: take
# the root lock first so a second invocation can't truncate a running
# upgrade's log, then re-exec into a detached unit (it must survive
# update.sh restarting anetbbs-web, whose cgroup the caller is in). The
# waiting systemd-run keeps the lock fd open until the unit finishes.
if [ -z "${ANETBBS_UPGRADE_DETACHED:-}" ]; then
    exec 9>>"$LOCK"
    flock -n 9 || fail "another upgrade is already running"
    install -d -o root -g root -m 0755 "$LOG_DIR"
    UPGRADE_LOG="$LOG_DIR/upgrade.log"
    [ ! -L "$UPGRADE_LOG" ] || fail "$UPGRADE_LOG is a symlink"
    RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$-$(od -An -N4 -tx4 /dev/urandom | tr -d ' ')"
    : > "$UPGRADE_LOG"
    chmod 0644 "$UPGRADE_LOG"
    log "run_id=$RUN_ID" >> "$UPGRADE_LOG"
    log "run_id=$RUN_ID detaching; progress in $UPGRADE_LOG"
    exec systemd-run --unit=anetbbs-upgrade --collect --quiet --wait \
        --property=StandardOutput=append:"$UPGRADE_LOG" \
        --property=StandardError=append:"$UPGRADE_LOG" \
        --setenv=ANETBBS_UPGRADE_DETACHED=1 \
        --setenv=ANETBBS_UPGRADE_RUN_ID="$RUN_ID" \
        "$0" "$@"
fi

# ── Detached phase ───────────────────────────────────────────────────────
[ -n "$RUN_ID" ] || RUN_ID="detached-$$"
log "version=$VERSION  installed=$INSTALLED  mode=$MODE  url=$URL  sha=${SHA_LC:0:16}…"
log "install dir: $INSTALL_DIR"

TMP=$(mktemp -d /tmp/anetbbs-upgrade-XXXXXX)
trap 'rm -rf "$TMP"' EXIT
TARBALL="$TMP/ANetBBS-$VERSION.tar.gz"
_curl() {   # _curl SCHEME MAXTIME OUT URL [extra...]
    local scheme="$1" maxt="$2" out="$3" url="$4"; shift 4
    local -a p
    if [ "$scheme" = https ]; then
        p=(--proto '=https' --proto-redir '=https' --location --max-redirs 3)
    else
        p=(--proto '=http' --max-redirs 0)
    fi
    curl --fail --silent --show-error "${p[@]}" --connect-timeout 15 --max-time "$maxt" "$@" -o "$out" "$url"
}
log "downloading"
_curl "$URL_SCHEME" 600 "$TARBALL" "$URL" || fail "download failed"
ACTUAL=$(sha256sum "$TARBALL" | awk '{print $1}')
[ "$ACTUAL" = "$SHA_LC" ] || fail "sha256 mismatch -- expected $SHA_LC, got $ACTUAL"
log "sha256 verified"

if [ "$MODE" = signed ]; then
    SIGFILE="$TMP/release.sig"
    _curl "$SIG_SCHEME" 60 "$SIGFILE" "$SIG_URL" --max-filesize 65536 || fail "signature download failed"
    REV_ARGS=(); [ "$HAVE_REVOKED" = no ] || REV_ARGS=(-r "$REVOKED")
    ssh-keygen -Y verify -f "$SIGNERS" -I anetbbs-release -n anetbbs-release \
        -s "$SIGFILE" ${REV_ARGS[@]+"${REV_ARGS[@]}"} < "$TARBALL" >/dev/null 2>&1 \
        || fail "release signature did NOT verify against $SIGNERS (unknown or revoked key, or tampered archive) -- refusing to install"
    log "release signature verified"
fi

EXTRACT="$TMP/ext"
mkdir -m 0700 "$EXTRACT"
log "extracting"
tar -xzf "$TARBALL" -C "$EXTRACT" --no-same-owner || fail "tar extract failed"
TOP="ANetBBS-$VERSION"
[ "$(find "$EXTRACT" -mindepth 1 -maxdepth 1 | wc -l)" = 1 ] || fail "archive must contain exactly one top-level entry"
INNER="$EXTRACT/$TOP"
[ -d "$INNER" ] && [ ! -L "$INNER" ] || fail "archive top-level directory is not $TOP"
[ -f "$INNER/VERSION" ] && [ ! -L "$INNER/VERSION" ] || fail "archive has no VERSION file"
INNER_VERSION=$(tr -d '[:space:]' < "$INNER/VERSION")
[ "$INNER_VERSION" = "$VERSION" ] || fail "archive VERSION '$INNER_VERSION' does not match requested $VERSION"
[ -f "$INNER/update.sh" ] && [ ! -L "$INNER/update.sh" ] || fail "archive missing update.sh"
log "archive identity verified: $TOP"

log "running update.sh"
cd "$INNER"
ANETBBS_UPGRADE_RUN=1 ANETBBS_EXPECTED_VERSION="$VERSION" ANETBBS_RELEASE_SHA256="$SHA_LC" \
    bash update.sh --install-dir "$INSTALL_DIR" || fail "update.sh exited non-zero"
_root_file "$STATE" && [ "$(_kv "$STATE" INSTALLED_VERSION)" = "$VERSION" ] \
    || fail "update.sh finished but did not record $VERSION as installed"
log "upgrade to $VERSION complete"
log "result=ok run_id=$RUN_ID"
