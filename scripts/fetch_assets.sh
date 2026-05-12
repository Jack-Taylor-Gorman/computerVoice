#!/usr/bin/env bash
# Download the Majel runtime assets (RVC voice model + sounds/) from a
# GitHub Release and extract them into the project root.
#
#   scripts/fetch_assets.sh                 # latest release
#   scripts/fetch_assets.sh assets-v2       # specific tag
#   scripts/fetch_assets.sh assets-v2 --force   # overwrite existing files

set -euo pipefail

TAG="${1:-}"
FORCE=0
for arg in "${@:2}"; do
  [[ "$arg" == "--force" ]] && FORCE=1
done

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

command -v gh >/dev/null || { echo "ERROR: gh CLI not installed"; exit 1; }

if [[ -z "$TAG" ]]; then
  TAG=$(gh release list --limit 1 --json tagName --jq '.[0].tagName' 2>/dev/null || true)
  [[ -n "$TAG" ]] || { echo "ERROR: no releases found"; exit 1; }
  echo "Using latest release tag: $TAG"
fi

if [[ $FORCE -eq 0 ]]; then
  for f in Majel/Majel.pth Majel/added_IVF_Flat_Majel_v2.index; do
    if [[ -e "$f" ]]; then
      echo "ERROR: $f already exists. Re-run with --force to overwrite." >&2
      exit 1
    fi
  done
  if [[ -d sounds && -n "$(ls -A sounds 2>/dev/null)" ]]; then
    echo "ERROR: sounds/ is non-empty. Re-run with --force to overwrite." >&2
    exit 1
  fi
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "[1/3] downloading assets from $TAG"
gh release download "$TAG" \
  --pattern 'majel-model.tar.gz' \
  --pattern 'majel-sounds.tar.gz' \
  --pattern 'CHECKSUMS.txt' \
  --dir "$WORK"

echo "[2/3] verifying checksums"
( cd "$WORK" && sha256sum -c CHECKSUMS.txt )

echo "[3/3] extracting into $ROOT"
tar xzf "$WORK/majel-model.tar.gz"  -C "$ROOT"
tar xzf "$WORK/majel-sounds.tar.gz" -C "$ROOT"

echo
echo "Done."
echo "  Majel/Majel.pth                       $(du -h Majel/Majel.pth | cut -f1)"
echo "  Majel/added_IVF_Flat_Majel_v2.index   $(du -h Majel/added_IVF_Flat_Majel_v2.index | cut -f1)"
echo "  sounds/                                $(du -sh sounds | cut -f1)  ($(find sounds -type f | wc -l) files)"
