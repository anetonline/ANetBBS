#!/usr/bin/env bash
# Privileged in-place upgrade for ANetBBS.
#
# Invoked by the sysop "Check for Updates" UI as:
#   sudo -n /home/<bbs-user>/anetbbs/deploy/run_upgrade.sh <version> <url> <sha256>
#
# This is the ONLY command the sudoers grant for this feature ever
# needs to know about -- see the detach block immediately below for
# why that's deliberate. The script re-execs itself (as root, no
# further sudo call) into a detached systemd unit before doing any
# real work, then downloads the tarball from <url>, verifies its
# SHA-256 against the value the admin UI fetched from the upstream
# /api/releases/latest, and runs the new tarball's update.sh.
#
# All three args are validated against strict regexes here BEFORE doing
# anything, since the sudoers grant is open to the service user — never
# trust the args even though the calling code is also ours.
#
# Logging: everything goes to stdout / stderr, which systemd (after the
# detach below) redirects to <INSTALL_DIR>/data/anetbbs_upgrade.log.
# The UI tails that file.

set -eu -o pipefail

LOG_PREFIX="[upgrade]"
log()  { printf '%s %s\n' "$LOG_PREFIX" "$*"; }
fail() { printf '%s FAIL: %s\n' "$LOG_PREFIX" "$*" >&2; exit 1; }

# ── Resolve install dir from /etc/anetbbs.install ─────────────────────────
# Moved ahead of the detach block below (it used to run after argument
# validation) because the detach block itself now needs INSTALL_DIR to
# build the log path. install.sh + update.sh write this file with
# INSTALL_DIR=/path/to/anetbbs so any privileged helper can find the
# right install without hardcoding.
INSTALL_DIR=""
if [ -r /etc/anetbbs.install ]; then
    # shellcheck source=/dev/null
    . /etc/anetbbs.install
fi
if [ -z "${INSTALL_DIR:-}" ]; then
    # Fall back to two-parents-up from this script's location.
    HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
    INSTALL_DIR=$(dirname "$HERE")
fi
[ -d "$INSTALL_DIR" ] || fail "install dir $INSTALL_DIR does not exist"

# ── Detach from the caller's cgroup ────────────────────────────────────────
# Real live outage (2026-10-27, three fix attempts), a SECOND real live
# outage (2026-10-28, the sudoers-drift bug), and a THIRD real live
# outage found the same day while verifying the second fix: once the
# sudoers problem was fixed, this re-exec itself started failing with
# systemd exit 209/STDOUT, "Failed to set up standard output: Permission
# denied" -- root cause: anetbbs-web.service has PrivateTmp=yes, so the
# /tmp/anetbbs_upgrade.log the WEB PROCESS creates/truncates lives in
# that service's own PRIVATE /tmp mount namespace. The `sudo`/this
# script's process tree inherits that same private namespace (sudo
# changes UID, not mount namespaces), but systemd/PID1 (which actually
# opens the StandardOutput target for the new unit) resolves the path
# against the REAL, system-wide /tmp -- a different, inconsistent view
# of "the same" path depending on which process looks at it. Rather
# than chase the exact permission semantics of that mismatch further,
# the log now lives under INSTALL_DIR/data/ -- a real, non-virtualized
# directory already used for persistent app data, visible identically
# to every process regardless of any unit's PrivateTmp setting.
#
# Earlier history, still relevant to why this is a re-exec and not a
# sudo-command flag: the mechanism used to escape anetbbs-web's cgroup
# (so update.sh's own restart of anetbbs-web, below, can't kill US as
# collateral damage) used to be baked into the SUDO-GATED command
# itself -- first `systemd-run --scope` (found not to actually detach:
# systemd-run becomes the wrapper's own parent, not a real escape),
# then `systemd-run --unit=... --wait --property=...` run directly by
# the web process. Both put detachment flags inside the one thing
# sudoers has to literally pattern-match against, so every change to
# the mechanism meant every existing install's /etc/sudoers.d/anetbbs
# had to change in lockstep via a successful update.sh run -- and when
# that refresh silently failed on a live box once, the box was stuck
# with no way to fix a sudoers problem via the one feature that needs
# sudoers to work at all. Fixed by moving detachment OUT of the
# sudo-gated command entirely: sudoers only ever grants plain
# `run_upgrade.sh *`, and THIS script (already root by the time this
# line runs) re-execs itself into a detached transient unit with no
# second sudo call involved -- whatever this mechanism needs to become
# in the future, it only ever touches this file.
UPGRADE_LOG="$INSTALL_DIR/data/anetbbs_upgrade.log"
if [ -z "${ANETBBS_UPGRADE_DETACHED:-}" ]; then
    export ANETBBS_UPGRADE_DETACHED=1
    exec systemd-run --unit=anetbbs-upgrade --collect --quiet --wait \
        --property=StandardOutput=append:"$UPGRADE_LOG" \
        --property=StandardError=append:"$UPGRADE_LOG" \
        --setenv=ANETBBS_UPGRADE_DETACHED=1 \
        "$0" "$@"
fi

# ── Argument validation ────────────────────────────────────────────────────
# Use case-based pattern matching instead of `[[ =~ ]]` regex. Bash treats
# `&` inside conditional regex expressions as a special token even when
# it's inside a character class, blowing up with "syntax error in
# conditional expression: unexpected token `&'". Patterns sidestep that.
[ $# -eq 3 ] || fail "usage: $0 <version> <url> <sha256>"
VERSION="$1"
URL="$2"
SHA="$3"

# Version: vX.Y[ab]Z.NN (alpha/beta build) or vX.Y.Z (stable release,
# since the v1.0.0 milestone). Accept either form.
if ! [[ "$VERSION" =~ ^v[0-9]+\.[0-9]+([ab][0-9]+\.[0-9]+|\.[0-9]+)$ ]]; then
    fail "version $VERSION does not match expected vX.Y[ab]Z.NN or vX.Y.Z form"
fi

# URL: must be http(s) and end with .tar.gz. Block shell metacharacters
# that could lead to command injection if the URL were ever fed to a
# subshell (we use it as an arg to curl/wget, but defensive layering
# is cheap).
case "$URL" in
    https://*.tar.gz|http://*.tar.gz) ;;
    *) fail "url $URL not a tar.gz over http(s)" ;;
esac
case "$URL" in
    *[\ \;\&\|\`\$\<\>\"\'\\]*) fail "url contains shell metacharacters" ;;
esac

# sha256: 64 hex chars.
if ! [[ "$SHA" =~ ^[0-9a-fA-F]{64}$ ]]; then
    fail "sha256 $SHA is not 64 hex chars"
fi
SHA_LC=$(printf '%s' "$SHA" | tr '[:upper:]' '[:lower:]')

log "version=$VERSION  url=$URL  sha=${SHA_LC:0:16}…"
log "install dir: $INSTALL_DIR"

# ── Verify URL origin against the configured registry ─────────────────────
# Real gap found in a security/performance audit: URL was only ever
# validated by SHAPE (https + .tar.gz + no shell metacharacters), never
# by ORIGIN -- since the caller supplies both the URL and the sha256
# checked against it below, an attacker able to invoke this script
# directly (the sudoers grant is to the service user, not gated behind
# the admin web UI -- see deploy/sudoers.anetbbs) could host their own
# tarball anywhere and compute a self-consistent hash, defeating the
# sha256 check entirely. Bounded here by requiring the download host to
# match REGISTRY_URL's own host -- read independently from .env (the
# same config source the web app itself uses via anetbbs/config.py),
# not trusted from argv, so a caller can't just also pass a self-
# consistent value for this check. REGISTRY_URL is deliberately sysop-
# configurable (a private network of peers can run their own release
# feed -- see anetbbs/web/upgrades.py's own docstring), so this can't
# be a fixed hardcoded host; it has to track whatever THIS install
# actually trusts. Falls back to the same default anetbbs/config.py
# itself uses when REGISTRY_URL isn't set in .env at all.
REGISTRY_HOST=""
ENV_FILE="$INSTALL_DIR/.env"
if [ -r "$ENV_FILE" ]; then
    REGISTRY_LINE=$(grep -m1 '^REGISTRY_URL=' "$ENV_FILE" 2>/dev/null || true)
    REGISTRY_VALUE="${REGISTRY_LINE#REGISTRY_URL=}"
    # Strip optional surrounding quotes some .env writers/editors add.
    REGISTRY_VALUE="${REGISTRY_VALUE%\"}"; REGISTRY_VALUE="${REGISTRY_VALUE#\"}"
    REGISTRY_VALUE="${REGISTRY_VALUE%\'}"; REGISTRY_VALUE="${REGISTRY_VALUE#\'}"
    _reg_no_scheme="${REGISTRY_VALUE#http://}"
    _reg_no_scheme="${_reg_no_scheme#https://}"
    REGISTRY_HOST="${_reg_no_scheme%%/*}"
fi
[ -n "$REGISTRY_HOST" ] || REGISTRY_HOST="bbs.a-net.fyi"

_url_no_scheme="${URL#http://}"
_url_no_scheme="${_url_no_scheme#https://}"
URL_HOST="${_url_no_scheme%%/*}"
[ "$URL_HOST" = "$REGISTRY_HOST" ] \
    || fail "url host '$URL_HOST' does not match this install's configured REGISTRY_URL host '$REGISTRY_HOST' -- refusing to download from an unexpected origin"
log "url origin verified against registry host: $REGISTRY_HOST"

# ── Download tarball to a unique tempdir we own ───────────────────────────
TMP=$(mktemp -d -t anetbbs-upgrade-XXXXXX)
trap 'rm -rf "$TMP"' EXIT

TARBALL="$TMP/ANetBBS-$VERSION.tar.gz"
log "downloading to $TARBALL"

# Prefer curl, fall back to wget. --fail makes curl exit non-zero on 4xx/5xx
# so a missing tarball doesn't write an HTML error page to disk.
if command -v curl >/dev/null 2>&1; then
    curl --fail --location --silent --show-error \
         --connect-timeout 15 --max-time 600 \
         -o "$TARBALL" "$URL" \
      || fail "curl download failed"
elif command -v wget >/dev/null 2>&1; then
    wget --quiet --timeout=600 -O "$TARBALL" "$URL" \
      || fail "wget download failed"
else
    fail "neither curl nor wget is installed"
fi

# ── Verify sha256 ─────────────────────────────────────────────────────────
ACTUAL=$(sha256sum "$TARBALL" | awk '{print $1}')
if [ "$ACTUAL" != "$SHA_LC" ]; then
    fail "sha256 mismatch — expected $SHA_LC, got $ACTUAL"
fi
log "sha256 verified"

# ── Extract ───────────────────────────────────────────────────────────────
EXTRACT="$TMP/ext"
mkdir -p "$EXTRACT"
log "extracting"
tar -xzf "$TARBALL" -C "$EXTRACT" --no-same-owner || fail "tar extract failed"

# The tarball contains a single top-level dir named ANetBBS-<version>/
INNER=$(find "$EXTRACT" -maxdepth 1 -mindepth 1 -type d | head -n 1)
[ -d "$INNER" ] || fail "tarball had no top-level directory"
log "extracted to $INNER"

[ -x "$INNER/update.sh" ] || chmod +x "$INNER/update.sh" 2>/dev/null || true
[ -f "$INNER/update.sh" ] || fail "tarball missing update.sh"

# ── Run update.sh ─────────────────────────────────────────────────────────
# update.sh expects to be run as root with bash, and it operates on the
# already-installed tree at INSTALL_DIR. update.sh restarts the web
# service, which will kill the HTTP request that triggered us — but we
# already re-exec'd into our own detached systemd unit above, so that
# restart cannot touch us.
log "running update.sh"
cd "$INNER"
# Real gap found in a full install/update re-verify audit: update.sh's
# own argument parser unconditionally resets INSTALL_DIR="" before
# reading --install-dir from argv, which silently discards the env var
# this script used to pass instead -- update.sh then re-derived the
# install dir itself (from the systemd unit's WorkingDirectory=, or a
# bare /opt/anetbbs fallback), usually landing on the same path by
# coincidence, but making the whole point of resolving INSTALL_DIR here
# from /etc/anetbbs.install dead plumbing. Pass it as the real flag
# update.sh actually parses.
ANETBBS_UPGRADE_RUN=1 bash update.sh --install-dir "$INSTALL_DIR" \
    || fail "update.sh exited non-zero"

log "upgrade to $VERSION complete"
