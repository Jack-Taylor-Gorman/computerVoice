import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../lib/api";

export function Library() {
  const [params, setParams] = useSearchParams();
  const state = params.get("state") || "";
  const { data, isLoading } = useQuery({ queryKey: ["clips", state], queryFn: () => api.listClips(state || undefined) });

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <div>
        <h3>Library</h3>
        <div className="hint">All clips captured, ingested, and labeled for the active project.</div>
      </div>

      <div className="card" style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <label style={{ margin: 0 }}>Filter by state</label>
        <select value={state} onChange={(e) => setParams(e.target.value ? { state: e.target.value } : {})}>
          <option value="">All</option>
          {["ingested", "vad_split", "transcribed", "labeled", "approved", "rejected", "exported"].map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
        {isLoading && <span className="hint">Loading…</span>}
      </div>

      <div className="card" style={{ padding: 0, overflow: "hidden" }}>
        <table>
          <thead>
            <tr>
              <th>Clip</th><th>State</th><th>Start</th><th>End</th><th>SNR</th><th>LUFS</th>
            </tr>
          </thead>
          <tbody>
            {(data as any[] | undefined)?.map((c) => (
              <tr key={c.id}>
                <td><Link to={`/clip/${c.id}`}>{c.id.slice(0, 8)}</Link></td>
                <td><span className={`pill ${c.state === "approved" ? "on" : ""}`}>{c.state}</span></td>
                <td>{c.start_s?.toFixed(2)}</td>
                <td>{c.end_s?.toFixed(2)}</td>
                <td>{c.snr_db?.toFixed(1) ?? "—"}</td>
                <td>{c.lufs?.toFixed(1) ?? "—"}</td>
              </tr>
            ))}
            {!isLoading && (data as any[] | undefined)?.length === 0 && (
              <tr><td colSpan={6}><span className="hint">No clips yet. Import audio or record a session.</span></td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
