import { useSources, useStats } from "@imagevault/api-client";
import { Button, Panel, Stat } from "@imagevault/ui";
import { Link } from "react-router";

import { useAddFolder } from "../addFolder";
import { EmptyState, ErrorView, LoadingRows, Notice } from "../components/States";
import { ago, num } from "../format";

export function Dashboard() {
  const stats = useStats();
  const sources = useSources();
  const add = useAddFolder();

  if (stats.isPending || sources.isPending) {
    return (
      <Screen>
        <LoadingRows rows={6} label="Loading library counters" />
      </Screen>
    );
  }
  if (stats.isError || sources.isError) {
    return (
      <Screen>
        <ErrorView
          what="the library counters"
          error={stats.error ?? sources.error}
          onRetry={() => {
            void stats.refetch();
            void sources.refetch();
          }}
        />
      </Screen>
    );
  }

  const s = stats.data;
  const p = s.photos;
  const jobs = s.jobs;
  const waiting = (jobs.queued ?? 0) + (jobs.running ?? 0);
  const failed = (jobs.failed ?? 0) + (jobs.dead ?? 0);
  const offlineFolders = sources.data.filter((x) => x.status === "unreachable").length;

  if (sources.data.length === 0) {
    return (
      <Screen>
        {add.result ? (
          <Notice tone={add.result.tone} onClose={add.clear}>
            {add.result.text}
          </Notice>
        ) : null}
        <EmptyState
          title="Add your first folder"
          action={
            <Button variant="primary" onClick={() => void add.start()} disabled={add.busy}>
              {add.busy ? "Adding…" : "Add folder…"}
            </Button>
          }
        >
          ImageVault indexes the photos in folders you choose. Your files stay where they are and are never
          changed.
        </EmptyState>
      </Screen>
    );
  }

  return (
    <Screen>
      <div className="grid">
        <Panel>
          <Stat label="Photos" value={num(p.total)} hint={p.hidden ? `${num(p.hidden)} hidden` : undefined} />
        </Panel>
        <Panel>
          <Stat label="Available" value={num(p.present)} tone="ok" hint="file found on disk" />
        </Panel>
        <Panel>
          <Stat
            label="On offline drives"
            value={num(p.unreachable)}
            tone={p.unreachable ? "warn" : "neutral"}
            hint={offlineFolders ? `${num(offlineFolders)} folder(s) offline` : "all folders reachable"}
          />
        </Panel>
        <Panel>
          <Stat label="Missing" value={num(p.missing)} tone={p.missing ? "warn" : "neutral"} hint="kept, file not found" />
        </Panel>
        <Panel>
          <Stat label="Favourites" value={num(p.favorites)} />
        </Panel>
        <Panel>
          <Stat
            label="Processing"
            value={num(waiting)}
            tone={waiting ? "info" : "neutral"}
            hint={s.queue?.paused ? "paused" : waiting ? "jobs waiting" : "all done"}
          />
        </Panel>
        <Panel>
          <Stat
            label="Failed"
            value={failed ? <Link to="/processing">{num(failed)}</Link> : "0"}
            tone={failed ? "danger" : "neutral"}
            hint={failed ? "see Processing" : "no errors"}
          />
        </Panel>
        <Panel>
          <Stat
            label="Folders"
            value={num(sources.data.length)}
            hint={`last scan ${ago(s.last_scan_at)}`}
          />
        </Panel>
      </div>
      {s.files_unsupported ? (
        <p className="muted small">{num(s.files_unsupported)} files are in a format ImageVault does not read yet.</p>
      ) : null}
    </Screen>
  );
}

function Screen({ children }: { children: React.ReactNode }) {
  return (
    <>
      <h1 className="screen-title">Dashboard</h1>
      {children}
    </>
  );
}
