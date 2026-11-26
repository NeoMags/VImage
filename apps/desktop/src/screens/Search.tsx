// Search by description and "More like this" (T4). Results come from CLIP on this PC, best
// match first. Clicking a photo opens its details; the arrows step through the results.
import { ApiError, useAi, useSearch, useSimilar } from "@imagevault/api-client";
import type { SearchResults } from "@imagevault/shared-types";
import type { UseQueryResult } from "@tanstack/react-query";
import { useCallback, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";

import { EmptyState, ErrorView, LoadingRows } from "../components/States";
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
  return <Results title={`“${q.trim()}”`} res={res} />;
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

function Results({ title, res, source }: { title: string; res: UseQueryResult<SearchResults>; source?: number }) {
  const thumb = useThumbUrl();
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
                className={h.id === openId ? "selected" : undefined}
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
      <div className="gallery-wrap">
        <div className="search-scroll">{body}</div>
        {openId !== null ? (
          <AssetDetail id={openId} full={full} onToggleFull={() => setFull((f) => !f)} onClose={() => setOpenId(null)} onStep={step} />
        ) : null}
      </div>
    </div>
  );
}
