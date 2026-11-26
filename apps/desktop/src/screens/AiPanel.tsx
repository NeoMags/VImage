// Automatic tags (T2): download the AI models once, then every photo is tagged in the background.
import { useAi, useInstallAi, useSaveAiOptions, useTaughtTags, useUntrainTag } from "@imagevault/api-client";
import { Button, Panel } from "@imagevault/ui";

import { ErrorView, errorText, LoadingRows } from "../components/States";
import { num } from "../format";

const mb = (bytes: number) => `${Math.round(bytes / 1e6)} MB`;

export function AiPanel() {
  const ai = useAi();
  const install = useInstallAi();
  const save = useSaveAiOptions();

  if (ai.isPending) return <Panel title="Automatic tags"><LoadingRows rows={2} label="Loading AI status" /></Panel>;
  if (ai.isError)
    return (
      <Panel title="Automatic tags">
        <ErrorView what="AI status" error={ai.error} onRetry={() => void ai.refetch()} />
      </Panel>
    );
  const s = ai.data;
  const d = s.download;
  const pct = d.total_bytes ? Math.floor((100 * d.done_bytes) / d.total_bytes) : 0;

  return (
    <Panel title="Automatic tags">
      <p className="small">
        ImageVault can look at each photo on this PC and add tags such as “beach”, “dog” or “receipt”, so you can
        filter by what is in them. Nothing leaves your PC; your files are never changed.
      </p>
      {!s.installed ? (
        d.state === "downloading" ? (
          <div role="status" aria-live="polite">
            <progress max={100} value={pct} aria-label="AI model download" /> {pct}% · {mb(d.done_bytes)} of{" "}
            {mb(d.total_bytes)}
            <p className="muted small">You can keep using ImageVault while this downloads.</p>
          </div>
        ) : (
          <>
            <Button variant="primary" disabled={install.isPending} onClick={() => install.mutate()}>
              {d.state === "failed" ? "Retry download" : `Download AI models (${mb(d.total_bytes)})`}
            </Button>
            {d.state === "failed" ? <p className="text-danger small">Download stopped: {d.error}</p> : null}
            <p className="muted small">A one-time download from Hugging Face. Afterwards it works offline.</p>
          </>
        )
      ) : (
        <>
          <label className="check">
            <input
              type="checkbox"
              checked={s.enabled}
              disabled={save.isPending}
              onChange={(e) => save.mutate({ enabled: e.target.checked, max_tags: s.max_tags })}
            />
            Tag my photos automatically
          </label>
          <label className="field-inline">
            Up to{" "}
            <select
              aria-label="Tags per photo"
              value={s.max_tags}
              disabled={save.isPending}
              onChange={(e) => save.mutate({ enabled: s.enabled, max_tags: Number(e.target.value) })}
            >
              {[3, 5, 8, 12].map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>{" "}
            tags per photo
          </label>
          <p className="small" role="status">
            {num(s.photos_done)} photos tagged
            {s.photos_waiting ? ` · ${num(s.photos_waiting)} waiting` : ""}
            {s.photos_failed ? ` · ${num(s.photos_failed)} could not be tagged` : ""}
          </p>
          <TaughtTags />
          <p className="muted small">
            Tags you remove from a photo are not added back. Changing the number of tags applies to photos tagged
            from now on.
          </p>
        </>
      )}
      {install.isError ? <p className="text-danger small">{errorText(install.error)}</p> : null}
      {save.isError ? <p className="text-danger small">{errorText(save.error)}</p> : null}
    </Panel>
  );
}

/** T5: tags you taught from a search, with Undo (removes the AI copies; ones you accepted stay). */
function TaughtTags() {
  const taught = useTaughtTags();
  const undo = useUntrainTag();
  const done = undo.isSuccess ? (
    <p className="muted small">Removed from {num(undo.data.photos_untagged)} photos. Tags you had accepted stay.</p>
  ) : null;
  if (!taught.data?.length) return done;
  return (
    <div>
      <h3 className="small">Tags you taught</h3>
      <ul className="taught-list" aria-label="Tags you taught">
        {taught.data.map((t) => (
          <li key={t.tag_id}>
            <strong>{t.name}</strong> <span className="muted small">from “{t.prompt}” · {num(t.photos)} photos</span>{" "}
            <Button size="sm" disabled={undo.isPending} onClick={() => undo.mutate(t.tag_id)}>
              Undo
            </Button>
          </li>
        ))}
      </ul>
      {done}
      {undo.isError ? <p className="text-danger small">{errorText(undo.error)}</p> : null}
    </div>
  );
}
