"""Pronunciation fixes applied to everything Majel says.

F5-TTS reads letters, not phonemes, so it guesses vowels from spelling and
gets uncommon or technical words wrong ("repo" with a short e, "sudo" as
"suh-doh"), and it treats every "." as a full stop, so "buddy.py" comes out
as "buddy. Pie." This module rewrites the text before synthesis:

1. Dots inside names are spoken: "buddy.py" -> "buddy dot pie",
   "github.com" -> "git hub dot com", ".gitignore" -> "dot git ignore".
   Sentence-ending full stops and abbreviations like "e.g." are left alone;
   version numbers ("1.2.48") are handled earlier by computerize.py.
2. Words F5 mispronounces are respelled so their vowels come out right,
   using plain-English cues: "ee" long E, "eh" short E, "ay" long A,
   "eye" long I, "oh" long O, "oo" as in moon.

Add your own respellings to ~/.majel_pronunciations.json as
{"word": "respelling"}; they take precedence over the built-in table.
Applied by computerize.py (summaries) and f5_daemon.py (everything else,
e.g. buddy's tab readouts). Idempotent, so applying it twice is harmless.
"""
import json
import re
from pathlib import Path

USER_LEXICON = Path.home() / ".majel_pronunciations.json"

# Whole words, matched case-insensitively.
LEXICON = {
    # long E
    "repo": "reepo", "repos": "reepos", "regex": "rej-ex", "regexes": "rej-exes",
    "enum": "ee-num", "enums": "ee-nums", "readme": "read me", "env": "en-vee", "venv": "vee-env",
    "evdev": "ee-vee-dev", "eval": "ee-val", "deque": "deck",
    # long A
    "async": "ay-sink", "ascii": "ass-key", "api": "ay pee eye", "apis": "ay pee eyes",
    "jpeg": "jay-peg", "jpg": "jay-peg", "png": "pee en gee",
    "yaml": "yam-ul", "yml": "yam-ul", "macos": "mac oh ess", "ios": "eye oh ess",
    # long I
    "py": "pie", "pyside": "pie-side", "pyside6": "pie-side six", "pytest": "pie-test",
    "pypi": "pie-pee-eye", "numpy": "num-pie", "scipy": "sigh-pie",
    "gui": "gooey", "ui": "you eye", "cli": "see el eye", "ai": "ay eye", "io": "eye oh",
    "nginx": "engine ex", "systemd": "system dee", "udev": "you-dev",
    # long O / oo
    "json": "jay-son", "toml": "tom-ul", "npm": "en pee em", "pnpm": "pee en pee em",
    # consonants, file types, letters
    "cache": "cash", "cached": "cashed", "caches": "cashes", "caching": "cashing",
    "github": "git hub", "gitlab": "git lab", "gitignore": "git ignore",
    "stdin": "standard in", "stdout": "standard out", "stderr": "standard error",
    "sql": "sequel", "sqlite": "sequel-ite", "url": "you are el", "urls": "you are els",
    "usb": "you ess bee", "hid": "aitch eye dee", "hidraw": "aitch eye dee raw",
    "ssh": "ess ess aitch", "xdotool": "ex-doh-tool", "pipewire": "pipe-wire",
    "wav": "wave", "mp3": "em pee three", "mp4": "em pee four", "svg": "ess vee gee",
    "md": "em dee", "sh": "ess aitch", "js": "jay ess", "ts": "tee ess", "tsx": "tee ess ex",
    "jsx": "jay ess ex", "css": "see ess ess", "html": "aitch tee em el", "txt": "text",
    "csv": "see ess vee", "pdf": "pee dee eff", "exe": "ee ex ee", "db": "dee bee",
    "cfg": "config", "ini": "in-ee", "rs": "are ess", "cpp": "see plus plus", "gz": "gee zee",
    "uk": "you kay", "f5": "eff five", "tts": "tee tee ess", "audeze": "aw-dee-zee",
}

ABBREVIATIONS = {"etc", "vs", "mr", "mrs", "ms", "dr", "st", "no", "approx", "inc", "ltd"}

# "word.word" or ".word": a dot with a letter or underscore right after it,
# inside a token (so "done. Next" and "v1.2" are untouched).
_DOTTED = re.compile(r"(?<![\w.])(\.?[A-Za-z_][\w\-]*(?:\.[A-Za-z_][\w\-]*)+|\.[A-Za-z_][\w\-]*)")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]*")


def _lexicon():
    table = dict(LEXICON)
    try:
        table.update({k.lower(): v for k, v in json.loads(USER_LEXICON.read_text()).items()})
    except (OSError, ValueError, AttributeError):
        pass
    return table


def _speak_dotted(m):
    token = m.group(1)
    parts = token.split(".")
    named = [p for p in parts if p]
    # e.g. / i.e. / U.S. and "etc."-style abbreviations stay as written.
    if all(len(p) == 1 for p in named) or (len(named) == 1 and named[0].lower() in ABBREVIATIONS):
        return token
    spoken = " dot ".join(p.replace("_", " ") for p in parts)
    return spoken.strip()


def respell_word(word, table):
    rep = table.get(word.lower())
    if rep is None or rep.lower() == word.lower():
        return word
    return rep.capitalize() if word[0].isupper() and not word.isupper() else rep


def apply(text):
    text = _DOTTED.sub(_speak_dotted, text)
    table = _lexicon()
    return _WORD.sub(lambda m: respell_word(m.group(0), table), text)


if __name__ == "__main__":
    import sys
    print(apply(" ".join(sys.argv[1:]) or sys.stdin.read()))
