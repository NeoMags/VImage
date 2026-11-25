import {
  type JobFilter,
  useJobs,
  useJobStats,
  useResources,
  useRetryAllFailed,
  useRetryJob,
  useSaveResources,
  useSetPaused,
} from "@imagevault/api-client";
import type { Resources } from "@imagevault/shared-types";
import { Button, Panel, Stat } from "@imagevault/ui";
import { useEffect, useState } from "react";

import { EmptyState, ErrorView, errorText, LoadingRows, Notice } from "../components/States";
import { ago, num } from "../format";

const STAGES = ["identify", "metadata", "hash", "thumbnail"];
const FILTERS: { label: string; value: JobFilter["status"] }[] = [
  { label: "Failed", value: "failed" },
  { label: "Gave up", value: "dead" },
  { label: "Waiting", value: "queued" },
  { label: "Running", value: "running" },
];

export function Processing() {
  const stats = useJobStats();
  const pause = useSetPaused();
  const retryAll = useRetryAllFailed();
  const [notice, setNotice] = useState<{ tone: "ok" | "danger"; text: string } | null>(null);

  const jobs = stats.data?.jobs ?? {};
  const failed = (jobs.failed ?? 0) + (jobs.dead ?? 0);
  const paused = stats.data?.queue?.paused ?? false;
  const gate = stats.data?.queue?.gate;

  const run = async (fn: () => Promise<unknown>, ok: string | null, failure: string) => {
    setNotice(null);
    try {
      await fn();
      if (ok) setNotice({ tone: "ok", text: ok });
    } catch (e) {
      setNotice({ tone: "danger", text: `${failure}: ${errorText(e)}` });
    }
  };

  return (
    <>
      <div className="screen-head">
        <h1 className="screen-title">Processing</h1>
        <Button
          onClick={() => void run(() => pause.mutateAsync(!paused), null, "Could not change the queue")}
          disabled={!stats.data || pause.isPending}
        >
          {paused ? "Resume processing" : "Pause processing"}
        </Button>
        <Button
          disabled={!failed || retryAll.isPending}
          onClick={() =>
            void run(
              async () => {
                const r = await retryAll.mutateAsync();
                setNotice({ tone: "ok", text: `${num(r.requeued)} job(s) queued again.` });
              },
              null,
              "Could not retry",
            )
          }
        >
          Retry all failed{failed ? ` (${num(failed)})` : ""}
        </Button>
      </div>
      {notice ? (
        <Notice tone={notice.tone} onClose={() => setNotice(null)}>
          {notice.text}
        </Notice>
      ) : null}

      {stats.isPending ? (
        <LoadingRows label="Loading the queue" />
      ) : stats.isError ? (
        <ErrorView what="the processing queue" error={stats.error} onRetry={() => void stats.refetch()} />
      ) : (
        <>
          <div className="grid">
            <Panel>
              <Stat label="Waiting" value={num(jobs.queued ?? 0)} hint={paused ? "paused" : gate ? `held: ${gate}` : undefined} />
            </Panel>
            <Panel>
              <Stat label="Running" value={num(jobs.running ?? 0)} tone={jobs.running ? "info" : "neutral"} hint={`${num(stats.data.queue?.slots ?? 0)} worker(s)`} />
            </Panel>
            <Panel>
              <Stat label="Done" value={num(jobs.succeeded ?? 0)} tone="ok" />
            </Panel>
            <Panel>
              <Stat label="Failed" value={num(failed)} tone={failed ? "danger" : "neutral"} />
            </Panel>
            <Panel>
              <Stat label="Skipped" value={num(jobs.skipped ?? 0)} hint="unsupported files" />
            </Panel>
          </div>
          <Panel title="By stage">
            <table className="table compact">
              <thead>
                <tr>
                  <th>Stage</th>
                  <th className="num">Waiting</th>
                  <th className="num">Running</th>
                  <th className="num">Done</th>
                  <th className="num">Failed</th>
                </tr>
              </thead>
              <tbody>
                {[...new Set([...STAGES, ...Object.keys(stats.data.by_stage)])].map((stage) => {
                  const b = stats.data.by_stage[stage] ?? {};
                  return (
                    <tr key={stage}>
                      <td>{stage}</td>
                      <td className="num">{num(b.queued ?? 0)}</td>
                      <td className="num">{num(b.running ?? 0)}</td>
                      <td className="num">{num(b.succeeded ?? 0)}</td>
                      <td className="num">{num((b.failed ?? 0) + (b.dead ?? 0))}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </Panel>
        </>
      )}
      <JobList onError={(text) => setNotice({ tone: "danger", text })} />
      <ResourcesForm />
    </>
  );
}

function JobList({ onError }: { onError: (text: string) => void }) {
  const [status, setStatus] = useState<JobFilter["status"]>("failed");
  const jobs = useJobs({ status });
  const retry = useRetryJob();
  const items = jobs.data?.pages.flatMap((p) => p.items) ?? [];
  const canRetry = status === "failed" || status === "dead";
  return (
    <Panel
      title={
        <span className="row">
          Jobs
          <span className="segmented" role="group" aria-label="Show jobs">
            {FILTERS.map((f) => (
              <button
                key={f.value}
                type="button"
                aria-pressed={status === f.value}
                onClick={() => setStatus(f.value)}
              >
                {f.label}
              </button>
            ))}
          </span>
        </span>
      }
    >
      {jobs.isPending ? (
        <LoadingRows label="Loading jobs" />
      ) : jobs.isError ? (
        <ErrorView what="jobs" error={jobs.error} onRetry={() => void jobs.refetch()} />
      ) : items.length === 0 ? (
        <EmptyState title={status === "failed" || status === "dead" ? "No failures" : "Nothing here"} />
      ) : (
        <>
          <table className="table compact">
            <thead>
              <tr>
                <th>File</th>
                <th>Stage</th>
                <th className="num">Tries</th>
                <th>Error</th>
                <th>When</th>
                <th aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {items.map((j) => (
                <tr key={j.id}>
                  <td className="mono small ellipsis" title={j.path ?? undefined}>
                    {j.path ?? `photo ${j.asset_id}`}
                  </td>
                  <td>{j.stage}</td>
                  <td className="num">{j.attempts}</td>
                  <td className="small ellipsis" title={j.last_error ?? undefined}>
                    {j.last_error ?? "–"}
                  </td>
                  <td className="muted small">{ago(j.finished_at ?? j.started_at)}</td>
                  <td className="actions">
                    {canRetry ? (
                      <Button
                        size="sm"
                        disabled={retry.isPending}
                        onClick={() =>
                          retry.mutateAsync(j.id).catch((e) => onError(`Could not retry: ${errorText(e)}`))
                        }
                      >
                        Retry
                      </Button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {jobs.hasNextPage ? (
            <Button size="sm" onClick={() => void jobs.fetchNextPage()} disabled={jobs.isFetchingNextPage}>
              {jobs.isFetchingNextPage ? "Loading…" : "Show older"}
            </Button>
          ) : null}
        </>
      )}
    </Panel>
  );
}

const RAM_CHOICES = [1024, 2048, 4096, 8192, 12288, 16384];

function ResourcesForm() {
  const current = useResources();
  const save = useSaveResources();
  const [draft, setDraft] = useState<Resources | null>(null);
  useEffect(() => {
    if (current.data) setDraft(current.data);
  }, [current.data]);

  if (current.isPending || !draft) return <Panel title="Resources"><LoadingRows rows={3} label="Loading resource settings" /></Panel>;
  if (current.isError) return <Panel title="Resources"><ErrorView what="resource settings" error={current.error} onRetry={() => void current.refetch()} /></Panel>;
  const dirty = JSON.stringify(draft) !== JSON.stringify(current.data);
  const set = <K extends keyof Resources>(k: K, v: Resources[K]) => setDraft({ ...draft, [k]: v });

  return (
    <Panel title="Resources">
      <form
        className="form"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate(draft);
        }}
      >
        <label>
          Worker threads
          <input
            type="number"
            min={1}
            max={64}
            value={draft.threads ?? 1}
            onChange={(e) => set("threads", Math.max(1, Math.min(64, Number(e.target.value) || 1)))}
          />
        </label>
        <label>
          Memory limit
          <select value={draft.max_ram_mb ?? 4096} onChange={(e) => set("max_ram_mb", Number(e.target.value))}>
            {[...new Set([...RAM_CHOICES, draft.max_ram_mb ?? 4096])].sort((a, b) => a - b).map((mb) => (
              <option key={mb} value={mb}>
                {mb >= 1024 ? `${mb / 1024} GB` : `${mb} MB`}
              </option>
            ))}
          </select>
        </label>
        <label>
          Graphics card
          <select value={draft.gpu ?? "auto"} onChange={(e) => set("gpu", e.target.value as Resources["gpu"])}>
            <option value="auto">Automatic</option>
            <option value="directml">DirectML (GPU)</option>
            <option value="cpu">Processor only</option>
          </select>
        </label>
        <label className="check">
          <input type="checkbox" checked={draft.only_when_idle ?? false} onChange={(e) => set("only_when_idle", e.target.checked)} />
          Only when the PC is idle
        </label>
        <label className="check">
          <input type="checkbox" checked={draft.only_on_ac ?? false} onChange={(e) => set("only_on_ac", e.target.checked)} />
          Only when plugged in
        </label>
        <div className="row">
          <Button type="submit" variant="primary" disabled={!dirty || save.isPending}>
            {save.isPending ? "Saving…" : "Save"}
          </Button>
          {dirty ? (
            <Button onClick={() => setDraft(current.data)}>Undo changes</Button>
          ) : save.isSuccess ? (
            <span className="muted small" role="status">Saved</span>
          ) : null}
          {save.isError ? <span className="text-danger small">{errorText(save.error)}</span> : null}
        </div>
      </form>
    </Panel>
  );
}
