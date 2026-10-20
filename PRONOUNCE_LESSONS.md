# Pronunciation model: lessons

Dated entries, newest first. The heading of each entry is the lesson.

## 2026-10-20 — F5 reads "y" as a short I inside a respelling; use a real word with the right vowel

"Wynd" (for "wind" the verb) was heard as `w ɪ n d` in all three takes, and
"Wynde" fared no better. "Whined" and "Wined" were right in all three. "lyve"
passed every evaluation take but came out as `l ɛ v` once in a longer live
sentence, so it is on the watch list. Respellings built from ordinary English
words with a fixed vowel ("whined", "led", "veet", "rau") are the safest.

## 2026-10-20 — "ay" on its own can be read as "eye"

The existing lexicon's "ay-sink" for "async" was heard as `aɪ s ɪ ŋ k` in most
takes. "ay pee eye" for "API" was fine, so the problem depends on the context.
"ace sink" and "eigh-sink" were best (2 of 3 takes), and "async" is still the
hardest technical word in the set.

## 2026-10-20 — Choose respellings by ear, not by intuition

`pronounce_model/respell_search.py` synthesises each candidate in its phrase
three times and scores it with the recogniser. For 10 of the 12 targets in
the first round, a candidate was right in 3 of 3 takes where the plain
spelling scored 0 of 3. It costs about four minutes per dozen candidates.

## 2026-10-20 — Respell only the readings F5 gets wrong from the plain spelling

F5 already says the majority reading of most heteronyms (read as "red" in
past contexts, close, use, record and present). Leaving those readings as
written (`None` in `HETERONYM_RESPELL`) means a classifier mistake towards
them changes nothing, while the minority readings ("lead" the metal,
"minute" the adjective, "wound" the past tense of wind) get a respelling.

## 2026-10-20 — The expected pronunciation must be fluent speech, and the wrong list must contain the actual failure

ARPAbet AH covers both ʌ and ə, so the scorer now accepts either for an
unstressed AH. The "noemit" case expected "no EE-mit", but "no uh-MIT" is
the normal reading, and F5 already said it. The "async" wrong list lacked
"eye-sink", which is the mistake F5 actually makes, so "eye-sink" was being
scored as a pass. Read the greedy `heard` string next to every verdict.

## 2026-10-20 — Hyphens must survive identifier splitting

Turning every hyphen into a space rewrote the lexicon's own hyphenated
respellings ("soo-doo" became "soo doo"), and it also rewrote ordinary prose.
Now only underscores become spaces, and the pieces between hyphens are split
individually.

## 2026-10-20 — Words that wordfreq has seen stop splitting; don't extend the list below Zipf 2

"readonly" (Zipf 1.53) and "filepath" (1.24) are in wordfreq because people
type them in code. With their own frequency they look like ordinary rare
words and are never split. The shipped list stops near Zipf 1.96 (the top
100,000) plus CMUdict. A never-seen token costs 9 ln 10 plus 1 nat per
character, so a long unknown token loses to two common words.

## 2026-10-20 — Free memory swings between 0.6 and 4 GB here; gate every model load

Other jobs (a Gradle daemon, ffmpeg, LibreOffice conversions) took
MemAvailable from 3.6 GB down to 0.6 GB within minutes. Every training and
recogniser run goes through `/tmp/pronounce_waitmem.sh`, which waits for
3 GB. Even `huggingface_hub.snapshot_download` used 500 MB of RSS.

## 2026-10-20 — Load only the recogniser's feature extractor and vocab.json

`AutoProcessor` for `facebook/wav2vec2-lv-60-espeak-cv-ft` builds a phonemizer
tokenizer, which needs espeak-ng (not installed) and a broken `babel` import.
Scoring only needs `Wav2Vec2FeatureExtractor` plus `vocab.json`. Set
`USE_TF=0` and `TRANSFORMERS_NO_TF=1`, or transformers imports TensorFlow.

## 2026-10-20 — The worker loads pronounce.py once; restart it by PID to verify a change

`f5_worker.py` imports `pronounce` at start-up, so an edited module is not
live until the worker restarts. Killing the worker by PID is safe: the
`f5_daemon.py` front respawns it on the next request, which takes about
11 s cold. Until the worker restarts, the eval's "raw" condition is the old
module's output, not truly raw text. After the restart, raw needs
`pronounce.RAW_PREFIX`.

## 2026-10-20 — Name module globals and functions differently

A module-level `_heteronyms = None` was shadowed by `def _heteronyms(...)`,
so the lazy loader returned the function. Caught in a scratch copy, before
it reached the live module.
