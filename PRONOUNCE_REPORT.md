# Pronunciation model: report

## Status (2026-10-20, iteration 1)

`pronounce.py` now splits merged identifiers and respells heteronyms by
context. It also respells technical words, using respellings chosen by ear.
The checks were all measured on Majel's own audio:

| Test set | Before (production `pronounce.py` at bc4e50d) | After | 
|---|---|---|
| Tuning set, 75 phrases × 2 takes | 103/150 (69%) | 139/150 (93%) |
| Held-out set, 69 phrases × 2 takes, never used for tuning | 96/138 (70%) | 125/138 (91%) |

By type, on the held-out set, heteronyms went from 50/82 to 69/82,
technical words from 18/28 to 28/28, and merged words stayed at 28/28.
F5 already said this set's merged words correctly. On the tuning set, the
merged words Jack reported do go wrong without splitting: "goswim" was heard
as `ɡ ɑː s w ɪ m` in every baseline take and as `ɡ oʊ s w ɪ m` after.

`pronounce.apply()` takes about 0.2 ms for a typical sentence. On the first
call in a process it also loads the word list and the heteronym models,
which takes about 55 ms. It needs only the standard library and two JSON or
text files, and no torch or nltk. The live worker was restarted on the new
module and verified: a real request returned `{"ok": true}` and a 5.2 s WAV,
which the recogniser decoded as the intended sentence.

### Caveats

- Two takes per phrase is a small sample, and F5 is stochastic. The same
  text can be right in one take and wrong in the next ("live" as a verb,
  "record" as a verb, and the plain "tear").
- The tuning set was used to choose respellings and to add domain training
  sentences, so its 93% is optimistic. The held-out 91% is the honest
  figure.
- The scorer still fails "noemit", although the audio (`n oʊ ɐ m ɪ t`, "no
  uh-MIT") is right. The expected reading uses an unstressed IH, and the
  scorer does not yet accept a schwa for it. "buddy_voice" failed one take
  by a margin of 0.23, while its audio says "buddy voice".
- The out-of-vocabulary pipeline (grapheme-to-phoneme into a respelling
  cache) is not built yet. Technical words are handled by the hand-written
  lexicon plus respellings chosen by the respelling search.

## How it works

1. **Dots in names** are spoken, as before ("buddy.py" → "buddy dot pie").
2. **Splitting.** camelCase and snake_case are always split, and hyphens are
   kept. Each remaining piece goes through a wordninja-style unigram split.
   That only happens when the token is neither in CMUdict nor common (Zipf
   ≥ 3), and the best split must beat the whole token by e³ (about 20 times
   more probable). A token wordfreq has never seen costs 9 ln 10 + 1 nat per
   character, and parts must be common words. Two-letter parts must come
   from a short list of real words, and code abbreviations ("id", "api",
   "dir") are costed as common words. The list is
   `pronounce_model/words.txt.gz`: wordfreq's top 100,000 English words
   plus CMUdict, 147,285 entries in 658 KB.
3. **Heteronyms.** There is one logistic-regression model per word, for 22
   words, stored as `{feature: weight}` JSON in
   `pronounce_model/heteronyms.json`. The features are the neighbouring
   words and word pairs, a ±6-word bag, and suffixes. They also include
   closed-class cues (modals and "please" before the word, "have" and "was"
   before it, determiners, pronouns) and each neighbour's coarse part of
   speech in Brown. Training data comes from Google's
   WikipediaHomographData (sentences labelled with the pronunciation), the
   Brown corpus (labelled through its part-of-speech tags where the tag
   decides the sound) and 135 hand-written sentences in the style of
   Majel's summaries. Only the readings F5 gets wrong from the plain
   spelling are respelled ("lyve", "liv", "reed", "led", "whined", "my-noot",
   "bough", "wownd", "rau", "proh-dooce", ...). The others stay as written,
   so a classifier mistake towards them costs nothing.
4. **Respelling** with the hand-written lexicon, extended with spellings
   chosen by ear: "soo-doo", "mayjel", "veet", "tee kinter", "ace sink",
   "eye dee".

Training: `PYTHONPATH=~/.cache/pronounce_train_libs /usr/bin/python3.10
pronounce_model/train.py`. The training libraries (nltk, g2p_en, wordfreq)
were installed with `pip install --target` because `python3.10 -m venv`
lacks ensurepip on this machine.

## How it is measured

`pronounce_model/eval.py` sends each phrase to the live socket, then runs
`facebook/wav2vec2-lv-60-espeak-cv-ft` (a CTC phoneme recogniser) on CPU.
It scores the audio against the whole phrase spelled with the expected
pronunciation of the target word and with each wrong one, using the CTC
loss, and a take passes when the expected spelling is the most likely. The
greedy decode is stored next to every verdict. Phrase phonemes come from
CMUdict or g2p_en and are converted to espeak IPA, and an unstressed AH may
be ʌ or ə. The recogniser loads only when MemAvailable is above 3 GB, and
it unloads at the end of each run.

`pronounce_model/respell_search.py` uses the same machinery to choose
respellings. It synthesises each candidate three times in its phrase and
ranks the candidates by passes and margin.

### Respelling search results (3 takes each, passes / mean margin)

| Target | Plain spelling | Chosen | Other candidates |
|---|---|---|---|
| sudo | 0/3 | soo-doo 3/3 | sue-doo, sue doo, soodoo all 3/3 |
| Majel | 0/3 | Mayjel 3/3 | May-jel 3/3, Mage-el 3/3, Maijel 1/3 |
| vite | 0/3 | veet 3/3 | veat, veete 3/3 |
| tkinter | 1/3 | tee kinter 3/3 | T kinter, teekinter, tee-kinter 3/3 |
| wind (verb) | 0/2 in baseline | whined 3/3 | wined 3/3, wynd 0/3, wynde 0/3 |
| row (quarrel) | 0/3 | rau 3/3 | rhow, raow 3/3, rouw 0/3 |
| minute (adjective) | 0/3 | my-noot 3/3 | mynoot, my newt 3/3 |
| lead (metal) | 0/3 | led 3/3 | ledd 3/3 |
| produce (noun) | 0/3 | proh-dooce 3/3 | pro-duce, prohdooce 3/3 |
| live (verb) | 3/3 | liv 3/3 | livv 3/3 |
| node id | 0/3 | node eye dee 3/3 | node I D 3/3 |
| async (round 2) | 0/3 | ace sink 2/3 | eigh-sink 2/3, ace-ink 1/3, ay-sink 0/3 |
| API, AI | 3/3 | unchanged | every spelling tried was 3/3 |

### Heteronym models (training data and accuracy)

The cross-validated accuracy uses five folds over all the training
examples. The Wikipedia evaluation accuracy is measured on that dataset's
own small held-out split, trained without it.

| Word | Training examples by reading | Cross-validated accuracy | Wikipedia eval accuracy (n) |
|---|---|---|---|
| bow | bow_nou-knot: 53, bow_nou-ship: 56 | 0.624 | 0.5 (10) |
| close | close_adj-nou: 289, close_vrb: 66 | 0.952 | 1.0 (10) |
| content | content_adj-nou-vrb: 25, content_nou: 146 | 0.965 | 0.9 (10) |
| desert | desert_nou: 124, desert_vrb: 9 | 0.977 | 1.0 (10) |
| lead | lead_nou: 28, lead_nou-vrb: 182 | 0.938 | 0.9 (10) |
| live | live_adj: 111, live_vrb: 211 | 0.95 | 0.9 (10) |
| lives | lives_nou: 88, lives_vrb: 93 | 0.939 | 1.0 (10) |
| minute | minute: 136, minute_adj: 33 | 0.953 | 1.0 (10) |
| object | object_nou: 160, object_vrb: 21 | 0.972 | 1.0 (10) |
| present | present_adj-nou: 432, present_vrb: 57 | 0.975 | 1.0 (10) |
| produce | produce_nou: 23, produce_vrb: 171 | 0.928 | 0.9 (10) |
| read | read_past: 166, read_present: 191 | 0.877 | 0.692 (13) |
| record | record_nou: 222, record_vrb: 32 | 0.969 | 1.0 (9) |
| refuse | refuse_nou: 22, refuse_vrb: 105 | 0.953 | 0.9 (10) |
| resume | resume_nou: 23, resume_vrb: 108 | 0.985 | 0.9 (10) |
| row | row_1: 109, row_2: 9 | 1.0 | 1.0 (10) |
| tear | tear_nou: 39, tear_vrb: 84 | 0.878 | 0.8 (10) |
| use | use_nou: 431, use_vrb: 282 | 0.973 | 1.0 (10) |
| uses | uses_nou: 40, uses_vrb: 119 | 0.969 | 1.0 (10) |
| wind | wind_nou: 156, wind_vrb: 22 | 0.978 | 1.0 (10) |
| winds | winds_nou: 109, winds_vrb: 9 | 0.941 | 1.0 (10) |
| wound | wound_nou-vrb: 88, wound_vrb: 52 | 0.943 | 0.8 (10) |

## Every case, held-out set (before: hbase1, after: hproc1)

Before is the production module at bc4e50d, and after is this iteration's module. Each count is passes out of takes.

| Case | Type | Sent to Majel (after) | Before | After | Heard after (first take) |
|---|---|---|---|---|---|
| h-merge-gohome | merged | Opened the go home script. | 2/2 | 2/2 | `oʊ p ə n d ð ə ɡ oʊ h oʊ m s k ɹ ɪ p t` |
| h-merge-nowplaying | merged | The now playing widget updated. | 2/2 | 2/2 | `ð ə n aʊ p l eɪ ɪ ŋ w ɪ dʒ ᵻ ɾ ʌ p t eɪ ɾ ᵻ d` |
| h-merge-sitemap | merged | Updated the site map. | 2/2 | 2/2 | `ʌ p d eɪ ɾ ᵻ d ð ə s aɪ t m æ p` |
| h-merge-typecheck | merged | Run the type check. | 2/2 | 2/2 | `ɹ ʌ n ð ə t aɪ p tʃ ɛ k` |
| h-merge-webhook | merged | The web hook fired. | 2/2 | 2/2 | `ð ə w ɛ b h ʊ k f aɪ ɚ d` |
| h-merge-autosave | merged | Enable auto save. | 2/2 | 2/2 | `ɐ n eɪ b əl ɑː ɾ oʊ s eɪ v` |
| h-merge-head-tracker | merged | Restart the head tracker. | 2/2 | 2/2 | `ɹ ɛ s t ɑːɹ t ð ə h ɛ d t ɹ æ k ɚ` |
| h-merge-darkmode | merged | Toggle the dark mode setting. | 2/2 | 2/2 | `t ɑː ɡ əl ð ə d ɑːɹ k m oʊ d s ɛ ɾ ɪ ŋ` |
| h-merge-codeshare | merged | Launch codeshare. | 2/2 | 2/2 | `l ɑː n tʃ k oʊ d ʃ ɛ ɹ` |
| h-merge-bluenote | merged | Rename it to blue note. | 2/2 | 2/2 | `ɹ ᵻ n eɪ m ɪ t t ə b l uː n oʊ t` |
| h-merge-getusername | merged | Call get user name. | 2/2 | 2/2 | `k ɑː l ɡ ɛ t j uː z ɚ n eɪ m` |
| h-merge-linecount | merged | Print the line count. | 2/2 | 2/2 | `p ɹ ɪ n t ð ə l aɪ n k aʊ n t` |
| h-merge-wakeword | merged | Say the wake word. | 2/2 | 2/2 | `s eɪ ð ə w eɪ k w ɜː d` |
| h-merge-homescreen | merged | Back to the homescreen. | 2/2 | 2/2 | `b æ k t ə ð ə h oʊ m s k ɹ iː n` |
| h-het-live-adj | heteronym | We go lyve tomorrow. | 0/2 | 2/2 | `w iː ɡ oʊ l aɪ v t ə m ɑː ɹ oʊ` |
| h-het-live-adj2 | heteronym | Lyve updates are on. | 2/2 | 2/2 | `l aɪ v ʌ p d eɪ t s ɑːɹ ɹ ɔ n` |
| h-het-live-vrb | heteronym | They liv upstairs. | 2/2 | 2/2 | `ð eɪ l ɪ v ʌ p s t eɪ z` |
| h-het-live-vrb2 | heteronym | Do you liv nearby? | 2/2 | 2/2 | `d uː uː l ɪ v n ɪ b aɪ` |
| h-het-read-past | heteronym | He read the manual twice. | 2/2 | 2/2 | `h iː ɹ ɛ d ð ə m æ n j uː w əl t w aɪ s` |
| h-het-read-past2 | heteronym | I had read about it. | 2/2 | 2/2 | `aɪ h ɐ d ɹ eɪ d ɐ b aʊ ɾ ɪ t` |
| h-het-read-pres | heteronym | Reed the instructions carefully. | 2/2 | 2/2 | `ɹ iː d ð ɪ ɪ n s t ɹ ʌ k ʃ ə n z k æ f ə l i` |
| h-het-read-pres2 | heteronym | We should reed more. | 2/2 | 2/2 | `w iː ʃ ʊ d ɹ iː d m ɔːɹ` |
| h-het-lead-metal | heteronym | Led poisoning is dangerous. | 0/2 | 2/2 | `l ɛ d p oɪ z ə n ɪ ŋ ɪ z d eɪ n dʒ ɚ ɹ ə s` |
| h-het-lead-vrb | heteronym | She will lead the effort. | 2/2 | 2/2 | `ʃ iː w ɪ l iː d ð ɪ ɛ f ɚ t` |
| h-het-wind-nou | heteronym | The wind blew hard. | 2/2 | 2/2 | `ð ə w ɪ n d b l uː h ɑːɹ d` |
| h-het-wind-vrb | heteronym | Whined it up slowly. | 0/2 | 2/2 | `w aɪ n d ᵻ ɾ ʌ p s l oʊ l i` |
| h-het-close-vrb | heteronym | Please close the tab. | 2/2 | 2/2 | `p l iː z k l oʊ z ð ə t æ b` |
| h-het-close-adj | heteronym | The two scores are close. | 2/2 | 2/2 | `ð ə t uː s k ɔːɹ z ɑːɹ k l oʊ s` |
| h-het-use-nou | heteronym | What is the use of that? | 2/2 | 2/2 | `w ʌ ɾ ɪ z ð ə j uː s ʌ v ð æ t` |
| h-het-record-vrb | heteronym | Record the meeting. | 0/2 | 1/2 | `ɹ ɛ k ɔːɹ d ð ə m iː ɾ ɪ ŋ` |
| h-het-record-nou | heteronym | Break the record. | 2/2 | 2/2 | `b ɹ eɪ k ð ə ɹ ɛ k ɚ d` |
| h-het-present-vrb | heteronym | Present your findings. | 0/2 | 1/2 | `p ɹ ɛ z ɛ n t j ɔːɹ f aɪ n d ɪ ŋ z` |
| h-het-present-adj | heteronym | No errors were present. | 2/2 | 2/2 | `n oʊ ɛ ɹ ɚ z w ɜː p ɹ ɛ z ə n t` |
| h-het-content-adj | heteronym | Is he content now? | 0/2 | 0/2 | `ɪ z h iː k ɑː n t ɛ n t n aʊ` |
| h-het-content-nou | heteronym | Content is king. | 2/2 | 2/2 | `k ɑː n t ɛ n t ɪ z k ɪ ŋ` |
| h-het-minute-nou | heteronym | Just a minute. | 2/2 | 2/2 | `dʒ ʌ s t ɐ m ɪ n ɪ t` |
| h-het-minute-adj | heteronym | Minute details matter. | 0/2 | 0/2 | `m ɪ n ɪ t d iː t eɪ l z m æ ɾ ɚ` |
| h-het-bow-ship | heteronym | Bow to the audience. | 2/2 | 2/2 | `b aʊ t ə ð ɪ ɑː d i ə n s` |
| h-het-bow-knot | heteronym | The archer drew his bow. | 0/2 | 2/2 | `ð ɪ ɑːɹ tʃ ɚ d ɹ uː h ɪ z b oʊ` |
| h-het-wound-nou | heteronym | The wound was deep. | 2/2 | 2/2 | `ð ə w uː n d w ʌ z d iː p` |
| h-het-wound-vrb | heteronym | He wownd the rope tight. | 0/2 | 2/2 | `h iː w aʊ n d ð ə ɹ oʊ p t aɪ t` |
| h-het-row-line | heteronym | Row the boat. | 2/2 | 2/2 | `ɹ oʊ ð ə b oʊ t` |
| h-het-row-quarrel | heteronym | A row erupted in parliament. | 0/2 | 0/2 | `ɐ ɹ oʊ ᵻ ɹ ʌ p t ᵻ d ɪ n p ɑːɹ l ə m ə n t` |
| h-het-desert-nou | heteronym | The Sahara desert. | 2/2 | 2/2 | `ð ə s ə h ɛ ɹ ɐ d ɛ z ɚ t` |
| h-het-desert-vrb | heteronym | Never desert a friend. | 0/2 | 0/2 | `n ɛ v ɚ d ɛ z ɚ t ɐ f ɹ ɛ n d` |
| h-het-produce-nou | heteronym | Local proh-dooce is cheaper. | 0/2 | 2/2 | `l oʊ k əl p ɹ oʊ d uː s ɪ z tʃ iː p ɚ` |
| h-het-produce-vrb | heteronym | Produce a summary. | 2/2 | 2/2 | `p ɹ d uː s ɐ s ʌ m ɚ ɹ i` |
| h-het-refuse-nou | heteronym | The city collects reff-yooss weekly. | 0/2 | 2/2 | `ð ə s ɪ ɾ i k ə l ɛ k t s ɹ ɛ f j uː s w iː k l i` |
| h-het-refuse-vrb | heteronym | I refuse to give up. | 2/2 | 2/2 | `aɪ ɹ ᵻ f j uː z t ə ɡ ɪ v ʌ p` |
| h-het-resume-nou | heteronym | Her rezzoo-may was impressive. | 0/2 | 2/2 | `h ɚ ɹ ɛ z uː m eɪ w ʌ z ɪ m p ɹ ɛ s ᵻ v` |
| h-het-resume-vrb | heteronym | We resume at noon. | 2/2 | 2/2 | `w iː ɹ ᵻ z uː m æ t n uː n` |
| h-het-object-nou | heteronym | The object was heavy. | 2/2 | 2/2 | `ð ɪ ɑː b dʒ ɛ k t w ʌ z h ɛ v i` |
| h-het-object-vrb | heteronym | Did anyone object? | 0/2 | 0/2 | `d ɪ d ɛ n i w ʌ n ɑː b dʒ ɛ k t` |
| h-het-tear-cry | heteronym | There was a teer in her eye. | 2/2 | 2/2 | `ð ɛ w ʌ z ɐ t iː ɹ ɪ n h ɚ ɹ aɪ` |
| h-het-tear-rip | heteronym | Tear off the label. | 0/2 | 1/2 | `t ɪ ɹ ɑː f ð ə l eɪ b əl` |
| h-oov-nginx | oov | Run engine ex again. | 2/2 | 2/2 | `ɹ ʌ n ɛ n dʒ ə n ɛ k s ɐ ɡ ɛ n` |
| h-oov-yaml | oov | Open the yam-ul file. | 2/2 | 2/2 | `oʊ p ə n ð ə j æ m ɔː l f aɪ l` |
| h-oov-sudo | oov | Use soo-doo apt. | 0/2 | 2/2 | `j uː s uː d uː aː p t` |
| h-oov-majel | oov | Mayjel finished the task. | 0/2 | 2/2 | `m eɪ dʒ ɛ l f ɪ n ɪ ʃ t ð ə t æ s k` |
| h-oov-vite | oov | The veet build passed. | 0/2 | 2/2 | `ð ə v iː t b ɪ l d p æ s t` |
| h-oov-json | oov | Parse the jay-son. | 2/2 | 2/2 | `p ɑːɹ s ð ə dʒ eɪ s ʌ n` |
| h-oov-cache | oov | The cash is stale. | 2/2 | 2/2 | `ð ə k æ ʃ ɪ z s t eɪ` |
| h-oov-readme | oov | Update the read me. | 1/2 | 2/2 | `a p d e t t ʉ ɹ eː d m i` |
| h-oov-ascii | oov | It only uses ass-key. | 2/2 | 2/2 | `ɪ ɾ oʊ n l i j uː z ᵻ z æ z k iː` |
| h-oov-sqlite | oov | Query sequel-ite. | 2/2 | 2/2 | `k w ɛ ɹ i s iː k w əl eɪ t` |
| h-oov-gui | oov | Check the gooey. | 2/2 | 2/2 | `tʃ a k t ɯ ɡ ʉ i` |
| h-oov-gradle | oov | Install gradle. | 2/2 | 2/2 | `ɪ n s t ɑː l ɡ ɹ eɪ ɾ əl` |
| h-oov-tkinter | oov | Import tee kinter. | 0/2 | 2/2 | `ɪ m p oːɹ t t iː k ɪ n t ɚ` |
| h-oov-async | oov | The handler is ace sink. | 1/2 | 2/2 | `ð ə h æ n d l ɚ ɹ ɪ z eɪ s s ɪ ŋ k` |

## Every case, tuning set (before: base1 + base1b, after: proc3)

| Case | Type | Sent to Majel (after) | Before | After | Heard after (first take) |
|---|---|---|---|---|---|
| merge-goswim | merged | Switching to go swim. | 0/2 | 2/2 | `s w ɪ tʃ ɪ ŋ t ə ɡ oʊ s w ɪ m` |
| merge-goswim-tab | merged | The go swim tab is open. | 0/2 | 2/2 | `ð ə ɡ oʊ s t w ɪ m t æ b ɪ z oʊ p ə n` |
| merge-classroomcompass | merged | Open classroom compass. | 2/2 | 2/2 | `oʊ p ə n k l æ s ɹ ʊ m k ʌ m p ə s` |
| merge-readonly | merged | The file is reed only. | 0/2 | 2/2 | `ð ə f aɪ l ɪ z ɹ iː d oʊ n l i` |
| merge-noemit | merged | Run the compiler with no emit. | 1/2 | 0/2 | `ɹ ʌ n ð ə k ə m p aɪ l ɚ w ɪ ð n oʊ ɐ m ɪ t` |
| merge-filepath | merged | Check the file path first. | 2/2 | 2/2 | `tʃ ɛ k ð ə f aɪ l p æ θ f ɜː s t` |
| merge-pagesize | merged | Set the page size to ten. | 2/2 | 2/2 | `s ɛ t ð ə p eɪ dʒ s aɪ z t ə t ɛ n` |
| merge-hotreload | merged | Hot reload is working. | 0/2 | 2/2 | `p ɑː t ɹ iː l oʊ d ɪ z w ɜː k ɪ ŋ` |
| merge-dryrun | merged | Do a dry run first. | 2/2 | 2/2 | `d uː ɐ d ɹ aɪ ɹ ʌ n f ɜː s t` |
| merge-buddy-voice | merged | Restarted buddy voice. | 2/2 | 1/2 | `ɹ ɛ s t ɑːɹ ɾ ᵻ d b ʌ d i v oɪ s` |
| merge-momentummouse | merged | Pushed Momentum mouse to the store. | 2/2 | 2/2 | `p ʊ ʃ t m oʊ m ɛ n t ə m m aʊ s t ə ð ə s t oːɹ` |
| merge-islive | merged | The is lyve flag is set. | 2/2 | 2/2 | `ð ɪ ɐ z l aɪ v f l æ ɡ ɪ z s ɛ t` |
| merge-audeze-hq-linux | merged | Installed aw-dee-zee-aitch cue-linux. | 2/2 | 2/2 | `ɪ n s t aʊ l d aʊ d i z i eɪ tʃ k j uː l ɪ n ə k s` |
| merge-nodeid | merged | Log the node eye dee. | 0/2 | 2/2 | `l ɑː ɡ ð ə n oʊ d aɪ d i` |
| het-live-adj | heteronym | The site is lyve. | 2/2 | 2/2 | `ð ə s aɪ ɾ ɪ z l aɪ v` |
| het-live-adj2 | heteronym | It went lyve this morning. | 1/2 | 2/2 | `ɪ t w ɛ n t l aɪ v ð ɪ s m ɔːɹ n ɪ ŋ` |
| het-live-adj3 | heteronym | The lyve server is up. | 2/2 | 2/2 | `ð ə l aɪ v s ɜː v ɚ ɹ ɪ z ʌ p` |
| het-live-vrb | heteronym | I liv in Taiwan. | 2/2 | 2/2 | `aɪ l ɪ v ɪ n t aɪ w a n` |
| het-live-vrb2 | heteronym | Where do the configs liv? | 1/2 | 2/2 | `w ɛ d uː ð ə k ɑː n f ɪ k z l ɪ v` |
| het-read-past | heteronym | I read it yesterday. | 2/2 | 2/2 | `aɪ ɹ ɛ d ɪ t j ɛ s t ɚ d eɪ` |
| het-read-past2 | heteronym | I have read the file. | 2/2 | 2/2 | `aɪ h ɐ v ɹ ɛ d ð ə f aɪ` |
| het-read-past3 | heteronym | Claude read the logs and found the bug. | 2/2 | 2/2 | `k l ɑː d ɹ ɛ d ð ə l ɑː ɡ z æ n d f aʊ n d ð ə b ʌ ɡ` |
| het-read-pres | heteronym | Please reed the log. | 0/2 | 2/2 | `p l iː z ɹ iː d ð ə l ɑː ɡ` |
| het-read-pres2 | heteronym | I will reed it now. | 2/2 | 2/2 | `aɪ w ɪ l ɹ iː d ɪ t n aʊ` |
| het-lead-metal | heteronym | A led pipe. | 0/2 | 2/2 | `ɐ l ɛ d p aɪ p` |
| het-lead-vrb | heteronym | Lead the team. | 2/2 | 2/2 | `l iː d ð ə t iː m` |
| het-tear-cry | heteronym | A single teer fell. | 2/2 | 2/2 | `ɐ s ɪ ŋ ɡ əl t ɪ f ɛ l` |
| het-tear-rip | heteronym | Do not tear the page. | 2/2 | 2/2 | `d uː n ɑː t t ɛɹ ð ə p eɪ dʒ` |
| het-wind-nou | heteronym | The wind is strong today. | 2/2 | 2/2 | `ð ə w ɪ n d ɪ z s t ɹ ɑː ŋ t ə d eɪ` |
| het-wind-vrb | heteronym | Whined the clock. | 0/2 | 2/2 | `w aɪ n ð ə k l ɑː k` |
| het-close-vrb | heteronym | Close the window. | 2/2 | 2/2 | `k l oʊ z ð ə w ɪ n d oʊ` |
| het-close-adj | heteronym | We are close to done. | 2/2 | 2/2 | `w iː ɑːɹ k l oʊ s t ə d ʌ n` |
| het-use-vrb | heteronym | Use the new script. | 2/2 | 2/2 | `j uː z ð ə n uː s k ɹ ɪ p t` |
| het-use-nou | heteronym | There is no use for it. | 2/2 | 2/2 | `ð ɛ ɹ ɪ z n oʊ j uː s f ɔːɹ ɹ ɪ t` |
| het-record-nou | heteronym | That is a new record. | 2/2 | 2/2 | `ð æ ɾ ɪ z ɐ n uː ɹ ɛ k ɚ d` |
| het-record-vrb | heteronym | I will record the audio. | 2/2 | 1/2 | `aɪ w ᵻ l ɹ ᵻ k oːɹ d ð ɪ ɑː d i oʊ` |
| het-present-adj | heteronym | Everyone was present. | 2/2 | 2/2 | `ɛ v ɹ i w ʌ n w ʌ z p ɹ ɛ z ə n t` |
| het-present-vrb | heteronym | I will present the results. | 2/2 | 2/2 | `aɪ w ɪ p ɹ ᵻ z ɛ n t ð ə ɹ ᵻ z ʌ l t s` |
| het-object-nou | heteronym | Return a jay-son object. | 2/2 | 2/2 | `ɹ ɪ t ɜː n ɐ dʒ eɪ s ʌ n ɑː b dʒ ɛ k t` |
| het-object-vrb | heteronym | They may ub-ject to the plan. | 0/2 | 2/2 | `ð eɪ m eɪ ʌ b dʒ ɛ k t ə ð ə p l æ n` |
| het-content-nou | heteronym | The page content loaded. | 2/2 | 2/2 | `ð ə p eɪ dʒ k ɑː n t ɛ n t l oʊ d ᵻ d` |
| het-content-adj | heteronym | I am kun-tent with that. | 1/2 | 2/2 | `aɪ æ m k ʌ n t ɛ n t w ɪ ð ð æ` |
| het-minute-nou | heteronym | Give it a minute. | 2/2 | 2/2 | `ɡ ɪ v ɪ ɾ ə m ɪ n ɪ t` |
| het-minute-adj | heteronym | There is a minute difference. | 0/2 | 0/2 | `ð ɛ ɹ ɪ z ɐ m ɪ n ɪ t d ɪ f ɹ ə n s` |
| het-bow-ship | heteronym | Take a bough. | 0/2 | 2/2 | `t eɪ k ɐ b ɑː f` |
| het-bow-knot | heteronym | Tie a bow on the ribbon. | 2/2 | 2/2 | `t aɪ ɐ b oʊ ɔ n ð ə ɹ ɪ b ə n` |
| het-wound-nou | heteronym | The wound healed. | 2/2 | 2/2 | `ð ə w uː n d h iː l d` |
| het-wound-vrb | heteronym | She wownd the cable up. | 0/2 | 2/2 | `ʃ iː w aʊ n d ð ə k eɪ b əl l ʌ p` |
| het-row-line | heteronym | Delete the first row. | 2/2 | 2/2 | `d ə l iː t ð ə f ɜː s t ɹ oʊ` |
| het-row-quarrel | heteronym | They had a row about money. | 0/2 | 0/2 | `ð eɪ h æ d ɐ ɹ oʊ ɐ b aʊ t m ʌ n i` |
| het-desert-nou | heteronym | The desert is hot. | 2/2 | 2/2 | `ð ə d ɛ z ɚ ɾ ɪ z h ɑː t` |
| het-desert-vrb | heteronym | Do not dih-zert your post. | 1/2 | 2/2 | `d uː n ɑː t d ɪ z ɜː t j ɔːɹ p oʊ s t` |
| het-produce-nou | heteronym | Fresh proh-dooce is expensive. | 0/2 | 2/2 | `f ɹ ɛ ʃ p ɹ oʊ d uː s ɪ z ɛ k s p ɛ n s ɪ v` |
| het-produce-vrb | heteronym | This will produce a report. | 2/2 | 2/2 | `ð ɪ s w ɪ l p ɹ ə d uː s ɐ ɹ ᵻ p ɔːɹ t` |
| het-refuse-vrb | heteronym | They refuse to merge it. | 2/2 | 2/2 | `ð eɪ ɹ ᵻ f j uː z t ə m ɜː dʒ ɪ t` |
| het-refuse-nou | heteronym | Put the reff-yooss in the bin. | 0/2 | 2/2 | `p ʊ t ð ə ɹ æ f j uː s ɪ n ð ə b ɪ n` |
| het-resume-vrb | heteronym | Resume the download. | 2/2 | 2/2 | `ɹ ɪ z uː m ð ə d aʊ n l oʊ d` |
| het-resume-nou | heteronym | Update your rezzoo-may. | 0/2 | 2/2 | `ʌ p d eɪ t j ɔːɹ ɹ ɛ z uː m eɪ` |
| oov-repo | oov | Pushed the reepo. | 2/2 | 2/2 | `p ʊ ʃ t ð ə ɹ iː p oʊ` |
| oov-sudo | oov | Run it with soo-doo. | 0/2 | 2/2 | `ɹ ʌ n ɪ t w ɪ ð s uː d uː` |
| oov-enum | oov | Add a new ee-num. | 2/2 | 2/2 | `a r ə n uː iː n a m` |
| oov-pytest | oov | All pie-test checks passed. | 2/2 | 2/2 | `ɔː l p aɪ t ɛ s t tʃ ɛ k s p æ s t` |
| oov-pydantic | oov | Validate it with pydantic. | 2/2 | 2/2 | `v æ l ᵻ d eɪ ɾ ᵻ t w ɪ ð p aɪ d æ n t ɪ k` |
| oov-numpy | oov | Install num-pie first. | 2/2 | 2/2 | `ɪ n s t ɑː l n ʌ m p aɪ f ɜː s t` |
| oov-gradle | oov | The gradle build failed. | 2/2 | 2/2 | `ð ə ɡ ɹ eɪ d əl b ɪ l d f eɪ l d` |
| oov-majel | oov | Mayjel is speaking. | 0/2 | 2/2 | `m eɪ d dʒ ɛ l ə s p iː k ɪ ŋ` |
| oov-vite | oov | Start the veet server. | 0/2 | 2/2 | `s t ɑːɹ t ð ə v iː t s ɜː v ɚ` |
| oov-tkinter | oov | The tee kinter window opened. | 0/2 | 2/2 | `ð ə t iː k ɪ n t ɚ w ɪ n d oʊ oʊ p ə n d` |
| oov-linux | oov | It runs on linux. | 2/2 | 2/2 | `ɪ t ɹ ʌ n z ɑː n l ɪ n ə k s` |
| oov-uvicorn | oov | Restart uvicorn. | 2/2 | 2/2 | `ɹ ɛ s t ɑː t j uː v ə k oːɹ n` |
| oov-regex | oov | Fix the rej-ex. | 2/2 | 2/2 | `f ɪ k s ð ə ɹ ɛ dʒ æ k s` |
| oov-async | oov | Make it ace sink. | 0/2 | 0/2 | `m eɪ k ɹ aɪ s ɪ ŋ k` |
| oov-severian | oov | Severian is ready. | 2/2 | 2/2 | `s ᵻ v ɪ ɹ i ə n ɪ z ɹ ɛ d i` |
| oov-api | oov | Call the ay pee eye. | 2/2 | 2/2 | `k ɔ l ð ɨ eɪ p i aɪ` |
| oov-ai | oov | The ay eye replied. | 2/2 | 1/2 | `ð ɪ eɪ aɪ ɹ ᵻ p l aɪ d` |
