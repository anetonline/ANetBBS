#!/usr/bin/env bash
# Download the js-dos v6.22 runtime (DOSBox compiled to WebAssembly).
#
# Files are NOT included in the git repository because wdosbox.wasm is ~5 MB.
# Run this script once on each installation before using in-browser DOS games.
#
# Output: anetbbs/static/js-dos/{js-dos.js,wdosbox.js,wdosbox.wasm}

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_ROOT="$(dirname "$SCRIPT_DIR")"
OUT_DIR="$INSTALL_ROOT/anetbbs/static/js-dos"

# js-dos 6.22 via jsDelivr (mirrors npm — no account needed)
VERSION="6.22"
BASE_URL="https://cdn.jsdelivr.net/npm/js-dos@${VERSION}"

FILES=(js-dos.js wdosbox.js wdosbox.wasm.js)

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
