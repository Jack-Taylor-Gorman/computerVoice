import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Routes, Route, NavLink, Navigate } from "react-router-dom";
import { Library } from "./pages/Library";
import { ClipEditor } from "./pages/ClipEditor";
import { Record as RecordPage } from "./pages/Record";
import { Export as ExportPage } from "./pages/Export";
import { Projects } from "./pages/Projects";

const qc = new QueryClient();

function Shell() {
  const link = ({ isActive }: { isActive: boolean }) => (isActive ? "active" : "");
  return (
    <div style={{ display: "grid", gridTemplateRows: "56px 1fr", height: "100%" }}>
      <nav className="top">
        <span className="brand">Voice Dataset Builder</span>
        <NavLink to="/projects" className={link}>Projects</NavLink>
        <NavLink to="/library" className={link}>Library</NavLink>
        <NavLink to="/record" className={link}>Record</NavLink>
        <NavLink to="/export" className={link}>Export</NavLink>
      </nav>
      <main style={{ overflow: "auto", padding: "20px 24px", maxWidth: 1280, margin: "0 auto", width: "100%" }}>
        <Routes>
          <Route path="/" element={<Navigate to="/projects" />} />
          <Route path="/projects" element={<Projects />} />
          <Route path="/library" element={<Library />} />
          <Route path="/clip/:id" element={<ClipEditor />} />
          <Route path="/record" element={<RecordPage />} />
          <Route path="/export" element={<ExportPage />} />
        </Routes>
      </main>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={qc}>
      <BrowserRouter>
        <Shell />
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
