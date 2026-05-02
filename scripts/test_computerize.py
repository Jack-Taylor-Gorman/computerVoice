#!/usr/bin/env python3
"""Smoke test for computerize.py — runs offline-mode rewrites on a deck
of inputs covering every code path the council called out, plus the
specific regressions I'm trying not to introduce. Prints input/output
pairs so a human can eyeball the diff at a glance.

Forces offline mode by clearing the API key path before each invocation.
"""
import os
import sys
from pathlib import Path

# Force offline path: pretend voice_mode is offline regardless of config.
os.environ["ANTHROPIC_API_KEY"] = ""

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import computerize  # noqa: E402

# Monkey-patch _voice_mode to "offline" so we test the regex/template path.
computerize._voice_mode = lambda: "offline"
computerize._api_key = lambda: None

CASES: list[tuple[str, str]] = [
    # Plain ack — should stay terse.
    ("Okay, done.",                                 "regression: ack"),
    ("Yes, that works.",                            "regression: yes-ack"),
    # Single-sentence prose with commas — must NOT trigger step enumeration.
    ("I fixed it, pushed, and tested.",             "regression: comma list NOT a plan"),
    ("Three matches were found in the auth logs.", "regression: bare quantified"),
    # #1 Parameter-naming clarifications.
    ("Could you clarify which file you mean?",      "#1 path clarification"),
    ("Can you specify which user account?",         "#1 user clarification"),
    ("Which one do you want?",                      "#1 generic selection"),
    ("Could you clarify when you need this?",       "#1 time clarification"),
    ("Could you clarify the value?",                "#1 value clarification"),
    # #2 Multi-step enumeration.
    ("Plan: refactor router, update tests, push to main.", "#2 plan colon list"),
    ("First I'll fix the bug, then I'll write a test, then I'll push.", "#2 first/then"),
    ("My plan: A, B, C, D, E, F.",                  "#2 caps at 4 steps"),
    # #3 Hedge-quantity scrubbing.
    ("I found several issues in the auth flow.",    "#3 several → multiple"),
    ("It will be done soon.",                       "#3 soon strip"),
    ("Approximately fourteen seconds remaining.",   "#3 approximately strip"),
    # #4 Warning. label-first prefix.
    ("There was an error occurred in the payment module.", "#4 generic error → Warning."),
    ("Failed to connect to the database.",          "#4 fail → Warning."),
    ("The server is offline.",                      "#4 status → Warning."),
    ("Fatal exception in worker.",                  "#4 fatal already prefixed"),
    # #5 Tightened scanning capture.
    ("Let me check to verify the auth.",            "#5 should NOT say 'Scanning to verify'"),
    ("Let me look at the auth flow.",               "#5 SHOULD say 'Scanning ...'"),
    ("Let me check the database connection.",       "#5 SHOULD scan database"),
    # Generic refusal — should stay short.
    ("Sorry, I can't do that.",                     "regression: refusal"),
    # Long Claude prose — should fall through to LLM/strip path; we'll see fallback.
    ("I went through the codebase and refactored the router module to support pluggable middlewares, then I updated the tests to cover the new behavior.", "regression: long prose"),
]


def main() -> None:
    width_label = max(len(label) for _, label in CASES)
    for src, label in CASES:
        out = computerize.computerize(src) or "(empty)"
        print(f"[{label:<{width_label}}]")
        print(f"  IN : {src}")
        print(f"  OUT: {out}")
        print()


if __name__ == "__main__":
    main()
