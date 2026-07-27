#!/usr/bin/env bash
# Privileged in-place upgrade for ANetBBS.
#
# Invoked by the sysop "Check for Updates" UI as:
#   sudo -n /home/<bbs-user>/anetbbs/deploy/run_upgrade.sh <version> <url> <sha256>
#
# It downloads the tarball from <url>, verifies its SHA-256 against the
# value the admin UI fetched from the upstream /api/releases/latest, and
# runs the new tarball's update.sh.
#
# All three args are validated against strict regexes here BEFORE doing
# anything, since the sudoers grant is open to the service user — never
# trust the args even though the calling code is also ours.
#
# Logging: everything goes to stdout / stderr, which the caller redirects
# to /tmp/anetbbs_upgrade.log. The UI tails that file.

set -eu -o pipefail

LOG_PREFIX="[upgrade]"
log()  { printf '%s %s\n' "$LOG_PREFIX" "$*"; }
fail() { printf '%s FAIL: %s\n' "$LOG_PREFIX" "$*" >&2; exit 1; }

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

# ── Resolve install dir from /etc/anetbbs.install ─────────────────────────
# install.sh + update.sh write this file with INSTALL_DIR=/path/to/anetbbs
# so any privileged helper can find the right install without hardcoding.
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
log "install dir: $INSTALL_DIR"

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
# already-installed tree at INSTALL_DIR. update.sh restarts gunicorn,
# which will kill the HTTP request that triggered us — but we run
# detached via the caller's nohup/setsid so we keep going.
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
