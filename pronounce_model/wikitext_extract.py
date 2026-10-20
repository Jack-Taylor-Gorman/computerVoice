"""Pull sentences containing a heteronym out of WikiText-103 (raw), for
train.py to label by part of speech. Needs pyarrow (the live venv has it;
nothing is imported from the corpus, it is only read as text).

  venv/bin/python -I pronounce_model/wikitext_extract.py
Reads ~/.cache/pronounce_data/wikitext/wikitext-103-raw-v1/train-*.parquet
and writes ~/.cache/pronounce_data/wikitext_sents.tsv (word<TAB>sentence).
"""
import re
from collections import Counter
from pathlib import Path

import pyarrow.parquet as pq

DATA = Path.home() / ".cache" / "pronounce_data"
WORDS = ["bow", "close", "content", "desert", "lead", "live", "lives", "minute", "object", "present",
         "produce", "read", "record", "refuse", "resume", "row", "tear", "use", "uses", "wind",
         "winds", "wound"]
CAP = 40000  # sentences per word
FIND = re.compile(r"\b(%s)\b" % "|".join(WORDS), re.I)
SPLIT = re.compile(r"(?<=[.!?]) (?=[A-Z\"])")


def clean(line):
    line = re.sub(r" @(.)@ ", r"\1", line)
    line = re.sub(r" ([.,;:!?)'])", r"\1", line)
    return re.sub(r"\( ", "(", line).strip()


def main():
    counts = Counter()
    out = open(DATA / "wikitext_sents.tsv", "w", encoding="utf-8")
    for path in sorted((DATA / "wikitext" / "wikitext-103-raw-v1").glob("train-*.parquet")):
        for batch in pq.ParquetFile(path).iter_batches(batch_size=20000, columns=["text"]):
            for para in batch.column(0).to_pylist():
                if not para or para.startswith(" =") or not FIND.search(para):
                    continue
                for s in SPLIT.split(clean(para)):
                    words = {m.group(1).lower() for m in FIND.finditer(s)}
                    n = len(s.split())
                    if not words or n < 5 or n > 45 or not s.isascii():
                        continue
                    for w in words:
                        if counts[w] < CAP:
                            counts[w] += 1
                            out.write(f"{w}\t{s}\n")
    out.close()
    print(dict(counts))


if __name__ == "__main__":
    main()
