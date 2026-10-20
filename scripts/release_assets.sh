#!/usr/bin/env bash
# Package the gitignored runtime assets (F5 voice model, legacy RVC model,
# sounds/) and publish them as a GitHub Release. Re-run to push a new version.
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
# The F5 voice f5_worker.py runs: EMA weights as fp16 safetensors
# (scripts/lean_convert_ckpt.py), its vocab and the reference clip.
F5_RUN="${MAJEL_F5_RUN:-majel_all_run2}"
F5_FILES=(
  "venv/lib/python3.10/ckpts/$F5_RUN/model_600_ema_fp16.safetensors"
  "venv/lib/python3.10/data/${F5_RUN}_pinyin/vocab.txt"
  "dataset/clips_curated/001__0.985__2.1s__Accessing_Library_Computer_Data.wav"
)

for f in "${MODEL_FILES[@]}" "${F5_FILES[@]}"; do
  [[ -f "$f" ]] || { echo "ERROR: missing $f"; exit 1; }
done
[[ -d "$SOUNDS_DIR" ]] || { echo "ERROR: missing $SOUNDS_DIR/"; exit 1; }

command -v gh >/dev/null || { echo "ERROR: gh CLI not installed"; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "ERROR: gh not authenticated"; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

MODEL_TAR="$WORK/majel-model.tar.gz"
SOUNDS_TAR="$WORK/majel-sounds.tar.gz"
F5_TAR="$WORK/majel-f5.tar"
CHECKSUMS="$WORK/CHECKSUMS.txt"

echo "[0/4] packing F5 voice -> $(basename "$F5_TAR") (uncompressed: safetensors don't shrink)"
tar cf "$F5_TAR" "${F5_FILES[@]}"

echo "[1/4] packing model -> $(basename "$MODEL_TAR")"
tar czf "$MODEL_TAR" "${MODEL_FILES[@]}"

echo "[2/4] packing sounds -> $(basename "$SOUNDS_TAR")"
tar czf "$SOUNDS_TAR" "$SOUNDS_DIR"

echo "[3/4] writing CHECKSUMS.txt"
( cd "$WORK" && sha256sum majel-f5.tar majel-model.tar.gz majel-sounds.tar.gz > CHECKSUMS.txt )

MODEL_SIZE=$(du -h "$MODEL_TAR" | cut -f1)
SOUNDS_SIZE=$(du -h "$SOUNDS_TAR" | cut -f1)
F5_SIZE=$(du -h "$F5_TAR" | cut -f1)
echo "       f5:     $F5_SIZE"
echo "       model:  $MODEL_SIZE"
echo "       sounds: $SOUNDS_SIZE"

NOTES=$(cat <<EOF
Majel runtime assets — F5 voice model, legacy RVC model and LCARS sound effects.

These files are gitignored from the main repo (large + redistributable
separately). Fetch with \`scripts/fetch_assets.sh $TAG\`.

| Asset | Contents | Size |
|---|---|---|
| \`majel-f5.tar\` | F5-TTS Majel voice (fp16 EMA weights), vocab, reference clip | $F5_SIZE |
| \`majel-model.tar.gz\` | \`Majel/Majel.pth\`, \`Majel/added_IVF_Flat_Majel_v2.index\` | $MODEL_SIZE |
| \`majel-sounds.tar.gz\` | \`sounds/\` (LCARS SFX clips) | $SOUNDS_SIZE |
| \`CHECKSUMS.txt\` | SHA-256 sums for the archives | — |

Source commit: $(git rev-parse --short HEAD)
EOF
)

echo "[4/4] uploading to release $TAG"
if gh release view "$TAG" >/dev/null 2>&1; then
  echo "       release $TAG already exists - re-uploading assets (--clobber)"
  gh release upload "$TAG" "$F5_TAR" "$MODEL_TAR" "$SOUNDS_TAR" "$CHECKSUMS" --clobber
  gh release edit "$TAG" --notes "$NOTES"
else
  gh release create "$TAG" \
    --title "Majel runtime assets ($TAG)" \
    --notes "$NOTES" \
    "${EXTRA_FLAGS[@]}" \
    "$F5_TAR" "$MODEL_TAR" "$SOUNDS_TAR" "$CHECKSUMS"
fi

echo
echo "Done. Release: $(gh release view "$TAG" --json url --jq .url)"
