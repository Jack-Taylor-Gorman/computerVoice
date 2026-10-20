"""Offline training for pronounce.py. Produces plain files the runtime reads
with the standard library alone:

  words.txt.gz     word<TAB>zipf*10, "+" when the word is in CMUdict
                   (wordfreq's top 100k English words plus CMUdict)
  heteronyms.json  one logistic-regression model per heteronym, weights as
                   {feature: weight}, features from pronounce.features()

Training data for the heteronyms:
  - Google's WikipediaHomographData (Gorman, Mazovetskiy & Nikolaev 2018):
    sentences labelled with the pronunciation of each homograph.
  - The Brown corpus (NLTK), labelled through its part-of-speech tags for
    the heteronyms where the tag decides the pronunciation ("read"/VBD ->
    read_past, "live"/JJ -> live_adj, ...).
  - domain_examples.tsv: a few hand-labelled sentences in the style of
    Majel's summaries (none of them are eval phrases).

Run with the system python3.10 (scikit-learn) and the training libraries:
  PYTHONPATH=~/.cache/pronounce_train_libs /usr/bin/python3.10 pronounce_model/train.py [words|heteronyms|all]
"""
import csv
import gzip
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import pronounce  # noqa: E402  (the runtime: features() must match exactly)

WIKI = Path.home() / ".cache" / "pronounce_data" / "WikipediaHomographData" / "data"
DOMAIN = HERE / "domain_examples.tsv"

HETERONYMS = sorted({k.rsplit("_", 1)[0] if "_" in k else k for k in pronounce.HETERONYM_RESPELL})

# Brown tag -> wordid, for heteronyms whose part of speech fixes the sound.
# Tags are matched on their prefix after dropping Brown's "-HL"/"-TL" suffixes.
BROWN_RULES = {
    "live": [("JJ", "live_adj"), ("RB", "live_adj"), ("VB", "live_vrb")],
    "lives": [("NNS", "lives_nou"), ("VBZ", "lives_vrb")],
    "read": [("VBD", "read_past"), ("VBN", "read_past"), ("VB", "read_present")],
    "close": [("JJ", "close_adj-nou"), ("RB", "close_adj-nou"), ("NN", "close_adj-nou"), ("VB", "close_vrb")],
    "use": [("NN", "use_nou"), ("VB", "use_vrb")],
    "uses": [("NNS", "uses_nou"), ("VBZ", "uses_vrb")],
    "record": [("NN", "record_nou"), ("JJ", "record_nou"), ("VB", "record_vrb")],
    "present": [("JJ", "present_adj-nou"), ("NN", "present_adj-nou"), ("RB", "present_adj-nou"), ("VB", "present_vrb")],
    "object": [("NN", "object_nou"), ("VB", "object_vrb")],
    "content": [("NN", "content_nou"), ("JJ", "content_adj-nou-vrb"), ("VB", "content_adj-nou-vrb")],
    "minute": [("JJ", "minute_adj"), ("NN", "minute")],
    "desert": [("NN", "desert_nou"), ("JJ", "desert_nou"), ("VB", "desert_vrb")],
    "produce": [("NN", "produce_nou"), ("VB", "produce_vrb")],
    "refuse": [("NN", "refuse_nou"), ("VB", "refuse_vrb")],
    "resume": [("NN", "resume_nou"), ("VB", "resume_vrb")],
    "wind": [("NN", "wind_nou"), ("VB", "wind_vrb")],
    "winds": [("NNS", "winds_nou"), ("VBZ", "winds_vrb")],
    "wound": [("NN", "wound_nou-vrb"), ("VBD", "wound_vrb"), ("VBN", "wound_vrb")],
    "tear": [("VB", "tear_vrb")],
    "lead": [("VB", "lead_nou-vrb")],
    "row": [("VB", "row_1")],
}


def build_words():
    from nltk.corpus import cmudict
    from wordfreq import top_n_list, zipf_frequency
    cmu = {w for w in cmudict.words() if w.isalpha()}
    table = {}
    for w in top_n_list("en", 100000):
        if w.isalpha() and w.isascii() and len(w) <= 24:
            table[w] = zipf_frequency(w, "en")
    for w in cmu:
        if w not in table and len(w) <= 24:
            table[w] = zipf_frequency(w, "en")
    with gzip.open(HERE / "words.txt.gz", "wt", encoding="utf-8", compresslevel=9) as f:
        for w in sorted(table, key=lambda k: -table[k]):
            f.write(f"{w}\t{round(table[w] * 10)}{'+' if w in cmu else ''}\n")
    print(f"words.txt.gz: {len(table)} words, {(HERE / 'words.txt.gz').stat().st_size // 1024} KB")


def _tokens(sentence):
    return pronounce._CTX.findall(sentence)


def wiki_examples(word, split):
    rows = []
    path = WIKI / split / f"{word}.tsv"
    if not path.exists():
        return rows
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            s, a = r["sentence"], int(r["start"])
            toks = [(m.start(), m.group(0)) for m in pronounce._CTX.finditer(s)]
            idx = [k for k, (st, t) in enumerate(toks) if st == a]
            if idx:
                rows.append(([t for _, t in toks], idx[0], r["wordid"]))
    return rows


def brown_examples():
    from nltk.corpus import brown
    out = defaultdict(list)
    for sent in brown.tagged_sents():
        words = [w for w, _ in sent]
        for i, (w, tag) in enumerate(sent):
            rules = BROWN_RULES.get(w.lower())
            if not rules:
                continue
            tag = re.sub(r"-(HL|TL|NC)$", "", tag).split("-")[0].split("+")[0].replace("*", "")
            for prefix, label in rules:
                # rules are ordered, so "read"/VBD is claimed before the bare VB rule
                if tag.startswith(prefix):
                    toks = _tokens(" ".join(words))
                    j = _find(toks, words, i)
                    if j is not None:
                        out[w.lower()].append((toks, j, label))
                    break
    return out


def _find(toks, words, i):
    """Index in pronounce's tokenisation of words[i] (re-tokenised Brown)."""
    before = len(_tokens(" ".join(words[:i])))
    if before < len(toks) and toks[before].lower() == words[i].lower():
        return before
    return None


def domain_examples():
    out = defaultdict(list)
    if not DOMAIN.exists():
        return out
    for line in DOMAIN.read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        label, sentence = line.split("\t", 1)
        word = label.rsplit("_", 1)[0] if label not in ("minute",) else "minute"
        toks = _tokens(sentence)
        idx = [k for k, t in enumerate(toks) if t.lower() == word]
        if idx:
            out[word].append((toks, idx[0], label))
    return out


# Brown tag prefix -> coarse class used by pronounce.features().
COARSE = [("NP", "N"), ("NN", "N"), ("VB", "V"), ("HV", "V"), ("BE", "V"), ("DO", "V"), ("MD", "M"),
          ("JJ", "J"), ("RB", "R"), ("QL", "R"), ("AT", "D"), ("DT", "D"), ("AP", "D"), ("PP$", "D"),
          ("CD", "D"), ("PP", "O"), ("WP", "O"), ("IN", "P"), ("TO", "T"), ("CC", "C"), ("CS", "C"),
          ("WDT", "W"), ("WRB", "W")]


def build_pos_classes():
    """Each word's majority coarse class in Brown (words seen 3+ times)."""
    from nltk.corpus import brown
    counts = defaultdict(Counter)
    for w, tag in brown.tagged_words():
        tag = re.sub(r"-(HL|TL|NC)$", "", tag).split("-")[0].split("+")[0].replace("*", "")
        for prefix, cls in COARSE:
            if tag.startswith(prefix):
                counts[w.lower()][cls] += 1
                break
    out = defaultdict(list)
    for w, c in counts.items():
        cls, n = c.most_common(1)[0]
        if sum(c.values()) >= 3 and pronounce._CTX.fullmatch(w):
            out[cls].append(w)
    return {cls: " ".join(sorted(ws)) for cls, ws in out.items()}


def build_heteronyms():
    import numpy as np
    from sklearn.feature_extraction import DictVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import StratifiedKFold, cross_val_predict

    pos = build_pos_classes()
    pronounce._pos_classes = {w: cls for cls, ws in pos.items() for w in ws.split()}
    brown = brown_examples()
    domain = domain_examples()
    models, report = {}, {}
    for word in HETERONYMS:
        train = wiki_examples(word, "train") + brown.get(word, []) + domain.get(word, []) * 3
        held = wiki_examples(word, "eval")
        labels = sorted({lab for _, _, lab in train + held})
        counts = Counter(lab for _, _, lab in train + held)
        if len(labels) < 2 or min(counts.values()) < 3:
            report[word] = {"skipped": dict(counts)}
            continue
        vec = DictVectorizer()
        Xtr = vec.fit_transform([dict.fromkeys(pronounce.features(t, i), 1) for t, i, _ in train])
        ytr = [lab for _, _, lab in train]
        clf = LogisticRegression(max_iter=2000, C=2.0, class_weight="balanced")
        r = {"n": dict(counts)}
        if held and len(set(ytr)) > 1:
            clf.fit(Xtr, ytr)
            Xh = vec.transform([dict.fromkeys(pronounce.features(t, i), 1) for t, i, _ in held])
            pred = clf.predict(Xh)
            r["wiki_eval_acc"] = round(float(np.mean(pred == np.array([lab for _, _, lab in held]))), 3)
            r["wiki_eval_n"] = len(held)
        # final model on everything
        alls = train + held
        X = vec.fit_transform([dict.fromkeys(pronounce.features(t, i), 1) for t, i, _ in alls])
        y = [lab for _, _, lab in alls]
        if min(Counter(y).values()) >= 5:
            folds = StratifiedKFold(n_splits=5, shuffle=True, random_state=0)
            pred = cross_val_predict(LogisticRegression(max_iter=2000, C=2.0, class_weight="balanced"), X, y, cv=folds)
            r["cv_acc"] = round(float(np.mean(np.array(pred) == np.array(y))), 3)
        clf.fit(X, y)
        names = vec.get_feature_names_out()
        coef = []
        for row in clf.coef_:
            coef.append({names[k]: round(float(v), 3) for k, v in enumerate(row) if abs(v) >= 0.02})
        models[word] = {"labels": [str(c) for c in clf.classes_], "coef": coef,
                        "b": [round(float(v), 4) for v in clf.intercept_]}
        report[word] = r
    models["_pos"] = pos
    (HERE / "heteronyms.json").write_text(json.dumps(models, separators=(",", ":"), sort_keys=True))
    (HERE / "heteronyms_report.json").write_text(json.dumps(report, indent=1, sort_keys=True))
    print(json.dumps(report, indent=1, sort_keys=True))
    print(f"heteronyms.json: {len(models) - 1} models, {(HERE / 'heteronyms.json').stat().st_size // 1024} KB")


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    if what in ("words", "all"):
        build_words()
    if what in ("heteronyms", "all"):
        build_heteronyms()
