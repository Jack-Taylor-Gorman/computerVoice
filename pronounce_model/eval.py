"""Measure the vowels Majel actually says.

Each case in eval_cases.json is a phrase plus the expected pronunciation of
one target word (ARPAbet) and one or more wrong pronunciations F5 tends to
produce. The phrase is synthesised through the live voice socket, then a
CTC phoneme recogniser (wav2vec2 trained on espeak IPA) scores the audio
against the whole phrase spelled each way. A case passes when the expected
pronunciation is the most likely one. Whisper is no use here: it forgives
mispronunciation by design.

  synth : python pronounce_model/eval.py synth NAME --cond raw|processed [--reps 2]
          (stdlib only; writes /tmp/pronounce_eval/NAME/*.wav + manifest.json)
  score : venv/bin/python pronounce_model/eval.py score NAME
          (loads the recogniser on CPU, only if MemAvailable > 3 GB)
  report: python3 pronounce_model/eval.py report NAME [NAME ...]
  table : python3 pronounce_model/eval.py table BEFORE[,BEFORE2] AFTER   (Markdown, per case)

PRONOUNCE_CASES=<file> swaps the case list (respell_search.py writes one
whose "text" is a candidate respelling and "score_text" the plain phrase).

"raw" sends the phrase as written. Note that the worker applies whatever
pronounce.py it loaded at start-up, so "raw" means "what Majel says today
for this text"; "processed" sends pronounce.apply(text) from this checkout.
"""
import json
import math
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CASES = Path(os.environ.get("PRONOUNCE_CASES", HERE / "eval_cases.json"))
OUT = Path("/tmp/pronounce_eval")
SOCK = os.environ.get("MAJEL_F5_SOCK", "/tmp/majel_f5.sock")
RECOGNISER = "facebook/wav2vec2-lv-60-espeak-cv-ft"
TRAIN_PY = ["/usr/bin/python3.10"]
TRAIN_ENV = dict(os.environ, PYTHONPATH=str(Path.home() / ".cache" / "pronounce_train_libs"))
MIN_FREE_GB = 3.0


def mem_available_gb():
    for line in open("/proc/meminfo"):
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) / 1048576
    return 0.0


def say(text, dst, timeout=120):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(SOCK)
    s.sendall(json.dumps({"text": text, "dst": dst}).encode() + b"\n")
    data = b""
    while not data.endswith(b"\n"):
        chunk = s.recv(4096)
        if not chunk:
            break
        data += chunk
    s.close()
    return json.loads(data.decode() or "{}")


def synth(name, cond, reps):
    sys.path.insert(0, str(ROOT))
    import pronounce
    cases = json.loads(CASES.read_text())
    d = OUT / name
    d.mkdir(parents=True, exist_ok=True)
    manifest = {"cond": cond, "reps": reps, "started": time.strftime("%Y-%m-%d %H:%M:%S"), "items": []}
    for c in cases:
        sent = c["text"] if cond == "raw" else pronounce.apply(c["text"])
        for r in range(reps):
            wav = d / f"{c['id']}__{r}.wav"
            t = time.monotonic()
            rep = say(sent, str(wav))
            manifest["items"].append({"id": c["id"], "rep": r, "sent": sent, "wav": str(wav),
                                      "ok": bool(rep.get("ok")), "err": rep.get("err"),
                                      "secs": round(time.monotonic() - t, 2)})
            print(c["id"], r, rep, repr(sent), flush=True)
    (d / "manifest.json").write_text(json.dumps(manifest, indent=1))


# ARPAbet -> the espeak-style IPA tokens the recogniser emits (en-us).
VOWELS = {"AA": "ɑː", "AE": "æ", "AH": "ʌ", "AO": "ɔː", "AW": "aʊ", "AY": "aɪ", "EH": "ɛ",
          "ER": "ɜː", "EY": "eɪ", "IH": "ɪ", "IY": "iː", "OW": "oʊ", "OY": "ɔɪ", "UH": "ʊ", "UW": "uː"}
CONS = {"B": "b", "CH": "tʃ", "D": "d", "DH": "ð", "F": "f", "G": "ɡ", "HH": "h", "JH": "dʒ",
        "K": "k", "L": "l", "M": "m", "N": "n", "NG": "ŋ", "P": "p", "R": "ɹ", "S": "s", "SH": "ʃ",
        "T": "t", "TH": "θ", "V": "v", "W": "w", "Y": "j", "Z": "z", "ZH": "ʒ"}
R_COLOURED = {"AA": "ɑːɹ", "AO": "ɔːɹ", "IH": "ɪɹ", "IY": "ɪɹ", "EH": "ɛɹ", "UH": "ʊɹ", "OW": "oːɹ"}


def arpa_to_ipa(phones):
    """['K','AH0','N'] -> ['k','ə','n']; AH0 -> ə, ER0 -> ɚ, unmarked AH -> ə."""
    out = []
    i = 0
    while i < len(phones):
        p = phones[i]
        base, stress = p.rstrip("012"), p[len(p.rstrip("012")):]
        nxt = phones[i + 1] if i + 1 < len(phones) else ""
        if base in R_COLOURED and nxt == "R":
            out.append(R_COLOURED[base])
            i += 2
            continue
        if base == "AH":
            out.append("ʌ" if stress in ("1", "2") else "ə")
        elif base == "ER":
            out.append("ɜː" if stress in ("1", "2") else "ɚ")
        elif base in VOWELS:
            out.append(VOWELS[base])
        else:
            out.append(CONS[base])
        i += 1
    return out


def phrase_phones(cases):
    """ARPAbet for every non-target word, from CMUdict or g2p_en (training libs)."""
    words = sorted({w for c in cases for w in _words(c.get("score_text", c["text"]))})
    code = ("import json,sys\nfrom g2p_en import G2p\ng=G2p()\n"
            "print(json.dumps({w:[p for p in g(w) if p.strip()] for w in json.load(sys.stdin)}))")
    r = subprocess.run(TRAIN_PY + ["-c", code], input=json.dumps(words), capture_output=True,
                       text=True, env=TRAIN_ENV, check=True)
    return json.loads(r.stdout)


def _words(text):
    import re
    return [w.lower() for w in re.findall(r"[A-Za-z][A-Za-z_'-]*[A-Za-z]|[A-Za-z]", text)]


def _ah_variants(phones):
    """ARPAbet AH without a stress mark is either ʌ or ə; try both."""
    idx = [k for k, p in enumerate(phones) if p == "AH"]
    out = []
    for mask in range(2 ** len(idx)):
        v = list(phones)
        for b, k in enumerate(idx):
            v[k] = "AH1" if mask >> b & 1 else "AH0"
        out.append(v)
    return out


def sequences(case, lex):
    """For each pronunciation of the target (expected first), the IPA token
    sequences of the whole phrase; the case's own "text" may already be a
    respelling, so the target is located by "slot" (word index) if given."""
    words = _words(case.get("score_text", case["text"]))
    target = case["target"].lower()
    slot = words.index(target)
    out = []
    for variant in [case["expect"]] + case["wrong"]:
        seqs = []
        for ph in _ah_variants(variant.split()):
            toks = []
            for k, w in enumerate(words):
                toks += arpa_to_ipa(ph if k == slot else lex[w])
            seqs.append(toks)
        out.append(seqs)
    return out


def score(name):
    free = mem_available_gb()
    if free < MIN_FREE_GB:
        sys.exit(f"MemAvailable is {free:.2f} GB, below {MIN_FREE_GB} GB; not loading the recogniser.")
    os.environ.update(USE_TF="0", USE_FLAX="0", TRANSFORMERS_NO_TF="1")
    import torch
    import soundfile as sf
    from huggingface_hub import snapshot_download
    from transformers import Wav2Vec2FeatureExtractor, Wav2Vec2ForCTC
    torch.set_num_threads(4)
    d = OUT / name
    manifest = json.loads((d / "manifest.json").read_text())
    cases = {c["id"]: c for c in json.loads(CASES.read_text())}
    lex = phrase_phones(list(cases.values()))
    # The phoneme tokenizer needs a working phonemizer/espeak; only its vocab is needed.
    snap = Path(snapshot_download(RECOGNISER, local_files_only=True))
    proc = Wav2Vec2FeatureExtractor.from_pretrained(snap)
    model = Wav2Vec2ForCTC.from_pretrained(snap, torch_dtype=torch.float32, low_cpu_mem_usage=True).eval()
    vocab = json.loads((snap / "vocab.json").read_text())
    blank = vocab["<pad>"]
    inv = {v: k for k, v in vocab.items()}
    results = []
    for it in manifest["items"]:
        c = cases[it["id"]]
        row = dict(it, target=c["target"], type=c["type"], expect=c["expect"], wrong=c["wrong"])
        if not it["ok"]:
            row["verdict"] = "synth-failed"
            results.append(row)
            continue
        wav, sr = sf.read(it["wav"], dtype="float32")
        if wav.ndim > 1:
            wav = wav.mean(axis=1)
        if sr != 16000:
            import torchaudio.functional as AF
            wav = AF.resample(torch.from_numpy(wav), sr, 16000).numpy()
        x = proc(wav, sampling_rate=16000, return_tensors="pt").input_values
        with torch.inference_mode():
            logits = model(x).logits[0]
        logp = torch.log_softmax(logits, dim=-1)
        ids = logits.argmax(-1).tolist()
        greedy, prev = [], None
        for i in ids:
            if i != prev and i != blank:
                greedy.append(inv[i])
            prev = i
        row["heard"] = " ".join(greedy)
        losses = []
        for seqs in sequences(c, lex):
            best = math.inf
            for seq in seqs:
                tgt = torch.tensor([vocab[t] for t in seq])
                loss = torch.nn.functional.ctc_loss(
                    logp.unsqueeze(1), tgt.unsqueeze(0), torch.tensor([logp.shape[0]]),
                    torch.tensor([len(tgt)]), blank=blank, reduction="sum", zero_infinity=True)
                best = min(best, float(loss))
            losses.append(round(best, 2))
        row["nll"] = losses
        row["verdict"] = "pass" if losses[0] < min(losses[1:]) else "fail"
        row["margin"] = round(min(losses[1:]) - losses[0], 2)
        results.append(row)
        print(it["id"], it["rep"], row["verdict"], row["margin"], row["heard"], flush=True)
    (d / "scores.json").write_text(json.dumps(results, indent=1, ensure_ascii=False))
    del model


def report(names):
    for name in names:
        rows = json.loads((OUT / name / "scores.json").read_text())
        by = {}
        for r in rows:
            by.setdefault(r["type"], []).append(r["verdict"] == "pass")
        tot = [r["verdict"] == "pass" for r in rows]
        print(f"{name}: {sum(tot)}/{len(tot)} = {sum(tot) / len(tot):.0%}  " +
              "  ".join(f"{k} {sum(v)}/{len(v)}" for k, v in sorted(by.items())))


def table(before, after):
    """Markdown table, one row per case: passes before / after and what was heard.
    before/after are comma-separated run names (merged)."""
    def load(names):
        rows = {}
        for n in names.split(","):
            for r in json.loads((OUT / n / "scores.json").read_text()):
                rows.setdefault(r["id"], []).append(r)
        return rows
    b, a = load(before), load(after)
    print("| Case | Type | Sent to Majel (after) | Before | After | Heard after (first take) |")
    print("|---|---|---|---|---|---|")
    for cid, rs in a.items():
        def n(rows):
            return f"{sum(r['verdict'] == 'pass' for r in rows)}/{len(rows)}" if rows else "–"
        heard = rs[0].get("heard", "").replace("|", "/")
        print(f"| {cid} | {rs[0]['type']} | {rs[0]['sent']} | {n(b.get(cid, []))} | {n(rs)} | `{heard}` |")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "synth":
        cond = sys.argv[sys.argv.index("--cond") + 1] if "--cond" in sys.argv else "raw"
        reps = int(sys.argv[sys.argv.index("--reps") + 1]) if "--reps" in sys.argv else 2
        synth(sys.argv[2], cond, reps)
    elif cmd == "score":
        score(sys.argv[2])
    elif cmd == "report":
        report(sys.argv[2:])
    elif cmd == "table":
        table(sys.argv[2], sys.argv[3])
    else:
        print(__doc__)
