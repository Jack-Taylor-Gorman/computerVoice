"""Text-level check of the heteronym classifiers: does pronounce.py pick the
reading each eval case expects? Instant and stdlib-only; the audio eval
(eval.py) remains the real measure.

  python3 pronounce_model/het_check.py [heteronyms.json]
"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import pronounce  # noqa: E402

READING = {
    "L AY V": "live_adj", "L IH V": "live_vrb", "R EH D": "read_past", "R IY D": "read_present",
    "L EH D": "lead_nou", "L IY D": "lead_nou-vrb", "K L OW S": "close_adj-nou", "K L OW Z": "close_vrb",
    "Y UW S": "use_nou", "Y UW Z": "use_vrb", "R EH K ER D": "record_nou", "R IH K AO R D": "record_vrb",
    "P R EH Z AH N T": "present_adj-nou", "P R IY Z EH N T": "present_vrb",
    "AA B JH EH K T": "object_nou", "AH B JH EH K T": "object_vrb",
    "K AA N T EH N T": "content_nou", "K AH N T EH N T": "content_adj-nou-vrb",
    "M IH N AH T": "minute", "M AY N UW T": "minute_adj",
    "R EH Z AH M EY": "resume_nou", "R IH Z UW M": "resume_vrb",
    "W IH N D": "wind_nou", "W AY N D": "wind_vrb", "T EH R": "tear_vrb", "T IH R": "tear_nou",
    "D EH Z ER T": "desert_nou", "D IH Z ER T": "desert_vrb", "R AW": "row_2", "R OW": "row_1",
    "R EH F Y UW S": "refuse_nou", "R IH F Y UW Z": "refuse_vrb",
    "P R OW D UW S": "produce_nou", "P R AH D UW S": "produce_vrb",
    "W UW N D": "wound_nou-vrb", "W AW N D": "wound_vrb", "B AW": "bow_nou-ship", "B OW": "bow_nou-knot",
}


def check(path, models):
    right, errors = 0, []
    cases = [c for c in json.loads(path.read_text()) if c["type"] == "heteronym"]
    for c in cases:
        want = READING[re.sub(r"\d", "", c["expect"])]
        toks = pronounce._CTX.findall(c["text"])
        i = [k for k, t in enumerate(toks) if t.lower() == c["target"].lower()][0]
        got = pronounce.classify(toks[i], toks, i, models[c["target"].lower()])
        # Readings that share a spelling (both None) are equivalent at runtime.
        same = got == want or (pronounce.HETERONYM_RESPELL.get(got) is None
                               and pronounce.HETERONYM_RESPELL.get(want) is None)
        right += same
        if not same:
            errors.append(f"   {c['text']!r}: wanted {want}, got {got}")
    print(f"{path.name}: {right}/{len(cases)}")
    print("\n".join(errors))
    return right, len(cases)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        pronounce.HETERONYM_FILE = Path(sys.argv[1])
    models = pronounce._load_heteronyms()
    tot = [check(HERE / f, models) for f in ("eval_cases.json", "eval_heldout.json", "eval_heldout2.json")]
    print(f"total {sum(a for a, _ in tot)}/{sum(b for _, b in tot)}")
