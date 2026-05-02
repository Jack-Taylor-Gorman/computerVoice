#!/usr/bin/env python3
"""Score auditioned F5-TTS outputs by running Whisper back over them
and computing CER vs the prompt that was supposed to be synthesized.

Council recommendation: with val=2 the training-loss curve is noise.
A held-out prompt deck + whisper-back CER is the cheapest objective
"is this checkpoint actually pronouncing words" signal we can wire up.

Usage:  ./venv/bin/python scripts/score_audition.py majel_clean_run2
Output:
  dataset/audition/<run>/scores.csv         per-(ckpt, prompt) CER
  dataset/audition/<run>/scores_summary.md  ckpt rank by mean CER

"Best checkpoint" by mean CER is rarely the last on small data — pair
this with audible audition before picking a winner.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AUDITION_DIR = ROOT / "dataset" / "audition"

PROMPTS = [
    "Captain on the bridge.",
    "Warp core breach in three minutes.",
    "Access denied. Restricted materials.",
    "Diagnostic complete. All systems nominal.",
    "Working.",
    "Hello, world. This is the Majel computer voice.",
]


def normalize(s: str) -> str:
    s = re.sub(r"[^\w\s]", "", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def cer(ref: str, hyp: str) -> float:
    """Character error rate (Levenshtein on chars / len(ref)). Lower is better."""
    ref = normalize(ref)
    hyp = normalize(hyp)
    if not ref:
        return 1.0
    n, m = len(ref), len(hyp)
    if m == 0:
        return 1.0
    prev = list(range(m + 1))
    for i in range(1, n + 1):
        cur = [i] + [0] * m
        for j in range(1, m + 1):
            ins = cur[j - 1] + 1
            dele = prev[j] + 1
            sub = prev[j - 1] + (0 if ref[i - 1] == hyp[j - 1] else 1)
            cur[j] = min(ins, dele, sub)
        prev = cur
    return prev[m] / n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run", help="audition run name, e.g. majel_clean_run2")
    ap.add_argument("--model", default="medium.en")
    args = ap.parse_args()

    run_dir = AUDITION_DIR / args.run
    if not run_dir.exists():
        sys.stderr.write(f"missing {run_dir}\n")
        return 2

    ckpt_dirs = sorted(d for d in run_dir.iterdir() if d.is_dir())
    if not ckpt_dirs:
        sys.stderr.write("no checkpoint subdirs\n")
        return 2

    print(f"loading whisper {args.model}…")
    import torch  # type: ignore
    import whisper  # type: ignore
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = whisper.load_model(args.model, device=device)

    rows: list[dict] = []
    for ck in ckpt_dirs:
        wavs = sorted(ck.glob("[0-9][0-9].wav"))
        for w in wavs:
            idx = int(w.stem) - 1
            if idx < 0 or idx >= len(PROMPTS):
                continue
            prompt = PROMPTS[idx]
            try:
                result = model.transcribe(
                    str(w), language="en", fp16=(device == "cuda"),
                    verbose=False, condition_on_previous_text=False,
                    temperature=0.0, no_speech_threshold=0.4,
                )
                hyp = (result.get("text") or "").strip()
            except Exception as e:
                hyp = ""
                print(f"  ! {w.name}: {e}")
            score = cer(prompt, hyp)
            rows.append({
                "ckpt": ck.name,
                "prompt_idx": idx + 1,
                "prompt": prompt,
                "whisper": hyp,
                "cer": round(score, 3),
            })
            print(f"[{ck.name:>12}] {idx+1}: cer={score:.2f}  '{hyp[:60]}'")

    csv_path = run_dir / "scores.csv"
    with csv_path.open("w") as f:
        w = csv.DictWriter(f, fieldnames=["ckpt", "prompt_idx", "prompt", "whisper", "cer"])
        w.writeheader()
        w.writerows(rows)

    # Per-checkpoint mean CER ranking.
    by_ck: dict[str, list[float]] = {}
    for r in rows:
        by_ck.setdefault(r["ckpt"], []).append(r["cer"])
    ranking = sorted(((k, sum(v) / len(v)) for k, v in by_ck.items()), key=lambda x: x[1])

    md = ["# F5-TTS audition scoring — " + args.run, "",
          "Whisper-back CER per checkpoint, lower is better.", "",
          "| rank | ckpt | mean CER | n |",
          "|------|------|----------|---|"]
    for i, (k, mean_c) in enumerate(ranking, 1):
        md.append(f"| {i} | `{k}` | {mean_c:.3f} | {len(by_ck[k])} |")
    md.append("")
    md.append("Best CER ≠ best timbre. Pair with audible audition.")

    md_path = run_dir / "scores_summary.md"
    md_path.write_text("\n".join(md) + "\n")

    print()
    print(f"wrote {csv_path}")
    print(f"wrote {md_path}")
    print()
    print("=== ranking by mean CER (lower better) ===")
    for i, (k, mean_c) in enumerate(ranking, 1):
        print(f"  {i}. {k}  cer={mean_c:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
