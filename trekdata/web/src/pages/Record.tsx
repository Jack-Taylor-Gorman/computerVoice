import { useRef, useState } from "react";

export function Record() {
  const [recording, setRecording] = useState(false);
  const [chunks, setChunks] = useState<Blob[]>([]);
  const [elapsed, setElapsed] = useState(0);
  const mrRef = useRef<MediaRecorder | null>(null);
  const tRef = useRef<number | null>(null);

  async function start() {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: { sampleRate: 48000, channelCount: 1, echoCancellation: false, noiseSuppression: false } });
    const mr = new MediaRecorder(stream, { mimeType: "audio/webm;codecs=opus" });
    setChunks([]);
    setElapsed(0);
    mr.ondataavailable = (e) => setChunks((c) => [...c, e.data]);
    mr.start(1000);
    mrRef.current = mr;
    setRecording(true);
    const t0 = performance.now();
    tRef.current = window.setInterval(() => setElapsed((performance.now() - t0) / 1000), 100) as unknown as number;
  }
  function stop() {
    mrRef.current?.stop();
    if (tRef.current) clearInterval(tRef.current);
    setRecording(false);
  }
  async function upload() {
    const blob = new Blob(chunks, { type: "audio/webm" });
    const fd = new FormData();
    fd.append("file", blob, `record_${Date.now()}.webm`);
    await fetch("/api/sources/upload", { method: "POST", body: fd });
    setChunks([]);
  }

  return (
    <div style={{ display: "grid", gap: 16, maxWidth: 560 }}>
      <div>
        <h3>Record</h3>
        <div className="hint">Capture speech from your microphone. Upload sends the clip into the ingest pipeline.</div>
      </div>

      <div className="card" style={{ display: "grid", gap: 16, justifyItems: "center", padding: 32 }}>
        <div
          style={{
            width: 120, height: 120, borderRadius: "50%",
            background: recording ? "#EE6300" : "#ffffff",
            border: "2px solid #EE6300",
            display: "grid", placeItems: "center", transition: "background .15s",
          }}
        >
          <div style={{ fontSize: 24, fontWeight: 600, color: recording ? "#ffffff" : "#EE6300" }}>
            {elapsed.toFixed(1)}s
          </div>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          {!recording ? (
            <button className="primary" onClick={start}>Start</button>
          ) : (
            <button className="reject" onClick={stop}>Stop</button>
          )}
          <button disabled={!chunks.length || recording} onClick={upload} className="ghost">
            Upload {chunks.length ? `(${chunks.length} chunks)` : ""}
          </button>
        </div>
      </div>
    </div>
  );
}
