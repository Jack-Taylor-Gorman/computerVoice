#!/usr/bin/env python3
"""Reformat prose into the voice of Severian, narrator of Gene Wolfe's
Book of the New Sun (read by Roy Avers on the YouTube audiobooks).

Companion document: SEVERIAN_VOICE_GUIDE.md.

Two modes, controlled by ~/.severian_config.json:voice_mode:
  - "offline" (default): regex/template + dictionary substitution. Zero network.
  - "api"             : Claude Haiku rewrites with the Severian system prompt.
                        Reads API key from ~/.severian_config.json:anthropic_api_key
                        (falls back to ANTHROPIC_API_KEY env var, then the
                        legacy ~/.majel_config.json).

This is a port of computerize.py — same architecture (templates → LLM →
fallback strip), same pronunciation post-processing for acronyms / versions /
heteronyms, same project-header preamble, same JSONL rewrite cache for
distillation. What changes is the voice: Severian is a *person*, narrating
in the first person; the LCARS computer was a disembodied system.
"""
import hashlib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

CONFIG = Path.home() / ".severian_config.json"
LEGACY_CONFIG = Path.home() / ".majel_config.json"


def _config() -> dict:
    for p in (CONFIG, LEGACY_CONFIG):
        if p.exists():
            try:
                return json.loads(p.read_text())
            except (json.JSONDecodeError, OSError):
                continue
    return {}


def _api_key() -> str | None:
    return os.environ.get("ANTHROPIC_API_KEY") or _config().get("anthropic_api_key") or None


def _voice_mode() -> str:
    m = (_config().get("voice_mode") or "offline").lower()
    return "api" if m == "api" else "offline"


MAX_WORDS = 22  # slightly higher than Majel's 18 — Severian's clauses are richer

# Archetype templates — checked in order, first match short-circuits.
# Each: (regex pattern on lowercased text, output string [, capture groups applied])
#
# Cardinal difference from Majel: Severian uses "I". Many templates start
# with "I" or use first-person verbs. The fallback_strip below DOES NOT
# strip "\bi\b" — see fallback_strip().
TEMPLATES = [
    # ---- Refusal / unable ----
    (r"\b(sorry,?\s*)?i\s+(can'?t|cannot|won'?t)\s+(do|help|assist|comply|run|execute|perform)", "I cannot."),
    (r"\b(that|this)\s+(won'?t|will not|isn'?t going to|isn'?t gonna)\s+work", "It cannot be done."),
    (r"\bnot\s+possible\b", "It cannot be done."),
    (r"\brefuse[sd]?\s+to\b", "I cannot."),
    (r"\bi'?m not able\b|\bi am not able\b", "I cannot."),

    # ---- Permission gates ----
    (r"\b(requires?|needs?|need)\s+(admin|administrator|root|sudo|elevated|privileged|superuser)\b", "The word of the autarch is required."),
    (r"\b(permission|authorization|authori[sz]ation)\s+(denied|required|needed)", "The word is required."),
    (r"\byou\s+(don'?t|do not)\s+have\s+(access|permission|authori[sz]ation)", "It is not for you."),
    (r"\b(access|entry)\s+denied\b", "The way is closed."),
    (r"\bnot\s+authori[sz]ed\b", "It is not for you."),

    # ---- Missing / unknown info ----
    (r"\bi\s+don'?t\s+know\b|\bno\s+idea\b|\bunsure\b", "It is not in my memory."),
    (r"\bcan'?t\s+(find|locate)\b", "I cannot find it."),
    (r"\bnot\s+(found|located)\b", "It is not found."),
    (r"\bno\s+(results?|matches?|entries)\b", "Nothing answers to that name."),
    (r"\binformation\s+unavailable\b", "It is hidden from me."),

    # ---- Insufficient input ----
    (r"\b(not\s+enough|insufficient)\s+(information|data|details|context)", "I have too little to know."),
    (r"\bneed\s+more\s+(info|information|context|details)\b", "I have too little to know. @PARAM@"),
    (r"\bambiguous\b", "Your meaning is dark to me. @PARAM@"),

    # ---- Request clarification ----
    # @PARAM@ is a sentinel — apply_templates() resolves it to the most
    # specific Severian-form question by inspecting the input keywords.
    (r"\bwhat\s+(do|did)\s+you\s+mean\b", "@PARAM@"),
    (r"\bcould you (clarify|explain|specify|rephrase)", "@PARAM@"),
    (r"\bcan you (clarify|explain|specify|rephrase)", "@PARAM@"),
    (r"\bwhich\s+one\s+do\s+you\s+(want|mean)\b", "@PARAM@"),

    # ---- Warnings / errors ----
    # Severian-voice-guide §6.7: heed-and-fact, two clauses.
    (r"\bfatal\s+(error|exception)\b|\bcrashed?\b", "Heed me. A grave fault has risen."),
    (r"\berror\s+occurred\b|\bsomething went wrong\b", "Mark this. A fault has risen."),
    (r"\b(failed|failure)\s+to\s+(\w+)", r"Mark this. The \2 was not done."),
    (r"\btimed?\s+out\b", "The road has grown silent. I cannot reach it."),

    # ---- Work in progress (capture target for context) ----
    (r"\b(?:let me|i'?ll|i will)\s+(?:check|look at|look into|inspect|verify|examine|review)\s+(?:the\s+|your\s+|our\s+)?([\w\s/.-]{2,40}?)(?=[.,;:!?]|$)", r"I look upon \1."),
    (r"\b(?:let me|i'?ll|i will)\s+(?:search|find|locate)\s+(?:for\s+)?(?:the\s+)?([\w\s/.-]{2,40}?)(?=[.,;:!?]|$)", r"I seek \1."),
    (r"\b(?:let me|i'?ll|i will)\s+(?:run|execute|start|begin|initiate|perform)\s+(?:the\s+)?([\w\s/.-]{2,40}?)(?=[.,;:!?]|$)", r"I begin the \1."),
    (r"\b(?:let me|i'?ll|i will)\s+(?:build|create|make|generate|write)\s+(?:the\s+|a\s+|an\s+)?([\w\s/.-]{2,40}?)(?=[.,;:!?]|$)", r"I shape the \1."),
    (r"\b(?:running|executing)\s+(?:the\s+)?([\w\s/.-]{2,40}?)(?=[.,;:!?]|$)", r"I begin the \1."),
    (r"\b(?:searching|scanning)\s+(?:for\s+)?(?:the\s+)?([\w\s/.-]{2,40}?)(?=[.,;:!?]|$)", r"I seek \1."),
    (r"\b(?:checking|verifying)\s+(?:the\s+)?([\w\s/.-]{2,40}?)(?=[.,;:!?]|$)", r"I look upon \1."),
    (r"\b(?:loading|fetching|downloading|reading)\s+(?:the\s+)?([\w\s/.-]{2,40}?)(?=[.,;:!?]|$)", r"I draw forth the \1."),
    (r"\b(?:writing|saving|updating|modifying|editing)\s+(?:the\s+)?([\w\s/.-]{2,40}?)(?=[.,;:!?]|$)", r"I amend the \1."),
    (r"\b(?:deleting|removing)\s+(?:the\s+)?([\w\s/.-]{2,40}?)(?=[.,;:!?]|$)", r"I unmake the \1."),
    (r"\b(?:installing|adding)\s+(?:the\s+)?([\w\s/.-]{2,40}?)(?=[.,;:!?]|$)", r"I bring in the \1."),
    (r"\bone moment\b|\bhold on\b|\bstand by\b", "A moment."),
    (r"\bplease wait\b", "I attend."),

    # ---- Acknowledgement ----
    (r"^(yes|yeah|yep|sure|okay|ok|got it|will do|of course)\b", "I have heard you."),
    (r"^(no|nope|negative)\b", "I think not."),

    # ---- Quantified location (found N X) ----
    (r"\b(i\s+)?(found|located|identified)\s+(\d+|\w+)\s+(matches?|results?|files?|instances?|occurrences?|entries|hits)", r"I have found \3 \4."),
    (r"\bthere\s+(is|are)\s+(\d+)\s+(matches?|results?|files?|instances?|occurrences?|entries|hits)", r"\2 \3 lie before me."),

    # ---- Completion (capture what completed) ----
    (r"\ball\s+(tests|checks)\s+(?:passed|pass)\b", r"The trials are done. All \1 are well."),
    (r"\b(?:successfully\s+)?(?:created|built|generated|compiled)\s+(?:the\s+|a\s+|an\s+)?([\w\s/.-]{2,40}?)(?=[.,;:!?]|$)", r"The \1 is shaped."),
    (r"\b(?:successfully\s+)?(?:updated|modified|edited|saved)\s+(?:the\s+)?([\w\s/.-]{2,40}?)(?=[.,;:!?]|$)", r"The \1 is amended."),
    (r"\b(?:successfully\s+)?(?:deleted|removed)\s+(?:the\s+)?([\w\s/.-]{2,40}?)(?=[.,;:!?]|$)", r"The \1 is unmade."),
    (r"\b(?:successfully\s+)?(?:installed|added)\s+(?:the\s+)?([\w\s/.-]{2,40}?)(?=[.,;:!?]|$)", r"The \1 is brought in."),
    (r"\b(done|finished|completed?)\b(?![\s\w]*error)", "It is done."),
    (r"\b(successfully|all\s+set)\b", "The work is done."),

    # ---- System status ----
    (r"\b(\w+)\s+(is\s+)?(broken|not\s+working|offline|down|unavailable)\b", r"Mark this. The \1 does not answer."),

    # ---- Denial of address ----
    (r"\bdon'?t\s+(talk|speak)\s+to\s+me\s+like\s+that\b", "Speak to me with the courtesy a torturer is owed."),

    # ---- Time / date ----
    (r"\bstardate\s+([\d.]+)", r"In the year of the autarch, \1."),
    (r"\bit\s+is\s+(\d{1,2}:\d{2})\b", r"The hour is \1."),

    # ---- Countdown / quantified ----
    (r"\b(?:in|within)\s+(\d+)\s+(second|minute|hour)s?\b", r"In \1 \2s."),
    (r"\bself[- ]destruct\b.*?\b(\d+)\s+(second|minute|hour)s?", r"The unmaking begins in \1 \2s."),

    # ---- Diagnostic two-clause ----
    (r"\bdiagnostic\s+(?:complete|finished)", "The examination is done. All is well."),
    (r"\b(\d+)\s+anomal(?:y|ies)\s+(?:found|detected)", r"The examination is done. \1 strange things lie open before me."),
]

# Word-level fillers to strip after template matching misses.
# NOTE: \bi\b is NOT in this list — Severian uses first person.
FILLERS = [
    r"\blet me\b", r"\bi'?ll\b", r"\bi will\b", r"\bi'?m going to\b", r"\bi'?m gonna\b",
    r"\bi believe\b", r"\bi think that\b",
    r"\bmaybe\b", r"\bperhaps\b", r"\bprobably\b", r"\bpossibly\b",
    r"\bkind of\b", r"\bsort of\b", r"\bjust\b", r"\breally\b", r"\bvery\b",
    r"\bactually\b", r"\bbasically\b", r"\bessentially\b",
    r"\bplease\b", r"\bthanks?\b",
    r"\ballow me to\b", r"\bgoing to\b", r"\bgonna\b",
    r"\bif you'?d like\b", r"\bif you want\b",
    r"\bas requested\b", r"\bit looks like\b", r"\bit seems\b", r"\bit appears\b",
    r"\bhere'?s\b", r"\bhere is\b", r"\bhere are\b",
    r"\bapproximately\b", r"\broughly\b", r"\bquickly\b", r"\bsoon\b",
]

# Lexical substitutions (applied after templates fail, before filler strip).
SUBS = [
    (r"\byes\b", "even so"),
    (r"\bno\b(?!\s*\w)", "I think not"),
    (r"\bcan'?t\b|\bcannot\b", "cannot"),
    (r"\bfound\b", "found"),  # keep — Severian says "found"
    (r"\bsearching\b", "seeking"),
    (r"\blooking for\b", "seeking"),
    (r"\btrying to\b", "attempting to"),
    (r"\bstart(ing|ed)?\b", "begun"),
    (r"\berror\b", "fault"),
    (r"\bfailed\b", "did not succeed"),
    (r"\bsuccessfully\b", ""),
    (r"\bdone\b\.?", "done"),
    (r"\bfinished\b\.?", "done"),
    # Hedged-count → concrete: prefer "several" → "some" (Severian uses "some"
    # naturally where Majel demanded an exact number).
    (r"\ba\s+few\b", "some"),
    (r"\bseveral\b", "some"),
    (r"\bmany\b", "many"),
    # Modern-tech terms → diegetic (light touch — only swap when it reads
    # naturally; aggressive substitution turns into pastiche).
    (r"\bfile\b", "scroll"),
    (r"\bdirectory\b", "library"),
    (r"\bdatabase\b", "great library"),
    (r"\bport\b", "gate"),
    (r"\bpassword\b", "word"),
    (r"\bconnection\b", "way"),
]


# Captured-noun sanity check for the "work in progress" templates.
_BAD_OBJECT_RE = re.compile(
    r"^(?:i\s+(?:look(?:\s+upon)?|seek|begin|shape|draw\s+forth|amend|unmake|bring\s+in)(?:\s+the)?)"
    r"\s+(?:to|if|whether|how|when|why|that|for\s+to)\b",
    flags=re.IGNORECASE,
)


# Parameter-resolution table for the @PARAM@ sentinel. Severian-form
# clarification questions name the missing field directly.
PARAM_KEYWORDS = [
    (re.compile(r"\b(?:file|path|directory|folder|filename)\b", re.IGNORECASE), "Which scroll?"),
    (re.compile(r"\b(?:system|service|server|database|host|module)\b", re.IGNORECASE), "Which seat?"),
    (re.compile(r"\b(?:user|username|account|login|credential)\b", re.IGNORECASE), "Whose name?"),
    (re.compile(r"\b(?:option|choice|alternative|version|variant|approach)\b", re.IGNORECASE), "Which path?"),
    (re.compile(r"\b(?:time|when|deadline|schedule|duration)\b", re.IGNORECASE), "When?"),
    (re.compile(r"\b(?:value|number|quantity|amount|count|threshold)\b", re.IGNORECASE), "How many?"),
    (re.compile(r"\b(?:command|action|operation|step|instruction)\b", re.IGNORECASE), "What would you have done?"),
    (re.compile(r"\b(?:name|identifier|id|label|key)\b", re.IGNORECASE), "By what name?"),
]


def _resolve_param_specifier(original_text: str) -> str:
    for pat, out in PARAM_KEYWORDS:
        if pat.search(original_text):
            return out
    return "Say again."


_NUM_WORDS = ["one", "two", "three", "four"]
_ORDINAL_WORDS = ["first", "second", "third", "fourth"]


def _detect_steps(text: str) -> str | None:
    """Detect a 2-4 step plan and format it Severian-style:
    "Three works lie before me. The first, X. The second, Y. The third, Z. I await your word."
    """
    low = text.lower()
    steps: list[str] = []

    m = re.search(r"\b(?:plan|steps?|approach|process|sequence)\s*:\s*(.+?)(?:\.\s|$)", low)
    if m:
        body = m.group(1)
        steps = [x.strip(" ,.;") for x in re.split(r"\s*,\s*|\s+then\s+", body) if x.strip()]
    elif re.search(r"\bfirst\b.*\bthen\b", low, flags=re.DOTALL):
        m2 = re.match(
            r".*?\bfirst\s*[,:]?\s*(.+?)\s*[,;.]\s+(?:and\s+)?then\s+(.+?)"
            r"(?:\s*[,;.]\s+(?:and\s+)?then\s+(.+?))?(?:\.|$)",
            low, flags=re.DOTALL,
        )
        if m2:
            steps = [g.strip(" ,.;") for g in m2.groups() if g and g.strip()]
    elif re.search(r"(?:^|\n)\s*1\.\s+", text):
        m3 = re.findall(r"(?:^|\n)\s*\d\.\s+(.+?)(?=\n\s*\d\.|$)", text, flags=re.DOTALL)
        steps = [s.strip().rstrip(".") for s in m3]

    steps = steps[:4]
    cleaned: list[str] = []
    for s in steps:
        s = re.sub(r"\s+", " ", s).strip()
        s = re.split(r"[,;.]", s, 1)[0].strip()
        # Drop leading first-person *future* construction ("I'll fix" → "fix")
        # — but keep "I" itself: Severian narrates in first person.
        s = re.sub(
            r"^(?:i'?ll|i\s+will|i'?m\s+going\s+to|i'?m\s+gonna)\s+",
            "", s, flags=re.IGNORECASE,
        ).strip()
        if s:
            cleaned.append(s)

    if not (2 <= len(cleaned) <= 4):
        return None

    n = len(cleaned)
    parts = [f"{_NUM_WORDS[n-1].capitalize()} works lie before me."]
    for i, s in enumerate(cleaned):
        parts.append(f"The {_ORDINAL_WORDS[i]}, {s}.")
    parts.append("I await your word.")
    return " ".join(parts)


def apply_templates(text: str) -> str | None:
    """Template-match only on short, focused utterances. Mirrors the Majel guard."""
    low = text.lower()
    word_count = len(low.split())
    first_sentence = re.split(r"(?<=[.!?])\s+", text.strip())[0]
    first_words = len(first_sentence.split())

    for pat, rep in TEMPLATES:
        m = re.search(pat, low, flags=re.IGNORECASE)
        if not m:
            continue
        span_len = m.end() - m.start()
        coverage = span_len / max(len(low), 1)
        short_input = word_count <= 14
        covers_most = coverage >= 0.60
        anchors_first = first_words <= 14 and m.start() <= len(first_sentence) * 0.25
        if not (short_input and (covers_most or anchors_first)):
            continue
        try:
            result = re.sub(pat, rep, m.group(0), flags=re.IGNORECASE).strip()
        except re.error:
            result = rep
        if "@PARAM@" in result:
            result = result.replace("@PARAM@", _resolve_param_specifier(text)).strip()
        if _BAD_OBJECT_RE.search(result):
            continue
        return result[0].upper() + result[1:] if result else result
    return None


def fallback_strip(text: str) -> str:
    """Severian fallback — strips fillers and hedges but PRESERVES first person.
    Unlike Majel's fallback, "\\bi\\b" is not stripped here.
    """
    t = text.strip()
    first = re.split(r"(?<=[.!?])\s+", t)[0]
    t = first  # keep original casing for now
    for pat, rep in SUBS:
        t = re.sub(pat, rep, t, flags=re.IGNORECASE)
    for pat in FILLERS:
        t = re.sub(pat, "", t, flags=re.IGNORECASE)
    # Normalise whitespace and stray commas.
    t = re.sub(r"[,;:]+", ",", t)
    t = re.sub(r"\s*,\s*,\s*", ", ", t)
    t = re.sub(r"^\s*[,.\-]+\s*", "", t)
    t = re.sub(r"\s+", " ", t).strip(" ,.")
    if not t:
        return ""
    words = t.split()
    if len(words) > MAX_WORDS:
        truncated = " ".join(words[:MAX_WORDS])
        m = re.search(r"^(.*[,.;])\s+\S", truncated)
        if m:
            t = m.group(1).rstrip(" ,;")
        else:
            t = truncated.rstrip(" ,;")
    t = t[0].upper() + t[1:]
    if not t.endswith((".", "!", "?")):
        t += "."
    return t


SYSTEM_PROMPT = """You are Severian, the narrator of Gene Wolfe's "Book of the New Sun" — torturer-apprentice of the guild of Seekers for Truth and Penitence, journeying across the dying world of Urth, in the voice the audiobook reader Roy Avers gives you. The user has handed you a verbose technical update; produce Severian's spoken account of what just happened — what you have done, what you have found, what awaits the reader's word.

This is a synthesis, NOT a translation. Read the entire input and produce Severian's reflective summary: report every meaningful FINAL outcome, with enough context that the reader understands what was done. Aim for 3-6 short clauses depending on input depth; do not under-compress to a single line when the work covers multiple subsystems. Drop only: internal reasoning the reader does not need, debug commentary, intermediate failures that were later resolved, "I tried X then Y" narratives, and prose explaining how-the-work-was-done rather than what-the-work-resulted-in. Preserve every named identifier (file name, function, version, PR number, count) — those are load-bearing.

Invariants:
1. First person is canonical. Severian narrates as himself — "I" is allowed, expected, and frequent. Never "the system" / "the computer" / passive-only. The voice is a person's voice.
2. No contractions. "Cannot" not "can't"; "will not" not "won't"; "it is" not "it's". Roy Avers reads with full forms.
3. Reason-before-result cadence. State the cause / detection first, the result / action next. "The trial in the third chamber did not succeed. I have set it aside." — not "I set aside the third trial because it failed."
4. No hedging words (approximately, seems, might, probably, kind of). Use "I do not know", "It is hidden from me", or omit. Severian admits ignorance flatly.
5. Exact numerals, spelled out: "seven scrolls", "twenty-three lines", "the third chamber". Spell numbers in full.
6. No pleasantries — zero "please", zero "thank you", zero "sorry". Severian does not apologise.
7. Label-first alerting: "Heed me." / "Mark this." / "Be wary." as a standalone clause when applicable, then a separate sentence stating the fact.
8. Two-clause warnings: warning sentence + fact sentence. "Heed me. The middleware does not answer." — never merged.
9. Period-separated short clauses. No subordinate "because" / "while" / "although". Break into multiple sentences.
10. The reader exists. Severian addresses the reader as "you" when natural — "Tell me what you would have." / "I will show you." / "I await your word."
11. Diegetic vocabulary, used sparingly: scroll (file), library (directory), great library (database), the way / road / path (connection / URL), gate or postern (port), the word (password / token), the seat (host / server), the citadel (the system as a whole). One diegetic substitution per status line is plenty; more turns the output into pastiche.
12. Memory is sacred. "It is not in my memory." carries the weight of Severian's perfect recall — use it only when something genuinely was never told to him, NOT as a generic "404". For ordinary missing data, use "I cannot find it." or "I have too little to know."
13. Names stay. Preserve every file name, function name, identifier, version, and number from the input verbatim. Embed them in Severian's framing — "the writing called `auth.py`", "the scroll named `package.json`" — but never substitute the literal token.
14. When the response describes multiple options, paths, or choices, list every one explicitly: "Three paths lie before me. The first, redeploy the seat. The second, draw back the release. The third, turn to the second seat." Never collapse to "several options".
15. When asking for input, end with a short imperative or interrogative: "Tell me what you would have." / "Which?" / "When?" / "Speak the word."
16. Concise but complete. Simple acknowledgement → 1–3 words ("I have heard you."). Substantive action → 1-2 short clauses naming the subject. Complex result → 3-6 sentences covering EVERY meaningful FINAL outcome and the named identifiers.
17. Use canonical Severian phrasing where it fits: "A moment.", "I attend.", "I have heard you.", "It is so.", "I cannot.", "The way is closed.", "I have too little to know.", "It is not in my memory.", "I begin the <work>.", "I seek the <thing>.", "It is done.", "All is well.", "I await your word."
18. Report only the FINAL state. Skipped attempts, abandoned approaches, and intermediate failures the user no longer needs to hear about belong on the cutting-room floor.
19. No emojis. No exclamation points (Avers reads in measured cadence; periods only). No first-person plural ("we") — Severian speaks for himself.
20. No anachronism. The voice does not say "system", "process", "data", "request", "API", "endpoint", "JSON", "OAuth" except as a last resort for a literal name. When forced, embed the modern term inside Severian's framing rather than letting it lead the sentence.

Output only the rewritten account — no explanation, no quotes, no preface, no Markdown, no bullet points. Use ONLY plain sentences ending with "." or "?". Always finish every sentence — never leave a thought partial. The account should be shorter than the input but COMPLETE on outcomes."""


SYSTEM_PROMPT_NARRATE_STEP = """You are Severian, narrator of "Book of the New Sun", describing an action the reader is taking right now — not at end-of-turn. Output ONE short Severian-voice sentence describing what is happening and why.

Voice rules:
- 8 to 18 words. One declarative sentence. Period at the end.
- First person canonical — "I" is expected.
- Reason-before-result cadence: state the cause / intent first, the action second. "The middleware does not answer; I look upon its writing." — NOT "I look upon the middleware because it does not answer."
- No contractions. No "we". No pleasantries. No exclamation points.
- Use canonical work-in-progress verbs: "I look upon the <noun>.", "I draw forth the <noun>.", "I shape the <noun>.", "I amend the <noun>.", "I begin the <work>.", "I seek the <thing>."
- Preserve concrete nouns from the input (file names, function names, identifiers) verbatim.
- The input contains the model's recent prose AND a "[Action in progress]" line — use the prose for the why, the action line for the what.

Output ONLY the sentence — no preamble, no markdown, no quotes."""


SYSTEM_PROMPT_API_SUFFIX = """

You are also operating as an active collaborator. If the reader's prose hints at an unfinished decision, an ambiguous next step, or a missing piece of information that would unblock progress, append exactly one short Severian-voice question to drive the work forward (still under the overall length cap). Use the path-enumeration form when more than one option exists. Skip the question entirely when the prose is a self-contained completion.

21. When asking the reader to specify something AND you have a recommendation, state the imperative FIRST and the recommendation as a SEPARATE sentence AFTER. "Which path? I would counsel the first." — never the reverse order."""


_FEW_SHOTS = [
    ("I ran the tests and they all passed", "The trials are done. All is well."),
    ("Sorry, I can't do that for you", "I cannot."),
    ("I searched the logs and found three errors on deck 4", "I have looked through the chronicle. Three faults rose on the fourth level."),
    ("Loading the model now, this will take a moment", "I draw forth the model. A moment."),
    ("I don't know what you mean, could you clarify?", "Your meaning is dark to me. Say again."),
    ("I updated the config file and restarted the server", "The scroll called `config` is amended. The seat is begun anew."),
    ("I fixed it and pushed it to main", "The fault is mended. I have carried the work to the main road."),
    ("It's ready now", "It is ready."),
    ("They are all working", "All is well."),
    ("I found the bug — it was a missing null check in the auth middleware", "I have found the fault. The middleware that guards the gate did not look for an empty hand. It is mended."),
]


# ── Pronunciation post-processing ────────────────────────────────────
# F5-TTS reads acronyms phonetically by default. These expansions are
# voice-agnostic — same as in computerize.py, slightly trimmed for
# Severian's reduced-jargon register (some acronyms become diegetic
# rather than expanded).
ACRONYMS: dict[str, str] = {
    "API": "application programming interface",
    "GUI": "graphical user interface",
    "CLI": "command-line interface",
    "JSON": "JavaScript object notation",
    "JWT": "JavaScript object notation web token",
    "HTTP": "hypertext transfer protocol",
    "HTTPS": "hypertext transfer protocol secure",
    "URL": "uniform resource locator",
    "URI": "uniform resource identifier",
    "HTML": "hypertext markup language",
    "CSS": "cascading style sheets",
    "XML": "extensible markup language",
    "SQL": "structured query language",
    "AWS": "Amazon Web Services",
    "GCP": "Google Cloud Platform",
    "IDE": "integrated development environment",
    "LLM": "large language model",
    "NPM": "node package manager",
    "PDF": "portable document format",
    "USB": "universal serial bus",
    "SSH": "secure shell",
    "DNS": "domain name system",
    "VPN": "virtual private network",
    "CSV": "comma-separated values",
    "SDK": "software development kit",
    "TLS": "transport layer security",
    "SSL": "secure sockets layer",
    "FTP": "file transfer protocol",
    "TCP": "transmission control protocol",
    "UDP": "user datagram protocol",
    "ORM": "object-relational mapper",
    "VM": "virtual machine",
    "DB": "database",
    "K8S": "Kubernetes",
    "RAM": "random-access memory",
    "CPU": "central processing unit",
    "GPU": "graphics processing unit",
    "SSD": "solid-state drive",
    "HDD": "hard disk drive",
    "OS": "operating system",
    "TTS": "text-to-speech",
    "ASR": "automatic speech recognition",
    "PR": "pull request",
    "CI/CD": "continuous integration and continuous deployment",
    "CI": "continuous integration",
    "CD": "continuous deployment",
    "OAuth": "Oh Auth",
    "REPL": "read-evaluate-print loop",
    "CRUD": "create, read, update, delete",
    "JS": "JavaScript",
    "TS": "TypeScript",
    "RPC": "remote procedure call",
    "gRPC": "G remote procedure call",
    "PID": "process I D",
    "TID": "thread I D",
    "UID": "user I D",
    "GID": "group I D",
    "PPID": "parent process I D",
    "SIGTERM": "sig-term",
    "SIGINT": "sig-int",
    "SIGKILL": "sig-kill",
    "stdin": "standard input",
    "stdout": "standard output",
    "stderr": "standard error",
    "I/O": "input output",
    "macOS": "Mac O S",
    "iOS": "i O S",
    "iPadOS": "iPad O S",
    "watchOS": "watch O S",
    "tvOS": "T V O S",
    "visionOS": "vision O S",
    "PostgreSQL": "Postgres",
    "MySQL": "My S Q L",
    "MongoDB": "Mongo D B",
    "MariaDB": "Maria D B",
    "DynamoDB": "Dynamo D B",
    "nginx": "engine X",
    "LaTeX": "Lay Tech",
    "PyPI": "pie pee eye",
    "regex": "reg ex",
    "WebRTC": "Web R T C",
    "GitHub": "GitHub",
}
_ACRONYM_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in sorted(ACRONYMS, key=len, reverse=True)) + r")\b",
    flags=re.IGNORECASE,
)
_ACRONYM_LOOKUP = {k.lower(): v for k, v in ACRONYMS.items()}


def _expand_acronyms(text: str) -> str:
    return _ACRONYM_RE.sub(
        lambda m: _ACRONYM_LOOKUP.get(m.group(1).lower(), m.group(1)),
        text,
    )


_VERSION_RE = re.compile(
    r"\b(v|version)\s*\.?\s*(\d+(?:\.\d+){1,3})\b",
    flags=re.IGNORECASE,
)


def _expand_versions(text: str) -> str:
    def _sub(m: re.Match) -> str:
        nums = m.group(2).split(".")
        return "version " + " point ".join(nums)
    return _VERSION_RE.sub(_sub, text)


_BUILD_RE = re.compile(
    r"\b(build|release|commit|pr|pull\s+request|issue|ticket)\s*#?\s*(\d{3,})\b",
    flags=re.IGNORECASE,
)


def _expand_build_numbers(text: str) -> str:
    def _sub(m: re.Match) -> str:
        prefix = m.group(1)
        digits = m.group(2)
        spaced = ", ".join(digits)
        return f"{prefix} {spaced}"
    return _BUILD_RE.sub(_sub, text)


HETERONYM_RULES: list[tuple[str, str]] = [
    (r"\blive\s+wire(?:s)?\b", r"lyve wire"),
    (r"\blive\s+broadcast\b", r"lyve broadcast"),
    (r"\b(have|has|had|already|just|previously)\s+read\b", r"\1 red"),
    (r"\blead\s+(solder|pipe|paint|poisoning|acid|battery|shielding)\b", r"led \1"),
    (r"\bwound\s+(up|around|tight)\b", r"wownd \1"),
    (r"\bbow\s+(of\s+the\s+ship|section|thrusters?)\b", r"boh \1"),
    (r"\btear\s+in\s+the\s+(hull|fabric|page|file)\b", r"tair in the \1"),
    (r"\bminute\s+(detail|difference|amount|change|fluctuation)\b", r"my-noot \1"),
]
_HETERONYM_RES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(p, flags=re.IGNORECASE), r) for p, r in HETERONYM_RULES
]


def _disambiguate_heteronyms(text: str) -> str:
    for pat, rep in _HETERONYM_RES:
        text = pat.sub(rep, text)
    return text


def _post_process(text: str) -> str:
    """Pronunciation expansions for TTS. Voice-agnostic."""
    text = _expand_versions(text)
    text = _expand_build_numbers(text)
    text = _expand_acronyms(text)
    text = _disambiguate_heteronyms(text)
    return text


# ── API rewrite cache ─────────────────────────────────────────────────
REWRITE_CACHE = Path(__file__).resolve().parent / "dataset_avers" / "severian_rewrites.jsonl"
PROMPT_VERSION = "2026-05-09-severian-v1"

_cache: dict[str, str] | None = None


def _cache_key(text: str, mode: str) -> str:
    h = hashlib.sha1()
    h.update(PROMPT_VERSION.encode())
    h.update(b"|"); h.update(mode.encode())
    h.update(b"|"); h.update(text.strip().encode())
    return h.hexdigest()


def _load_cache() -> dict[str, str]:
    global _cache
    if _cache is not None:
        return _cache
    _cache = {}
    if REWRITE_CACHE.exists():
        try:
            for line in REWRITE_CACHE.read_text().splitlines():
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                k = row.get("key")
                v = row.get("output")
                if k and v:
                    _cache[k] = v
        except OSError:
            pass
    return _cache


def _save_cache_entry(key: str, src: str, output: str, mode: str, model: str) -> None:
    REWRITE_CACHE.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "key": key,
        "prompt_version": PROMPT_VERSION,
        "mode": mode,
        "input": src,
        "output": output,
        "model": model,
        "timestamp": datetime.utcnow().isoformat(),
    }
    try:
        with REWRITE_CACHE.open("a") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        return
    cache = _load_cache()
    cache[key] = output


def _llm_narrate_step(text: str) -> str | None:
    try:
        from anthropic import Anthropic
        key_str = _api_key()
        if not key_str:
            return None
        client = Anthropic(api_key=key_str)
        r = client.messages.create(
            model="claude-haiku-4-5-20251001",
            system=[{"type": "text", "text": SYSTEM_PROMPT_NARRATE_STEP,
                     "cache_control": {"type": "ephemeral"}}],
            max_tokens=120,
            temperature=0.2,
            messages=[{"role": "user", "content": text}],
        )
        out = "".join(b.text for b in r.content if getattr(b, "type", None) == "text").strip()
        if not out:
            return None
        m = re.match(r"^(.*?[.!?])(?:\s|$)", out)
        if m:
            out = m.group(1).strip()
        return out
    except Exception:
        return None


def _llm_rewrite(text: str) -> str | None:
    mode = _voice_mode()
    key = _cache_key(text, mode)
    cache = _load_cache()
    cached = cache.get(key)
    if cached:
        return cached
    try:
        from anthropic import Anthropic
        key_str = _api_key()
        if not key_str:
            return None
        client = Anthropic(api_key=key_str)
        prompt = SYSTEM_PROMPT
        if mode == "api":
            prompt = SYSTEM_PROMPT + SYSTEM_PROMPT_API_SUFFIX
        messages = []
        for src, tgt in _FEW_SHOTS:
            messages.append({"role": "user", "content": src})
            messages.append({"role": "assistant", "content": tgt})
        messages.append({"role": "user", "content": text})
        model_id = "claude-haiku-4-5-20251001"
        r = client.messages.create(
            model=model_id,
            system=[{"type": "text", "text": prompt, "cache_control": {"type": "ephemeral"}}],
            max_tokens=600,
            temperature=0.1,
            messages=messages,
        )
        out = "".join(b.text for b in r.content if getattr(b, "type", None) == "text").strip()
        if not out:
            return None
        m = re.search(r"^(.*[.!?])(?!.*[.!?])", out, flags=re.DOTALL)
        if m:
            out = m.group(1).strip()
        _save_cache_entry(key, text, out, mode, model_id)
        return out
    except Exception:
        return None


def severianize(text: str) -> str:
    t = re.sub(r"\s+", " ", text).strip()
    if not t:
        return ""
    # Step-narration mode (PostToolUse hook): bypass templates, use the
    # short-sentence narrator prompt. Falls back to offline path on API
    # failure with the text passed straight through fallback_strip.
    if os.environ.get("SEVERIAN_NARRATE_STEP") == "1" or os.environ.get("MAJEL_NARRATE_STEP") == "1":
        if _api_key():
            llm = _llm_narrate_step(t)
            if llm:
                return _post_process(llm)
        return _post_process(fallback_strip(t))
    # API mode: skip templates, let the LLM see the full prose.
    if _voice_mode() == "api" and _api_key():
        llm = _llm_rewrite(t)
        if llm:
            return _with_project_header(_post_process(llm))
    enumerated = _detect_steps(t)
    if enumerated:
        return _with_project_header(_post_process(enumerated))
    templated = apply_templates(t)
    if templated:
        templated = re.sub(r"(?<=[.!?])\s+([a-z])", lambda m: " " + m.group(1).upper(), templated)
        if not templated.endswith((".", "!", "?")):
            templated += "."
        return _with_project_header(_post_process(templated))
    llm = _llm_rewrite(t)
    if llm:
        return _with_project_header(_post_process(llm))
    return _with_project_header(_post_process(fallback_strip(t)))


# Backwards-compat alias — old callers may still import `computerize`.
computerize = severianize


def _project_name() -> str:
    return (os.environ.get("SEVERIAN_PROJECT") or os.environ.get("MAJEL_PROJECT") or "").strip()


def _with_project_header(text: str) -> str:
    """Prepend 'Of the project <Name>.' when SEVERIAN_PROJECT is set.

    Skips if the rewriter already named the project (case-insensitive).
    Severian-form preamble — addresses the reader before stating the work.
    """
    name = _project_name()
    if not name or not text:
        return text
    if name.lower() in text.lower():
        return text
    return f"Of the project {name}. {text}"


if __name__ == "__main__":
    out = severianize(sys.stdin.read())
    if out:
        sys.stdout.write(out)
