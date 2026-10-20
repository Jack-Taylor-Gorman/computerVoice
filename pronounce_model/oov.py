"""Out-of-vocabulary respellings, chosen by ear and cached in respell.json.

The words come from mine_words.py (rare words in the assistant's prose).
Each gets an expected pronunciation: oov_expect.tsv first (hand-checked,
for brands and initialisms), then CMUdict, then g2p_en (flagged, since
that is itself a guess from the spelling). The pipeline is:

  cands   (training python) : expected phones + a screening case per word
          PYTHONPATH=~/.cache/pronounce_train_libs /usr/bin/python3.10 pronounce_model/oov.py cands
  synth/score the screen with eval.py (PRONOUNCE_CASES=/tmp/pronounce_oov/screen.cases.json)
  respell                   : respelling candidates, from the phones, for every
                              word the screen found misread
  synth/score those the same way (respell.cases.json)
  pick                      : writes pronounce_model/respell.json with the
                              respellings that beat the plain spelling

A screening case is the word in a carrier phrase, sent with
pronounce.RAW_PREFIX so the worker speaks it untouched. Its wrong readings
are generated: every other vowel in each primary-stressed slot, so the scorer asks
"is the stressed vowel the expected one?" without a hand-written list.
"""
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import pronounce  # noqa: E402
from eval import arpa_to_ipa  # noqa: E402

WORK = Path("/tmp/pronounce_oov")
EVAL_OUT = Path("/tmp/pronounce_eval")
EXPECT = HERE / "oov_expect.tsv"
RESPELL = HERE / "respell.json"
CARRIER = "The next item is {}, as planned."
VOWELS = ["AA", "AE", "AH", "AO", "AW", "AY", "EH", "ER", "EY", "IH", "IY", "OW", "OY", "UH", "UW"]
CONTRACTION_STUBS = {"didn", "doesn", "isn", "couldn", "aren", "hasn", "wouldn", "wasn", "hadn",
                     "shouldn", "weren", "mustn", "needn"}
LETTERS = {"A": "EY1", "B": "B IY1", "C": "S IY1", "D": "D IY1", "E": "IY1", "F": "EH1 F",
           "G": "JH IY1", "H": "EY1 CH", "I": "AY1", "J": "JH EY1", "K": "K EY1", "L": "EH1 L",
           "M": "EH1 M", "N": "EH1 N", "O": "OW1", "P": "P IY1", "Q": "K Y UW1", "R": "AA1 R",
           "S": "EH1 S", "T": "T IY1", "U": "Y UW1", "V": "V IY1", "W": "D AH1 B AH0 L Y UW0",
           "X": "EH1 K S", "Y": "W AY1", "Z": "Z IY1"}


def load_expect():
    out = {}
    if EXPECT.exists():
        for line in EXPECT.read_text().splitlines():
            if line.strip() and not line.startswith("#"):
                w, ph = line.split("\t")[:2]
                out[w.lower()] = ph.strip()
    return out


def wrong_readings(expect):
    """Every single substitution of a stressed vowel, minus any that sound
    the same as the expected reading once converted to IPA."""
    ph = expect.split()
    same = " ".join(arpa_to_ipa(ph))
    out, seen = [], {same}
    for k, p in enumerate(ph):
        base, stress = p.rstrip("012"), p[len(p.rstrip("012")):]
        # Primary stress only: secondary-stressed vowels reduce in fluent
        # speech ("caveat" -> "kav-ee-ut"), which is not a misreading.
        if base not in VOWELS or stress != "1":
            continue
        for v in VOWELS:
            if {v, base} <= {"AO", "AA"}:
                continue  # merged in American speech, and by the recogniser
            alt = ph[:k] + [v + stress] + ph[k + 1:]
            ipa = " ".join(arpa_to_ipa(alt))
            if ipa not in seen:
                seen.add(ipa)
                out.append(" ".join(alt))
    return out


def cands(min_sessions=3, zipf_below=3.2):
    from nltk.corpus import cmudict
    from g2p_en import G2p
    cmu = cmudict.dict()
    g2p = G2p()
    expect = load_expect()
    mined = json.loads((WORK / "mined.json").read_text())
    table = pronounce._lexicon()
    rows, cases = [], []
    words = [(r["word"], r["forms"][0], r) for r in mined]
    words += [(w, w, {"sessions": 0}) for w in expect if w not in {r["word"] for r in mined}]
    for w, form, r in words:
        if w in CONTRACTION_STUBS or w in table or r.get("sessions", 0) and r["sessions"] < min_sessions:
            continue
        caps = form.isupper() and len(form) > 1
        if expect.get(w) == "-":
            continue
        if w in expect:
            ph, src = expect[w], "hand"
        elif r.get("sessions") and not (not r["cmu"] or r["zipf"] < zipf_below or caps):
            continue
        elif w in cmu and not caps:
            ph, src = " ".join(cmu[w][0]), "cmudict"
        elif caps and w not in cmu:
            ph, src = " ".join(LETTERS[c] for c in form), "letters"
        else:
            ph, src = " ".join(p for p in g2p(w) if p.strip()), "g2p_en"
        if not re.fullmatch(r"[A-Za-z]+", form):
            continue
        rows.append({"word": w, "form": form, "phones": ph, "src": src})
        cases.append({"id": "oov-" + w, "type": "oov", "target": form,
                      "text": pronounce.RAW_PREFIX + CARRIER.format(form),
                      "score_text": CARRIER.format(form), "expect": ph, "wrong": wrong_readings(ph)})
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / "phones.json").write_text(json.dumps(rows, indent=0))
    (WORK / "screen.cases.json").write_text(json.dumps(cases, indent=0, ensure_ascii=False))
    by = defaultdict(int)
    for r in rows:
        by[r["src"]] += 1
    print(f"{len(cases)} screening cases -> {WORK / 'screen.cases.json'}  ({dict(by)})")


# ARPAbet -> plain-English spelling cues, the same ones the hand-written
# lexicon uses. Each entry lists alternatives; the first is the default.
OPEN = {"IY": ["ee"], "IH": ["ih", "i"], "EY": ["ay", "ai"], "EH": ["eh", "e"], "AE": ["a"],
        "AA": ["ah", "o"], "AO": ["aw"], "OW": ["oh", "o"], "UH": ["oo"], "UW": ["oo"],
        "AH": ["uh", "u"], "AY": ["eye", "igh", "ai"], "AW": ["ow"], "OY": ["oy"], "ER": ["er"]}
# "i_e" is a silent-e spelling ("tim" + "e"); F5 reads "y" as a short I, so no "y".
CLOSED = {"IY": ["ee"], "IH": ["i"], "EY": ["a_e", "ay"], "EH": ["e"], "AE": ["a"], "AA": ["o", "ah"],
          "AO": ["aw"], "OW": ["o_e", "oh"], "UH": ["oo"], "UW": ["oo", "u_e"], "AH": ["u"],
          "AY": ["i_e", "eye", "igh"], "AW": ["ow"], "OY": ["oy"], "ER": ["er"]}
UNSTRESSED = {"AH": ["uh", "a"], "IH": ["i", "ih"], "ER": ["er"]}
R_VOWEL = {"AA": "ar", "AO": "or", "EH": "air", "IH": "eer", "IY": "eer", "UH": "oor", "OW": "or"}
CONS = {"B": "b", "CH": "ch", "D": "d", "DH": "th", "F": "f", "G": "g", "HH": "h", "JH": "j",
        "K": "k", "L": "l", "M": "m", "N": "n", "NG": "ng", "P": "p", "R": "r", "S": "s", "SH": "sh",
        "T": "t", "TH": "th", "V": "v", "W": "w", "Y": "y", "Z": "z", "ZH": "zh"}
ONSETS = {("S", "T"), ("S", "P"), ("S", "K"), ("S", "N"), ("S", "M"), ("S", "L"), ("S", "W")} | {
    (a, b) for a in ("P", "B", "T", "D", "K", "G", "F", "TH", "SH") for b in ("R", "L", "W")}


def syllables(ph):
    """Split ARPAbet into (onset, vowel, coda) syllables, maximal legal onset."""
    nuc = [k for k, p in enumerate(ph) if p.rstrip("012") in VOWELS]
    if not nuc:
        return []
    out, start = [], 0
    for n, v in enumerate(nuc):
        nxt = nuc[n + 1] if n + 1 < len(nuc) else None
        if nxt is None:
            end = len(ph)
        else:
            cl = ph[v + 1:nxt]
            if len(cl) <= 1:
                end = v + 1
            elif tuple(cl[-2:]) in ONSETS:
                end = nxt - 2
            else:
                end = nxt - 1
        out.append((ph[start:v], ph[v], ph[v + 1:end]))
        start = end
    return out


def spell(ph, choice=0):
    """One respelling of the phones; choice picks alternative vowel spellings."""
    sylls = syllables(ph.split())
    parts = []
    for k, (on, v, co) in enumerate(sylls):
        base, stress = v.rstrip("012"), v[len(v.rstrip("012")):]
        s = "".join(CONS[c] for c in on)
        vs = ""
        if co and co[0] == "R" and base in R_VOWEL:
            s += R_VOWEL[base]
            co = co[1:]
        else:
            if stress == "0" and base in UNSTRESSED:
                opts = UNSTRESSED[base] if co or base != "IH" else ["ih", "eh"]
            elif base == "AY" and not co:
                opts = ["igh", "eye"] if on else ["eye", "igh"]
            else:
                opts = (CLOSED if co else OPEN)[base]
            if len(co) > 1:  # silent e only after a single consonant
                opts = [o for o in opts if not o.endswith("_e")] or ["oh"]
            vs = opts[min(choice, len(opts) - 1)]
            s += vs.split("_")[0]
        cs = "".join(CONS[c] for c in co)
        if vs.endswith("_e"):
            cs += "e"
        # "g" before e/i/y reads as j; a lone final "s" after a vowel reads as z.
        s = re.sub(r"g(?=[eiy])", "gh", s)
        if cs == "s" and not vs.endswith("_e") and base in ("IH", "EH", "AE", "AH", "UH") and k == len(sylls) - 1:
            cs = "ss"
        parts.append(s + cs)
    return parts


def respell_variants(ph):
    a, b = spell(ph, 0), spell(ph, 1)
    out = []
    for v in ("-".join(a), "".join(a), "-".join(b), " ".join(a)):
        if v and v not in out:
            out.append(v)
    return out


# Screen failures that are the expectation's fault, not F5's: CMUdict's
# "recreating" is the leisure sense, and "durations", "caveats", "jagged"
# and "shortlist" came out in an accepted variant.
NOT_MISREAD = {"recreating", "durations", "caveats", "jagged", "shortlist"}
# Extra candidates where the phones-to-spelling rules are unlikely to find
# the best one (abbreviations said as words, names with a known spelling cue).
HAND_CANDIDATES = {
    "thu": ["Thursday", "Thurs"], "ctrl": ["control", "kontrol"],
    "nvidia": ["en-vidia", "en-viddia", "envidya"], "ffmpeg": ["eff eff em peg", "F F em peg"],
    "llm": ["el el em", "L L M"], "adb": ["ay dee bee", "A D B"], "fps": ["eff pee ess", "F P S"],
    "pid": ["pee eye dee", "P I D"], "indiegogo": ["indee go go", "indie go go"],
    "stremio": ["strem-ee-oh", "stremmy-oh"], "deploys": ["dee-ploys", "deploise"],
    "pasting": ["paysting", "pace-ting"], "unread": ["un-red", "unred"],
    "retry": ["ree-try", "re-try"], "retries": ["ree-tries", "re-tries"],
}


def respell():
    rows = json.loads((EVAL_OUT / "oov_screen" / "scores.json").read_text())
    phones = {r["word"]: r for r in json.loads((WORK / "phones.json").read_text())}
    screen = {c["id"]: c for c in json.loads((WORK / "screen.cases.json").read_text())}
    passes = defaultdict(list)
    for r in rows:
        passes[r["id"]].append(r["verdict"] == "pass")
    cases = []
    for cid, v in passes.items():
        w = cid[4:]
        if all(v) or w in NOT_MISREAD:
            continue
        base = screen[cid]
        cands = HAND_CANDIDATES.get(w, []) + respell_variants(phones[w]["phones"])
        for cand in dict.fromkeys(cands):
            text = CARRIER.format(cand)
            cases.append(dict(base, id=f"{cid}~{cand}", text=pronounce.RAW_PREFIX + text))
        cases.append(dict(base, id=f"{cid}~{base['target']}"))  # plain spelling, same takes
    (WORK / "respell.cases.json").write_text(json.dumps(cases, indent=0, ensure_ascii=False))
    print(f"{len(cases)} respelling cases for {len({c['id'].split('~')[0] for c in cases})} words")


def pick(min_wins=3):
    rows = json.loads((EVAL_OUT / "oov_respell" / "scores.json").read_text())
    by = defaultdict(list)
    for r in rows:
        if "margin" in r:
            by[r["id"]].append(r)
    groups = defaultdict(list)
    for cid, rs in by.items():
        base, cand = cid.split("~", 1)
        wins = sum(r["verdict"] == "pass" for r in rs)
        groups[base].append((wins, len(rs), sum(r["margin"] for r in rs) / len(rs), cand,
                             rs[0].get("heard", "")))
    cache = json.loads(RESPELL.read_text()) if RESPELL.exists() else {}
    chosen = {}
    for base, cs in sorted(groups.items()):
        target = base[4:]
        plain = [c for c in cs if c[3].lower() == target]
        pw = plain[0][0] if plain else 0
        cs.sort(key=lambda c: (-c[0], -c[2]))
        best = cs[0]
        ok = best[3].lower() != target and best[0] >= min_wins and best[0] > pw
        print(f"{target:16} plain {pw}/{plain[0][1] if plain else '?'}  best {best[3]!r} "
              f"{best[0]}/{best[1]} margin {best[2]:.1f} {'KEEP' if ok else 'skip'}")
        if ok:
            chosen[target] = best[3]
    cache.update(chosen)
    RESPELL.write_text(json.dumps(dict(sorted(cache.items())), indent=0, ensure_ascii=False) + "\n")
    print(f"{len(chosen)} respellings added; {len(cache)} in {RESPELL}")


def audit():
    """Each built-in LEXICON entry in a carrier phrase, plain and respelled."""
    rows = [l.split("\t") for l in (HERE / "lexicon_expect.tsv").read_text().splitlines()
            if l.strip() and not l.startswith("#")]
    cases = []
    for w, ph in rows:
        ph = ph.strip()
        if ph == "L":
            ph = " ".join(LETTERS[c] for c in w.upper())
        form = w.upper() if ph == " ".join(LETTERS[c] for c in w.upper()) else w
        plain = CARRIER.format(form)
        base = {"type": "lexicon", "target": form, "score_text": plain, "expect": ph,
                "wrong": wrong_readings(ph)}
        cases.append(dict(base, id=f"lex-{w}~plain", text=pronounce.RAW_PREFIX + plain))
        cases.append(dict(base, id=f"lex-{w}~lexicon", text=pronounce.RAW_PREFIX + pronounce.apply(plain)))
    (WORK / "audit.cases.json").write_text(json.dumps(cases, indent=0, ensure_ascii=False))
    print(f"{len(cases)} audit cases")


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "cands":
        cands()
    elif cmd == "respell":
        respell()
    elif cmd == "pick":
        pick(int(sys.argv[2]) if len(sys.argv) > 2 else 3)
    elif cmd == "audit":
        audit()
    elif cmd == "spell":
        for ph in sys.argv[2:]:
            print(ph, "->", respell_variants(ph))
