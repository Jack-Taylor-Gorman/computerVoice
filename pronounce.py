"""Pronunciation fixes applied to everything Majel says.

F5-TTS reads letters, not phonemes, so it guesses vowels from spelling and
gets uncommon or technical words wrong ("repo" with a short e, "sudo" as
"suh-doh"), and it treats every "." as a full stop, so "buddy.py" comes out
as "buddy. Pie." This module rewrites the text before synthesis:

1. Dots inside names are spoken: "buddy.py" -> "buddy dot pie",
   "github.com" -> "git hub dot com", ".gitignore" -> "dot git ignore".
   Sentence-ending full stops and abbreviations like "e.g." are left alone;
   version numbers ("1.2.48") are handled earlier by computerize.py.
2. Joined words are split so each part gets its own vowels: camelCase,
   snake_case and kebab-case always, and run-together words ("goswim" ->
   "go swim") when a unigram word-frequency model finds the split far more
   probable than the unknown whole (pronounce_model/words.txt.gz).
3. Heteronyms ("live", "read", "lead", ...) are respelled to the reading
   their context calls for, using small per-word logistic-regression models
   trained offline by pronounce_model/train.py and stored as plain JSON
   (pronounce_model/heteronyms.json).
4. Words F5 mispronounces are respelled so their vowels come out right,
   using plain-English cues: "ee" long E, "eh" short E, "ay" long A,
   "eye" long I, "oh" long O, "oo" as in moon. The hand-written table below
   comes first, then pronounce_model/respell.json (generated offline).

Add your own respellings to ~/.majel_pronunciations.json as
{"word": "respelling"}; they take precedence over the built-in table.
Applied by computerize.py (summaries) and f5_worker.py (everything else,
e.g. buddy's tab readouts). Idempotent, so applying it twice is harmless.
Needs only the standard library; the models load lazily on first use.
"""
import gzip
import json
import math
import re
from pathlib import Path

USER_LEXICON = Path.home() / ".majel_pronunciations.json"
MODEL_DIR = Path(__file__).resolve().parent / "pronounce_model"
WORDS_FILE = MODEL_DIR / "words.txt.gz"
HETERONYM_FILE = MODEL_DIR / "heteronyms.json"
RESPELL_FILE = MODEL_DIR / "respell.json"

# Whole words, matched case-insensitively.
LEXICON = {
    # long E
    "repo": "reepo", "repos": "reepos", "regex": "rej-ex", "regexes": "rej-exes",
    "enum": "ee-num", "enums": "ee-nums", "readme": "read me", "env": "en-vee", "venv": "vee-env",
    "evdev": "ee-vee-dev", "eval": "ee-val", "deque": "deck",
    # long A
    "async": "ace sink", "ascii": "ass-key", "api": "ay pee eye", "apis": "ay pee eyes",
    "jpeg": "jay-peg", "jpg": "jay-peg", "png": "pee en gee",
    "yaml": "yam-ul", "yml": "yam-ul", "macos": "mac oh ess", "ios": "eye oh ess",
    # long I
    "py": "pie", "pyside": "pie-side", "pyside6": "pie-side six", "pytest": "pie-test",
    "pypi": "pie-pee-eye", "numpy": "num-pie", "scipy": "sigh-pie",
    "gui": "gooey", "ui": "you eye", "cli": "see el eye", "ai": "ay eye", "io": "eye oh",
    "nginx": "engine ex", "systemd": "system dee", "udev": "you-dev",
    # long O / oo
    "json": "jay-son", "toml": "tom-ul", "npm": "en pee em", "pnpm": "pee en pee em",
    # consonants, file types, letters
    "cache": "cash", "cached": "cashed", "caches": "cashes", "caching": "cashing",
    "github": "git hub", "gitlab": "git lab", "gitignore": "git ignore",
    "stdin": "standard in", "stdout": "standard out", "stderr": "standard error",
    "sql": "sequel", "sqlite": "sequel-ite", "url": "you are el", "urls": "you are els",
    "usb": "you ess bee", "hid": "aitch eye dee", "hidraw": "aitch eye dee raw",
    "ssh": "ess ess aitch", "xdotool": "ex-doh-tool", "pipewire": "pipe-wire",
    "wav": "wave", "mp3": "em pee three", "mp4": "em pee four", "svg": "ess vee gee",
    "md": "em dee", "sh": "ess aitch", "js": "jay ess", "ts": "tee ess", "tsx": "tee ess ex",
    "jsx": "jay ess ex", "css": "see ess ess", "html": "aitch tee em el", "txt": "text",
    "csv": "see ess vee", "pdf": "pee dee eff", "exe": "ee ex ee", "db": "dee bee",
    "cfg": "config", "ini": "in-ee", "rs": "are ess", "cpp": "see plus plus", "gz": "gee zee",
    "uk": "you kay", "f5": "eff five", "tts": "tee tee ess", "audeze": "aw-dee-zee",
    "hq": "aitch cue", "id": "eye dee", "ids": "eye dees",
    # chosen by ear with pronounce_model/respell_search.py (3/3 takes right)
    "sudo": "soo-doo", "majel": "mayjel", "vite": "veet", "tkinter": "tee kinter",
}

# Heteronym readings -> respelling. None leaves the word as written: that is
# the reading F5 already gives from the plain spelling (measured by
# pronounce_model/eval.py), so a misclassification there costs nothing.
HETERONYM_RESPELL = {
    "live_adj": "lighv", "live_vrb": "liv",
    "lives_nou": "lyves", "lives_vrb": None,
    "read_past": None, "read_present": "reed",
    "lead_nou": "led", "lead_nou-vrb": None,
    "tear_nou": "teer", "tear_vrb": None,
    "wind_nou": None, "wind_vrb": "whined",
    "winds_nou": None, "winds_vrb": None,
    "close_adj-nou": None, "close_vrb": None,
    "use_nou": None, "use_vrb": None,
    "uses_nou": None, "uses_vrb": None,
    "record_nou": None, "record_vrb": None,
    "present_adj-nou": None, "present_vrb": None,
    "object_nou": None, "object_vrb": "ub-ject",
    "content_nou": None, "content_adj-nou-vrb": "kun-tent",
    "minute": None, "minute_adj": "my-noot",
    "bow_nou-knot": None, "bow_nou-ship": "bough",
    "wound_nou-vrb": None, "wound_vrb": "wownd",
    "row_1": None, "row_2": "rau",
    "desert_nou": None, "desert_vrb": "dih-zert",
    "produce_nou": "proh-dooce", "produce_vrb": None,
    "refuse_nou": "reff-yooss", "refuse_vrb": None,
    "resume_nou": "rezzoo-may", "resume_vrb": None,
}
HETERONYM_WORDS = {k.rsplit("_", 1)[0] if "_" in k else k for k in HETERONYM_RESPELL}

# Closed-class context words: these generalise from the training corpora to
# phrasings they rarely contain ("please read", "will record").
BASE_FORM_CUES = {"will", "would", "can", "could", "should", "must", "may", "might", "shall", "to",
                  "please", "let", "let's", "lets", "don't", "dont", "do", "does", "did", "didn't",
                  "cannot", "can't", "won't", "i'll", "we'll", "you'll", "they'll", "i'd", "we'd"}
PERFECT_CUES = {"have", "has", "had", "i've", "we've", "you've", "they've", "having", "been",
                "was", "were", "is", "are", "be", "being", "already", "just"}
DETERMINERS = {"a", "an", "the", "this", "that", "these", "those", "my", "your", "his", "her",
               "its", "our", "their", "no", "every", "each", "some", "any"}
PRONOUNS = {"i", "you", "he", "she", "it", "we", "they", "claude", "jack"}

ABBREVIATIONS = {"etc", "vs", "mr", "mrs", "ms", "dr", "st", "no", "approx", "inc", "ltd"}

# "word.word" or ".word": a dot with a letter or underscore right after it,
# inside a token (so "done. Next" and "v1.2" are untouched).
_DOTTED = re.compile(r"(?<![\w.])(\.?[A-Za-z_][\w\-]*(?:\.[A-Za-z_][\w\-]*)+|\.[A-Za-z_][\w\-]*)")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]*")
# An identifier-ish token: letters/digits joined by "_" or "-".
_IDENT = re.compile(r"(?<![\w'])[A-Za-z][A-Za-z0-9]*(?:[_-]+[A-Za-z0-9]+)*(?![\w'])")
# "PDFs" and "GPUs" are plural initialisms, not "PD" + "Fs".
_CAMEL = re.compile(r"[A-Z]{2,}s(?![a-z])|[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+")
_CAPS_HEAD = re.compile(r"[A-Z]{2}[a-z]{2,}")  # "GOswim"; "HTTPServer" keeps camel rules
# Context tokens for the heteronym models: words and clause punctuation.
_CTX = re.compile(r"[A-Za-z0-9']+|[.,;:!?]")

# Splitting: a part costs -ln p(word); a split must beat the unknown whole by
# this many nats (e^3, about 20 times more probable) plus a per-part penalty.
SPLIT_MARGIN = 3.0
PART_PENALTY = 1.0
MIN_PART_ZIPF = 3.0   # parts must be ordinary words (Zipf 3 = once per million words)
KNOWN_ZIPF = 3.0      # a whole token this common is a word in its own right
MAX_PART = 18
LN10 = math.log(10)
# -ln p of a token wordfreq has never seen: rarer than once per billion
# words, and less likely the longer it is.
UNKNOWN_COST = 9 * LN10
UNKNOWN_PER_CHAR = 1.0
# Two-letter parts are mostly abbreviations in wordfreq ("tr", "st", "pp");
# only these real words may be split off.
# Abbreviations that are far commoner inside identifiers than in prose
# ("nodeid", "userapi"), so they are costed as if they were common words.
CODE_PARTS = {"id", "ids", "api", "url", "db", "ui", "io", "js", "ts", "py", "env", "cfg", "src",
              "lib", "dir", "tmp", "img", "btn", "msg", "num", "str", "idx", "obj", "args", "cli",
              "gui", "json", "yaml", "html", "css", "sql", "dev", "prod", "auth", "init", "config"}
CODE_PART_ZIPF = 5.0
TWO_LETTER = {"go", "to", "do", "no", "so", "up", "in", "on", "at", "is", "it", "be", "by", "me",
              "my", "we", "he", "us", "of", "or", "an", "as", "if", "am", "oh", "ok", "hi", "id",
              "ai", "ui", "os", "re", "py"}

RAW_PREFIX = "\x00raw\x00"  # eval.py's escape hatch: speak the text untouched

_words = None
_het_models = None
_pos_classes = None  # word -> coarse Brown part of speech, for context features
_respell = None


def _load_words():
    """word -> "zipf*10" with a trailing "+" for CMUdict words (parsed on lookup)."""
    global _words
    if _words is None:
        try:
            with gzip.open(WORDS_FILE, "rt", encoding="utf-8") as f:
                _words = dict(line.split("\t") for line in f.read().splitlines())
        except (OSError, ValueError):
            _words = {}
    return _words


def _zipf(words, w):
    v = words.get(w)
    return (int(v.rstrip("+")) / 10, v.endswith("+")) if v else (0.0, False)


def _load_heteronyms():
    global _het_models
    if _het_models is None:
        try:
            _het_models = json.loads(HETERONYM_FILE.read_text())
        except (OSError, ValueError):
            _het_models = {}
    return _het_models


def _pos_of(word):
    global _pos_classes
    if _pos_classes is None:
        _pos_classes = {}
        for cls, words in _load_heteronyms().get("_pos", {}).items():
            _pos_classes.update(dict.fromkeys(words.split(), cls))
    return _pos_classes.get(word, "?")


def _lexicon():
    global _respell
    if _respell is None:
        try:
            _respell = json.loads(RESPELL_FILE.read_text())
        except (OSError, ValueError):
            _respell = {}
    table = dict(_respell)
    table.update(LEXICON)
    try:
        table.update({k.lower(): v for k, v in json.loads(USER_LEXICON.read_text()).items()})
    except (OSError, ValueError, AttributeError):
        pass
    return table


def _respelled_tokens(table):
    """Every word this module can emit, so a second pass leaves them alone."""
    out = set()
    for v in list(table.values()) + [v for v in HETERONYM_RESPELL.values() if v]:
        for part in re.split(r"[\s-]+", v.lower()):
            out.add(part)
        out.add(v.lower())
    return out


def _speak_dotted(m):
    token = m.group(1)
    parts = token.split(".")
    named = [p for p in parts if p]
    # e.g. / i.e. / U.S. and "etc."-style abbreviations stay as written.
    if all(len(p) == 1 for p in named) or (len(named) == 1 and named[0].lower() in ABBREVIATIONS):
        return token
    spoken = " dot ".join(p.replace("_", " ") for p in parts)
    return spoken.strip()


def split_word(word, words=None):
    """wordninja-style unigram segmentation of a lower-case token, or None.

    Only splits a token that is not itself a known word, and only when the
    best split is much more probable than leaving it whole.
    """
    words = _load_words() if words is None else words
    w = word.lower()
    z, in_cmu = _zipf(words, w)
    if in_cmu or z >= KNOWN_ZIPF or len(w) < 5 or not w.isalpha():
        return None
    n = len(w)
    best = [0.0] + [math.inf] * n
    back = [0] * (n + 1)
    for i in range(1, n + 1):
        for j in range(max(0, i - MAX_PART), i):
            part = w[j:i]
            pz = _zipf(words, part)[0]
            if part in CODE_PARTS:
                pz = max(pz, CODE_PART_ZIPF)
            elif pz < MIN_PART_ZIPF or len(part) == 1 or (len(part) == 2 and part not in TWO_LETTER):
                continue
            c = best[j] + (9 - pz) * LN10 + PART_PENALTY
            if c < best[i]:
                best[i], back[i] = c, j
    if best[n] == math.inf:
        return None
    parts, i = [], n
    while i > 0:
        parts.append(w[back[i]:i])
        i = back[i]
    parts.reverse()
    whole = (9 - z) * LN10 if z > 0 else UNKNOWN_COST + UNKNOWN_PER_CHAR * n
    if len(parts) < 2 or best[n] + SPLIT_MARGIN > whole:
        return None
    return parts


def _split_ident(m, table, protected):
    token = m.group(0)
    if token.lower() in table or token.lower() in protected:
        return token
    out = []
    # Underscores become spaces (F5 has no symbol for them); hyphens already
    # read as a word break, so they stay and only the pieces between change.
    for piece in re.split(r"(_+|-+)", token):
        if not piece or piece[0] == "_":
            continue
        if piece[0] == "-":
            out.append(piece)
            continue
        sub = _CAMEL.findall(piece) if re.search(r"[a-z][A-Z]|[A-Z]{2}[a-z]", piece) else [piece]
        if _CAPS_HEAD.fullmatch(piece) and len(sub) > 1:
            # Camel rules would cut "GOswim" into "G" + "Oswim"; a word split
            # of the whole ("go swim") is the likelier reading when there is one.
            whole = split_word(piece.lower())
            if whole:
                sub = whole
        for k, s in enumerate(sub):
            if k and not s.isupper():
                s = s.lower()  # "isLive" -> "is live", but "getAPIKey" keeps "API"
            low = s.lower()
            if s.isalpha() and low not in table and low not in protected:
                parts = split_word(low)
                if parts:
                    if s[0].isupper():
                        parts[0] = parts[0].capitalize()
                    out.extend(parts)
                    continue
            out.append(s)
    return re.sub(r" ?(-+) ?", r"\1", " ".join(out))


def features(tokens, i):
    """Context features for the heteronym at tokens[i] (shared with train.py)."""
    low = [t.lower() for t in tokens]
    get = lambda k: low[k] if 0 <= k < len(low) else ("<s>" if k < 0 else "</s>")
    f = {"bias", "w-1=" + get(i - 1), "w-2=" + get(i - 2), "w+1=" + get(i + 1), "w+2=" + get(i + 2),
         "w-1,w+1=" + get(i - 1) + "|" + get(i + 1), "w-2,w-1=" + get(i - 2) + "|" + get(i - 1),
         "w+1,w+2=" + get(i + 1) + "|" + get(i + 2),
         "s-1=" + get(i - 1)[-2:], "s+1=" + get(i + 1)[-2:]}
    for k in range(max(0, i - 6), min(len(low), i + 7)):
        if k != i and low[k][0].isalnum():
            f.add("bow=" + low[k])
    if tokens[i][0].isupper():
        f.add("cap" if i > 0 and low[i - 1] not in ".!?" else "init")
    for name, k in (("p1", i - 1), ("p2", i - 2), ("n1", i + 1)):
        w = get(k)
        for cls, members in (("base", BASE_FORM_CUES), ("perf", PERFECT_CUES),
                             ("det", DETERMINERS), ("pron", PRONOUNS)):
            if w in members:
                f.add(name + "=" + cls)
        if w in ("<s>", "</s>") or w in ".,;:!?":
            f.add(name + "=edge")
    c = {k: "M" if get(i + k) in BASE_FORM_CUES else _pos_of(get(i + k)) for k in (-2, -1, 1, 2)}
    f.update(("p1c=" + c[-1], "p2c=" + c[-2], "n1c=" + c[1], "n2c=" + c[2],
              "p1c,n1c=" + c[-1] + "|" + c[1], "p2c,p1c=" + c[-2] + "|" + c[-1]))
    return f


def classify(word, tokens, i, model):
    feats = features(tokens, i)
    coef = model["coef"]
    labels = model["labels"]
    if len(labels) == 2:
        z = model["b"][0] + sum(coef[0].get(f, 0.0) for f in feats)
        return labels[1] if z > 0 else labels[0]
    zs = [model["b"][k] + sum(coef[k].get(f, 0.0) for f in feats) for k in range(len(labels))]
    return labels[max(range(len(labels)), key=lambda k: zs[k])]


def _match_case(word, rep):
    if word.isupper() and len(word) > 1:
        return rep.upper()
    return rep.capitalize() if word[0].isupper() else rep


def _respell_heteronyms(text):
    spans = [(m.start(), m.end()) for m in _CTX.finditer(text)]
    tokens = [text[a:b] for a, b in spans]
    if not any(t.lower() in HETERONYM_WORDS for t in tokens):
        return text
    models = _load_heteronyms()
    out, last = [], 0
    for i, (a, b) in enumerate(spans):
        model = models.get(tokens[i].lower())
        if model is None:
            continue
        rep = HETERONYM_RESPELL.get(classify(tokens[i], tokens, i, model))
        if rep:
            out.append(text[last:a])
            out.append(_match_case(tokens[i], rep))
            last = b
    out.append(text[last:])
    return "".join(out)


def respell_word(word, table):
    rep = table.get(word.lower())
    if rep is None or rep.lower() == word.lower():
        return word
    return rep.capitalize() if word[0].isupper() and not word.isupper() else rep


def apply(text):
    if text.startswith(RAW_PREFIX):
        return text[len(RAW_PREFIX):]
    text = _DOTTED.sub(_speak_dotted, text)
    table = _lexicon()
    protected = _respelled_tokens(table)
    text = _IDENT.sub(lambda m: _split_ident(m, table, protected), text)
    text = _respell_heteronyms(text)
    return _WORD.sub(lambda m: respell_word(m.group(0), table), text)


if __name__ == "__main__":
    import sys
    print(apply(" ".join(sys.argv[1:]) or sys.stdin.read()))
