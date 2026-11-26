// Search (T4 + K4). First the photos whose words or text match (name, title, tags, notes, the text
// in the picture, exact pieces like part of a phone number), then photos that look like the
// description (CLIP, best match first). Clicking a photo opens its details; the arrows step
// through all results. "Show in gallery" keeps the text search as a filter there.
import { ApiError, useAi, useAssets, useSearch, useSimilar, useTeachTag } from "@imagevault/api-client";
import { Button } from "@imagevault/ui";
import type { AssetSummary, SearchResults } from "@imagevault/shared-types";
import type { UseQueryResult } from "@tanstack/react-query";
import { useCallback, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";

import { EmptyState, ErrorView, errorText, LoadingRows } from "../components/States";
import { AssetDetail } from "../detail/AssetDetail";
import { num } from "../format";
import { setFilters } from "../gallery/filters";
import { useThumbUrl } from "../gallery/thumbs";

const TEXT_LIMIT = 60;
const MAX_Q = 200; // the engine's limit for a text search

export function SearchScreen() {
  const [params] = useSearchParams();
  const q = (params.get("q") ?? "").trim();
  const ai = useAi();
  const installed = ai.data?.installed ?? false;
  const res = useSearch(installed ? q : "");
  const text = useAssets({ q: q.slice(0, MAX_Q), limit: TEXT_LIMIT });
  if (!q)
    return (
      <EmptyState title="Search your photos">
        Type words from a photo, its text or its tags (“invoice”, part of an error code or phone number), or describe it
        (“dog on a beach”).
      </EmptyState>
    );
  return <Results key={q} title={`“${q}”`} res={installed ? res : null} prompt={q} text={text} textQ={q.slice(0, MAX_Q)} />;
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

type TextResult = ReturnType<typeof useAssets>;

function Results({
  title,
  res,
  source,
  prompt,
  text,
  textQ,
}: {
  title: string;
  /** CLIP results; null when the AI models are not installed (text matches only). */
  res: UseQueryResult<SearchResults> | null;
  source?: number;
  /** Set on a search by description: offers "Teach a tag" (T5). */
  prompt?: string;
  /** K4: photos whose words or text match. */
  text?: TextResult;
  textQ?: string;
}) {
  const thumb = useThumbUrl();
  const navigate = useNavigate();
  const [keep, setKeep] = useState<number | null>(null); // teaching: how many of the best matches count
  const [openId, setOpenId] = useState<number | null>(null);
  const [full, setFull] = useState(false);
  const items = res?.data?.items ?? [];
  const textItems: AssetSummary[] = text?.data?.pages.flatMap((p) => p.items) ?? [];
  const order = [...textItems.map((a) => a.id), ...items.map((h) => h.id).filter((id) => !textItems.some((a) => a.id === id))];
  const step = useCallback(
    (d: -1 | 1) =>
      setOpenId((cur) => {
        const i = order.indexOf(cur ?? -1);
        return order[Math.min(order.length - 1, Math.max(0, i + d))] ?? cur;
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [order.join(",")],
  );

  let textBlock = null;
  if (text && textQ) {
    const more = text.hasNextPage;
    textBlock = (
      <section aria-label="Text matches" className="search-section">
        <h2 className="section-title">
          Words and text
          {textItems.length ? (
            <span className="muted small">
              {" "}
              · {more ? `${textItems.length}+` : num(textItems.length)} {textItems.length === 1 ? "photo" : "photos"}
            </span>
          ) : null}
          {textItems.length ? (
            <Button
              size="sm"
              onClick={() => {
                setFilters({ q: textQ });
                navigate("/photos");
              }}
            >
              Show in gallery
            </Button>
          ) : null}
        </h2>
        {text.isPending ? (
          <LoadingRows rows={1} label="Searching text" />
        ) : text.isError ? (
          <ErrorView what="the text search" error={text.error} onRetry={() => void text.refetch()} />
        ) : !textItems.length ? (
          <p className="muted small">No photo has these words in its name, tags, notes or text.</p>
        ) : (
          <ul className="search-grid" aria-label="Text matches">
            {textItems.map((a) => {
              const src = thumb(a.thumb_path);
              return (
                <li key={a.id}>
                  <button
                    type="button"
                    className={a.id === openId ? "selected" : undefined}
                    aria-label={a.file_name ?? a.uid}
                    title={a.file_name ?? undefined}
                    onClick={() => setOpenId(a.id)}
                  >
                    {src ? <img src={src} alt="" loading="lazy" /> : <span className="muted small">No preview</span>}
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </section>
    );
  }

  let body;
  if (res === null)
    body = (
      <p className="muted small">
        To also find photos by what they show (“dog on a beach”), download the AI models in{" "}
        <Link to="/settings">Settings → Automatic tags</Link>.
      </p>
    );
  else if (res.isPending) body = <LoadingRows rows={4} label="Searching" />;
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
          {items.length && !text ? <span className="muted"> · best {num(items.length)} matches</span> : null}
        </h1>
      </div>
      {res?.data && res.data.photos_analysed ? (
        <p className="muted small">
          Searched {num(res.data.photos_analysed)} analysed photos. Photos still waiting for automatic tags are not included yet.
        </p>
      ) : null}
      {prompt && items.length ? <TeachBar prompt={prompt} items={items} keep={keep} onKeep={setKeep} /> : null}
      <div className="gallery-wrap">
        <div className="search-scroll">
          {textBlock}
          {text ? (
            <section aria-label="Looks like" className="search-section">
              <h2 className="section-title">
                Looks like this
                {items.length ? <span className="muted small"> · best {num(items.length)} matches</span> : null}
              </h2>
              {body}
            </section>
          ) : (
            body
          )}
        </div>
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
