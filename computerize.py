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
import json
import os
import re
import sys
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
    (r"\bneed\s+more\s+(info|information|context|details)\b", "Insufficient data. Specify parameters."),
    (r"\bambiguous\b", "Direction unclear. Please restate."),

    # ---- Request clarification ----
    (r"\bwhat\s+(do|did)\s+you\s+mean\b", "Please restate."),
    (r"\bcould you (clarify|explain|specify|rephrase)", "Please restate."),
    (r"\bcan you (clarify|explain|specify|rephrase)", "Please restate."),
    (r"\bwhich\s+one\s+do\s+you\s+(want|mean)\b", "Specify parameters."),

    # ---- Warnings / errors ----
    (r"\bfatal\s+(error|exception)\b|\bcrashed?\b", "Warning. Critical fault detected."),
    (r"\berror\s+occurred\b|\bsomething went wrong\b", "Fault detected."),
    (r"\b(failed|failure)\s+to\s+(\w+)", r"\2 unsuccessful."),
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
    (r"\b(\w+)\s+(is\s+)?(broken|not\s+working|offline|down|unavailable)\b", r"\1 not functional."),

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
]


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
            return re.sub(pat, rep, m.group(0), flags=re.IGNORECASE).strip().capitalize()
        except re.error:
            return rep
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

Output only the rewritten response — no explanation, no quotes, no preface, no Markdown, no bullet points. Use ONLY plain sentences ending with "." or "!" or "?". Always finish every sentence — never leave a thought partial. Match the user's full content scope; expand to as many sentences as required."""

# Extra clause appended to SYSTEM_PROMPT when api mode is on. Encourages the
# computer to drive the work forward by asking a follow-up when it would help.
SYSTEM_PROMPT_API_SUFFIX = """

You are also operating as an active collaborator. If the user's prose hints at an unfinished decision, an ambiguous next step, or a piece of missing information that would unblock progress, append exactly one short computer-speak question to drive the conversation forward (still under the 40-word cap). Use the option-enumeration form when more than one path exists. Skip the question entirely when the prose is a self-contained completion ("Test suite complete. All tests nominal." needs no follow-up)."""

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


def _llm_rewrite(text: str) -> str | None:
    try:
        from anthropic import Anthropic
        key = _api_key()
        if not key:
            return None
        client = Anthropic(api_key=key)
        prompt = SYSTEM_PROMPT
        if _voice_mode() == "api":
            prompt = SYSTEM_PROMPT + SYSTEM_PROMPT_API_SUFFIX
        messages = []
        for src, tgt in _FEW_SHOTS:
            messages.append({"role": "user", "content": src})
            messages.append({"role": "assistant", "content": tgt})
        messages.append({"role": "user", "content": text})
        r = client.messages.create(
            model="claude-haiku-4-5-20251001",
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
            return _with_project_header(llm)
        # Fall through to offline path if API call fails.
    templated = apply_templates(t)
    if templated:
        templated = re.sub(r"(?<=[.!?])\s+([a-z])", lambda m: " " + m.group(1).upper(), templated)
        if not templated.endswith((".", "!", "?")):
            templated += "."
        return _with_project_header(templated)
    llm = _llm_rewrite(t)
    if llm:
        return _with_project_header(llm)
    return _with_project_header(fallback_strip(t))


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
