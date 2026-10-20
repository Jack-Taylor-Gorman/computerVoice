"""Mine the words Majel is likely to say from Claude Code transcripts.

Reads the assistant's prose in ~/.claude/projects/**/*.jsonl (what the Stop
hook summarises and speaks), counts whole alphabetic words by the number of
sessions they appear in, and writes the ones that are rare or unknown in
pronounce_model/words.txt.gz to /tmp/pronounce_oov/mined.json. Only words
of 3 to 15 letters with no digits or underscores attached are counted, so
identifiers and keys never reach the output. Stdlib only.

  python3 pronounce_model/mine_words.py [MIN_SESSIONS]
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import pronounce  # noqa: E402

WORD = re.compile(r"(?<![\w./\\@-])[A-Za-z]{3,15}(?![\w/\\@]|-\w|\.\w)")
OUT = Path("/tmp/pronounce_oov/mined.json")


def texts(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if '"assistant"' not in line or '"text"' not in line:
                continue
            try:
                msg = json.loads(line).get("message") or {}
            except ValueError:
                continue
            if msg.get("role") != "assistant" or not isinstance(msg.get("content"), list):
                continue
            for part in msg["content"]:
                if part.get("type") == "text":
                    # Code spans and blocks are not prose Majel would read.
                    yield re.sub(r"```.*?```|`[^`]*`", " ", part["text"], flags=re.S)


def main(min_sessions):
    words = pronounce._load_words()
    sessions, total, cased = Counter(), Counter(), Counter()
    for path in Path.home().glob(".claude/projects/*/*.jsonl"):
        seen = set()
        for t in texts(path):
            for w in WORD.findall(t):
                low = w.lower()
                total[low] += 1
                cased[w] += 1
                seen.add(low)
        sessions.update(seen)
    table = pronounce._lexicon()
    out = []
    for w, n in sessions.most_common():
        if n < min_sessions:
            break
        z, in_cmu = pronounce._zipf(words, w)
        if w in table or w in pronounce.HETERONYM_WORDS or z >= 3.5:
            continue
        forms = sorted((c for c in cased if c.lower() == w), key=lambda c: -cased[c])
        out.append({"word": w, "sessions": n, "count": total[w], "zipf": z, "cmu": in_cmu,
                    "forms": forms[:3]})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=0))
    print(f"{len(out)} rare words in >= {min_sessions} sessions -> {OUT}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 3)
