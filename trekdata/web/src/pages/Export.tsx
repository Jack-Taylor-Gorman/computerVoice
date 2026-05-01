import { useState } from "react";
import { api } from "../lib/api";

export function Export() {
  const [minSnr, setMinSnr] = useState(20);
  const [trainFrac, setTrainFrac] = useState(0.9);
  const [valFrac, setValFrac] = useState(0.05);
  const [format, setFormat] = useState("ljspeech");
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);

  async function run() {
    setRunning(true); setErr(null); setResult(null);
    try {
      const r = await api.startExport({ format, min_snr_db: minSnr, train_frac: trainFrac, val_frac: valFrac });
      setResult(r);
    } catch (e: any) { setErr(String(e?.message || e)); }
    finally { setRunning(false); }
  }

  return (
    <div style={{ display: "grid", gap: 16, maxWidth: 720 }}>
      <div>
        <h3>Export dataset</h3>
        <div className="hint">Materializes approved clips into a training-ready directory (LJSpeech or HuggingFace Datasets).</div>
      </div>

      <div className="card" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        <div>
          <label>Format</label>
          <select value={format} onChange={(e) => setFormat(e.target.value)} style={{ width: "100%" }}>
            <option value="ljspeech">LJSpeech + JSONL sidecar</option>
            <option value="hf_datasets">HuggingFace Datasets</option>
          </select>
          <label>Minimum SNR (dB)</label>
          <input type="number" value={minSnr} onChange={(e) => setMinSnr(Number(e.target.value))} style={{ width: "100%" }} />
        </div>
        <div>
          <label>Train fraction</label>
          <input type="number" step="0.01" value={trainFrac} onChange={(e) => setTrainFrac(Number(e.target.value))} style={{ width: "100%" }} />
          <label>Validation fraction</label>
          <input type="number" step="0.01" value={valFrac} onChange={(e) => setValFrac(Number(e.target.value))} style={{ width: "100%" }} />
        </div>
      </div>

      <div>
        <button className="primary" disabled={running} onClick={run}>
          {running ? "Exporting…" : "Export approved clips"}
        </button>
      </div>

      {err && <div className="card" style={{ borderColor: "#EE6300", color: "#EE6300" }}>{err}</div>}
      {result && (
        <div className="card">
          <h3>Export complete</h3>
          <pre style={{ margin: 0, fontSize: 12 }}>{JSON.stringify(result, null, 2)}</pre>
        </div>
      )}
    </div>
  );
}
