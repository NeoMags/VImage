import { useBackups, useJobStats } from "@imagevault/api-client";
import { Badge } from "@imagevault/ui";
import { useEffect, useRef, useState } from "react";
import { NavLink, Navigate, Route, Routes, useLocation, useNavigate, useParams, useSearchParams } from "react-router";

import { AiGroupsNav } from "./ai/AiGroupsNav";
import { CollectionsNav } from "./collections/CollectionsNav";
import { useLiveState } from "./engine";
import { num } from "./format";
import { Assets } from "./screens/Assets";
import { NeedsRestore } from "./screens/Backup";
import { Dashboard } from "./screens/Dashboard";
import { Processing } from "./screens/Processing";
import { SearchScreen, SimilarScreen } from "./screens/Search";
import { Settings } from "./screens/Settings";
import { Sources } from "./screens/Sources";

export function App() {
  const backups = useBackups();
  const [skipped, setSkipped] = useState(false);
  const damaged = skipped ? null : (backups.data?.needs_restore ?? null);
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
        <CollectionsNav />
        <AiGroupsNav />
      </nav>
      <main className="content">
        <EngineBanner />
        {damaged ? (
          <NeedsRestore damaged={damaged} onSkip={() => setSkipped(true)} />
        ) : (
          <Routes>
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/photos" element={<Assets />} />
            <Route path="/collections/:id" element={<CollectionScreen />} />
            <Route path="/search" element={<SearchScreen />} />
            <Route path="/similar/:id" element={<SimilarScreen />} />
            <Route path="/sources" element={<Sources />} />
            <Route path="/processing" element={<Processing />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="*" element={<Navigate to="/dashboard" replace />} />
          </Routes>
        )}
      </main>
    </div>
  );
}

function CollectionScreen() {
  const { id } = useParams();
  const n = Number(id);
  return Number.isInteger(n) && n > 0 ? <Assets key={n} collectionId={n} /> : <Navigate to="/photos" replace />;
}

function Header() {
  const stats = useJobStats();
  const jobs = stats.data?.jobs;
  const busy = jobs ? (jobs.queued ?? 0) + (jobs.running ?? 0) : 0;
  const paused = stats.data?.queue?.paused;
  return (
    <header className="header">
      <span className="brand">ImageVault</span>
      <SearchBox />
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

/** Search by words, text in photos (K4) and description (T4). Ctrl+K (or /) jumps here from anywhere; Enter shows the results. */
export function SearchBox() {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const [params] = useSearchParams();
  const current = pathname === "/search" ? (params.get("q") ?? "") : "";
  const [text, setText] = useState(current);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => setText(current), [current]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = e.target instanceof HTMLElement && (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA" || e.target.isContentEditable);
      if ((e.key === "k" && (e.ctrlKey || e.metaKey)) || (e.key === "/" && !typing)) {
        e.preventDefault();
        input.current?.focus();
        input.current?.select();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  return (
    <form
      className="search-box"
      role="search"
      onSubmit={(e) => {
        e.preventDefault();
        const q = text.trim();
        if (q) navigate(`/search?q=${encodeURIComponent(q)}`);
      }}
    >
      <input
        ref={input}
        type="search"
        aria-label="Search photos"
        placeholder="Search photos… (Ctrl+K)"
        maxLength={300}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => e.key === "Escape" && input.current?.blur()}
      />
    </form>
  );
}
