#!/bin/bash
# Render → QC → retry loop. Stops when QC reports zero bad chunks or
# after MAX_RETRIES retry rounds.
#
# Usage:
#   scripts/render_with_qc.sh <cleaned.txt> <attribution.jsonl> <out_dir> <pacing.jsonl> [MAX_RETRIES]
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

BOOK="${1:?need cleaned book}"
ATTR="${2:?need attribution.jsonl}"
OUT="${3:?need out dir}"
PACING="${4:?need pacing.jsonl}"
MAX_RETRIES="${5:-3}"

PY="$ROOT/venv/bin/python"
mkdir -p "$OUT/chunks" "$OUT/raw"

attempt=1
while :; do
  echo "============================================================"
  echo "RENDER attempt $attempt"
  echo "============================================================"
  "$PY" scripts/multi_voice_read.py "$BOOK" "$ATTR" \
    --out "$OUT" --pacing "$PACING" --no-play

  echo "============================================================"
  echo "QC sweep $attempt"
  echo "============================================================"
  if [ "$attempt" -ge "$((MAX_RETRIES + 1))" ]; then
    # final pass — QC report only, don't delete (we accept what we have)
    "$PY" scripts/qc_chunks.py "$OUT" --attempt "$attempt"
    echo "max retries reached; accepting current chunks"
    break
  fi
  set +e
  "$PY" scripts/qc_chunks.py "$OUT" --attempt "$attempt" --delete-bad
  rc=$?
  set -e
  # If QC found zero bad → no deletions happened → done.
  bad_count=$(grep -c '"hard":\s*\[\s*[^]]' "$OUT/qc_report_attempt${attempt}.jsonl" 2>/dev/null || true)
  if ! ls "$OUT"/qc_stash/attempt${attempt}_*.wav >/dev/null 2>&1; then
    echo "✓ no bad chunks at attempt $attempt — done"
    break
  fi
  attempt=$((attempt + 1))
done

echo "DONE. Master: $OUT/complete.wav"
