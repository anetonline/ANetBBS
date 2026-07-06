#!/usr/bin/env bash
# Build a release tarball for ANetBBS.
# Output: /tmp/ANetBBS-<version>.tar.gz
#
# The tarball wraps everything inside a single top-level directory
# ANetBBS-<version>/ — required by run_upgrade.sh which looks for
# a single subdirectory after extraction to find update.sh.
#
# Naming matches the auto-update regex: ANetBBS-(v\d+\.\d+a\d+\.\d+)\.tar\.gz
#
# --- Why this builds from git instead of a raw directory walk -----------
# The previous version of this script tarred the working directory
# directly, with a manually maintained --exclude list of "known bad"
# files/directories. That's a denylist: it only stops leaking files
# someone remembered to add to the list in advance. It has repeatedly
# failed to do that — bbs.log (containing a plaintext admin password
# from a test boot) leaked into 7+ past release tarballs, and stray
# test-scratch SQLite files leaked into others, because nobody
# specifically added exclusions for those exact filenames before they
# existed.
#
# This version inverts the default: it starts from git's OWN idea of
# "safe to ship" (tracked files, plus untracked files that aren't
# gitignored) and only adds back a short, deliberate allowlist of
# binary bundles that are intentionally kept out of git history for
# size reasons (doors/, data/dos-games/) but ARE part of the shipped
# product. Anything new and unexpected that shows up in the working
# directory — a stray log file, a leftover test database, an editor
# swap file — is excluded by construction, with zero need to update
# this script, as long as .gitignore covers it (which is the same file
# that already needs to be correct for git itself, so there's one
# source of truth instead of two that can drift apart).

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION="$(cat "$SCRIPT_DIR/VERSION" | tr -d '[:space:]')"
OUT="/tmp/ANetBBS-${VERSION}.tar.gz"
TOPDIR="ANetBBS-${VERSION}"

echo "Building $OUT (top-level dir: $TOPDIR) ..."

if ! git -C "$SCRIPT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "ERROR: $SCRIPT_DIR is not a git repository — this script needs" >&2
  echo "git's own tracked/ignored-file knowledge to build a safe release." >&2
  exit 1
fi

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

FILE_LIST="$WORKDIR/files.txt"

# Base: everything git considers safe (tracked, or untracked-but-not-
# gitignored). Excludes this script itself explicitly (a sysop's own
# checkout doesn't need to re-run build-release.sh).
( cd "$SCRIPT_DIR" && git ls-files --cached --others --exclude-standard ) \
  | grep -v '^build-release\.sh$' \
  > "$FILE_LIST"

# Deliberate allowlist: binary bundles kept out of git history for size
# reasons, but still part of the shipped product. Each gets the same
# runtime-state carve-outs the old --exclude list had, since these
# directories are NOT git-tracked at all (so .gitignore can't help here
# — there's nothing for it to ignore INSIDE an untracked tree). Also
# excludes any nested .git/.svn/.hg dirs some vendored door happens to
# still have from however it was originally obtained (found one under
# doors/synch/botwars/ the hard way).
( cd "$SCRIPT_DIR" && find doors -type f 2>/dev/null \
    ! -path '*/.git/*' \
    ! -path '*/.svn/*' \
    ! -path '*/.hg/*' \
    ! -path 'doors/mystic/rdq3/saves/*' \
    ! -path 'doors/mystic/rdq3/data/*' \
    ! -path 'doors/synch/botwars/botwars_saves/*' \
    ! -path 'doors/synch/botwars/botwars_data/*' \
    ! -path 'doors/synch/botwars/scores.ans' \
) >> "$FILE_LIST"
( cd "$SCRIPT_DIR" && find data/dos-games -type f 2>/dev/null \
    ! -path '*/.git/*' ! -path '*/.svn/*' ! -path '*/.hg/*' \
) >> "$FILE_LIST"

sort -u "$FILE_LIST" -o "$FILE_LIST"

tar czf "$OUT" \
  --transform "s|^|${TOPDIR}/|" \
  --no-recursion \
  -C "$SCRIPT_DIR" \
  --files-from="$FILE_LIST"

SIZE="$(du -h "$OUT" | cut -f1)"
echo "Done: $OUT ($SIZE) — $(wc -l < "$FILE_LIST") files"

# Sanity check — use tar tf <archive> <member> to test membership (no pipes = no SIGPIPE)
if ! tar tf "$OUT" "${TOPDIR}/update.sh" >/dev/null 2>&1; then
  echo "ERROR: tarball missing ${TOPDIR}/update.sh"
  rm -f "$OUT"; exit 1
fi

# Leak check — still worth keeping as a second, independent layer of
# defense even though the file-list construction above should already
# make these impossible. Pipe is intentional here; grep exits 0 only
# if something BAD is found.
LEAKED=$(tar tzf "$OUT" | grep -E \
  "^${TOPDIR}/\.env$|\
^${TOPDIR}/\.claude|\
^${TOPDIR}/bbs\.log|\
\.log$|\
\.db$|\
\.db-shm$|\
\.db-wal$|\
/\.git/|\
/\.svn/|\
/\.hg/|\
doors/synch/botwars/scores\.ans$|\
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
anetsims/data/topsysops|\
botwars/scores\.ans" || true)
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
