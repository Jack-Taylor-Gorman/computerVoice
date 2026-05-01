import { useEffect, useState } from "react";

type Project = {
  id: string;
  name: string;
  speaker: string;
  target_model: string;
  sample_rate: number;
  tag_preset: string;
  notes: string;
};

const DEFAULTS: Project[] = [
  { id: "new", name: "Untitled voice", speaker: "", target_model: "f5-tts", sample_rate: 24000, tag_preset: "generic", notes: "" },
];

export function Projects() {
  const [projects, setProjects] = useState<Project[]>(() => {
    const raw = localStorage.getItem("voicedb.projects");
    return raw ? JSON.parse(raw) : DEFAULTS;
  });
  const [draft, setDraft] = useState<Project>(DEFAULTS[0]);

  useEffect(() => { localStorage.setItem("voicedb.projects", JSON.stringify(projects)); }, [projects]);

  function save() {
    const id = draft.id === "new" ? crypto.randomUUID() : draft.id;
    const next: Project = { ...draft, id };
    setProjects((p) => [next, ...p.filter((x) => x.id !== id && x.id !== "new")]);
    setDraft({ ...DEFAULTS[0] });
  }

  return (
    <div style={{ display: "grid", gap: 20 }}>
      <div>
        <h3>Projects</h3>
        <div className="hint">A project scopes a dataset to one speaker and one target voice model. The rest of the app operates on the active project.</div>
      </div>

      <div className="card">
        <h3>New / edit project</h3>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
          <div>
            <label>Project name</label>
            <input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} style={{ width: "100%" }} />
            <label>Speaker / subject</label>
            <input value={draft.speaker} onChange={(e) => setDraft({ ...draft, speaker: e.target.value })} style={{ width: "100%" }} placeholder="e.g. Majel Barrett, narrator_01, myself" />
            <label>Notes</label>
            <textarea value={draft.notes} onChange={(e) => setDraft({ ...draft, notes: e.target.value })} style={{ width: "100%", minHeight: 80 }} />
          </div>
          <div>
            <label>Target voice model</label>
            <select value={draft.target_model} onChange={(e) => setDraft({ ...draft, target_model: e.target.value })} style={{ width: "100%" }}>
              <option value="f5-tts">F5-TTS (MIT, low-resource)</option>
              <option value="styletts2">StyleTTS2</option>
              <option value="xtts-v2">XTTS-v2</option>
              <option value="gpt-sovits">GPT-SoVITS</option>
              <option value="rvc">RVC (voice-conversion only)</option>
              <option value="piper">Piper</option>
              <option value="custom">Custom</option>
            </select>
            <label>Sample rate (Hz)</label>
            <select value={draft.sample_rate} onChange={(e) => setDraft({ ...draft, sample_rate: Number(e.target.value) })} style={{ width: "100%" }}>
              <option value={16000}>16000</option>
              <option value={22050}>22050</option>
              <option value={24000}>24000</option>
              <option value={44100}>44100</option>
              <option value={48000}>48000</option>
            </select>
            <label>Tag preset</label>
            <select value={draft.tag_preset} onChange={(e) => setDraft({ ...draft, tag_preset: e.target.value })} style={{ width: "100%" }}>
              <option value="generic">Generic (emotion, prosody, quality)</option>
              <option value="narration">Narration (chapter, scene, tone)</option>
              <option value="conversation">Conversation (turn, addressee, intent)</option>
              <option value="trek_computer">Trek computer archetypes</option>
              <option value="custom">Custom (defined per clip)</option>
            </select>
            <div style={{ marginTop: 16, display: "flex", gap: 8 }}>
              <button className="primary" onClick={save}>Save project</button>
              <button className="ghost" onClick={() => setDraft({ ...DEFAULTS[0] })}>Reset</button>
            </div>
          </div>
        </div>
      </div>

      <div className="card">
        <h3>Your projects</h3>
        {projects.length === 0 ? (
          <div className="hint">No projects yet.</div>
        ) : (
          <table>
            <thead><tr><th>Name</th><th>Speaker</th><th>Model</th><th>SR</th><th>Preset</th><th></th></tr></thead>
            <tbody>
              {projects.filter((p) => p.id !== "new").map((p) => (
                <tr key={p.id}>
                  <td>{p.name}</td>
                  <td>{p.speaker || <span className="hint">—</span>}</td>
                  <td>{p.target_model}</td>
                  <td>{p.sample_rate}</td>
                  <td><span className="pill">{p.tag_preset}</span></td>
                  <td style={{ textAlign: "right" }}>
                    <button className="ghost" onClick={() => setDraft(p)}>Edit</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
