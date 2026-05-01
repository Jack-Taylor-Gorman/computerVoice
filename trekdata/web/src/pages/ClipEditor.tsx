import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import WaveSurfer from "wavesurfer.js";
import RegionsPlugin from "wavesurfer.js/dist/plugins/regions.js";
import { api } from "../lib/api";

export function ClipEditor() {
  const { id = "" } = useParams();
  const nav = useNavigate();
  const { data: clip } = useQuery({ queryKey: ["clip", id], queryFn: () => api.getClip(id) });
  const { data: tagsRaw } = useQuery({ queryKey: ["archetypes"], queryFn: () => api.archetypes() });
  const tags = (tagsRaw as any[] | undefined) || [];

  const waveRef = useRef<HTMLDivElement | null>(null);
  const wsRef = useRef<WaveSurfer | null>(null);
  const [transcript, setTranscript] = useState("");
  const [category, setCategory] = useState("unclassified");
  const [quality, setQuality] = useState(3);
  const [trigger, setTrigger] = useState("");
  const [addressee, setAddressee] = useState("");

  useEffect(() => {
    if (!waveRef.current || !id) return;
    const regions = RegionsPlugin.create();
    const ws = WaveSurfer.create({
      container: waveRef.current,
      waveColor: "#f4b07a",
      progressColor: "#EE6300",
      cursorColor: "#111111",
      height: 160,
      barWidth: 2,
      barRadius: 2,
      plugins: [regions],
    });
    ws.load(`/api/clips/${id}/audio`);
    wsRef.current = ws;
    return () => ws.destroy();
  }, [id]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "TEXTAREA" || t.tagName === "INPUT")) return;
      if (e.code === "Space") { e.preventDefault(); wsRef.current?.playPause(); }
      if (e.key === "Enter") { void api.approve(id).then(() => nav(-1)); }
      if (e.key === "x" || e.key === "X") { void api.reject(id).then(() => nav(-1)); }
      if (/^[0-9]$/.test(e.key)) {
        const k = Number(e.key);
        const a = tags.find((x) => x.shortcut === k);
        if (a) setCategory(a.key);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [id, tags, nav]);

  function save() {
    void api.putLabel(id, {
      transcript_raw: transcript, archetype: category, quality,
      trigger_utterance: trigger, addressee,
    });
  }

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div>
          <h3>Clip {id.slice(0, 8)}</h3>
          <span className="pill">{(clip as any)?.state || "loading"}</span>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="ghost" onClick={save}>Save</button>
          <button className="approved" onClick={() => api.approve(id).then(() => nav(-1))}>Approve</button>
          <button className="reject" onClick={() => api.reject(id).then(() => nav(-1))}>Reject</button>
        </div>
      </div>

      <div className="card" style={{ padding: 12 }}>
        <div ref={waveRef} />
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: 16 }}>
        <div className="card">
          <label>Transcript</label>
          <textarea value={transcript} onChange={(e) => setTranscript(e.target.value)} style={{ width: "100%", minHeight: 100 }} />
          <label>Trigger utterance (optional — what prompted this clip)</label>
          <input value={trigger} onChange={(e) => setTrigger(e.target.value)} style={{ width: "100%" }} />
          <label>Addressee (optional)</label>
          <input value={addressee} onChange={(e) => setAddressee(e.target.value)} style={{ width: "100%" }} />
        </div>
        <div className="card">
          <label>Category</label>
          <select value={category} onChange={(e) => setCategory(e.target.value)} style={{ width: "100%" }}>
            {tags.map((a) => (
              <option key={a.key} value={a.key}>
                {a.shortcut != null ? `${a.shortcut}. ` : ""}{a.label}
              </option>
            ))}
          </select>
          <label>Quality (1-5)</label>
          <input type="number" min={1} max={5} value={quality} onChange={(e) => setQuality(Number(e.target.value))} style={{ width: "100%" }} />
          <div className="hint" style={{ marginTop: 12, lineHeight: 1.6 }}>
            <div><strong>Space</strong> play / pause</div>
            <div><strong>Enter</strong> approve</div>
            <div><strong>X</strong> reject</div>
            <div><strong>1–9</strong> category shortcut</div>
            <div><strong>0</strong> unclassified</div>
          </div>
        </div>
      </div>
    </div>
  );
}
