// Asset detail panel (B13, doc 07 §3): preview, what the file says, what you added, where it lives.
import {
  ApiError,
  keys,
  useAsset,
  useAssetEdits,
  useAssetPreview,
  useCollections,
  usePatchAsset,
  useRetryJob,
  useTags,
} from "@imagevault/api-client";
import type { AssetDetail as Detail } from "@imagevault/shared-types";
import { Badge, Button, type Tone } from "@imagevault/ui";
import { useQueryClient } from "@tanstack/react-query";
import { type ReactNode, useEffect, useState } from "react";
import { useNavigate } from "react-router";

import { EmptyState, ErrorView, errorText, LoadingRows } from "../components/States";
import { ago, bytes } from "../format";
import { useThumbUrl } from "../gallery/thumbs";
import { revealInExplorer } from "../native";
import { TextSection } from "./TextSection";

export interface DetailProps {
  id: number;
  full: boolean;
  onToggleFull: () => void;
  onClose: () => void;
  /** Previous / next photo in the gallery's order. */
  onStep: (delta: -1 | 1) => void;
}

export function AssetDetail({ id, full, onToggleFull, onClose, onStep }: DetailProps) {
  const asset = useAsset(id);
  const navigate = useNavigate();

  // ←/→ step through photos, Enter toggles full window, Esc closes (not while typing).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable)) return;
      if (!t?.closest?.(".detail")) return; // the gallery handles its own keys
      if (e.key === "ArrowLeft") onStep(-1);
      else if (e.key === "ArrowRight") onStep(1);
      else if (e.key === "Escape") onClose();
      else if (e.key === "Enter") onToggleFull();
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onStep, onClose, onToggleFull]);

  return (
    <aside className={`detail${full ? " full" : ""}`} aria-label="Photo details" tabIndex={-1}>
      <div className="detail-bar">
        <Button size="sm" aria-label="Previous photo" onClick={() => onStep(-1)}>
          ←
        </Button>
        <Button size="sm" aria-label="Next photo" onClick={() => onStep(1)}>
          →
        </Button>
        <span className="spacer" />
        <Button size="sm" title="Photos that look like this one" onClick={() => navigate(`/similar/${id}`)}>
          More like this
        </Button>
        <Button size="sm" onClick={onToggleFull}>
          {full ? "Side panel" : "Full window"}
        </Button>
        <Button size="sm" aria-label="Close details" onClick={onClose}>
          ✕
        </Button>
      </div>
      {asset.isPending ? (
        <LoadingRows rows={8} label="Loading photo details" />
      ) : asset.isError ? (
        asset.error instanceof ApiError && asset.error.status === 404 ? (
          <EmptyState title="This photo is no longer in the library">Its folder may have been removed.</EmptyState>
        ) : (
          <ErrorView what="this photo" error={asset.error} onRetry={() => void asset.refetch()} />
        )
      ) : (
        <Body key={id} a={asset.data} full={full} />
      )}
    </aside>
  );
}

function Body({ a, full }: { a: Detail; full: boolean }) {
  return (
    <div className={full ? "detail-body two-col" : "detail-body"}>
      <Preview a={a} />
      <div className="detail-info">
        <TitleRow a={a} />
        <FromFile a={a} />
        <Yours a={a} />
        {a.kind === "image" || a.kind === "screenshot" ? <TextSection id={a.id} /> : null}
        <Locations a={a} />
        <Processing a={a} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------------- preview
function Preview({ a }: { a: Detail }) {
  const thumbUrl = useThumbUrl();
  const canPreview = a.kind === "image" || a.kind === "screenshot";
  const preview = useAssetPreview(canPreview ? a.id : null);
  const [zoom, setZoom] = useState(false);
  const small = a.derivatives.find((d) => d.kind === "thumb_512") ?? a.derivatives.find((d) => d.kind === "thumb_256");
  const src = thumbUrl(preview.data?.rel_path ?? small?.rel_path ?? null);
  return (
    <figure className="detail-preview">
      <div
        className={`preview-frame${zoom ? " zoomed" : ""}`}
        onClick={() => preview.data && setZoom(!zoom)}
        title={preview.data ? (zoom ? "Fit to panel" : "Actual size of the preview") : undefined}
      >
        {src ? <img src={src} alt={a.title.value ?? a.file_name ?? ""} draggable={false} /> : <span className="muted">No preview</span>}
      </div>
      <figcaption className="muted small" aria-live="polite">
        {!canPreview
          ? null
          : preview.isPending
            ? "Making a sharper preview…"
            : preview.isError
              ? `Showing the small thumbnail: ${errorText(preview.error)}`
              : null}
      </figcaption>
    </figure>
  );
}

// ---------------------------------------------------------------------------------- title, favourite, hide
function TitleRow({ a }: { a: Detail }) {
  const patch = usePatchAsset();
  const edits = useAssetEdits(a.id);
  const [draft, setDraft] = useState<string | null>(null);
  const shown = a.title.value ?? a.file_name ?? `Photo ${a.id}`;
  const save = () => {
    if (draft === null) return;
    const title = draft.trim();
    setDraft(null);
    if (title !== (a.title.value ?? "")) patch.mutate({ id: a.id, patch: { title: title || null } });
  };
  return (
    <div className="detail-title">
      {draft === null ? (
        <h2 title="Click to rename" onClick={() => setDraft(a.title.value ?? "")}>
          {shown}
        </h2>
      ) : (
        <input
          autoFocus
          aria-label="Title"
          maxLength={500}
          value={draft}
          placeholder={a.file_name ?? "Title"}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={save}
          onKeyDown={(e) => {
            if (e.key === "Enter") save();
            if (e.key === "Escape") setDraft(null);
          }}
        />
      )}
      {a.title.origin && a.title.value ? <OriginTag origin={a.title.origin} /> : null}
      <span className="spacer" />
      <Button
        size="sm"
        aria-pressed={a.is_favorite}
        aria-label={a.is_favorite ? "Remove from favourites" : "Add to favourites"}
        disabled={patch.isPending}
        onClick={() => patch.mutate({ id: a.id, patch: { is_favorite: !a.is_favorite } })}
      >
        {a.is_favorite ? "★" : "☆"}
      </Button>
      <Button size="sm" disabled={edits.setHidden.isPending} onClick={() => edits.setHidden.mutate(!a.is_hidden)}>
        {a.is_hidden ? "Unhide" : "Hide"}
      </Button>
      {patch.isError ? <p className="text-danger small">{errorText(patch.error)}</p> : null}
      {edits.setHidden.isError ? <p className="text-danger small">{errorText(edits.setHidden.error)}</p> : null}
    </div>
  );
}

const ORIGIN_LABEL = { source: "from the file", user: "yours", ai: "AI-suggested" } as const;
function OriginTag({ origin }: { origin: keyof typeof ORIGIN_LABEL }) {
  return <span className="muted small origin">{ORIGIN_LABEL[origin]}</span>;
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="detail-section">
      <h3>{title}</h3>
      {children}
    </section>
  );
}

// ---------------------------------------------------------------------------------- from the file
function FromFile({ a }: { a: Detail }) {
  const m = a.metadata;
  const camera = [m?.camera_make, m?.camera_model].filter(Boolean).join(" ");
  const exposure = [
    m?.exposure_s ? (m.exposure_s < 1 ? `1/${Math.round(1 / m.exposure_s)} s` : `${m.exposure_s} s`) : null,
    m?.f_number ? `f/${m.f_number}` : null,
    m?.iso ? `ISO ${m.iso}` : null,
    m?.focal_length_mm ? `${m.focal_length_mm} mm` : null,
  ]
    .filter(Boolean)
    .join(" · ");
  const rows: [string, ReactNode][] = [
    ["Size", [a.width && a.height ? `${a.width}×${a.height}` : null, a.mime_type.split("/")[1]?.toUpperCase(), bytes(a.byte_size)].filter(Boolean).join(" · ")],
    [
      "Taken",
      a.taken_at.value ? (
        <>
          {new Date(a.taken_at.value).toLocaleString()} {a.taken_at.origin ? <OriginTag origin={a.taken_at.origin} /> : null}
        </>
      ) : (
        <span className="muted">not in the file{a.date ? ` · sorted by ${new Date(a.date).toLocaleDateString()}` : ""}</span>
      ),
    ],
  ];
  if (camera) rows.push(["Camera", camera]);
  if (m?.lens) rows.push(["Lens", m.lens]);
  if (exposure) rows.push(["Exposure", exposure]);
  if (m?.gps_lat != null && m.gps_lon != null) rows.push(["Location", `${m.gps_lat.toFixed(5)}, ${m.gps_lon.toFixed(5)}`]);
  if (m?.color_profile) rows.push(["Colour profile", m.color_profile]);
  return (
    <Section title="From the file">
      <dl className="kv">
        {rows.map(([k, v]) => (
          <div key={k} className="kv-row">
            <dt>{k}</dt>
            <dd>{v}</dd>
          </div>
        ))}
      </dl>
    </Section>
  );
}

// ---------------------------------------------------------------------------------- yours
function Yours({ a }: { a: Detail }) {
  const edits = useAssetEdits(a.id);
  const tags = useTags();
  const collections = useCollections();
  const [tag, setTag] = useState("");
  const [note, setNote] = useState("");
  const [editing, setEditing] = useState<{ id: number; body: string } | null>(null);
  const failed = Object.values(edits).find((m) => m.isError);
  const inColl = new Set(a.collections.map((c) => c.id));
  // T3: AI tags you have not accepted are shown apart; accepting one moves it to your tags.
  const suggested = a.tags.filter((t) => t.origin === "ai" && !t.confirmed);
  const mine = a.tags.filter((t) => !(t.origin === "ai" && !t.confirmed));
  return (
    <Section title="Yours">
      {suggested.length ? (
        <div className="chips" aria-label="Suggested by AI">
          <span className="muted small">Suggested by AI:</span>
          {suggested.map((t) => (
            <span key={t.id} className="chip chip-ai" title={t.confidence != null ? `Confidence ${Math.round(t.confidence * 100)}%` : undefined}>
              {t.name}
              <button type="button" aria-label={`Accept tag ${t.name}`} onClick={() => edits.addTags.mutate([t.name])}>
                ✓
              </button>
              <button type="button" aria-label={`Remove tag ${t.name}`} onClick={() => edits.removeTag.mutate(t.id)}>
                ×
              </button>
            </span>
          ))}
        </div>
      ) : null}
      <div className="chips" aria-label="Tags">
        {mine.map((t) => (
          <span key={t.id} className="chip">
            {t.name}
            {t.origin === "source" ? <OriginTag origin={t.origin} /> : null}
            <button type="button" aria-label={`Remove tag ${t.name}`} onClick={() => edits.removeTag.mutate(t.id)}>
              ×
            </button>
          </span>
        ))}
        <form
          className="row"
          onSubmit={(e) => {
            e.preventDefault();
            const name = tag.trim();
            if (name) edits.addTags.mutate([name], { onSuccess: () => setTag("") });
          }}
        >
          <input className="inline-input" aria-label="Add a tag" placeholder="Add a tag…" list="iv-detail-tags" maxLength={100} value={tag} onChange={(e) => setTag(e.target.value)} />
          <datalist id="iv-detail-tags">{tags.data?.map((t) => <option key={t.id} value={t.name} />)}</datalist>
        </form>
      </div>

      <div className="chips" aria-label="Collections">
        {a.collections.map((c) => (
          <span key={c.id} className="chip">
            ▣ {c.name}
            <button type="button" aria-label={`Remove from ${c.name}`} onClick={() => edits.removeFromCollection.mutate(c.id)}>
              ×
            </button>
          </span>
        ))}
        {collections.data?.some((c) => !inColl.has(c.id)) ? (
          <select
            aria-label="Add to collection"
            value=""
            onChange={(e) => e.target.value && edits.addToCollection.mutate(Number(e.target.value))}
          >
            <option value="">Add to collection…</option>
            {collections.data.filter((c) => !inColl.has(c.id)).map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        ) : null}
      </div>

      <div className="notes" aria-label="Notes">
        {a.notes.map((n) =>
          editing?.id === n.id ? (
            <form
              key={n.id}
              className="stack"
              onSubmit={(e) => {
                e.preventDefault();
                if (editing.body.trim()) edits.editNote.mutate({ noteId: n.id, body: editing.body }, { onSuccess: () => setEditing(null) });
              }}
            >
              <textarea aria-label="Edit note" value={editing.body} maxLength={20000} onChange={(e) => setEditing({ id: n.id, body: e.target.value })} />
              <div className="row">
                <Button size="sm" type="submit" variant="primary">
                  Save
                </Button>
                <Button size="sm" onClick={() => setEditing(null)}>
                  Cancel
                </Button>
              </div>
            </form>
          ) : (
            <div key={n.id} className="note">
              <p className="pre">{n.body}</p>
              <div className="row muted small">
                {ago(n.updated_at)}
                <button type="button" className="link" onClick={() => setEditing({ id: n.id, body: n.body })}>
                  Edit
                </button>
                <button type="button" className="link" onClick={() => edits.deleteNote.mutate(n.id)}>
                  Delete
                </button>
              </div>
            </div>
          ),
        )}
        <form
          className="stack"
          onSubmit={(e) => {
            e.preventDefault();
            if (note.trim()) edits.addNote.mutate(note, { onSuccess: () => setNote("") });
          }}
        >
          <textarea aria-label="New note" placeholder="Add a note…" maxLength={20000} value={note} onChange={(e) => setNote(e.target.value)} />
          {note.trim() ? (
            <Button size="sm" type="submit" disabled={edits.addNote.isPending}>
              Add note
            </Button>
          ) : null}
        </form>
      </div>
      {failed ? <p className="text-danger small">{errorText(failed.error)}</p> : null}
    </Section>
  );
}

// ---------------------------------------------------------------------------------- locations
const STATE: Record<Detail["locations"][number]["state"], [string, Tone]> = {
  present: ["Here", "ok"],
  moved: ["Moved", "neutral"],
  missing: ["Not found", "warn"],
  unreachable: ["Drive offline", "warn"],
};

function Locations({ a }: { a: Detail }) {
  const [err, setErr] = useState<string | null>(null);
  return (
    <Section title="Locations">
      {a.locations.length === 0 ? <p className="muted small">No known file.</p> : null}
      <ul className="locations">
        {a.locations.map((l) => (
          <li key={l.id}>
            <Badge tone={STATE[l.state][1]}>{STATE[l.state][0]}</Badge>
            <span className="mono small loc-path" title={l.path}>
              {l.path}
            </span>
            <Button
              size="sm"
              disabled={!l.reachable}
              title={l.reachable ? undefined : "The file cannot be reached right now"}
              onClick={() => revealInExplorer(l.path).catch((e) => setErr(errorText(e)))}
            >
              Show in Explorer
            </Button>
          </li>
        ))}
      </ul>
      {err ? <p className="text-danger small">{err}</p> : null}
    </Section>
  );
}

// ---------------------------------------------------------------------------------- processing
function Processing({ a }: { a: Detail }) {
  const retry = useRetryJob();
  const qc = useQueryClient();
  const problems = a.jobs.filter((j) => j.status === "failed" || j.status === "dead");
  if (a.status === "ready" && problems.length === 0) return null;
  return (
    <Section title="Processing">
      <p className="small">
        Status: <strong>{a.status}</strong>
        {a.error ? <span className="text-danger"> · {a.error}</span> : null}
      </p>
      <ul className="jobs">
        {a.jobs.map((j) => (
          <li key={j.id} className="small">
            {j.stage}: {j.status}
            {j.attempts > 1 ? ` (${j.attempts} tries)` : ""}
            {j.last_error ? <span className="muted"> · {j.last_error}</span> : null}
            {j.status === "failed" || j.status === "dead" ? (
              <Button
                size="sm"
                disabled={retry.isPending}
                onClick={() => retry.mutate(j.id, { onSuccess: () => void qc.invalidateQueries({ queryKey: keys.asset.one(a.id) }) })}
              >
                Retry
              </Button>
            ) : null}
          </li>
        ))}
      </ul>
      {retry.isError ? <p className="text-danger small">{errorText(retry.error)}</p> : null}
    </Section>
  );
}
