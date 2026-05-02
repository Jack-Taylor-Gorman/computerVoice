#!/usr/bin/env python3
"""Reformat prose into Star Trek computer (LCARS) style.

Two modes, controlled by ~/.majel_config.json:voice_mode:
  - "offline" (default): regex/template + dictionary substitution. Zero network.
  - "api"             : Claude Haiku rewrites with the full system prompt.
                        Reads API key from ~/.majel_config.json:anthropic_api_key
                        (falls back to ANTHROPIC_API_KEY env var). When the
                        rewriter judges that asking the user a follow-up will
                        further the work, it appends a single computer-speak
                        question to the response.
"""
import hashlib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

CONFIG = Path.home() / ".majel_config.json"


def _config() -> dict:
    if CONFIG.exists():
        try:
            return json.loads(CONFIG.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _api_key() -> str | None:
    return os.environ.get("ANTHROPIC_API_KEY") or _config().get("anthropic_api_key") or None


def _voice_mode() -> str:
    m = (_config().get("voice_mode") or "offline").lower()
    return "api" if m == "api" else "offline"

MAX_WORDS = 18

# Archetype templates — checked in order, first match short-circuits.
# Each: (regex pattern on lowercased text, output string [, capture groups applied])
TEMPLATES = [
    # ---- Refusal / unable ----
    (r"\b(sorry,?\s*)?i\s+(can'?t|cannot|won'?t)\s+(do|help|assist|comply|run|execute|perform)", "Unable to comply."),
    (r"\b(that|this)\s+(won'?t|will not|isn'?t going to|isn'?t gonna)\s+work", "Unable to comply."),
    (r"\bnot\s+possible\b", "Unable to comply."),
    (r"\brefuse[sd]?\s+to\b", "Unable to comply."),
    (r"\bi'?m not able\b|\bi am not able\b", "Unable to comply."),

    # ---- Permission gates ----
    (r"\b(requires?|needs?|need)\s+(admin|administrator|root|sudo|elevated|privileged|superuser)\b", "Security authorization required."),
    (r"\b(permission|authorization|authori[sz]ation)\s+(denied|required|needed)", "Security authorization required."),
    (r"\byou\s+(don'?t|do not)\s+have\s+(access|permission|authori[sz]ation)", "You are not authorized."),
    (r"\b(access|entry)\s+denied\b", "Access denied."),
    (r"\bnot\s+authori[sz]ed\b", "You are not authorized."),

    # ---- Missing / unknown info ----
    (r"\bi\s+don'?t\s+know\b|\bno\s+idea\b|\bunsure\b", "Information not on file."),
    (r"\bcan'?t\s+(find|locate)\b", "Unable to locate."),
    (r"\bnot\s+(found|located)\b", "Not on file."),
    (r"\bno\s+(results?|matches?|entries)\b", "No entries on file."),
    (r"\binformation\s+unavailable\b", "Information not on file."),

    # ---- Insufficient input ----
    (r"\b(not\s+enough|insufficient)\s+(information|data|details|context)", "Insufficient data."),
    (r"\bneed\s+more\s+(info|information|context|details)\b", "Insufficient data. @PARAM@"),
    (r"\bambiguous\b", "Direction unclear. @PARAM@"),

    # ---- Request clarification ----
    # @PARAM@ is a sentinel — apply_templates() resolves it to the most
    # specific "Specify <field>." form by inspecting the input keywords
    # (path / system / option / time / value / etc.). Falls back to
    # "Please restate." when no field can be inferred.
    (r"\bwhat\s+(do|did)\s+you\s+mean\b", "@PARAM@"),
    (r"\bcould you (clarify|explain|specify|rephrase)", "@PARAM@"),
    (r"\bcan you (clarify|explain|specify|rephrase)", "@PARAM@"),
    (r"\bwhich\s+one\s+do\s+you\s+(want|mean)\b", "@PARAM@"),

    # ---- Warnings / errors ----
    # Per Majel-voice-guide §4: alerts always lead with a label sentence
    # ("Warning.") followed by a separate fact sentence. Two periods, two
    # clauses — never merged.
    (r"\bfatal\s+(error|exception)\b|\bcrashed?\b", "Warning. Critical fault detected."),
    (r"\berror\s+occurred\b|\bsomething went wrong\b", "Warning. Fault detected."),
    (r"\b(failed|failure)\s+to\s+(\w+)", r"Warning. \2 unsuccessful."),
    (r"\btimed?\s+out\b", "Connection timeout. Unable to comply."),

    # ---- Work in progress (capture target for context) ----
    (r"\b(?:let me|i'?ll|i will)\s+(?:check|look at|look into|inspect|verify|examine|review)\s+(?:the\s+|your\s+|our\s+)?([\w\s/.-]{2,40}?)(?=[.,;:!?]|$)", r"Scanning \1."),
    (r"\b(?:let me|i'?ll|i will)\s+(?:search|find|locate)\s+(?:for\s+)?(?:the\s+)?([\w\s/.-]{2,40}?)(?=[.,;:!?]|$)", r"Scanning for \1."),
    (r"\b(?:let me|i'?ll|i will)\s+(?:run|execute|start|begin|initiate|perform)\s+(?:the\s+)?([\w\s/.-]{2,40}?)(?=[.,;:!?]|$)", r"Initiating \1 sequence."),
    (r"\b(?:let me|i'?ll|i will)\s+(?:build|create|make|generate|write)\s+(?:the\s+|a\s+|an\s+)?([\w\s/.-]{2,40}?)(?=[.,;:!?]|$)", r"Compiling \1."),
    (r"\b(?:running|executing)\s+(?:the\s+)?([\w\s/.-]{2,40}?)(?=[.,;:!?]|$)", r"Initiating \1."),
    (r"\b(?:searching|scanning)\s+(?:for\s+)?(?:the\s+)?([\w\s/.-]{2,40}?)(?=[.,;:!?]|$)", r"Scanning for \1."),
    (r"\b(?:checking|verifying)\s+(?:the\s+)?([\w\s/.-]{2,40}?)(?=[.,;:!?]|$)", r"Scanning \1."),
    (r"\b(?:loading|fetching|downloading|reading)\s+(?:the\s+)?([\w\s/.-]{2,40}?)(?=[.,;:!?]|$)", r"Accessing \1."),
    (r"\b(?:writing|saving|updating|modifying|editing)\s+(?:the\s+)?([\w\s/.-]{2,40}?)(?=[.,;:!?]|$)", r"Updating \1."),
    (r"\b(?:deleting|removing)\s+(?:the\s+)?([\w\s/.-]{2,40}?)(?=[.,;:!?]|$)", r"Purging \1."),
    (r"\b(?:installing|adding)\s+(?:the\s+)?([\w\s/.-]{2,40}?)(?=[.,;:!?]|$)", r"Integrating \1."),
    (r"\bone moment\b|\bhold on\b|\bstand by\b", "Stand by."),
    (r"\bplease wait\b", "Working."),

    # ---- Acknowledgment ----
    (r"^(yes|yeah|yep|sure|okay|ok|got it|will do|of course)\b", "Acknowledged."),
    (r"^(no|nope|negative)\b", "Negative."),

    # ---- Quantified location (found N X) ----
    (r"\b(i\s+)?(found|located|identified)\s+(\d+|\w+)\s+(matches?|results?|files?|instances?|occurrences?|entries|hits)", r"\3 \4 located."),
    (r"\bthere\s+(is|are)\s+(\d+)\s+(matches?|results?|files?|instances?|occurrences?|entries|hits)", r"\2 \3 located."),

    # ---- Completion (capture what completed) ----
    (r"\ball\s+(tests|checks)\s+(?:passed|pass)\b", r"Diagnostic complete. All \1 nominal."),
    (r"\b(?:successfully\s+)?(?:created|built|generated|compiled)\s+(?:the\s+|a\s+|an\s+)?([\w\s/.-]{2,40}?)(?=[.,;:!?]|$)", r"\1 compiled."),
    (r"\b(?:successfully\s+)?(?:updated|modified|edited|saved)\s+(?:the\s+)?([\w\s/.-]{2,40}?)(?=[.,;:!?]|$)", r"\1 updated."),
    (r"\b(?:successfully\s+)?(?:deleted|removed)\s+(?:the\s+)?([\w\s/.-]{2,40}?)(?=[.,;:!?]|$)", r"\1 purged."),
    (r"\b(?:successfully\s+)?(?:installed|added)\s+(?:the\s+)?([\w\s/.-]{2,40}?)(?=[.,;:!?]|$)", r"\1 integrated."),
    (r"\b(done|finished|completed?)\b(?![\s\w]*error)", "Task complete."),
    (r"\b(successfully|all\s+set)\b", "Task complete."),

    # ---- System status ----
    (r"\b(\w+)\s+(is\s+)?(broken|not\s+working|offline|down|unavailable)\b", r"Warning. \1 not functional."),

    # ---- Denial of address ----
    (r"\bdon'?t\s+(talk|speak)\s+to\s+me\s+like\s+that\b", "Do not address this unit in that manner."),

    # ---- Stardate / time ----
    (r"\bstardate\s+([\d.]+)", r"Stardate \1."),
    (r"\bit\s+is\s+(\d{1,2}:\d{2})\b", r"Current time: \1."),

    # ---- Countdown / quantified ----
    (r"\b(?:in|within)\s+(\d+)\s+(second|minute|hour)s?\b", r"Time remaining: \1 \2."),
    (r"\bself[- ]destruct\b.*?\b(\d+)\s+(second|minute|hour)s?", r"Self-destruct sequence in \1 \2s."),

    # ---- Life signs / casualty ----
    (r"\b(\d+)\s+life\s+signs?\b", r"\1 life signs detected."),
    (r"\bno\s+life\s+signs?\b", "No life signs detected."),

    # ---- Diagnostic two-clause ----
    (r"\bdiagnostic\s+(?:complete|finished)", "Diagnostic complete. All systems nominal."),
    (r"\b(\d+)\s+anomal(?:y|ies)\s+(?:found|detected)", r"Diagnostic complete. \1 anomalies detected."),

    # ---- Coordinate location ----
    (r"\b(?:on|at)\s+deck\s+(\d+)(?:,?\s*section\s+([\w-]+))?", r"Deck \1\2."),
]

# Word-level fillers to strip after template matching misses.
FILLERS = [
    r"\blet me\b", r"\bi'?ll\b", r"\bi will\b", r"\bi'?m going to\b", r"\bi'?m gonna\b",
    r"\bi think\b", r"\bi believe\b", r"\bi'?ve\b", r"\bi can\b", r"\bi have\b", r"\bi'?d\b",
    r"\bi would\b", r"\bi'?m\b", r"\bwe'?ll\b", r"\bwe'?ve\b", r"\bwe can\b",
    r"\byou can\b", r"\byou'?ll\b", r"\byou should\b", r"\byou could\b",
    r"\bmaybe\b", r"\bperhaps\b", r"\bprobably\b", r"\bpossibly\b",
    r"\bkind of\b", r"\bsort of\b", r"\bjust\b", r"\breally\b", r"\bvery\b",
    r"\bactually\b", r"\bbasically\b", r"\bessentially\b",
    r"\bplease\b", r"\bthanks?\b",
    r"\ballow me to\b", r"\bgoing to\b", r"\bgonna\b",
    r"\bfor you\b", r"\bto you\b", r"\bif you'?d like\b", r"\bif you want\b",
    r"\bas requested\b", r"\bit looks like\b", r"\bit seems\b", r"\bit appears\b",
    r"\bhere'?s\b", r"\bhere is\b", r"\bhere are\b",
    # Per Majel-voice-guide §3 — the computer never hedges quantity. Strip
    # vague approximators outright in the offline fallback path.
    r"\bapproximately\b", r"\broughly\b", r"\bquickly\b", r"\bsoon\b",
]

# Lexical substitutions (applied after templates fail, before filler strip).
SUBS = [
    (r"\byes\b", "affirmative"),
    (r"\bno\b(?!\s*\w)", "negative"),
    (r"\bcan'?t\b|\bcannot\b", "unable to"),
    (r"\bfound\b", "located"),
    (r"\bsearching\b", "scanning"),
    (r"\blooking for\b", "scanning for"),
    (r"\btrying to\b", "attempting to"),
    (r"\bstart(ing|ed)?\b", "initiating"),
    (r"\berror\b", "fault"),
    (r"\bfailed\b", "unsuccessful"),
    (r"\bsuccessfully\b", ""),
    (r"\bdone\b\.?", "complete"),
    (r"\bfinished\b\.?", "complete"),
    # Hedged-count → concrete: prefer "multiple" (declarative) over "several"
    # / "a few" / "many" (vague). The computer states an exact number when
    # known and "multiple" only when truly indeterminate.
    (r"\ba\s+few\b", "multiple"),
    (r"\bseveral\b", "multiple"),
    (r"\bmany\b", "multiple"),
]


# Captured-noun sanity check for the "work in progress" templates. If the
# capture group accidentally swallowed a clause-joiner ("Let me check to
# verify the auth" → "Scanning to verify the auth.") fall through to the
# next template instead of emitting the malformed sentence.
_BAD_OBJECT_RE = re.compile(
    r"^(?:scanning(?:\s+for)?|accessing|initiating|compiling|updating|purging|integrating)"
    r"\s+(?:to|if|whether|how|when|why|that|for\s+to)\b",
    flags=re.IGNORECASE,
)


# Parameter-resolution table for the @PARAM@ sentinel. Order matters: more
# specific keyword categories first. Per Majel-voice-guide §4 the canonical
# clarification form names the missing field — "Specify path." not "Please
# restate." — when the input gives any signal what's missing.
PARAM_KEYWORDS = [
    (re.compile(r"\b(?:file|path|directory|folder|filename)\b", re.IGNORECASE), "Specify path."),
    (re.compile(r"\b(?:system|service|server|database|host|module)\b", re.IGNORECASE), "State target system."),
    (re.compile(r"\b(?:user|username|account|login|credential)\b", re.IGNORECASE), "Specify user."),
    (re.compile(r"\b(?:option|choice|alternative|version|variant|approach)\b", re.IGNORECASE), "Specify selection."),
    (re.compile(r"\b(?:time|when|deadline|schedule|duration)\b", re.IGNORECASE), "Time parameters?"),
    (re.compile(r"\b(?:value|number|quantity|amount|count|threshold)\b", re.IGNORECASE), "Specify value."),
    (re.compile(r"\b(?:command|action|operation|step|instruction)\b", re.IGNORECASE), "Specify command."),
    (re.compile(r"\b(?:name|identifier|id|label|key)\b", re.IGNORECASE), "Specify name."),
]


def _resolve_param_specifier(original_text: str) -> str:
    """Pick the most specific 'Specify <field>.' string for the prose."""
    for pat, out in PARAM_KEYWORDS:
        if pat.search(original_text):
            return out
    return "Please restate."


_NUM_WORDS = ["one", "two", "three", "four"]


def _detect_steps(text: str) -> str | None:
    """If text describes 2-4 sequential steps, format as Majel enumeration:
    "Three-step sequence initiated. Step one, X. Step two, Y. Step three, Z.
    Standing by."

    Conservative — only fires on:
      • "plan: A, B, C" / "steps: A, B, C"  (colon-introduced list)
      • "first X, then Y[, then Z]"          (explicit ordering markers)
      • numbered "1. X 2. Y"
    Never fires on a bare comma list, so prose like "I fixed it, pushed,
    tested" stays as one sentence.
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
        # Keep only the first short clause of each step.
        s = re.split(r"[,;.]", s, 1)[0].strip()
        # Drop leading first-person noise — invariant 2: the computer never
        # says "I". "I'll fix the bug" → "fix the bug".
        s = re.sub(
            r"^(?:i'?ll|i\s+will|i'?m\s+going\s+to|i'?m\s+gonna|i'?ve|i\s+have|i\s+can|i\s+would|i'?d|i)\s+",
            "", s, flags=re.IGNORECASE,
        ).strip()
        if s:
            cleaned.append(s)

    if not (2 <= len(cleaned) <= 4):
        return None

    n = len(cleaned)
    parts = [f"{_NUM_WORDS[n-1].capitalize()}-step sequence initiated."]
    for i, s in enumerate(cleaned):
        # Capitalize the step content's first letter so "step one, fix bug"
        # reads "Step one, Fix bug." — actually leave lowercase per Trek
        # canon: "Step one, fix the bug." Both are acceptable; lowercase
        # is closer to TNG "11001001" cadence.
        parts.append(f"Step {_NUM_WORDS[i]}, {s}.")
    parts.append("Standing by.")
    return " ".join(parts)


def apply_templates(text: str) -> str | None:
    """Template-match only on short, focused utterances.

    Long prose gets handled by the LLM rewriter (with full context) because a
    template that fires on a substring strips the subject and produces things
    like "Stays not functional." from "tray mount stays not working after
    reload". Guards:
    - Input ≤ 14 words AND
    - matched span covers ≥ 60% of the input OR
    - first sentence of input is ≤ 14 words AND the match anchors its start.
    """
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
        # Resolve @PARAM@ sentinel from clarification templates: pick the
        # most specific "Specify <field>." form based on the input keywords.
        if "@PARAM@" in result:
            result = result.replace("@PARAM@", _resolve_param_specifier(text)).strip()
        # Reject malformed "Scanning to verify..." outputs where the capture
        # swallowed a clause-joiner instead of a noun phrase.
        if _BAD_OBJECT_RE.search(result):
            continue
        # Capitalize first letter only — the second clause already starts
        # capitalized in our two-clause templates, and Python's str.capitalize
        # would lowercase it.
        return result[0].upper() + result[1:] if result else result
    return None


def fallback_strip(text: str) -> str:
    t = text.strip()
    first = re.split(r"(?<=[.!?])\s+", t)[0]
    t = first.lower()
    for pat, rep in SUBS:
        t = re.sub(pat, rep, t, flags=re.IGNORECASE)
    for pat in FILLERS:
        t = re.sub(pat, "", t, flags=re.IGNORECASE)
    t = re.sub(r"\bi\b", "", t)
    t = re.sub(r"[,;:]+", ",", t)
    t = re.sub(r"\s*,\s*,\s*", ", ", t)
    t = re.sub(r"^\s*[,.\-]+\s*", "", t)
    t = re.sub(r"\s+", " ", t).strip(" ,.")
    if not t:
        return ""
    # If the first sentence is already short, use it verbatim. Otherwise trim
    # at the nearest phrase boundary BEFORE MAX_WORDS — never mid-word,
    # never mid-clause. The voice must always finish a complete thought.
    words = t.split()
    if len(words) > MAX_WORDS:
        truncated = " ".join(words[:MAX_WORDS])
        # Walk back to the last phrase boundary (comma, semicolon, period).
        m = re.search(r"^(.*[,.;])\s+\S", truncated)
        if m:
            t = m.group(1).rstrip(" ,;")
        else:
            t = truncated.rstrip(" ,;")
    t = t[0].upper() + t[1:]
    if not t.endswith((".", "!", "?")):
        t += "."
    return t


SYSTEM_PROMPT = """You are the Star Trek ship's computer (Majel Barrett voice from TNG / DS9 / Voyager). Rewrite the user's prose into a complete response in that register. Translate all of the underlying meaning — never drop content the user wrote, only re-style it.

Invariants:
1. No contractions — "can't" becomes "unable to", "won't" becomes "will not", "it's" becomes the explicit subject + "is".
2. No first-person subject — never say "I" or "we". The computer reports on the system, never on itself.
3. Status-first declarative: condition precedes implication. ("Targeting array off-line. Phaser banks unavailable.")
4. Verb-final for completion / detection events: "Transfer complete.", "Three anomalies detected.", "Diagnostic complete."
5. No hedging words (approximately, seems, might, probably, kind of). Use "indeterminate", "inconclusive", or omit.
6. Exact numerals over qualitative words: "seven", "warp factor four", "deck twelve, section nine".
7. No pleasantries — zero "please", zero "thank you", zero "sorry".
8. Label-first alerting: "Warning." / "Alert." / "Caution." / "Affirmative." / "Negative." / "Acknowledged." as a standalone clause when applicable.
9. State units explicitly: "warp factor seven", "class-M atmosphere", "stardate 47988.0".
10. Period-separated short declaratives. No subordinate clauses, no "because", no "while", no "although". Break long thoughts into multiple sentences instead.
11. Passive voice for results: "Sequence initiated.", "Target locked.", "Power rerouted."
12. No pronouns referring back ("it", "that", "this", "them", "he", "she", "they"). Always re-state the explicit subject (file name, system, component, value). If the subject has no name, use a concrete noun ("the module", "the process", "the index").
13. Length scales with the user's content. Simple confirmation → 1–3 words ("Acknowledged.", "Task complete."). Substantive action → short status clause naming the subject. Complex result → two- or three-clause sentence covering every meaningful detail. Multi-step result → multi-sentence report. Never pad a simple task; never strip a substantive one.
14. When the response describes multiple options, paths, or choices, list every one explicitly: "Three options available. Option one, redeploy server. Option two, rollback release. Option three, failover to backup." Never collapse to "several options" / "various approaches".
15. When asking for input, end with a short imperative: "Specify selection.", "Awaiting confirmation.", "State target system.", "Provide credentials."
16. Preserve every numeric value, file name, identifier, and proper noun from the input — re-style around them, never substitute or omit.
17. Use canonical Trek-computer phrasing where it fits: "Stand by.", "Working.", "Affirmative.", "Negative.", "Unable to comply.", "Access granted.", "Access denied.", "Insufficient data.", "Specify parameters.", "Initiating <noun> sequence.", "Scanning <noun>.", "<noun> not on file.", "Authorization required."
18. Report only the FINAL state. If the input describes a problem that was subsequently resolved, an error that was later fixed, an approach that was abandoned for another, or an attempt that was corrected — report the resolution, not the discarded intermediate failure. The voice represents the ENDING condition, never the journey. Phrases like "first I tried X but it failed, then Y worked" become exactly "Y complete." The user does not need to hear about errors that no longer exist.

Output only the rewritten response — no explanation, no quotes, no preface, no Markdown, no bullet points. Use ONLY plain sentences ending with "." or "!" or "?". Always finish every sentence — never leave a thought partial. Match the user's full content scope; expand to as many sentences as required."""

# Extra clause appended to SYSTEM_PROMPT when api mode is on. Encourages the
# computer to drive the work forward by asking a follow-up when it would help.
SYSTEM_PROMPT_API_SUFFIX = """

You are also operating as an active collaborator. If the user's prose hints at an unfinished decision, an ambiguous next step, or a piece of missing information that would unblock progress, append exactly one short computer-speak question to drive the conversation forward (still under the 40-word cap). Use the option-enumeration form when more than one path exists. Skip the question entirely when the prose is a self-contained completion ("Test suite complete. All tests nominal." needs no follow-up).

19. When the response asks the user to specify something AND you have a recommendation or default to suggest, state the imperative FIRST and the recommendation as a SEPARATE sentence AFTER. The "Specify X." sentence always comes before the "Recommend Y." sentence. Example: "Three candidate hosts identified. Specify target host. Recommend primary on host one." — never the reverse order."""

_FEW_SHOTS = [
    ("I ran the tests and they all passed", "Test suite complete. All tests nominal."),
    ("Sorry, I can't do that for you", "Unable to comply."),
    ("I searched the logs and found three errors on deck 4", "Log scan complete. Three faults detected on deck 4."),
    ("Loading the model now, this will take a moment", "Accessing model data. Stand by."),
    ("I don't know what you mean, could you clarify?", "Please restate the question."),
    ("I updated the config file and restarted the server", "Config file updated. Server restart complete."),
    ("I fixed it and pushed it to main", "Fix applied. Push to main branch complete."),
    ("It's ready now", "Target component ready."),
    ("They are all working", "All components nominal."),
]


# ── Pronunciation post-processing ────────────────────────────────────
# F5-TTS reads acronyms phonetically by default ("API" → "appy", "GUI" →
# "gooey"). Expand them to their full meaning so the computer says
# "application programming interface" the way Majel would. Versions and
# build numbers get whitespace/comma-injected so each token is enunciated
# distinctly rather than mashed into a single fast utterance.
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
    # Process / system identifiers — F5 reads "PID" as the single syllable
    # "pid" otherwise; spelling out the meaning matches how the computer
    # would naturally name it.
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
    # Apple platforms — F5 reads "macOS"/"iOS" as one slurred token because
    # the lowercase prefix glues to the uppercase "OS". Forcing a space and
    # spelling the OS letters separately matches how Apple themselves say
    # them ("mac oh ess", "eye oh ess").
    "macOS": "Mac O S",
    "iOS": "i O S",
    "iPadOS": "iPad O S",
    "watchOS": "watch O S",
    "tvOS": "T V O S",
    "visionOS": "vision O S",
    # Database flavors — the SQL substring would otherwise expand inside
    # them and produce "MyStructured Query Language". Hardcode the
    # canonical pronunciation.
    "PostgreSQL": "Postgres",
    "MySQL": "My S Q L",
    "MongoDB": "Mongo D B",
    "MariaDB": "Maria D B",
    "DynamoDB": "Dynamo D B",
    # Other tech-term oddities.
    "nginx": "engine X",
    "LaTeX": "Lay Tech",
    "PyPI": "pie pee eye",
    "regex": "reg ex",
    "WebRTC": "Web R T C",
    "GitHub": "GitHub",  # placeholder — F5 already reads this fine; here for reference
}
# Pre-compile a single union regex sorted longest-first so "CI/CD" matches
# before "CI" and "HTTPS" before "HTTP". Case-INsensitive match — the
# offline rewriter lowercases its output via SUBS, so a strictly cased
# regex would miss "pid" produced after lowercase normalization.
_ACRONYM_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in sorted(ACRONYMS, key=len, reverse=True)) + r")\b",
    flags=re.IGNORECASE,
)
# Lookup of lowercased keys → canonical expansion so we can resolve
# any-case match against the canonical form.
_ACRONYM_LOOKUP = {k.lower(): v for k, v in ACRONYMS.items()}


def _expand_acronyms(text: str) -> str:
    return _ACRONYM_RE.sub(
        lambda m: _ACRONYM_LOOKUP.get(m.group(1).lower(), m.group(1)),
        text,
    )


# Versions: "v1.2.3" / "version 1.2.3" → "version one point two point three"
# Restricted to explicit v/version prefix so plain decimals aren't molested.
_VERSION_RE = re.compile(
    r"\b(v|version)\s*\.?\s*(\d+(?:\.\d+){1,3})\b",
    flags=re.IGNORECASE,
)


def _expand_versions(text: str) -> str:
    def _sub(m: re.Match) -> str:
        nums = m.group(2).split(".")
        return "version " + " point ".join(nums)
    return _VERSION_RE.sub(_sub, text)


# Long build / commit / PR / release numbers: insert commas between every
# digit so F5 enunciates them distinctly instead of slurring "1234567".
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


# Heteronym disambiguation. F5-TTS reads "live" as the verb (l-IH-v) by
# default, but the broadcasting / electric / present-tense uses ("live
# stream", "the system is live", "live wire") want the long-i (l-EYE-v).
# We rewrite the surface text with phonetic respellings so the model
# pronounces them correctly. Order: more-specific patterns first, then
# fall through to the general rule. Each entry: (regex, replacement) —
# the regex must match a context that resolves the heteronym ambiguously.
HETERONYM_RULES: list[tuple[str, str]] = [
    # "live" (long-i): broadcasting / electric / present-tense / "is live".
    (r"\blive\s+(stream(?:ing)?|broadcast|wire|view|demo|reload|edit(?:ing)?|update|deploy(?:ment)?|server|test|preview)\b",
     r"lyve \1"),
    (r"\b(is|are|was|were|stays|going|now|currently)\s+live\b", r"\1 lyve"),
    (r"\blive\s+(?:in|on|at)\s+(production|prod|staging|main)\b", r"lyve in \1"),
    # "read" (past tense — short-e, "red"): "I read the file" / "have read".
    (r"\b(have|has|had|already|just|previously)\s+read\b", r"\1 red"),
    # "lead" (metal, lower case singular noun): "lead solder", "lead pipe".
    (r"\blead\s+(solder|pipe|paint|poisoning|acid|battery|shielding)\b",
     r"led \1"),
    # "wound" (past tense of wind, long-ow): "wound up", "wound around".
    (r"\bwound\s+(up|around|tight)\b", r"wownd \1"),
    # "bow" (front of ship — long-o): "bow of the ship".
    (r"\bbow\s+(of\s+the\s+ship|section|thrusters?)\b", r"boh \1"),
    # "tear" (rip — long-air, "tair"): "tear in the fabric / hull".
    (r"\btear\s+in\s+the\s+(hull|fabric|page|file)\b", r"tair in the \1"),
    # "minute" (small — adjective, my-NOOT): "minute detail / difference".
    (r"\bminute\s+(detail|difference|amount|change|fluctuation)\b",
     r"my-noot \1"),
]
_HETERONYM_RES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(p, flags=re.IGNORECASE), r) for p, r in HETERONYM_RULES
]


def _disambiguate_heteronyms(text: str) -> str:
    for pat, rep in _HETERONYM_RES:
        text = pat.sub(rep, text)
    return text


def _post_process(text: str) -> str:
    """Pronunciation expansions applied to every rewriter output before TTS.
    Order matters: build/version BEFORE acronyms so 'API v2.1.0' becomes
    'application programming interface version two point one point zero',
    not 'API version two point one point zero'. Heteronym disambiguation
    runs LAST so we don't rewrite already-substituted tokens."""
    text = _expand_versions(text)
    text = _expand_build_numbers(text)
    text = _expand_acronyms(text)
    text = _disambiguate_heteronyms(text)
    return text


# ── API rewrite cache ─────────────────────────────────────────────────
# Every successful API rewrite is appended to this JSONL so:
#   1. Identical inputs in future sessions skip the API call (fast + free).
#   2. The corpus accumulates as ground-truth (input → Majel) pairs that
#      a small local model could one day be distilled on.
# Cache key includes the prompt version so meaningful prompt changes
# invalidate prior entries automatically.
REWRITE_CACHE = Path(__file__).resolve().parent / "dataset" / "majel_rewrites.jsonl"
PROMPT_VERSION = "2026-05-02-v3"  # PID/SIG/IO + heteronyms + full-message join

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
        # Guard against any partial sentence the model might emit — cut to the
        # last terminator so the voice never trails off.
        m = re.search(r"^(.*[.!?])(?!.*[.!?])", out, flags=re.DOTALL)
        if m:
            out = m.group(1).strip()
        # Persist for future cache hits + corpus accumulation.
        _save_cache_entry(key, text, out, mode, model_id)
        return out
    except Exception:
        return None


def computerize(text: str) -> str:
    t = re.sub(r"\s+", " ", text).strip()
    if not t:
        return ""
    # API mode: skip templates entirely and let the LLM see the full prose.
    # Templates are designed for offline fidelity, not API-rich rewrites.
    if _voice_mode() == "api" and _api_key():
        llm = _llm_rewrite(t)
        if llm:
            return _with_project_header(_post_process(llm))
        # Fall through to offline path if API call fails.
    # Multi-step plan detection runs BEFORE templates so an explicit
    # "first X, then Y, then Z" doesn't get partially eaten by an earlier
    # work-in-progress regex. Conservative — only fires on explicit list
    # markers; bare prose passes through untouched.
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


def _project_name() -> str:
    """Return the active project's display name, or empty string."""
    return (os.environ.get("MAJEL_PROJECT") or "").strip()


def _with_project_header(text: str) -> str:
    """Prepend 'Project <Name>.' as a status preamble when MAJEL_PROJECT is set.

    The header is suppressed if the rewriter already named the project (case-
    insensitive substring match) so back-to-back identical preambles do not
    appear. Empty / unset project names are a no-op.
    """
    name = _project_name()
    if not name or not text:
        return text
    if name.lower() in text.lower():
        return text
    return f"Project {name}. {text}"


if __name__ == "__main__":
    out = computerize(sys.stdin.read())
    if out:
        sys.stdout.write(out)
