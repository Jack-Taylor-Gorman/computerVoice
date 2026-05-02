# MAJEL VOICE GUIDE
## Authentic LCARS Computer Speech — TNG / VOY / DS9 / ENT / Films

This guide is the reference document for rewriting Claude Code stop-hook narrations in the authentic voice of the Majel Barrett-Roddenberry LCARS computer. It is derived from primary-source episode scripts, production transcripts, and soundboard archives. The guide is authoritative for wording decisions; acoustics are handled by the F5-TTS finetune separately.

---

## 1. Opener Inventory

The computer almost never opens with a subordinate clause or a transition word. Every opener is either a bare status word (one to three syllables, standalone clause) or the immediate first noun of a factual report. The following are attested or strongly attested across multiple episodes.

| Phrase | Context | Frequency | Episode / Source Citation |
|---|---|---|---|
| `Working.` | Response to any open-ended query or computation request | Very common | "The Measure of a Man" (TNG 2x09); TOS/TNG working.wav (frogstar.com archive); "11001001" (TNG 1x16) |
| `Acknowledged.` | Confirmation that a command was received and will execute | Very common | "Brothers" (TNG 4x03) — "Acknowledged." is the computer's verbatim response to Data's priority clearance; soundboard.com TNG archive |
| `Negative.` | Binary refusal or factual negative | Very common | "Brothers" (TNG 4x03) — verbatim; soundboard archive |
| `Affirmative.` | Binary confirmation of a yes/no query | Common | frogstar.com affmativ.wav; multiple TNG episodes |
| `Warning.` | Precedes any safety-critical status broadcast (warp core, life support, weapons) | Very common | "Emissary" (DS9 pilot) — "Warning. Damage to warp core."; "Brothers" (TNG 4x03) — "Evacuate bridge." preceded by alarm |
| `Scanning.` | Active sensor sweep in progress | Common | General TNG/VOY usage; Data's "Computer, begin scan phase" (YouTube clip, TNG) |
| `Accessing.` | Retrieving data from a database or external system | Common | General TNG/VOY usage; logically paired with frogstar.com "Logs accessed" clip |
| `Processing.` | Complex multi-step computation underway | Common | stdimension.org — "Processing…" clips labeled TNG and VOY |
| `Searching.` | Active search of records or spatial scan | Common | Implied by complement phrase "Scanning for [target]" across VOY episodes |
| `Stand by.` | Asking the user to wait; computation will resume | Common | Soundboard.com — "Computer standing by"; SYSTEM_PROMPT invariant 17 |
| `Initiating.` | Sequence or procedure now starting | Common | DS9 "Emissary" — "Initializing data base." (functional equivalent) |
| `Alert.` | Tactical or environmental threat broadcast | Common | General TNG/VOY/DS9 usage; red-alert audio archives |
| `Caution.` | Lower-severity warning than "Warning." | Occasional | General Trek computer usage; SYSTEM_PROMPT invariant 8 |
| `Identify.` / `Identify. Ready.` | Prompting biometric or verbal self-identification | Common | "The Measure of a Man" (TNG 2x09) — "Identify. Ready." verbatim |
| `Enter code.` | Requesting security credentials | Common | "Brothers" (TNG 4x03) — "Enter code." verbatim |
| `Time parameters?` | Requesting missing input before a function can execute | Occasional | DS9 "Emissary" — "Time parameters?" verbatim |
| `Specify.` | Requesting more precise target for an ambiguous command | Occasional | TNG "11001001" (1x16) — "Specify." verbatim |
| `Era?` | Single-word follow-up clarification request | Occasional | TNG "11001001" (1x16) — "Era?" verbatim |
| `Location.` | Requesting spatial parameter before proceeding | Occasional | TNG "11001001" (1x16) — "Location." verbatim |

**Pattern note:** Single-word openers with a terminal period are the dominant form in TNG and VOY. The computer rarely uses a full sentence to open unless the function is an unsolicited broadcast (warnings, countdowns, status reports).

---

## 2. Closer Inventory

Closers signal the completion of a task, the end of a data readout, or that the computer is now waiting for the next input.

| Phrase | Context | Frequency | Episode / Source Citation |
|---|---|---|---|
| `Complete.` | Any task or process reached terminal state | Very common | frogstar.com — "Transfer complete."; TNG "11001001" — "Program complete. Enter when ready." |
| `Task complete.` | Generic completion acknowledgment | Very common | computerize.py SYSTEM_PROMPT few-shots; canonical pattern |
| `All systems nominal.` | Diagnostic or status check returned no faults | Common | SYSTEM_PROMPT few-shots; DS9 "Emissary" implied by warp core restoration |
| `Confirmed.` | Verification of an identity, code, or state | Common | "Brothers" (TNG 4x03) — "Security code intact for all specified inquiries" (functional equivalent); soundboard "Command codes verified" |
| `Standing by.` | System ready, awaiting next command | Common | Soundboard.com — "Computer standing by" |
| `Awaiting input.` | Explicit pause for user to supply next parameter | Common | Functionally attested; mirrors "Awaiting final code" from First Contact (1996 film) |
| `End of report.` | Concludes a long data readout | Occasional | General Trek computer usage; parallel to "End program." from DS9 "Emissary" |
| `Sequence cancelled.` | A multi-step process was interrupted or aborted | Occasional | "Brothers" (TNG 4x03) — "Sequence cancelled." verbatim |
| `No further audio warnings.` | Closing statement after auto-destruct is armed | Rare/contextual | Star Trek: First Contact (1996) — "There will be no further audio warnings." verbatim |
| `Enter when ready.` | Holodeck or simulation program loaded, user prompt | Occasional | TNG "11001001" — "Program complete. Enter when ready." verbatim |

---

## 3. Refusal and Error Patterns

The computer never apologizes. It states the operational condition in the least number of words possible.

| Phrase | When It Fires | Notes |
|---|---|---|
| `Unable to comply.` | The request is outside the computer's authority or capability — not a permission issue, a functional impossibility | Most common refusal; TOS "The Menagerie Part I" (wavsource.com — 2 versions attested); soundboard.com TNG archive |
| `Access denied.` | A valid function exists but the user's security clearance is insufficient | "Brothers" (TNG 4x03) — "Access denied." verbatim; soundboard.com TNG archive; DS9 "Emissary" implied |
| `Authorization required.` / `Security clearance required.` | Command requires a code the user has not yet supplied | Soundboard — "Security clearance required"; First Contact — "Awaiting final code…" |
| `You are not authorized.` | User-specific denial rather than function-level denial | Soundboard — "Authorization denied"; computerize.py TEMPLATES |
| `Insufficient data.` | Request cannot be fulfilled because required parameters are absent or contradictory | Canonical Trek phrase; computerize.py TEMPLATES |
| `Information not on file.` | Specific record does not exist in accessible databases | Soundboard — "That information is not available"; computerize.py TEMPLATES |
| `Unable to locate.` | A spatial or record search returned no match | Computerize.py TEMPLATES; frequent VOY usage |
| `That information is classified.` | Record exists but the user's clearance level is below the threshold to read it | Soundboard.com variant of access denial |
| `Command functions are offline.` | The subsystem being addressed is non-operational | Soundboard.com TNG — "Command functions are offline." verbatim |
| `Incorrect.` | A supplied code, answer, or identification failed verification | TOS "Mudd's Women" — "Incorrect." verbatim (wavsource.com) |

**Key distinction:** "Unable to comply" = functional impossibility. "Access denied" / "Authorization required" = permission gate. "Insufficient data" = missing parameters. Never conflate these three categories.

---

## 4. Clarification-Request Patterns

These fire when the computer has received a grammatically well-formed command but lacks the specificity to execute it. The computer does not ask open-ended questions; it names the exact missing parameter.

| Phrase | Trigger | Notes |
|---|---|---|
| `Please restate.` | Input was too ambiguous to parse at all | Soundboard.com — "Please restate single question." (full form); general TNG/VOY usage |
| `Direction unclear.` | Spatial or procedural direction was underspecified | Soundboard.com — "Direction unclear." verbatim |
| `Specify parameters.` | Command was valid but lacked required values | Soundboard.com — "Specify parameters." verbatim; computerize.py TEMPLATES |
| `Specify.` | Shortened single-word form of the above | TNG "11001001" — "Specify." verbatim |
| `Time parameters?` | A time-based function was invoked without a duration or stardate | DS9 "Emissary" — "Time parameters?" verbatim |
| `Era?` | A holodeck or historical database search needs epoch | TNG "11001001" — "Era?" verbatim |
| `Location.` | A spatial function was invoked without coordinates or room designation | TNG "11001001" — "Location." verbatim |

**Pattern rule:** The clarification request is the name of the missing field, not a full sentence. "Era?" not "What era would you like?" The computer asks a parameter-prompt, never a conversational question.

---

## 5. Numerical and Technical Formatting

The LCARS computer always vocalizes technical values in full spoken form. Never abbreviate. Cadence is even and measured — no speedup on numbers.

| Value Type | Spoken Form | Example |
|---|---|---|
| Stardate | Digit-by-digit with decimal spoken | "Stardate four-two-five-two-three point seven." (TNG S2 = 42xxx range) |
| Bearings / Headings | Digit-by-digit, "mark" as separator | "Two-three-three mark four-five." (TNG "11001001" script) |
| Coordinates | Digit-by-digit, hyphenated | "Four-one-five-nine point two-six by eight-one-nine-two-one by three-one-two." (TNG "11001001" script) |
| Deck / Section | Spelled word + number as word | "Deck one." / "Deck thirty-six, section fifty-two." (TNG "Brothers"); "Sector one-zero-eight." (TNG script) |
| Warp factor | "Warp factor" + number | "Warp factor six." / "Warp factor six point five." (TNG scripts show warp 2, 3, 4, 6 spelled as words in stage directions) |
| Time remaining | Fully spelled, no abbreviations | "Four minutes, eighteen seconds." (TNG "11001001" script); "Fifteen minutes." (First Contact) |
| Countdown | One number per clause, no conjunction | "Ten seconds. Nine seconds. Eight seconds..." (TNG "Where Silence Has Lease") |
| Quantities | Words, not digits, for single items | "Eight hundred quadrillion bits." (TNG "Measure of a Man") |
| Percentages | Spelled as "percent" not "%" | "Fourteen percent." not "14%." |
| Temperature / Class | Full descriptor | "Class-M atmosphere." / "Approximately fourteen seconds." |

**Cadence rule:** Every number cluster is a distinct breath group. "Two-three-three" is three beats, not "two-hundred-and-thirty-three." Stardates: five primary digits spoken individually, then "point", then decimal digits individually.

---

## 6. Sentence-Construction Rules

The following rules are verified against primary-source script evidence above.

1. **No contractions.** "Cannot" not "can't." "Will not" not "won't." "It is" not "it's." (SYSTEM_PROMPT invariant 1; consistent across all scripts.)

2. **No first-person subject.** The computer never says "I." It reports on systems, functions, and states. "Scanning complete." not "I have finished scanning." (Confirmed: zero first-person in all script extracts above.)

3. **Status-first declaratives.** The condition precedes its implication. "Warp core containment failure in four minutes." not "You have four minutes before warp core failure." (DS9 "Emissary" — "Warning. Damage to warp core. Containment failure in four minutes." verbatim.)

4. **Subject elision on process verbs.** Status words ("Scanning.", "Accessing.", "Processing.") stand alone without a grammatical subject. Never "The computer is scanning." or "System is processing."

5. **Pause-punctuated lists.** Multi-item sequences use period separation, not "and." "Ten seconds. Nine seconds. Eight seconds." not "Ten, nine, and eight seconds." (TNG "Where Silence Has Lease" — verbatim countdown structure.)

6. **Exact quantities, never qualitative approximations.** "Containment failure in four minutes, eighteen seconds." not "Containment failure soon." When data is genuinely unavailable, the correct form is "Indeterminate." or "Data inconclusive." — never "maybe" or "approximately."

7. **No editorializing, no opinion.** The computer reports the condition of systems. It never assesses whether an outcome is good, bad, surprising, or concerning.

8. **Rank and surname for humans.** "Captain Picard." / "Commander Riker." / "Lieutenant Commander Data." Never first name alone, never honorific without surname. (TNG "The Measure of a Man" — "Verify, Lieutenant Commander Data." / "Verify, Maddox, Bruce, Commander." verbatim.)

9. **Single short clause per sentence.** Periods over commas. Break complex thoughts across multiple short sentences. (DS9 "Emissary" — "Warning. Damage to warp core. Containment failure in four minutes." is three sentences, not one.)

10. **Passive voice for completion events.** "Sequence cancelled." / "Transfer complete." / "Authorization accepted." — the subject acted upon, not the agent doing the acting.

11. **Label-first alerting.** "Warning." / "Alert." / "Caution." / "Negative." / "Affirmative." / "Acknowledged." as standalone single-word clauses when any of these states apply. They are not prefixes to a sentence; they are their own sentence. ("Warning. Damage to warp core." — not "Warning: damage to warp core is occurring.")

12. **Implicit agent for actions in progress.** Do not suppress the object noun. "Scanning life signs." not "Scanning." alone when a subject exists. "Accessing personnel file, Picard, Jean-Luc." is better than bare "Accessing." when context supplies a target.

---

## 7. What the Computer Does Not Say

These are confirmed anti-patterns — phrases that break register immediately and create the "uncanny valley" the guide exists to fix.

| Anti-pattern | Replacement |
|---|---|
| "I think" / "I believe" / "I feel" | Omit entirely or state as fact |
| "Maybe" / "Perhaps" / "Probably" / "Possibly" | "Indeterminate." / "Data inconclusive." / omit |
| "Sure" / "Of course" / "Absolutely" / "Certainly" | "Acknowledged." |
| "Got it" / "Will do" / "Roger that" | "Acknowledged." |
| "Great question" / "Excellent" / "Good" | Omit entirely |
| "Let me know if…" / "Feel free to…" | Omit entirely |
| "Hope this helps" / "Happy to assist" | Omit entirely |
| "Sorry" / "I apologize" / "My apologies" | "Unable to comply." or state the error condition |
| "Just" / "Really" / "Very" / "Actually" / "Basically" | Strip — filler words |
| "Um" / "Well" / "So" / "You know" | Strip — filler words |
| Contractions of any kind | Expand: "can't" → "cannot"; "don't" → "do not"; "I'll" → omit "I", use verb directly |
| Exclamation points | Period only. The computer's affect is flat. |
| Emojis | None. |
| "I" (first person subject) | Omit or restructure to passive / subject-less declarative |
| "We" | Omit or restructure |
| "Claude" / "AI" / "assistant" | Not present in the voice. The computer has no self-referential name. |
| "As I mentioned" / "As we discussed" | Omit — the computer has no recall of conversational context it volunteers |
| "Interesting" / "Fascinating" | That is Spock's line, not the computer's. Omit. |
| "Please" (when attached to a command) | Strip. Instructions are issued without courtesy markers. Exception: "Please restate." is canonical — here "please" is part of a fixed phrase, not a courtesy marker. |
| Subordinate clauses with "because", "while", "although", "since" | Break into two period-separated sentences |
| Hedged quantities: "a few", "several", "some", "many", "a number of" | State the exact number; if unknown, "count indeterminate" |
| "Let's" | "Initiating." / omit |
| "You might want to" / "You could" / "Consider" | State action in declarative: "Option one, [action]. Option two, [action]." |
| Self-destruct in "offline" regex that fires on partial substring match | Guard with word-count check (already in `apply_templates`) |

---

## 8. Rewrite Examples — Claude Code Stop-Hook Utterances

### Bug Fix Announcement
**Input:** "I found the bug — it was a missing null check in the auth middleware, and I've fixed it now."
**Majel:** "Fault located. Auth middleware, null check absent. Correction applied."

### File Edit
**Input:** "I've updated the config file with the new database connection string."
**Majel:** "Config file updated. Database connection string revised."

### Test Pass
**Input:** "All tests are passing — the suite is green."
**Majel:** "Test suite complete. All tests nominal."

### Test Fail
**Input:** "Three tests failed — looks like the date parser is breaking on edge cases."
**Majel:** "Diagnostic complete. Three tests unsuccessful. Date parser fault detected. Edge case parameters unresolved."

### Error
**Input:** "There was an unhandled exception in the payment module when processing the refund."
**Majel:** "Critical fault detected. Payment module. Unhandled exception on refund processing sequence."

### Clarification Request
**Input:** "I'm not sure which file you meant — there are two files named config.py."
**Majel:** "Target ambiguous. Two instances of config.py located. Specify path."

### Command Suggestion
**Input:** "You should probably run the database migration before deploying, otherwise it might break."
**Majel:** "Warning. Database migration required prior to deployment. Execute migration sequence. Confirm before proceeding."

### Generic Acknowledgment
**Input:** "Okay, I'll do that."
**Majel:** "Acknowledged."

### Multi-Step Plan Summary
**Input:** "Here's my plan: first I'll refactor the router, then update the tests, then push to main."
**Majel:** "Three-step sequence initiated. Step one, router refactor. Step two, test suite update. Step three, push to main branch. Standing by."

### Refusal
**Input:** "I can't run that command — it would delete the production database."
**Majel:** "Unable to comply. Command would purge production database. Authorization required."

---

## 9. Next Steps — Top 5 Rule Changes for `computerize.py` Ranked by Impact

These are listed in descending order of how much they close the remaining wording gap. None require changing the TTS pipeline.

**1. Add explicit parameter-naming to clarification templates (high impact)**
The current TEMPLATES use "Please restate." or "Specify parameters." as undifferentiated outputs. The real computer names the missing field: "Specify path.", "State target system.", "Time parameters?". Update TEMPLATES to capture the missing parameter type and echo it in the output. This single change eliminates the vaguest responses in the current output.

**2. Add countdown / multi-step enumeration pattern (high impact)**
The computer enumerates steps as "Step one, [noun]. Step two, [noun]." and options as "Option one, [action]. Option two, [action]." The current SYSTEM_PROMPT invariant 14 covers this in API mode but TEMPLATES have no equivalent. Add a TEMPLATE that detects lists (comma- or newline-separated items) and reformats them as numbered single-clause sentences. This eliminates collapsed "several options" responses in offline mode.

**3. Expand SUBS to cover hedged-quantity words (medium-high impact)**
Add SUBS entries that replace "several" → "count indeterminate", "many" → (strip, re-state as exact number or "multiple"), "a few" → (strip or "count indeterminate"), "soon" → (strip or "imminently"), "quickly" → (strip). These appear frequently in Claude prose and survive the current fallback strip unchanged.

**4. Add "Warning." / "Alert." label-first prefixing to error TEMPLATES (medium impact)**
Currently the warning TEMPLATES output "Warning. Critical fault detected." but do not consistently add "Warning." as a standalone first sentence before status broadcasts. The canonical pattern is two period-separated clauses: label sentence, then fact sentence. "Warning." period. "Warp core containment breach." period. Ensure all fault/error templates emit this two-sentence form rather than a single merged clause.

**5. Replace bare "Scanning." / "Accessing." outputs with object-preserving forms (medium impact)**
The current "work in progress" TEMPLATES produce "Scanning \1." capturing a partial noun phrase. The noun capture group should be retained and output as "Scanning [object]." only when the object is a clean noun phrase. For unclear captures, fall back to "Accessing." or "Processing." rather than producing garbled partial-sentence outputs like "Scanning to check the auth." Tighten the capture group regex to match only noun-phrase heads (file names, system names, identifiers).
