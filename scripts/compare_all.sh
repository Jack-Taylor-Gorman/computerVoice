#!/usr/bin/env bash
# Run all three comparisons back to back. ~3–6 minutes total.
#   1. clean vs all (which dataset wins)
#   2. F5 vs RVC  (should F5 replace production)
#   3. F5 vs real (how close to ground truth)
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN="${1:-majel_clean_run2}"
CKPT="${2:-model_600}"

echo
echo "▶▶▶  Majel voice comparison · ${RUN}/${CKPT}  ▶▶▶"

bash "$DIR/compare_clean_vs_all.sh" "$CKPT"
echo
read -rp "── press Enter for COMPARISON 2 (F5 vs RVC) ── " _
bash "$DIR/compare_f5_vs_rvc.sh" "$RUN" "$CKPT"
echo
read -rp "── press Enter for COMPARISON 3 (F5 vs real Majel) ── " _
bash "$DIR/compare_f5_vs_real.sh" "$RUN" "$CKPT"

echo
echo "════════════════════════════════════════════════════════════════"
echo "  ALL COMPARISONS DONE"
echo "════════════════════════════════════════════════════════════════"
