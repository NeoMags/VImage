import {
  useCancelScan,
  useRemovalPreview,
  useRemoveSource,
  useScanSource,
  useSources,
} from "@imagevault/api-client";
import type { Source } from "@imagevault/shared-types";
import { Badge, Button, ConfirmDialog, type Tone } from "@imagevault/ui";
import { useState } from "react";

import { useAddFolder } from "../addFolder";
import { EmptyState, ErrorView, errorText, LoadingRows, Notice } from "../components/States";
import { ago, num, plural } from "../format";

export function sourceState(s: Source): { label: string; tone: Tone; title?: string } {
  if (s.status === "unreachable") return { label: "Offline", tone: "warn", title: "The drive or share is not connected" };
  if (s.scanning) return { label: `Scanning · ${num(s.last_scan?.files_seen ?? 0)} files`, tone: "info" };
  if (s.status === "paused") return { label: "Paused", tone: "neutral" };
  if (s.last_scan?.status === "failed")
    return { label: "Scan failed", tone: "danger", ...(s.last_scan.last_error ? { title: s.last_scan.last_error } : {}) };
  if (s.watching) return { label: "Live", tone: "ok", title: "Changes are picked up as they happen" };
  return { label: "Idle", tone: "neutral" };
}

export function Sources() {
  const sources = useSources();
  const add = useAddFolder();
  const scan = useScanSource();
  const cancel = useCancelScan();
  const [removing, setRemoving] = useState<Source | null>(null);
  const [notice, setNotice] = useState<{ tone: "ok" | "danger"; text: string } | null>(null);
  const shown = notice ?? add.result;

  const act = async (fn: () => Promise<unknown>, failure: string) => {
    setNotice(null);
    try {
      await fn();
    } catch (e) {
      setNotice({ tone: "danger", text: `${failure}: ${errorText(e)}` });
    }
  };

  return (
    <>
      <div className="screen-head">
        <h1 className="screen-title">Sources</h1>
        <Button variant="primary" onClick={() => void add.start()} disabled={add.busy}>
          {add.busy ? "Adding…" : "+ Add folder"}
        </Button>
      </div>
      {shown ? (
        <Notice
          tone={shown.tone}
          onClose={() => {
            setNotice(null);
            add.clear();
          }}
        >
          {shown.text}
        </Notice>
      ) : null}

      {sources.isPending ? (
        <LoadingRows label="Loading folders" />
      ) : sources.isError ? (
        <ErrorView what="your folders" error={sources.error} onRetry={() => void sources.refetch()} />
      ) : sources.data.length === 0 ? (
        <EmptyState title="No folders yet">Add a folder and ImageVault will index the photos in it.</EmptyState>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>Folder</th>
              <th className="num">Files</th>
              <th>Status</th>
              <th>Last scan</th>
              <th aria-label="Actions" />
            </tr>
          </thead>
          <tbody>
            {sources.data.map((s) => {
              const st = sourceState(s);
              const missing = (s.files.missing ?? 0) + (s.files.moved ?? 0);
              return (
                <tr key={s.id}>
                  <td>
                    <div className="strong">{s.display_name}</div>
                    <div className="muted small mono">{s.root_path}</div>
                  </td>
                  <td className="num">
                    {num(s.files.present ?? 0)}
                    {missing ? <div className="muted small">{num(missing)} missing</div> : null}
                  </td>
                  <td title={st.title}>
                    <Badge tone={st.tone}>{st.label}</Badge>
                  </td>
                  <td className="muted">{s.scanning ? "now" : ago(s.last_scan?.finished_at ?? s.last_scan_at)}</td>
                  <td className="actions">
                    {s.scanning ? (
                      <Button size="sm" onClick={() => void act(() => cancel.mutateAsync(s.id), "Could not stop the scan")}>
                        Stop scan
                      </Button>
                    ) : (
                      <Button
                        size="sm"
                        disabled={s.status === "unreachable"}
                        onClick={() => void act(() => scan.mutateAsync(s.id), "Could not start a scan")}
                      >
                        Scan now
                      </Button>
                    )}
                    <Button size="sm" variant="danger" onClick={() => setRemoving(s)}>
                      Remove
                    </Button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      {removing ? (
        <RemoveDialog
          source={removing}
          onClose={() => setRemoving(null)}
          onDone={(text) => {
            setRemoving(null);
            setNotice({ tone: "ok", text });
          }}
        />
      ) : null}
    </>
  );
}

function RemoveDialog({ source, onClose, onDone }: { source: Source; onClose: () => void; onDone: (msg: string) => void }) {
  const preview = useRemovalPreview(source.id);
  const remove = useRemoveSource();
  const p = preview.data;
  return (
    <ConfirmDialog
      open
      onOpenChange={(o) => (o ? null : onClose())}
      title={`Remove “${source.display_name}”?`}
      confirmLabel="Remove folder"
      danger
      busy={remove.isPending}
      confirmDisabled={!p}
      onConfirm={async () => {
        if (!p) return;
        try {
          const out = await remove.mutateAsync({ sourceId: source.id, confirm: p.assets_only_here });
          onDone(`Removed ${source.display_name}: ${plural(out.assets_deleted, "photo")} left the library.`);
        } catch {
          /* shown below; the count may have changed since the preview, which refreshes */
          void preview.refetch();
        }
      }}
    >
      {preview.isPending ? (
        <LoadingRows rows={2} label="Counting photos" />
      ) : preview.isError ? (
        <ErrorView what="what would be removed" error={preview.error} onRetry={() => void preview.refetch()} />
      ) : (
        <>
          <p className="mono small">{p!.root_path}</p>
          <p>
            ImageVault stops watching this folder. <strong>{plural(p!.assets_only_here, "photo")}</strong> found only
            here will leave the library, with their tags, notes and thumbnails. Photos that also exist in another folder
            stay.
          </p>
          <p>Your files on disk are not touched.</p>
          {remove.isError ? <p className="text-danger">{errorText(remove.error)}</p> : null}
        </>
      )}
    </ConfirmDialog>
  );
}
