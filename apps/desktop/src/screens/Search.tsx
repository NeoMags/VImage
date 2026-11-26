// Search by description and "More like this" (T4). Results come from CLIP on this PC, best
// match first. Clicking a photo opens its details; the arrows step through the results.
import { ApiError, useAi, useSearch, useSimilar, useTeachTag } from "@imagevault/api-client";
import { Button } from "@imagevault/ui";
import type { SearchResults } from "@imagevault/shared-types";
import type { UseQueryResult } from "@tanstack/react-query";
import { useCallback, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";

import { EmptyState, ErrorView, errorText, LoadingRows } from "../components/States";
import { AssetDetail } from "../detail/AssetDetail";
import { num } from "../format";
import { useThumbUrl } from "../gallery/thumbs";

export function SearchScreen() {
  const [params] = useSearchParams();
  const q = params.get("q") ?? "";
  const ai = useAi();
  const res = useSearch(q);
  if (!q.trim()) return <EmptyState title="Search your photos">Type what you are looking for in the search box above, for example “dog on a beach”.</EmptyState>;
  if (ai.data && !ai.data.installed) return <NeedsModels />;
  return <Results key={q.trim()} title={`“${q.trim()}”`} res={res} prompt={q.trim()} />;
}

export function SimilarScreen() {
  const { id } = useParams();
  const n = Number(id);
  const res = useSimilar(n);
  return <Results key={n} title="More like this" res={res} source={n} />;
}

function NeedsModels() {
  return (
    <EmptyState title="Search by description needs the AI models">
      Download them once in <Link to="/settings">Settings → Automatic tags</Link>. After that, search works offline.
    </EmptyState>
  );
}

function Results({
  title,
  res,
  source,
  prompt,
}: {
  title: string;
  res: UseQueryResult<SearchResults>;
  source?: number;
  /** Set on a search by description: offers "Teach a tag" (T5). */
  prompt?: string;
}) {
  const thumb = useThumbUrl();
  const [keep, setKeep] = useState<number | null>(null); // teaching: how many of the best matches count
  const [openId, setOpenId] = useState<number | null>(null);
  const [full, setFull] = useState(false);
  const items = res.data?.items ?? [];
  const step = useCallback(
    (d: -1 | 1) =>
      setOpenId((cur) => {
        const i = items.findIndex((h) => h.id === cur);
        return items[Math.min(items.length - 1, Math.max(0, i + d))]?.id ?? cur;
      }),
    [items],
  );

  let body;
  if (res.isPending) body = <LoadingRows rows={4} label="Searching" />;
  else if (res.isError) {
    const e = res.error;
    body =
      e instanceof ApiError && e.status === 409 ? (
        source != null ? (
          <EmptyState title="This photo has not been analysed yet">It will be once automatic tags have reached it.</EmptyState>
        ) : (
          <NeedsModels />
        )
      ) : (
        <ErrorView what="the search" error={e} onRetry={() => void res.refetch()} />
      );
  } else if (!items.length) {
    body = (
      <EmptyState title="Nothing to show yet">
        {res.data.photos_analysed ? "No photos matched." : "No photos have been analysed yet. Switch on automatic tags in Settings."}
      </EmptyState>
    );
  } else {
    body = (
      <ul className="search-grid" aria-label="Search results">
        {items.map((h, i) => {
          const src = thumb(h.thumb_path);
          return (
            <li key={h.id}>
              <button
                type="button"
                className={[h.id === openId ? "selected" : "", keep != null && i >= keep ? "dimmed" : ""].join(" ").trim() || undefined}
                aria-label={`${h.file_name ?? h.uid}, match ${i + 1}`}
                title={h.file_name ?? undefined}
                onClick={() => setOpenId(h.id)}
              >
                {src ? <img src={src} alt="" loading="lazy" /> : <span className="muted small">No preview</span>}
              </button>
            </li>
          );
        })}
      </ul>
    );
  }

  return (
    <div className="assets-screen">
      <div className="screen-head">
        <h1 className="screen-title">
          {title}
          {items.length ? <span className="muted"> · best {num(items.length)} matches</span> : null}
        </h1>
      </div>
      {res.data && res.data.photos_analysed ? (
        <p className="muted small">
          Searched {num(res.data.photos_analysed)} analysed photos. Photos still waiting for automatic tags are not included yet.
        </p>
      ) : null}
      {prompt && items.length ? <TeachBar prompt={prompt} items={items} keep={keep} onKeep={setKeep} /> : null}
      <div className="gallery-wrap">
        <div className="search-scroll">{body}</div>
        {openId !== null ? (
          <AssetDetail id={openId} full={full} onToggleFull={() => setFull((f) => !f)} onClose={() => setOpenId(null)} onStep={step} />
        ) : null}
      </div>
    </div>
  );
}

/** T5: name this search and tag its best N matches; new photos that match as well get the tag. */
function TeachBar({
  prompt,
  items,
  keep,
  onKeep,
}: {
  prompt: string;
  items: SearchResults["items"];
  keep: number | null;
  onKeep: (n: number | null) => void;
}) {
  const teach = useTeachTag();
  const [name, setName] = useState(prompt.slice(0, 100));
  if (keep == null)
    return (
      <div className="teach-bar">
        <Button size="sm" onClick={() => onKeep(Math.min(12, items.length))}>
          Teach a tag from this search…
        </Button>
      </div>
    );
  const n = Math.min(Math.max(1, keep), items.length);
  const threshold = items[n - 1]!.score;
  return (
    <form
      className="teach-bar"
      aria-label="Teach a tag"
      onSubmit={(e) => {
        e.preventDefault();
        if (name.trim()) teach.mutate({ name: name.trim(), prompt, threshold });
      }}
    >
      <label className="field-inline">
        Tag
        <input className="inline-input" aria-label="Tag name" maxLength={100} value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      <label className="field-inline">
        on the best
        <input
          type="number"
          aria-label="How many of the best matches"
          min={1}
          max={items.length}
          value={n}
          onChange={(e) => onKeep(Number(e.target.value) || 1)}
        />
        matches
      </label>
      <Button size="sm" variant="primary" type="submit" disabled={teach.isPending || !name.trim()}>
        Teach
      </Button>
      <Button size="sm" type="button" onClick={() => (teach.reset(), onKeep(null))}>
        Cancel
      </Button>
      <p className="muted small" role="status">
        {teach.isSuccess
          ? `Tagged ${num(teach.data.photos_tagged)} photos “${teach.data.name}”. New photos that match get it too. Undo in Settings → Automatic tags.`
          : teach.isError
            ? errorText(teach.error)
            : `Every photo that matches at least as well as match ${n} gets the tag (as an AI tag you can review), now and as new photos arrive.`}
      </p>
    </form>
  );
}
