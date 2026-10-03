#!/usr/bin/env bash
# Build the ANetBBS Enhanced Client add-on tarball -- a separate,
# OPTIONAL overlay package, not part of the stock ANetBBS release
# build-release.sh produces. Same reasoning as keeping RDQ3/ANetCHESS
# as their own separate projects rather than bundling door-game
# archives into the main ANetBBS distribution (see
# feedback_keep_door_games_separate_from_anetbbs) -- a sysop who wants
# the browser-based Canvas+WebSocket terminal client fetches this
# add-on and applies it themselves; everyone else's stock install never
# carries these files at all.
#
# Output: /tmp/ANetBBS-EnhancedClient-addon-<version>.tar.gz
#
# Install (sysop side): extract over an existing ANetBBS install (same
# scp + tar + systemctl restart workflow as any other update), then set
# ENHANCED_ENABLED=true (and ENHANCED_HOST/ENHANCED_PORT if the
# defaults don't fit) in config and restart anetbbs.service. See
# anetbbs/enhanced_client/README.md for the full walkthrough.
#
# What's NOT in this package: the small `if self.term_mode ==
# 'enhanced':` branches threaded through session.py/menu_engine.py/
# games.py/etc. are core ANetBBS plumbing (the same file every other
# protocol's own term_mode dispatch already lives in -- there's no
# clean way to ship "session.py minus the enhanced branches" without
# maintaining a second diverging copy of that file). Those branches
# ship with EVERY stock ANetBBS install already, same as PETSCII's own
# always-present-but-disabled-by-default code -- completely inert
# unless ENHANCED_ENABLED=true AND this add-on's files are present.
# main.py's own startup logs a clear warning and skips the listener
# (every other protocol still starts normally) if a sysop flips that
# flag without fetching this add-on first.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_ROOT="$(dirname "$SCRIPT_DIR")"
VERSION="$(cat "$INSTALL_ROOT/VERSION" | tr -d '[:space:]')"
OUT="/tmp/ANetBBS-EnhancedClient-addon-${VERSION}.tar.gz"
TOPDIR="ANetBBS-EnhancedClient-addon-${VERSION}"

echo "Building $OUT (top-level dir: $TOPDIR) ..."

# The exact, fixed file set that makes up the add-on -- genuinely
# self-contained, new files with no existing-file edits mixed in (the
# edits to existing core files are NOT part of this package; see the
# header comment above). Kept as an explicit allowlist, not a directory
# walk, so a stray scratch/debug file dropped next to these during
# testing can never leak into the package by accident.
FILES=(
  anetbbs/core/enhanced_server.py
  anetbbs/features/enhanced_protocol.py
  anetbbs/static/fonts/Flexi_IBM_VGA_False.ttf
  anetbbs/static/fonts/Flexi_IBM_VGA_False.woff
)

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT
FILE_LIST="$WORKDIR/files.txt"
: > "$FILE_LIST"

for f in "${FILES[@]}"; do
  if [[ ! -f "$INSTALL_ROOT/$f" ]]; then
    echo "ERROR: expected add-on file missing: $f" >&2
    exit 1
  fi
  echo "$f" >> "$FILE_LIST"
done

# anetbbs/enhanced_client/ is the whole client -- walked explicitly
# (rather than git ls-files, which would also need this repo's own
# standing "don't commit until Jerry says so" state to cooperate) so
# this script works the same way whether or not the feature has been
# committed to git yet.
if [[ ! -d "$INSTALL_ROOT/anetbbs/enhanced_client" ]]; then
  echo "ERROR: anetbbs/enhanced_client/ directory missing" >&2
  exit 1
fi
( cd "$INSTALL_ROOT" && find anetbbs/enhanced_client -type f ) >> "$FILE_LIST"

sort -u "$FILE_LIST" -o "$FILE_LIST"

tar czf "$OUT" \
  --transform "s|^|${TOPDIR}/|" \
  --no-recursion \
  -C "$INSTALL_ROOT" \
  --files-from="$FILE_LIST"

SIZE="$(du -h "$OUT" | cut -f1)"
echo "Done: $OUT ($SIZE) — $(wc -l < "$FILE_LIST") files"
echo
echo "Install on a target ANetBBS install:"
echo "  tar -xzf $(basename "$OUT") --strip-components=1 -C /path/to/anetbbs"
echo "  # then set ENHANCED_ENABLED=true in config and restart anetbbs.service"
