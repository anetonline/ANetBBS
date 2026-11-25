#!/usr/bin/env bash
# Build a release tarball for ANetBBS.
# Output: /tmp/ANetBBS-v<version>.tar.gz (directory: ANETBBS_RELEASE_OUT_DIR)
#
# The tarball wraps everything inside a single top-level directory
# ANetBBS-v<version>/ — required by run_upgrade.sh which looks for
# a single subdirectory after extraction to find update.sh.
#
# Naming matches the auto-update regex: ANetBBS-(v\d+\.\d+(?:[ab]\d+\.\d+|\.\d+))\.tar\.gz
# (covers alpha/beta build-number form AND the stable vMAJOR.MINOR.PATCH form)
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
# This version inverts the default: it ships exactly the files of the
# current commit (git archive of HEAD, from a clean checkout only). Door-game trees (doors/) and DOS game bundles
# (data/dos-games/) are never shipped. Anything new and unexpected
# that shows up in the working directory — a stray log file, a
# leftover test database, an editor swap file — is excluded by construction, with zero need to update
# this script, as long as .gitignore covers it (which is the same file
# that already needs to be correct for git itself, so there's one
# source of truth instead of two that can drift apart).

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if ! git -C "$SCRIPT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "ERROR: $SCRIPT_DIR is not a git repository — this script builds from a commit." >&2
  exit 1
fi
# Commit-bound and reproducible: the archive is built from HEAD, never from
# the working tree, and only from a clean checkout (no modified or untracked
# non-ignored files), so what ships is exactly one commit.
if [[ -n "$(git -C "$SCRIPT_DIR" status --porcelain --untracked-files=normal)" ]]; then
  echo "ERROR: the working tree is not clean -- commit or remove these first:" >&2
  git -C "$SCRIPT_DIR" status --short >&2
  exit 1
fi
COMMIT="$(git -C "$SCRIPT_DIR" rev-parse HEAD)"
SOURCE_DATE_EPOCH="$(git -C "$SCRIPT_DIR" log -1 --format=%ct HEAD)"
VERSION="$(git -C "$SCRIPT_DIR" show HEAD:VERSION | tr -d '[:space:]')"
VERSION="${VERSION#v}"
PACKAGE_VERSION="v${VERSION}"
OUT="${ANETBBS_RELEASE_OUT_DIR:-/tmp}/ANetBBS-${PACKAGE_VERSION}.tar.gz"
TOPDIR="ANetBBS-${PACKAGE_VERSION}"

echo "Building $OUT (top-level dir: $TOPDIR) from commit $COMMIT ..."

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

FILE_LIST="$WORKDIR/files.txt"

# Base: everything git considers safe (tracked, or untracked-but-not-
# gitignored). Excludes this script itself explicitly (a sysop's own
# checkout doesn't need to re-run build-release.sh) and the ANetBBS
# Enhanced Client add-on's own files -- that feature is deliberately
# NOT part of the stock release (same reasoning as keeping RDQ3/
# ANetCHESS as their own separate projects rather than bundled door-
# game archives): a sysop who wants it fetches
# tools/build_enhanced_client_addon.sh's own separate tarball and
# applies it themselves. The small `if self.term_mode == 'enhanced':`
# branches threaded through session.py/menu_engine.py/games.py/main.py/
# config.py are NOT excluded here -- they're core ANetBBS plumbing
# (the same files every other protocol's own term_mode dispatch
# already lives in) and ship inert-by-default (ENHANCED_ENABLED=false)
# with every stock install, same as PETSCII's own always-present
# support. Keep this list in sync with build_enhanced_client_addon.sh's
# own FILES array if that one changes.
git -C "$SCRIPT_DIR" ls-tree -r --name-only HEAD \
  | grep -v '^build-release\.sh$' \
  | grep -v '^anetbbs/core/enhanced_server\.py$' \
  | grep -v '^anetbbs/features/enhanced_protocol\.py$' \
  | grep -v '^anetbbs/enhanced_client/' \
  | grep -v '^anetbbs/static/fonts/Flexi_IBM_VGA_False\.' \
  > "$FILE_LIST"

# Same reasoning, for the add-on-only test files: two have a hard,
# module-level `from anetbbs.core.enhanced_server import ...` /
# `from anetbbs.features import enhanced_protocol` that would error at
# pytest COLLECTION time (not just at the one test that needs it) in a
# tree missing those add-on files; two more specifically test
# anetbbs/enhanced_client/index.html's own client-side JS content, not
# core plumbing. Every other enhanced-client-named test file tests core
# session.py/menu_engine.py/games.py behavior that ships regardless and
# stays in the stock tarball unchanged.
grep -v -E '^tests/test_enhanced_(server_deadconn|protocol|client_block_chars|client_hex_to_rgb)\.py$' \
  "$FILE_LIST" > "$FILE_LIST.tmp" && mv "$FILE_LIST.tmp" "$FILE_LIST"

# Door-game trees and DOS game bundles are NOT part of the release.
# doors/ and data/dos-games/ are untracked local trees (gitignored), so
# the git listing above already leaves them out; this filter is a second
# guard in case either is ever tracked or un-ignored by mistake.
grep -v -E '^(doors|data/dos-games)/' "$FILE_LIST" > "$FILE_LIST.tmp" \
  && mv "$FILE_LIST.tmp" "$FILE_LIST"

LC_ALL=C sort -u "$FILE_LIST" -o "$FILE_LIST"

# Contents come from the commit (git archive), then a normalized tar:
# sorted names, root:root numeric owners, commit-time mtimes, no group/world
# write bits, gzip without a timestamp. Rebuilding the same commit gives the
# same bytes, and no builder account name ends up in the archive.
mkdir -p "$WORKDIR/tree"
git -C "$SCRIPT_DIR" archive --format=tar HEAD | tar -x -C "$WORKDIR/tree"
tar --create --format=gnu --sort=name --no-recursion \
  --owner=0 --group=0 --numeric-owner --mtime="@$SOURCE_DATE_EPOCH" --mode='go-w' \
  --transform "s|^|${TOPDIR}/|" \
  -C "$WORKDIR/tree" --files-from="$FILE_LIST" \
  | gzip -n -9 > "$OUT"

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

# Excluded content must not be present (fail, do not warn).
EXCLUDED=$(tar tzf "$OUT" | grep -E "^${TOPDIR}/(doors|data/dos-games)/|/(doom|duke3d)\.zip$" || true)
if [[ -n "$EXCLUDED" ]]; then
  echo "ERROR: excluded content in tarball:"; echo "$EXCLUDED"
  rm -f "$OUT" "$OUT.sha256" "$OUT.sig"; exit 1
fi

echo "Sanity check passed — ${TOPDIR}/update.sh present, no sensitive files."

# ── sha256 sidecar + optional release signature ─────────────────────────────
# The .sha256 sidecar is what /admin/upgrades/ and peers verify. A detached
# OpenSSH signature (.sig) lets every install that trusts the key in
# deploy/release-signers verify the release no matter which host serves it
# (mirrors, private feeds). Signing key: ANETBBS_SIGNING_KEY, default
# ~/.config/anetbbs/release_signing_key. Create one once with:
#   ssh-keygen -t ed25519 -f ~/.config/anetbbs/release_signing_key -C anetbbs-release
# and add "anetbbs-release <contents of the .pub file>" to deploy/release-signers.
( cd "$(dirname "$OUT")" && sha256sum "$(basename "$OUT")" > "$(basename "$OUT").sha256" )
chmod 0644 "$OUT" "$OUT.sha256"
SIGNING_KEY="${ANETBBS_SIGNING_KEY:-$HOME/.config/anetbbs/release_signing_key}"
SIGNERS_FILE="$SCRIPT_DIR/deploy/release-signers"
REVOKED_FILE="$SCRIPT_DIR/deploy/release-revoked-keys"
LEDGER="${ANETBBS_SIGNING_LEDGER:-$HOME/.config/anetbbs/signed-releases}"
rm -f "$OUT.sig"
if [[ -f "$SIGNING_KEY" ]]; then
  # The signature must verify against the signer file this very commit
  # ships, with its revocation list -- never skipped.
  if ! [[ -f "$SIGNERS_FILE" ]] || ! grep -qv '^\s*\(#\|$\)' "$SIGNERS_FILE"; then
    echo "ERROR: $SIGNERS_FILE lists no key -- refusing to sign"; rm -f "$OUT" "$OUT.sha256"; exit 1
  fi
  KEY_FP="$(ssh-keygen -lf "$SIGNING_KEY" | awk '{print $2}')"
  if [[ -f "$REVOKED_FILE" ]] && while read -r l; do [[ "$l" =~ ^(ssh-|ecdsa-) ]] && printf '%s\n' "$l"; done < "$REVOKED_FILE" \
        | ssh-keygen -lf - 2>/dev/null | awk '{print $2}' | grep -qxF "$KEY_FP"; then
    echo "ERROR: signing key $KEY_FP is on deploy/release-revoked-keys -- refusing to sign"; rm -f "$OUT" "$OUT.sha256"; exit 1
  fi
  # One signed archive per version per key: re-signing different bytes
  # under a version this key already signed would leave two valid,
  # indistinguishable archives for it. Rebuilding the same commit gives
  # the same digest and is fine.
  DIGEST="$(cut -c1-64 "$OUT.sha256")"
  if [[ -f "$LEDGER" ]] && awk -v v="$PACKAGE_VERSION" -v k="$KEY_FP" -v d="$DIGEST" \
        '$1==v && $2==k && $3!=d {bad=1} END {exit !bad}' "$LEDGER"; then
    echo "ERROR: $PACKAGE_VERSION was already signed by $KEY_FP with different contents ($LEDGER)."
    echo "       Bump the version instead of re-signing."; rm -f "$OUT" "$OUT.sha256"; exit 1
  fi
  ssh-keygen -Y sign -f "$SIGNING_KEY" -n anetbbs-release "$OUT" >/dev/null \
    || { echo "ERROR: signing failed"; exit 1; }
  chmod 0644 "$OUT.sig"
  REV_ARGS=(); [[ -f "$REVOKED_FILE" ]] && REV_ARGS=(-r "$REVOKED_FILE")
  ssh-keygen -Y verify -f "$SIGNERS_FILE" -I anetbbs-release -n anetbbs-release \
    -s "$OUT.sig" "${REV_ARGS[@]}" < "$OUT" >/dev/null \
    || { echo "ERROR: signature does not verify against $SIGNERS_FILE"; rm -f "$OUT.sig"; exit 1; }
  if ! [[ -f "$LEDGER" ]] || ! awk -v v="$PACKAGE_VERSION" -v k="$KEY_FP" '$1==v && $2==k {f=1} END {exit !f}' "$LEDGER"; then
    ( umask 077; printf '%s %s %s %s %s\n' "$PACKAGE_VERSION" "$KEY_FP" "$DIGEST" "$COMMIT" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$LEDGER" )
  fi
  echo "Signed: $OUT.sig (key $KEY_FP, commit $COMMIT)"
else
  echo "NOTE: no signing key at $SIGNING_KEY -- release is unsigned (installs require signed web upgrades by default)."
fi
if [[ -f "$OUT.sig" ]]; then echo "Upload together: $OUT  $OUT.sha256  $OUT.sig"; else echo "Upload together: $OUT  $OUT.sha256"; fi
