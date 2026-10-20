# Pronunciation model: to do

## Done (2026-10-20, iteration 1)

- [x] Evaluation harness (`pronounce_model/eval.py`): live-socket synthesis,
      wav2vec2 espeak CTC recogniser on CPU, minimal-pair scoring. Tuning set
      of 75 cases and held-out set of 69 cases.
- [x] Baseline measured on both sets.
- [x] Splitting: camelCase, snake_case and per-piece wordninja-style unigram
      segmentation (`pronounce_model/words.txt.gz`, 147k words).
- [x] Heteronyms: 22 logistic-regression models (Wikipedia homograph data +
      Brown, labelled by part of speech, + domain sentences), with closed-class
      and coarse part-of-speech context features, exported to JSON.
- [x] Respelling search (`pronounce_model/respell_search.py`). Lexicon fixes
      for sudo, Majel, vite, tkinter, async and id.
- [x] Live worker restarted on the new module and verified.

## Next

1. [ ] Run a respelling search for "live" (adjective) in longer sentences:
       "lyve" was heard as "lev" once. Candidates: "lyve", "laiv", "lie-v",
       "lyev", "lighve".
2. [ ] Lexicon audit: synthesise every `LEXICON` entry in a carrier phrase
       and check it by ear. "ay-sink" was broken, and others may be too.
       Needs an expected ARPAbet per entry.
3. [ ] Out-of-vocabulary pipeline (not built yet; `respell.json` is not
       written). Mine the words Majel actually says from Claude transcripts.
       Keep those absent from the frequency list. Get phonemes from
       CMUdict or g2p_en, generate respelling candidates from the phonemes,
       and confirm each with a respelling search before caching it in
       `pronounce_model/respell.json`.
4. [ ] Classifier errors on held-out phrases: "Is he content now?",
       "Never desert a friend", "Did anyone object?", "Minute details
       matter", "A row erupted". More labelled data is the likely fix:
       sentence-final verbs after "did/will" and the adjective "minute"
       before a plural noun.
5. [ ] Majority readings left plain are sometimes misread. Record (verb),
       present (verb) and tear (rip) were right in only 1 of 2 held-out
       takes. Search respellings for both readings of these words.
6. [ ] Stability: repeat the held-out run (F5 is stochastic; two takes per
       case is thin) and report the spread.
