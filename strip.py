#!/usr/bin/env python3
"""Strip markdown/code/paths/URLs from assistant prose before TTS."""
import re
import sys


def strip(text: str) -> str:
    t = text
    t = re.sub(r"```.*?```", " ", t, flags=re.DOTALL)
    t = re.sub(r"`[^`]+`", " ", t)
    t = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    t = re.sub(r"https?://\S+", " ", t)
    t = re.sub(r"(?:^|\s)(?:[/~]|\./|\.\./)[^\s`'\"]+", " ", t)
    t = re.sub(r"\b[\w.-]+\.(?:py|js|ts|tsx|jsx|md|json|yaml|yml|toml|sh|rs|go|java|c|cpp|h|hpp|html|css|sql|txt|log|cfg|ini|lock|pth|zip)\b(?::\d+)?", " ", t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"\1", t)
    t = re.sub(r"(?<!\*)\*([^*\n]+)\*", r"\1", t)
    t = re.sub(r"^#{1,6}\s+", "", t, flags=re.MULTILINE)
    t = re.sub(r"^\s*[-*+]\s+", "", t, flags=re.MULTILINE)
    t = re.sub(r"^\s*\d+\.\s+", "", t, flags=re.MULTILINE)
    t = re.sub(r"[_~>|]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


if __name__ == "__main__":
    data = sys.stdin.read()
    out = strip(data)
    if len(out) < 3:
        sys.exit(0)
    sys.stdout.write(out)
