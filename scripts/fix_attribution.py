#!/usr/bin/env python3
"""Rebuild attribution.jsonl from cleaned.txt + an existing (broken)
attribution.jsonl used only as a speaker-hint source.

The Haiku pass that produced the original attribution emitted dialogue
TAGS (narrator action sentences like "Blood shook his head.") tagged
with a character speaker. The renderer (multi_voice_read.build_segments)
treats every row as a dialogue line, so the result is inverted:
narrator actions get spoken in character voices and the actual quoted
dialogue (which lives in the gaps) gets spoken in the narrator's voice.

Approach (no Haiku spend):
  1. Walk cleaned.txt and extract every quoted dialogue span "...".
  2. Pre-classify every old attribution row as a dialogue-TAG: parse its
     prose to find a SUBJECT (Blood/Silk/Crane/Musk/...) and a VERB KIND
     ("dialogic" = said/asked/replied; "reaction" = shook/nodded/smiled).
  3. For each quote, look at the nearest tag immediately before and
     immediately after (within ~80 chars). Apply directional rules:
        - dialogic-before/after → that subject speaks this quote
        - reaction-BEFORE        → that subject speaks this quote
                                   ("Blood shook his head. 'I'm done.'")
        - reaction-AFTER         → that subject does NOT speak this quote;
                                   speaker is the OTHER party in the scene
                                   ("'I'm tired.' Silk yawned.")
  4. When ambiguous (no nearby tag), alternate from the previous speaker.
  5. Fallback for the truly contextless first quote: the most-frequent
     OTHER character across all tags in the piece.
"""
from __future__ import annotations
import argparse
import json
import re
from collections import Counter
from pathlib import Path


# Known character-name → canonical speaker key (lowercase, matches
# SPEAKER_TO_VOICE in multi_voice_read.py).
NAME_TO_SPEAKER = {
    "silk": "silk",
    "blood": "blood",
    "musk": "musk",
    "crane": "crane",
    "rose": "rose",
    "mint": "mint",
    "chenille": "chenille",
    "auk": "auk",
    "patera silk": "silk",
    "patera": "silk",
    "doctor crane": "crane",
    "doctor": "crane",
    "biood": "blood",  # OCR corruption
}


NARRATOR_LABELS = {"severian-narrator", "narrator", "unknown", "", "severian-character"}


# Vocatives (the address form) → who they refer to in this scene.
# A speaker can NEVER address themselves by these forms; if the chosen
# speaker is the vocative's referent, that's a contradiction and the
# attribution must be flipped.
VOCATIVES = {
    "patera":       "silk",   # only patera in the scene
    "patera silk":  "silk",
    "silk":         "silk",
    "blood":        "blood",
    "musk":         "musk",
    "crane":        "crane",
    "doctor":       "crane",  # only doctor in the scene
    "doctor crane": "crane",
    "maytera":      None,     # could be any of Rose/Mint/Marble — can't pin a single referent
}


# A vocative is a name set off by punctuation: ", Patera." or " Patera?"
# at the END of a clause, or "Patera," at the start.
_VOC_NAMES = sorted({k for k in VOCATIVES if VOCATIVES[k]}, key=len, reverse=True)
_VOCATIVE_RE = re.compile(
    r"(?:[,!?.]\s+|^\s*)(" + "|".join(re.escape(n) for n in _VOC_NAMES) + r")\s*(?=[,!?.]|$)",
    re.IGNORECASE | re.MULTILINE,
)


def vocative_referents(quote_body: str) -> set[str]:
    """Return the set of speaker-keys this quote ADDRESSES (via vocatives).
    The actual speaker cannot be in this set."""
    out: set[str] = set()
    for m in _VOCATIVE_RE.finditer(quote_body):
        voc = m.group(1).lower()
        ref = VOCATIVES.get(voc)
        if ref:
            out.add(ref)
    return out


# Verb classification. Dialogic verbs mark "X said this quote".
# Reaction verbs are physical/emotive actions; in tag-BEFORE position the
# subject typically speaks the next quote (reacts then talks); in
# tag-AFTER position the subject typically did NOT speak the quote (the
# other party did, and our subject reacted).
# Continuation verbs: "X continued/added/went-on" means the speaker hasn't
# changed across this tag. The PRECEDING and FOLLOWING quote both belong
# to X. Treated as a special dialogic subclass.
CONTINUATION_VERBS = {"continued", "added", "resumed", "concluded", "finished", "went"}

DIALOGIC_VERBS = {
    "said", "asked", "replied", "answered", "murmured", "whispered",
    "explained", "inquired", "told", "responded",
    "remarked", "observed", "demanded", "snapped", "muttered", "called",
    "began", "agreed", "admitted", "ordered", "shouted", "exclaimed",
    "stammered", "stuttered", "promised", "warned", "growled", "hissed",
    "grunted", "sighed",
} | CONTINUATION_VERBS
REACTION_VERBS = {
    "shook", "frowned", "smiled", "nodded", "glanced", "sat", "paused",
    "reached", "chuckled", "laughed", "waved", "pointed", "rose", "stood",
    "sniffed", "grinned", "stared", "looked", "closed", "turned", "jabbed",
    "set", "put", "took", "lifted", "dropped", "bent", "crossed", "leaned",
    "tilted", "clenched", "grimaced", "blinked", "winced", "snorted",
    "coughed", "breathed", "visualized", "considered", "thought", "watched",
    "felt", "saw", "heard", "started", "decided", "hesitated", "stiffened",
    "stretched", "spread", "drew", "moved", "stepped", "walked", "rubbed",
    "scratched", "stroked", "emphasized", "gestured", "made", "knew",
    "noticed", "wondered", "remembered", "realized", "wished", "tried",
    "began", "let", "got", "had", "did", "went", "came",
}


def detect_subject(tag_text: str) -> str | None:
    """Find the canonical speaker key for the SUBJECT of a tag sentence."""
    if not tag_text:
        return None
    t = tag_text.strip().strip('"').strip()
    # Leading "Doctor X", "Patera X", or just Capitalized name
    m = re.match(r"^(Doctor\s+\w+|Patera\s+\w+|[A-Z][a-z]+)\b", t)
    if m:
        cand = m.group(1).lower()
        if cand in NAME_TO_SPEAKER:
            return NAME_TO_SPEAKER[cand]
        last = cand.split()[-1]
        if last in NAME_TO_SPEAKER:
            return NAME_TO_SPEAKER[last]
    # Inverted: "said Silk softly"
    m = re.search(
        r"\b(?:said|asked|replied|murmured|answered|whispered|added|continued|explained|inquired|told|responded|remarked|observed|demanded|snapped|muttered|called|warned|growled|hissed|grunted|sighed)\s+([A-Z][a-z]+)\b",
        t,
    )
    if m:
        cand = m.group(1).lower()
        if cand in NAME_TO_SPEAKER:
            return NAME_TO_SPEAKER[cand]
    # Any known name anywhere — first hit
    for name in re.findall(r"\b([A-Z][a-z]+)\b", t):
        c = name.lower()
        if c in NAME_TO_SPEAKER:
            return NAME_TO_SPEAKER[c]
    return None


def classify_verb(tag_text: str) -> str:
    """Return 'continuation' | 'dialogic' | 'reaction' | 'unknown'."""
    if not tag_text:
        return "unknown"
    words = re.findall(r"\b\w+\b", tag_text.lower())
    for w in words:
        if w in CONTINUATION_VERBS:
            return "continuation"
        if w in DIALOGIC_VERBS:
            return "dialogic"
        if w in REACTION_VERBS:
            return "reaction"
    return "unknown"


def find_quotes(text: str) -> list[tuple[int, int, str]]:
    """List of (open_quote_pos, char_after_close_quote, inner_body).

    Classifies every `"` as OPEN or CLOSE by its surrounding characters,
    then pairs each OPEN with the next CLOSE. This avoids parity drift
    from orphan quotes (OCR artefacts, missing punctuation, etc.) which
    would otherwise turn a single missing `"` into N consecutive
    narrative-as-quote misextractions for the rest of the file.

    Heuristic:
      - OPEN  = `"` preceded by start-of-text, whitespace, or `(` `[`
                AND followed by a letter or digit.
      - CLOSE = `"` preceded by a letter, digit, or `.,?!:;)`
                AND followed by whitespace, newline, EOF, or `.,?!:;)`.
      - Both = ambiguous; prefer the kind we're currently expecting
               (toggle), which gives the old simple-pairing behavior
               in the common case.
    """
    n = len(text)
    # build (pos, kind) list
    classified = []
    for i, c in enumerate(text):
        if c != '"':
            continue
        prev = text[i - 1] if i > 0 else ""
        nxt = text[i + 1] if i + 1 < n else ""
        opens = (prev == "" or prev.isspace() or prev in "([") and (nxt.isalnum() or nxt in "-'")
        closes = (prev.isalnum() or prev in ".,?!:;)") and (nxt == "" or nxt.isspace() or nxt in ".,?!:;)")
        if opens and not closes:
            classified.append((i, "open"))
        elif closes and not opens:
            classified.append((i, "close"))
        else:
            classified.append((i, "ambig"))

    # Pair: walk in order, looking for an OPEN, then find the next CLOSE.
    # AMBIG quotes flex to whatever we currently need.
    out = []
    j = 0
    while j < len(classified):
        pos, kind = classified[j]
        if kind == "close":
            # orphan close — skip
            j += 1; continue
        # treat as open (open or ambig-as-open)
        open_pos = pos
        # find next close (or ambig-as-close)
        k = j + 1
        close_pos = -1
        while k < len(classified):
            p2, k2 = classified[k]
            if k2 == "close" or k2 == "ambig":
                close_pos = p2; break
            # another open: orphan; the current open might itself be wrong,
            # but treat the LATER open as truth (an unclosed dialogue is
            # rare; nested opens almost always mean we mis-classified one).
            # Skip the current open and try the later one.
            j = k - 1
            close_pos = -2
            break
            k += 1
        if close_pos == -2:
            j += 1; continue
        if close_pos == -1:
            break  # no more closes
        body = text[open_pos + 1:close_pos]
        if body.strip():
            out.append((open_pos, close_pos + 1, body))
        j = k + 1
    return out


def pick_other_party(recent: list[str], exclude: str | None,
                     global_top: list[str]) -> str | None:
    """Return a 'not-exclude' speaker, preferring the most recent."""
    for s in reversed(recent):
        if s != exclude:
            return s
    for s in global_top:
        if s != exclude:
            return s
    return None


def rebuild(text_path: Path, attr_path: Path, out_path: Path) -> None:
    text = text_path.read_text(encoding="utf-8")
    old_rows = [json.loads(l) for l in attr_path.read_text().splitlines() if l.strip()]
    old_rows.sort(key=lambda r: r.get("char_start", 0))

    classified = []
    for r in old_rows:
        body = r.get("text", "")
        subj = detect_subject(body)
        kind = classify_verb(body)
        if subj is None:
            continue
        # For dialogic tags, the trailing punctuation tells us direction:
        #   "X said," → introduces NEXT quote (forward)
        #   "X said." → tags the PREVIOUS quote (backward)
        b = body.strip()
        trailing = b[-1] if b else ""
        direction = "forward" if trailing in (",", ":") else "backward"
        classified.append({
            "cs": r.get("char_start", -1),
            "ce": r.get("char_end", -1),
            "subj": subj,
            "kind": kind,
            "dir": direction,
        })

    subj_counts = Counter(c["subj"] for c in classified)
    global_top = [s for s, _ in subj_counts.most_common()]

    quotes = find_quotes(text)
    # quotes: list of (qs, qe, body). We'll build a parallel speakers[] list.
    n = len(quotes)
    speakers: list[str | None] = [None] * n

    # Sequential tag→quote assignment:
    # For each tag, identify the quote immediately before it (largest
    # qe ≤ tag.ce + slack) and immediately after (smallest qs ≥ tag.cs - slack).
    # Then by verb kind:
    #   - dialogic tag: subject said the PRECEDING quote
    #     ("'Hi,' said Silk.") — falls back to next quote if no prev.
    #   - reaction tag: subject speaks the NEXT quote
    #     ("Blood shook his head. 'I'm done.'") — falls back to prev if no next.
    # Slack of 4 chars handles 1-char overlaps where tag.cs ≈ quote.qe due
    # to the closing-quote-character coincidence ("foo." takes position 0..4
    # where the closing ' " ' is at 3; an immediately-following tag's cs=3).
    SLACK = 4
    qs_arr = [q[0] for q in quotes]
    qe_arr = [q[1] for q in quotes]
    import bisect
    for t in classified:
        # quote immediately before tag: largest i with qe[i] <= t.cs + SLACK
        i_prev = bisect.bisect_right(qe_arr, t["cs"] + SLACK) - 1
        # quote immediately after tag: smallest j with qs[j] >= t.ce - SLACK
        i_next = bisect.bisect_left(qs_arr, t["ce"] - SLACK)
        # but also constrain that tag is "near" each (within ~120 chars
        # between tag boundary and quote boundary)
        BAND = 120
        # clamp negative gaps to 0 (overlap is fine — Wolfe's prose often
        # has the tag's first word at the same char as the closing `"`).
        gap_after_prev = max(0, t["cs"] - qe_arr[i_prev]) if i_prev >= 0 else 1 << 30
        gap_before_next = max(0, qs_arr[i_next] - t["ce"]) if i_next < n else 1 << 30
        prev_ok = gap_after_prev <= BAND
        next_ok = gap_before_next <= BAND
        targets: list[int] = []
        if t["kind"] == "continuation":
            # "X continued" / "X added" → BOTH preceding and following
            # quote belong to X (same speaker across the tag).
            if prev_ok: targets.append(i_prev)
            if next_ok: targets.append(i_next)
        elif t["kind"] == "dialogic":
            # "X said," → forward; "X said." → backward
            if t["dir"] == "forward":
                tgt = i_next if next_ok else (i_prev if prev_ok else None)
            else:
                tgt = i_prev if prev_ok else (i_next if next_ok else None)
            if tgt is not None: targets.append(tgt)
        elif t["kind"] == "reaction":
            tgt = i_next if next_ok else (i_prev if prev_ok else None)
            if tgt is not None: targets.append(tgt)
        else:
            tgt = i_next if next_ok else (i_prev if prev_ok else None)
            if tgt is not None: targets.append(tgt)
        for target in targets:
            if speakers[target] is None:
                speakers[target] = t["subj"]
        # We do NOT overwrite an existing assignment — first tag in
        # position order wins, which matches reading order.

    # Fill unassigned quotes by alternation.
    last_assigned: str | None = None
    for i in range(n):
        if speakers[i] is not None:
            last_assigned = speakers[i]
            continue
        # Look at neighbors: previous assigned and next assigned
        prev_s = None
        for k in range(i - 1, -1, -1):
            if speakers[k] is not None:
                prev_s = speakers[k]; break
        next_s = None
        for k in range(i + 1, n):
            if speakers[k] is not None:
                next_s = speakers[k]; break
        if prev_s and next_s and prev_s == next_s:
            # quote is sandwiched between same-speaker quotes — likely
            # continuation of that speaker's monologue across paragraphs.
            speakers[i] = prev_s
        elif prev_s and next_s and prev_s != next_s:
            # alternation: lean toward the upcoming speaker (a tag locked
            # in next_s, so prev_s likely already ended).
            speakers[i] = next_s
        elif prev_s:
            # standard alternation: not prev_s
            speakers[i] = pick_other_party([prev_s], exclude=prev_s,
                                            global_top=global_top) or prev_s
        elif next_s:
            speakers[i] = pick_other_party([next_s], exclude=next_s,
                                            global_top=global_top) or next_s
        else:
            speakers[i] = global_top[0] if global_top else "severian-narrator"
        last_assigned = speakers[i]

    # Vocative constraint pass: if a quote addresses character X (e.g. "Yes,
    # Patera." → addresses Silk), the speaker cannot be X. Override to the
    # most recent OTHER speaker.
    voc_overrides = 0
    for i, (qs, qe, body) in enumerate(quotes):
        forbidden = vocative_referents(body)
        if speakers[i] in forbidden:
            # find the nearest other-character speaker (from neighbors)
            replacement = None
            for k in range(i - 1, -1, -1):
                if speakers[k] is not None and speakers[k] not in forbidden:
                    replacement = speakers[k]; break
            if not replacement:
                for k in range(i + 1, n):
                    if speakers[k] is not None and speakers[k] not in forbidden:
                        replacement = speakers[k]; break
            if not replacement:
                for s in global_top:
                    if s not in forbidden:
                        replacement = s; break
            if replacement and replacement != speakers[i]:
                speakers[i] = replacement
                voc_overrides += 1
    if voc_overrides:
        print(f"vocative-eliminations: {voc_overrides}")

    new_rows = []
    for (qs, qe, body), spk in zip(quotes, speakers):
        new_rows.append({
            "char_start": qs,
            "char_end": qe,
            "speaker": spk,
            "text": re.sub(r"\s+", " ", body).strip(),
        })

    c = Counter(r["speaker"] for r in new_rows)
    print(f"old rows: {len(old_rows)}  →  new rows: {len(new_rows)}")
    print(f"speakers: {c.most_common()}")
    out_path.write_text("\n".join(json.dumps(r) for r in new_rows) + "\n")
    print(f"wrote: {out_path}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cleaned")
    ap.add_argument("attribution")
    ap.add_argument("out")
    args = ap.parse_args()
    rebuild(Path(args.cleaned), Path(args.attribution), Path(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
