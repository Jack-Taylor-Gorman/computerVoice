"""Trek computer utterance archetypes — 16 canonical categories.

Derived from the data-scientist taxonomy. Key 0 is reserved for unclassified.
The numeric shortcut maps to keyboard 1-9, 0 in the Clip Editor; labels beyond
9 are dropdown-only.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Archetype:
    key: str
    shortcut: int | None  # 1-9, 0=unclassified, None=dropdown-only
    label: str
    template: str
    invariants: tuple[str, ...]


ARCHETYPES: tuple[Archetype, ...] = (
    Archetype("unclassified", 0, "Unclassified", "", ()),
    Archetype("status_info", 1, "Informational / Status", "[Noun] at [value].", ("subject-less", "status-first")),
    Archetype("refusal", 2, "Refusal / Inability", "Unable to comply.", ("terminal", "no-apology")),
    Archetype("auth_gate", 3, "Authorization Gate", "Access denied. [Rank] [Name] is not authorized.", ("firm", "additive-clause")),
    Archetype("clarification", 4, "Clarification Request", "Please restate the question.", ("neutral-imperative",)),
    Archetype("working", 5, "Working / Processing", "Working.", ("pure-state-signal",)),
    Archetype("completion", 6, "Completion", "[Task] complete.", ("verb-final", "status-final")),
    Archetype("warning", 7, "Warning / Alert", "Warning. [Condition] detected.", ("label-first", "urgent")),
    Archetype("enumerated", 8, "Enumerated Result", "[N] matches located.", ("count-first",)),
    Archetype("query_prompt", 9, "Query / Prompt", "Specify parameters.", ("subject-less-imperative",)),
    Archetype("acknowledgment", None, "Acknowledgment", "Acknowledged.", ("terminal",)),
    Archetype("location", None, "Location / Detection", "[Entity] located. Deck [N], section [X].", ("verb-final-subject",)),
    Archetype("negation", None, "Negative Result", "No records on file.", ("flat-negation",)),
    Archetype("diagnostic", None, "Diagnostic Report", "Diagnostic complete. All systems nominal.", ("two-clause",)),
    Archetype("stardate", None, "Time / Stardate", "Stardate [XXXXX.X].", ("pure-data",)),
    Archetype("casualty", None, "Casualty / Life Sign", "[N] life signs. One human, [N] Klingon.", ("species-precise",)),
    Archetype("countdown", None, "Countdown / Quantified", "Self-destruct sequence in [M] minutes, [S] seconds.", ("monotone", "exact-units")),
)

BY_KEY = {a.key: a for a in ARCHETYPES}
BY_SHORTCUT = {a.shortcut: a for a in ARCHETYPES if a.shortcut is not None}

STYLE_INVARIANTS: tuple[str, ...] = (
    "No contractions. 'Can't' becomes 'unable to'. 'Won't' does not exist.",
    "No first-person subject. The computer never says 'I'. It is implicit.",
    "Status-first declarative order: condition precedes implication.",
    "Verb-final in completion and detection: 'Transfer complete.', not 'The transfer has completed.'",
    "No hedging vocabulary: no 'approximately', 'seems', 'might'. Use 'indeterminate' or 'inconclusive'.",
    "Exact numerals preferred over qualitative descriptors.",
    "No pleasantries. Computer-to-crew contains zero 'please'.",
    "Label-first alerting: 'Warning.' / 'Alert.' / 'Caution.' as a standalone clause.",
    "Units and classifications stated explicitly: 'warp factor seven', 'class-M atmosphere'.",
    "Sentence count bounded to 1-3. Use period-separated short declaratives, not subordinating conjunctions.",
    "Passive constructions preferred for results: 'Three anomalies detected.'",
)
