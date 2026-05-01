const BASE = "/api";

async function j<T>(r: Response): Promise<T> {
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}

export const api = {
  health: () => fetch(`${BASE}/health`).then(j),
  listClips: (state?: string) => fetch(`${BASE}/clips${state ? `?state=${state}` : ""}`).then(j),
  getClip: (id: string) => fetch(`${BASE}/clips/${id}`).then(j),
  trim: (id: string, start_s: number, end_s: number) =>
    fetch(`${BASE}/clips/${id}/trim`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ start_s, end_s }) }).then(j),
  putLabel: (id: string, body: any) =>
    fetch(`${BASE}/labels/${id}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then(j),
  approve: (id: string) => fetch(`${BASE}/labels/${id}/approve`, { method: "POST" }).then(j),
  reject: (id: string) => fetch(`${BASE}/labels/${id}/reject`, { method: "POST" }).then(j),
  archetypes: () => fetch(`${BASE}/labels/archetypes`).then(j),
  startExport: (body: any) =>
    fetch(`${BASE}/export`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then(j),
};
