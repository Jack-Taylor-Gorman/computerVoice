#!/usr/bin/env bash
# Package the gitignored runtime assets (RVC voice model + sounds/) and
# publish them as a GitHub Release. Re-run to push a new version.
#
#   scripts/release_assets.sh                 # default tag: assets-v1
#   scripts/release_assets.sh assets-v2
#   scripts/release_assets.sh assets-v2 --draft

set -euo pipefail

TAG="${1:-assets-v1}"
shift || true
EXTRA_FLAGS=("$@")

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

MODEL_FILES=(
  "Majel/Majel.pth"
  "Majel/added_IVF_Flat_Majel_v2.index"
)
SOUNDS_DIR="sounds"

for f in "${MODEL_FILES[@]}"; do
  [[ -f "$f" ]] || { echo "ERROR: missing $f"; exit 1; }
done
[[ -d "$SOUNDS_DIR" ]] || { echo "ERROR: missing $SOUNDS_DIR/"; exit 1; }

command -v gh >/dev/null || { echo "ERROR: gh CLI not installed"; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "ERROR: gh not authenticated"; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

MODEL_TAR="$WORK/majel-model.tar.gz"
SOUNDS_TAR="$WORK/majel-sounds.tar.gz"
CHECKSUMS="$WORK/CHECKSUMS.txt"

echo "[1/4] packing model -> $(basename "$MODEL_TAR")"
tar czf "$MODEL_TAR" "${MODEL_FILES[@]}"

echo "[2/4] packing sounds -> $(basename "$SOUNDS_TAR")"
tar czf "$SOUNDS_TAR" "$SOUNDS_DIR"

echo "[3/4] writing CHECKSUMS.txt"
( cd "$WORK" && sha256sum majel-model.tar.gz majel-sounds.tar.gz > CHECKSUMS.txt )

MODEL_SIZE=$(du -h "$MODEL_TAR" | cut -f1)
SOUNDS_SIZE=$(du -h "$SOUNDS_TAR" | cut -f1)
echo "       model:  $MODEL_SIZE"
echo "       sounds: $SOUNDS_SIZE"

NOTES=$(cat <<EOF
Majel runtime assets — RVC voice model + LCARS sound effects.

These files are gitignored from the main repo (large + redistributable
separately). Fetch with \`scripts/fetch_assets.sh $TAG\`.

| Asset | Contents | Size |
|---|---|---|
| \`majel-model.tar.gz\` | \`Majel/Majel.pth\`, \`Majel/added_IVF_Flat_Majel_v2.index\` | $MODEL_SIZE |
| \`majel-sounds.tar.gz\` | \`sounds/\` (LCARS SFX clips) | $SOUNDS_SIZE |
| \`CHECKSUMS.txt\` | SHA-256 sums for both tarballs | — |

Source commit: $(git rev-parse --short HEAD)
EOF
)

echo "[4/4] uploading to release $TAG"
if gh release view "$TAG" >/dev/null 2>&1; then
  echo "       release $TAG already exists - re-uploading assets (--clobber)"
  gh release upload "$TAG" "$MODEL_TAR" "$SOUNDS_TAR" "$CHECKSUMS" --clobber
  gh release edit "$TAG" --notes "$NOTES"
else
  gh release create "$TAG" \
    --title "Majel runtime assets ($TAG)" \
    --notes "$NOTES" \
    "${EXTRA_FLAGS[@]}" \
    "$MODEL_TAR" "$SOUNDS_TAR" "$CHECKSUMS"
fi

echo
echo "Done. Release: $(gh release view "$TAG" --json url --jq .url)"
