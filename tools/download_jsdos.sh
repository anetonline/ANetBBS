#!/usr/bin/env bash
# Download the js-dos v6.22 runtime (DOSBox compiled to WebAssembly).
#
# Files are NOT included in the git repository because wdosbox.wasm.js is ~5 MB.
# Run this script once on each installation before using in-browser DOS games.
#
# Output: anetbbs/static/js-dos/{js-dos.js,wdosbox.js,wdosbox.wasm.js}

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_ROOT="$(dirname "$SCRIPT_DIR")"
OUT_DIR="$INSTALL_ROOT/anetbbs/static/js-dos"

# js-dos 6.22 via jsDelivr (mirrors npm — no account needed)
VERSION="6.22"
BASE_URL="https://cdn.jsdelivr.net/npm/js-dos@${VERSION}"

FILES=(js-dos.js wdosbox.js wdosbox.wasm.js)

# Real gap found in a security/performance audit: these three files
# become real, executable JS served to every visitor who opens an
# in-browser DOS game -- downloaded with no integrity check at all, so
# a compromised CDN edge or a MITM on a network without proper TLS
# validation could substitute malicious JS with no way for a sysop to
# notice. SHA-256 pinned against the actual v6.22 release fetched
# directly from jsDelivr; verified with sha256sum after each download,
# and the file is deleted rather than left in place on a mismatch.
declare -A EXPECTED_SHA256=(
    [js-dos.js]="7b7d7d4bc22d4f0582a7e6e3b299abae5b02a854afeddcc17e7960ef33b1836d"
    [wdosbox.js]="84e17b39fb3e1bbdbdbba40f4cddc262afb4baa44b12e2b088f9df66849a9be5"
    [wdosbox.wasm.js]="6f0a70c14bdbe22f92df971ec70b9050559fc53329cfc79734f30cac2c73601a"
)

echo "=== Downloading js-dos v${VERSION} ==="
echo "Destination: $OUT_DIR"
echo

mkdir -p "$OUT_DIR"

for f in "${FILES[@]}"; do
    dest="$OUT_DIR/$f"
    if [[ -f "$dest" ]]; then
        echo "  already exists: $f  (delete to re-download)"
        continue
    fi
    url="$BASE_URL/$f"
    echo "  downloading: $f"
    if command -v curl &>/dev/null; then
        curl -fsSL --progress-bar -o "$dest" "$url"
    elif command -v wget &>/dev/null; then
        wget -q --show-progress -O "$dest" "$url"
    else
        echo "ERROR: neither curl nor wget found." >&2
        exit 1
    fi

    expected="${EXPECTED_SHA256[$f]:-}"
    if [[ -z "$expected" ]]; then
        echo "ERROR: no pinned checksum for $f -- refusing to trust an" >&2
        echo "  unverified download. Add it to EXPECTED_SHA256 above." >&2
        rm -f "$dest"
        exit 1
    fi
    actual="$(sha256sum "$dest" | awk '{print $1}')"
    if [[ "$actual" != "$expected" ]]; then
        echo "ERROR: checksum mismatch for $f -- refusing to keep a file" >&2
        echo "  that doesn't match the pinned js-dos v${VERSION} release." >&2
        echo "  expected: $expected" >&2
        echo "  actual:   $actual" >&2
        rm -f "$dest"
        exit 1
    fi
done

echo
echo "=== Done ==="
echo
echo "Files written to: $OUT_DIR"
ls -lh "$OUT_DIR"
echo
echo "Now create your game bundles with:"
echo "  python tools/prepare_dos_games.py --source-dir /path/to/DOOM --exe DOOM.EXE --output doom --name 'DOOM (Shareware)'"
echo "  python tools/prepare_dos_games.py --source-dir /path/to/DUKE3D --exe DUKE3D.EXE --output duke3d --name 'Duke Nukem 3D (Shareware)'"
