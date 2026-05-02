#!/usr/bin/env bash
# F5-TTS fine-tune launcher for a single Majel dataset bundle.
#
# Usage:  scripts/finetune_f5.sh dataset/datasets/majel_clean_<ts>
#
# Pipeline:
#   1. Write F5-TTS-format CSV (audio_file|text, abs paths) from metadata.jsonl
#   2. Run F5-TTS prepare_csv_wavs.py → raw.arrow / duration.json / vocab.txt
#      into the location f5-tts_finetune-cli expects.
#   3. Launch f5-tts_finetune-cli with --finetune (downloads base ckpt on
#      first run via cached_path).
set -euo pipefail

if [ $# -lt 1 ]; then
    echo "usage: $0 <dataset_bundle_dir>" >&2
    exit 2
fi

DS_PATH="$(realpath "$1")"
DS_NAME="$(basename "$DS_PATH")"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/venv/bin/python"

# f5-tts_finetune-cli looks at f5_tts/../../data/<dataset_name>_<tokenizer>/
# That resolves to venv/lib/python3.10/data/<name>_pinyin.
# Council recommendation: stay on the base model's native tokenizer
# (pinyin) — switching to char on a 5-min English corpus risks embedding
# misalignment because F5TTS_v1_Base was pretrained on the pinyin vocab.
F5_DATA_DIR="$ROOT/venv/lib/python3.10/data/${DS_NAME}_pinyin"

echo "[$(date +%H:%M:%S)] === F5 fine-tune: ${DS_NAME} ==="
echo "  bundle:    ${DS_PATH}"
echo "  prep dir:  ${F5_DATA_DIR}"

# 1. CSV reformat — F5-TTS wants `audio_file|text` with absolute paths.
F5_CSV="${DS_PATH}/metadata_f5.csv"
"$PY" - "$DS_PATH" "$F5_CSV" <<'PYEOF'
import json, sys
from pathlib import Path
ds = Path(sys.argv[1]).resolve()
out = Path(sys.argv[2])
n = 0
with out.open("w") as f:
    f.write("audio_file|text\n")
    for line in (ds / "metadata.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        path = (ds / r["audio_path"]).resolve()
        text = r["transcript"].replace("|", " ").replace("\n", " ").strip()
        f.write(f"{path}|{text}\n")
        n += 1
print(f"wrote {n} rows → {out}")
PYEOF

# 2. F5-TTS prepare. raw.arrow + duration.json + vocab.txt land in F5_DATA_DIR.
mkdir -p "$F5_DATA_DIR"
"$PY" "$ROOT/venv/lib/python3.10/site-packages/f5_tts/train/datasets/prepare_csv_wavs.py" \
    "$F5_CSV" "$F5_DATA_DIR" --workers 4

# 3. Launch fine-tune. First run pulls F5TTS_v1_Base checkpoint from HF.
# Tuned per council review for a sub-15-min corpus:
#   - tokenizer pinyin: matches base-model native vocab (avoids embedding
#     misalignment vs char tokenizer on a tiny English corpus)
#   - epochs 30: 80 over-trains on ~100 samples (≈2k steps); 30≈750 steps
#   - save every 100: more checkpoints to audition (best ckpt is rarely
#     the last on small data)
#   - keep last 10: room to A/B early vs late checkpoints
# VRAM budget: GPU has 7.6 GiB total; majel_daemon holds ~766 MiB for
# production inference (don't kill it). Effective budget ≈ 6.8 GiB.
# Fits with: 8-bit Adam (--bnb_optimizer halves optimizer state),
# physical batch 1, grad-accum 4 (= effective batch 4), expandable
# CUDA segments to avoid fragmentation OOMs.
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
"$ROOT/venv/bin/f5-tts_finetune-cli" \
    --exp_name F5TTS_v1_Base \
    --dataset_name "${DS_NAME}" \
    --finetune \
    --tokenizer pinyin \
    --epochs 30 \
    --batch_size_type sample \
    --batch_size_per_gpu 1 \
    --grad_accumulation_steps 4 \
    --bnb_optimizer \
    --num_warmup_updates 100 \
    --save_per_updates 100 \
    --keep_last_n_checkpoints 10 \
    --logger tensorboard

echo "[$(date +%H:%M:%S)] === DONE: ${DS_NAME} ==="
