#!/usr/bin/env bash
# Build a release tarball for ANetBBS.
# Output: /tmp/ANetBBS-<version>.tar.gz
#
# The tarball wraps everything inside a single top-level directory
# ANetBBS-<version>/ — required by run_upgrade.sh which looks for
# a single subdirectory after extraction to find update.sh.
#
# Naming matches the auto-update regex: ANetBBS-(v\d+\.\d+a\d+\.\d+)\.tar\.gz

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION="$(cat "$SCRIPT_DIR/VERSION" | tr -d '[:space:]')"
OUT="/tmp/ANetBBS-${VERSION}.tar.gz"
TOPDIR="ANetBBS-${VERSION}"

echo "Building $OUT (top-level dir: $TOPDIR) ..."

tar czf "$OUT" \
  --exclude='.git' \
  --exclude='.claude' \
  --exclude='./build-release.sh' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='*.pyo' \
  --exclude='./.env' \
  --exclude='./instance' \
  --exclude='./data/doors' \
  --exclude='./data/echomail' \
  --exclude='./data/uploads' \
  --exclude='./data/ftp_root' \
  --exclude='./data/binkp' \
  --exclude='./data/mrc' \
  --exclude='./data/sessions*' \
  --exclude='./data/gallery*' \
  --exclude='./data/*.db' \
  --exclude='./data/*.db-shm' \
  --exclude='./data/*.db-wal' \
  --exclude='./data/ssh_host_key' \
  --exclude='./data/ssh_host_key.pub' \
  --exclude='./data/admin_password.txt' \
  --exclude='./doors/mystic/rdq3/saves' \
  --exclude='./doors/mystic/rdq3/data' \
  --exclude='./doors/synch/botwars/botwars_saves' \
  --exclude='./doors/synch/botwars/botwars_data' \
  --exclude='./vendor/games/anetsims/door32.sys' \
  --exclude='./vendor/games/anetsims/data/scores.ans' \
  --exclude='./vendor/games/anetsims/data/scores.asc' \
  --exclude='./vendor/games/anetsims/data/sysop_sysop.dat' \
  --exclude='./vendor/games/anetsims/data/topsysops.dat' \
  --exclude='./mrc/bridge/data' \
  --exclude='./mrc/bridge/data-*' \
  --exclude='./mrc/bridge/logs' \
  --exclude='./mrc/logs' \
  --exclude='./mrc/bridge/config/config.json' \
  --exclude='./mrc/bridge/config/config.bottomless.json' \
  --exclude='./mrc/bridge/config/config.ca.json' \
  --exclude='./mrc/bridge/config/config.uk.json' \
  --exclude='./mrc/bridge/config.json' \
  --transform "s|^\./|${TOPDIR}/|" \
  -C "$SCRIPT_DIR" .

SIZE="$(du -h "$OUT" | cut -f1)"
echo "Done: $OUT ($SIZE)"

# Sanity check — use tar tf <archive> <member> to test membership (no pipes = no SIGPIPE)
if ! tar tf "$OUT" "${TOPDIR}/update.sh" >/dev/null 2>&1; then
  echo "ERROR: tarball missing ${TOPDIR}/update.sh"
  rm -f "$OUT"; exit 1
fi

# Leak check — pipe is intentional here; grep exits 0 only if something BAD is found
LEAKED=$(tar tzf "$OUT" | grep -E \
  "^${TOPDIR}/\.env$|\
^${TOPDIR}/\.claude|\
/data/profiles\.json|\
/data/sessions\.json|\
mrc/bridge/data-|\
doors/synch/botwars/botwars_saves/[^.]|\
doors/synch/botwars/botwars_data/leaderboards|\
doors/synch/botwars/botwars_data/maintenance_reports|\
doors/mystic/rdq3/saves/|\
anetsims/door32\.sys|\
anetsims/data/scores|\
anetsims/data/sysop|\
anetsims/data/topsysops" || true)
if [[ -n "$LEAKED" ]]; then
  echo "ERROR: sensitive files in tarball:"; echo "$LEAKED"
  rm -f "$OUT"; exit 1
fi

# Confirm dos-games are present
if ! tar tf "$OUT" "${TOPDIR}/data/dos-games/doom.zip" >/dev/null 2>&1; then
  echo "WARNING: ${TOPDIR}/data/dos-games/doom.zip missing from tarball"
fi
if ! tar tf "$OUT" "${TOPDIR}/data/dos-games/duke3d.zip" >/dev/null 2>&1; then
  echo "WARNING: ${TOPDIR}/data/dos-games/duke3d.zip missing from tarball"
fi

echo "Sanity check passed — ${TOPDIR}/update.sh present, no sensitive files."
