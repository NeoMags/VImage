// Backups, restore and export (B19): Settings panels, and the screen shown when the library
// could not be opened at start.
import {
  useBackupNow,
  useBackupOptions,
  useBackups,
  useExport,
  useRestore,
  useSaveBackupOptions,
  useTaskProgress,
} from "@imagevault/api-client";
import type { Backup } from "@imagevault/shared-types";
import { Button, Panel } from "@imagevault/ui";
import { useState } from "react";

import { ErrorView, errorText, LoadingRows, Notice } from "../components/States";
import { ago, bytes, num, plural } from "../format";
import { pickBackupFile, pickFolder, pickSavePath, revealInExplorer } from "../native";

const KIND_LABEL: Record<Backup["kind"], string> = {
  auto: "Automatic",
  manual: "Made by you",
  "pre-restore": "Before a restore",
  "pre-migration": "Before an update",
  damaged: "Damaged (set aside)",
};

type Target = { name: string; label: string } | { path: string; label: string };

/** Asks before replacing the library; says what will happen. */
function ConfirmRestore({ target, onDone, onCancel }: { target: Target; onDone: (msg: string) => void; onCancel: () => void }) {
  const restore = useRestore();
  const body = "name" in target ? { name: target.name } : { path: target.path };
  return (
    <div className="confirm" role="alertdialog" aria-label="Confirm restore">
      <p>
        <strong>Replace your library with {target.label}?</strong> Tags, notes, collections, titles and hidden photos go
        back to how they were then. Your photo files are not touched. A safety copy of the library as it is now is
        made first, so you can undo this.
      </p>
      <div className="row">
        <Button
          variant="primary"
          size="sm"
          disabled={restore.isPending}
          onClick={() =>
            restore.mutate(body, {
              onSuccess: (r) =>
                onDone(
                  `Restored from ${r.restored_from}. Safety copy: ${r.pre_restore}.` +
                    (r.thumbnails_requeued ? ` ${plural(r.thumbnails_requeued, "thumbnail")} will be made again.` : ""),
                ),
            })
          }
        >
          {restore.isPending ? "Restoring…" : "Restore"}
        </Button>
        <Button size="sm" disabled={restore.isPending} onClick={onCancel}>
          Cancel
        </Button>
      </div>
      {restore.isError ? <p className="text-danger small">Restore failed: {errorText(restore.error)}</p> : null}
    </div>
  );
}

function BackupList({ onRestored, showDamaged = true }: { onRestored: (msg: string) => void; showDamaged?: boolean }) {
  const list = useBackups();
  const [target, setTarget] = useState<Target | null>(null);
  const [pickErr, setPickErr] = useState<string | null>(null);
  if (list.isPending) return <LoadingRows rows={3} label="Loading backups" />;
  if (list.isError) return <ErrorView what="the backups" error={list.error} onRetry={() => void list.refetch()} />;
  const rows = list.data.backups.filter((b) => showDamaged || b.kind !== "damaged");
  return (
    <div className="stack">
      {rows.length === 0 ? (
        <p className="muted small">No backups yet. The first automatic one is made the next time the app starts.</p>
      ) : (
        <table className="table compact" aria-label="Backups">
          <thead>
            <tr>
              <th>Made</th>
              <th>Kind</th>
              <th className="num">Size</th>
              <th className="actions" />
            </tr>
          </thead>
          <tbody>
            {rows.map((b) => (
              <tr key={b.name}>
                <td title={b.name}>
                  {new Date(b.created_at).toLocaleString()} <span className="muted small">· {ago(b.created_at)}</span>
                </td>
                <td>{KIND_LABEL[b.kind]}</td>
                <td className="num">{bytes(b.size)}</td>
                <td className="actions">
                  {b.kind !== "damaged" ? (
                    <Button
                      size="sm"
                      onClick={() => setTarget({ name: b.name, label: `the backup from ${new Date(b.created_at).toLocaleString()}` })}
                    >
                      Restore…
                    </Button>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="row">
        <Button
          size="sm"
          onClick={() => {
            setPickErr(null);
            pickBackupFile()
              .then((path) => path && setTarget({ path, label: path }))
              .catch((e) => setPickErr(errorText(e)));
          }}
        >
          Restore from a file…
        </Button>
        <Button size="sm" onClick={() => void revealInExplorer(list.data.folder)}>
          Show backups in Explorer
        </Button>
      </div>
      {pickErr ? <p className="text-danger small">{pickErr}</p> : null}
      {target ? (
        <ConfirmRestore
          target={target}
          onCancel={() => setTarget(null)}
          onDone={(msg) => {
            setTarget(null);
            onRestored(msg);
          }}
        />
      ) : null}
    </div>
  );
}

export function BackupPanel() {
  const opts = useBackupOptions();
  const saveOpts = useSaveBackupOptions();
  const backupNow = useBackupNow();
  const [notice, setNotice] = useState<{ tone: "ok" | "danger"; text: string } | null>(null);
  return (
    <Panel title="Backup">
      <div className="stack">
        <p className="muted small">
          A backup is a copy of your library database: tags, notes, collections, titles and settings. Thumbnails are not
          included; they are made again from your photos. One is made automatically each day the app runs.
        </p>
        {notice ? (
          <Notice tone={notice.tone} onClose={() => setNotice(null)}>
            {notice.text}
          </Notice>
        ) : null}
        <div className="row">
          <Button
            size="sm"
            variant="primary"
            disabled={backupNow.isPending}
            onClick={() =>
              backupNow.mutate(undefined, {
                onSuccess: (r) =>
                  setNotice(
                    r.copy_error
                      ? { tone: "danger", text: `Backup made, but the second copy failed: ${r.copy_error}` }
                      : { tone: "ok", text: `Backup made (${bytes(r.backup.size)}).` },
                  ),
                onError: (e) => setNotice({ tone: "danger", text: `Backup failed: ${errorText(e)}` }),
              })
            }
          >
            {backupNow.isPending ? "Backing up…" : "Back up now"}
          </Button>
        </div>
        {opts.isPending ? (
          <LoadingRows rows={1} label="Loading backup settings" />
        ) : opts.isError ? (
          <ErrorView what="backup settings" error={opts.error} onRetry={() => void opts.refetch()} />
        ) : (
          <dl className="kv">
            <dt>Keep</dt>
            <dd>
              <select
                aria-label="Automatic backups to keep"
                value={opts.data.keep ?? 7}
                disabled={saveOpts.isPending}
                onChange={(e) => saveOpts.mutate({ ...opts.data, keep: Number(e.target.value) })}
              >
                {[3, 7, 14, 30].map((n) => (
                  <option key={n} value={n}>
                    last {n} automatic backups
                  </option>
                ))}
              </select>
            </dd>
            <dt>Second copy</dt>
            <dd>
              {opts.data.copy_to ? <span className="mono small">{opts.data.copy_to}</span> : <span className="muted">Off</span>}
              <Button
                size="sm"
                disabled={saveOpts.isPending}
                onClick={() =>
                  void pickFolder("Also copy backups to this folder").then(
                    (f) => f && saveOpts.mutate({ ...opts.data, copy_to: f }),
                  )
                }
              >
                Choose…
              </Button>
              {opts.data.copy_to ? (
                <Button size="sm" disabled={saveOpts.isPending} onClick={() => saveOpts.mutate({ ...opts.data, copy_to: null })}>
                  Turn off
                </Button>
              ) : null}
            </dd>
          </dl>
        )}
        {saveOpts.isError ? <p className="text-danger small">{errorText(saveOpts.error)}</p> : null}
        <p className="muted small">A second copy on another drive (for example your backup HDD) protects against a disk failure.</p>
        <BackupList onRestored={(text) => setNotice({ tone: "ok", text })} />
      </div>
    </Panel>
  );
}

export function ExportPanel() {
  const [format, setFormat] = useState<"json" | "csv">("json");
  const [includeHidden, setIncludeHidden] = useState(false);
  const run = useExport();
  const progress = useTaskProgress("export");
  const stamp = new Date().toISOString().slice(0, 10);
  return (
    <Panel title="Export">
      <div className="stack">
        <p className="muted small">
          JSON has everything per photo (paths, metadata, tags, notes, collections), with AI suggestions labelled. CSV has
          one row per photo for a spreadsheet. Exports are for reading elsewhere; to bring a library back, use a backup.
        </p>
        <div className="row">
          <fieldset className="radios">
            <legend className="sr-only">Format</legend>
            {(["json", "csv"] as const).map((f) => (
              <label key={f} className="check">
                <input type="radio" name="export-format" checked={format === f} onChange={() => setFormat(f)} />
                {f.toUpperCase()}
              </label>
            ))}
          </fieldset>
          <label className="check">
            <input type="checkbox" checked={includeHidden} onChange={(e) => setIncludeHidden(e.target.checked)} />
            Include hidden photos
          </label>
          <Button
            size="sm"
            variant="primary"
            disabled={run.isPending}
            onClick={() =>
              void pickSavePath(`ImageVault-${stamp}.${format}`, format).then(
                (path) => path && run.mutate({ format, path, include_hidden: includeHidden }),
              )
            }
          >
            {run.isPending ? "Exporting…" : "Export…"}
          </Button>
        </div>
        {run.isPending && progress ? (
          <p className="small" aria-live="polite">
            {num(progress.done)} of {num(progress.total)} photos written…
          </p>
        ) : null}
        {run.isSuccess ? (
          <Notice tone="ok" onClose={() => run.reset()}>
            Exported {plural(run.data.photos, "photo")} ({bytes(run.data.bytes)}) to <span className="mono small">{run.data.path}</span>{" "}
            <button type="button" className="link" onClick={() => void revealInExplorer(run.data.path)}>
              Show in Explorer
            </button>
          </Notice>
        ) : null}
        {run.isError ? (
          <Notice tone="danger" onClose={() => run.reset()}>
            Export failed: {errorText(run.error)}
          </Notice>
        ) : null}
      </div>
    </Panel>
  );
}

/** Shown instead of the app when library.db could not be opened at start (it was set aside). */
export function NeedsRestore({ damaged, onSkip }: { damaged: string; onSkip: () => void }) {
  const [done, setDone] = useState<string | null>(null);
  return (
    <div className="stack needs-restore">
      <h1 className="screen-title">Your library could not be opened</h1>
      <p>
        The library database was damaged, so ImageVault set it aside (as <span className="mono small">{damaged}</span>, nothing
        was deleted) and started with an empty library. Your photo files are fine. Restore the latest backup to get your
        tags, notes and collections back.
      </p>
      {done ? (
        <Notice tone="ok">{done}</Notice>
      ) : (
        <Panel title="Backups">
          <BackupList showDamaged={false} onRestored={setDone} />
        </Panel>
      )}
      {!done ? (
        <div>
          <Button size="sm" onClick={onSkip}>
            Start with an empty library
          </Button>
        </div>
      ) : null}
    </div>
  );
}
