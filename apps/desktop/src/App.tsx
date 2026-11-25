import { useJobStats } from "@imagevault/api-client";
import { Badge } from "@imagevault/ui";
import { NavLink, Navigate, Route, Routes } from "react-router";

import { useLiveState } from "./engine";
import { num } from "./format";
import { Assets } from "./screens/Assets";
import { Dashboard } from "./screens/Dashboard";
import { Processing } from "./screens/Processing";
import { Settings } from "./screens/Settings";
import { Sources } from "./screens/Sources";

export function App() {
  return (
    <div className="app">
      <Header />
      <nav className="sidebar" aria-label="Main">
        <NavLink to="/dashboard">Dashboard</NavLink>
        <NavLink to="/photos">All photos</NavLink>
        <NavLink to="/sources">Sources</NavLink>
        <NavLink to="/processing">
          Processing <QueueBadge />
        </NavLink>
        <NavLink to="/settings">Settings</NavLink>
      </nav>
      <main className="content">
        <EngineBanner />
        <Routes>
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/photos" element={<Assets />} />
          <Route path="/sources" element={<Sources />} />
          <Route path="/processing" element={<Processing />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="*" element={<Navigate to="/dashboard" replace />} />
        </Routes>
      </main>
    </div>
  );
}

function Header() {
  const stats = useJobStats();
  const jobs = stats.data?.jobs;
  const busy = jobs ? (jobs.queued ?? 0) + (jobs.running ?? 0) : 0;
  const paused = stats.data?.queue?.paused;
  return (
    <header className="header">
      <span className="brand">ImageVault</span>
      <span className="spacer" />
      {jobs ? (
        <span className="muted small" aria-live="polite">
          {paused ? "Processing paused" : busy ? `Processing · ${num(busy)} waiting` : "Idle"}
        </span>
      ) : null}
    </header>
  );
}

function QueueBadge() {
  const stats = useJobStats();
  const jobs = stats.data?.jobs;
  if (!jobs) return null;
  const failed = (jobs.failed ?? 0) + (jobs.dead ?? 0);
  if (failed) return <Badge tone="danger">{num(failed)}</Badge>;
  const waiting = (jobs.queued ?? 0) + (jobs.running ?? 0);
  return waiting ? <Badge tone="info">{num(waiting)}</Badge> : null;
}

/** Shown while the live connection to the engine is down (it reconnects by itself). */
function EngineBanner() {
  const live = useLiveState();
  if (live !== "reconnecting") return null;
  return (
    <div className="banner" role="status">
      Reconnecting to the ImageVault engine… Changes will show again in a moment.
    </div>
  );
}
