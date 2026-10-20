#!/usr/bin/env python3
"""Quick regression eval for severianize.py.

Runs a panel of representative assistant outputs through the rewriter and
prints them next to expected-feel Severian. No assertions — the rewriter is
LLM-driven in API mode, so output varies. The point is to eyeball the panel
and confirm nothing has regressed (e.g., starts emitting "I" stripped, or
LCARS phrasing leaks back in, or contractions appear).

Usage:
    venv/bin/python scripts/test_severianize.py
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REWRITER = ROOT / "severianize.py"

# Each entry: (input prose, must_contain regex pattern, must_not_contain)
# `must_not_contain` is a list of patterns that would indicate a regression.
PANEL: list[tuple[str, list[str], list[str]]] = [
    (
        "I ran the tests and they all passed",
        [r"\bI\b|\bdone\b|\bwell\b|\btrials\b|\bworks?\b"],
        [r"\bcan'?t\b", r"\bunable to comply\b", r"affirmative"],
    ),
    (
        "Sorry, I can't do that for you",
        [r"\bcannot\b|\bnot\b"],
        [r"unable to comply", r"sorry", r"can'?t"],
    ),
    (
        "I don't know what you mean, could you clarify?",
        [r"memory|dark|again|plainly|word"],
        [r"please restate", r"specify parameters"],
    ),
    (
        "I deleted three old branches",
        [r"three|cut away|unmade|gone|set aside"],
        [r"purged", r"\bdeleted\b\."],
    ),
    (
        "all done",
        [r"done|well|finished|complete"],
        [r"\bworking\b", r"affirmative"],
    ),
    (
        "I updated config.py and restarted the server",
        [r"config|amended|seat|writing|begun anew"],
        [r"please", r"sorry"],
    ),
    (
        "you don't have permission for that operation",
        [r"closed|not for you|forbidden"],
        [r"access denied", r"authorization required"],
    ),
    (
        "Three tests failed in the date parser",
        [r"three|trial|failed|not succeed"],
        [r"\bfaults? detected\b"],
    ),
]


def run_rewriter(prose: str) -> str:
    out = subprocess.run(
        [str(ROOT / "venv" / "bin" / "python"), str(REWRITER)],
        input=prose, capture_output=True, text=True, timeout=120,
    )
    return out.stdout.strip()


def main() -> int:
    if not REWRITER.exists():
        print(f"missing: {REWRITER}", file=sys.stderr)
        return 2

    n_pass = 0
    n_warn = 0
    n_fail = 0
    print(f"running {len(PANEL)} cases through {REWRITER.name}\n")
    for prose, must, must_not in PANEL:
        out = run_rewriter(prose)
        flags: list[str] = []
        for p in must:
            if not re.search(p, out, flags=re.IGNORECASE):
                flags.append(f"missing /{p}/")
        for p in must_not:
            if re.search(p, out, flags=re.IGNORECASE):
                flags.append(f"contains /{p}/ (regression)")
        # Universal anti-patterns Severian must never produce.
        for anti in (r"\bcan'?t\b", r"\bwon'?t\b", r"\bdon'?t\b", r"\bisn'?t\b",
                     r"\bI'll\b", r"\bI've\b"):
            if re.search(anti, out):
                flags.append(f"contraction /{anti}/")
        if not out:
            flags.append("empty output")

        if not flags:
            status = "PASS"
            n_pass += 1
        elif any("regression" in f or "contraction" in f or "empty" in f for f in flags):
            status = "FAIL"
            n_fail += 1
        else:
            status = "WARN"
            n_warn += 1

        print(f"[{status}] {prose!r}")
        print(f"        → {out!r}")
        for f in flags:
            print(f"        ! {f}")
        print()

    print(f"summary: {n_pass} pass / {n_warn} warn / {n_fail} fail")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
