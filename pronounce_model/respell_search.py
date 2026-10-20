"""Pick respellings by ear: synthesise each candidate spelling of a target
word inside its eval phrase, score it with the phoneme recogniser against
the expected pronunciation, and rank the candidates.

  python3 pronounce_model/respell_search.py make NAME   (writes /tmp/pronounce_eval/NAME.cases.json)
  PRONOUNCE_CASES=/tmp/pronounce_eval/NAME.cases.json python3 pronounce_model/eval.py synth NAME --cond raw --reps 3
  PRONOUNCE_CASES=... venv/bin/python pronounce_model/eval.py score NAME
  python3 pronounce_model/respell_search.py rank NAME

Candidates that pronounce.py would itself rewrite are dropped, so the text
the worker speaks is exactly the candidate whichever version it has loaded.
"""
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import pronounce  # noqa: E402

OUT = Path("/tmp/pronounce_eval")

CANDIDATES = {
    "oov-async": ["async", "ay-sink", "ace-ink", "A-sink", "ace sink", "aysync", "eigh-sink", "a sink"],
    "oov-api": ["API", "ay pee eye", "A P I", "A. P. I", "eigh pee eye", "ae pee eye"],
    "oov-ai": ["AI", "ay eye", "A I", "A. I", "eigh eye", "ae eye"],
}


def make(name):
    cases = {c["id"]: c for c in json.loads((HERE / "eval_cases.json").read_text())}
    out = []
    for cid, cands in CANDIDATES.items():
        base = cases[cid]
        for cand in cands:
            text = re.sub(r"\b%s\b" % re.escape(base["target"]), cand, base["text"], count=1, flags=re.I)
            if pronounce.apply(text) != text and cand != base["target"]:
                print("skip (pronounce.py would rewrite it):", cid, cand, "->", pronounce.apply(text))
                continue
            out.append(dict(base, id=f"{cid}~{cand}", text=text, score_text=base["text"]))
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}.cases.json"
    path.write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(f"{len(out)} candidate cases -> {path}")


def rank(name):
    rows = json.loads((OUT / name / "scores.json").read_text())
    by = defaultdict(list)
    for r in rows:
        if "margin" in r:
            by[r["id"]].append(r)
    groups = defaultdict(list)
    for cid, rs in by.items():
        base, cand = cid.split("~", 1)
        wins = sum(r["verdict"] == "pass" for r in rs)
        margin = sum(r["margin"] for r in rs) / len(rs)
        groups[base].append((wins, len(rs), round(margin, 2), cand, [r["heard"] for r in rs]))
    for base, cands in groups.items():
        print(base)
        for wins, n, margin, cand, heard in sorted(cands, key=lambda t: (-t[0], -t[2])):
            print(f"   {wins}/{n}  margin {margin:7.2f}  {cand!r:20} heard: {heard[0]}")


if __name__ == "__main__":
    {"make": make, "rank": rank}[sys.argv[1]](sys.argv[2])
