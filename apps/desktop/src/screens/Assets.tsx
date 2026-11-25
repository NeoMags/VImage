// All Assets (B12): timeline / grid / list over the whole library, loaded month by month.
import { keys, unwrap, useApi, useAssetMonths, useCollections, useSources, useTags } from "@imagevault/api-client";
import type { AssetFilter, AssetMonths, AssetSummary, BulkBody } from "@imagevault/shared-types";
import { Button, Skeleton } from "@imagevault/ui";
import { useQueryClient } from "@tanstack/react-query";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";

import { AssetDetail } from "../detail/AssetDetail";
import { FilterPanel } from "../gallery/FilterPanel";
import { activeCount, chipsFor, setFilters, useFilters } from "../gallery/filters";
import { EmptyState, ErrorView, errorText, LoadingRows, Notice } from "../components/States";
import { bytes, num, plural } from "../format";
import { runBulk } from "../gallery/bulk";
import {
  columnsFor,
  layoutRows,
  locate,
  type Order,
  rowOfItem,
  type Segment,
  segmentsFor,
  type Sort,
  type View,
  yearMarks,
} from "../gallery/layout";
import { SegmentLoader } from "../gallery/loader";
import * as Sel from "../gallery/selection";
import { useThumbUrl } from "../gallery/thumbs";
import { revealInExplorer } from "../native";

const SIZES = { S: 120, M: 180, L: 260 } as const;
type Size = keyof typeof SIZES;
const GAP = 6;
const HEADER_H = 44;
const LIST_ROW_H = 56;

interface Prefs {
  view: View;
  sort: Sort;
  order: Order;
  size: Size;
  filtersOpen: boolean;
}
const DEFAULT_PREFS: Prefs = { view: "timeline", sort: "date", order: "desc", size: "M", filtersOpen: false };

function loadPrefs(): Prefs {
  try {
    return { ...DEFAULT_PREFS, ...(JSON.parse(localStorage.getItem("iv.gallery") ?? "{}") as Partial<Prefs>) };
  } catch {
    return DEFAULT_PREFS;
  }
}

export function Assets() {
  const [prefs, setPrefsState] = useState<Prefs>(loadPrefs);
  const [hidden, setHidden] = useState(false);
  const setPrefs = (p: Partial<Prefs>) =>
    setPrefsState((old) => {
      const next = { ...old, ...p };
      try {
        localStorage.setItem("iv.gallery", JSON.stringify(next));
      } catch {
        /* per-viewer convenience only */
      }
      return next;
    });
  // Timeline is the date sort with month headers; other sorts have no months to show.
  const view: View = prefs.view === "timeline" && prefs.sort !== "date" ? "grid" : prefs.view;
  const extra = useFilters();
  const filter = useMemo<AssetFilter>(() => ({ ...extra, hidden }), [extra, hidden]);
  const filtered = activeCount(extra) > 0;
  const months = useAssetMonths(filter);

  return (
    <div className="assets-screen">
      <div className="screen-head">
        <h1 className="screen-title">
          {hidden ? "Hidden photos" : "All photos"}
          {months.data ? <span className="muted"> · {num(months.data.total)}</span> : null}
        </h1>
        <label className="inline-field">
          Sort
          <select value={prefs.sort} onChange={(e) => setPrefs({ sort: e.target.value as Sort })}>
            <option value="date">Date taken</option>
            <option value="indexed">Date added</option>
            <option value="size">File size</option>
          </select>
        </label>
        <Button
          size="sm"
          aria-label={prefs.order === "desc" ? "Newest or largest first" : "Oldest or smallest first"}
          onClick={() => setPrefs({ order: prefs.order === "desc" ? "asc" : "desc" })}
        >
          {prefs.order === "desc" ? "↓" : "↑"}
        </Button>
        <span className="segmented" role="group" aria-label="View">
          {(
            [
              ["timeline", "Timeline"],
              ["grid", "Grid"],
              ["list", "List"],
            ] as [View, string][]
          ).map(([v, label]) => (
            <button
              key={v}
              type="button"
              aria-pressed={view === v}
              disabled={v === "timeline" && prefs.sort !== "date"}
              title={v === "timeline" && prefs.sort !== "date" ? "Timeline needs the date sort" : undefined}
              onClick={() => setPrefs({ view: v })}
            >
              {label}
            </button>
          ))}
        </span>
        {view !== "list" ? (
          <span className="segmented" role="group" aria-label="Thumbnail size">
            {(Object.keys(SIZES) as Size[]).map((s) => (
              <button key={s} type="button" aria-pressed={prefs.size === s} onClick={() => setPrefs({ size: s })}>
                {s}
              </button>
            ))}
          </span>
        ) : null}
        <label className="check">
          <input type="checkbox" checked={hidden} onChange={(e) => setHidden(e.target.checked)} />
          Show hidden
        </label>
        <Button
          size="sm"
          aria-pressed={prefs.filtersOpen}
          aria-controls="filter-panel"
          onClick={() => setPrefs({ filtersOpen: !prefs.filtersOpen })}
        >
          Filters{filtered ? ` (${activeCount(extra)})` : ""}
        </Button>
      </div>
      {filtered ? <FilterChips /> : null}

      <div className="assets-body">
        {prefs.filtersOpen ? <FilterPanel filter={filter} onClose={() => setPrefs({ filtersOpen: false })} /> : null}
        <div className="assets-main">

      {months.isPending ? (
        <LoadingRows rows={6} label="Loading your photos" />
      ) : months.isError ? (
        <ErrorView what="your photos" error={months.error} onRetry={() => void months.refetch()} />
      ) : months.data.total === 0 && filtered ? (
        <EmptyState title="No photos match these filters">
          <Button size="sm" onClick={() => setFilters({})}>
            Clear filters
          </Button>
        </EmptyState>
      ) : months.data.total === 0 ? (
        <EmptyState title={hidden ? "No hidden photos" : "No photos yet"}>
          {hidden
            ? "Photos you hide stay on disk and show up here."
            : "Add a folder under Sources. Photos appear here as soon as they are found."}
        </EmptyState>
      ) : (
        <Gallery
          key={`${hidden}|${prefs.sort}|${prefs.order}|${JSON.stringify(extra)}`}
          filter={filter}
          months={months.data}
          updatedAt={months.dataUpdatedAt}
          sort={prefs.sort}
          order={prefs.order}
          view={view}
          cell={SIZES[prefs.size]}
          hiddenView={hidden}
        />
      )}
        </div>
      </div>
    </div>
  );
}

function FilterChips() {
  const f = useFilters();
  const tags = useTags();
  const sources = useSources();
  const collections = useCollections();
  const chips = chipsFor(f, {
    tag: (id) => tags.data?.find((t) => t.id === id)?.name,
    source: (id) => sources.data?.find((s) => s.id === id)?.display_name,
    collection: (id) => collections.data?.find((c) => c.id === id)?.name,
  });
  return (
    <div className="chips filter-chips" aria-label="Active filters">
      {chips.map((c) => (
        <span key={c.key} className="chip">
          {c.label}
          <button type="button" aria-label={`Remove filter ${c.label}`} onClick={() => setFilters(c.remove)}>
            ×
          </button>
        </span>
      ))}
      <button type="button" className="link small" onClick={() => setFilters({})}>
        Clear all
      </button>
    </div>
  );
}

interface GalleryProps {
  filter: AssetFilter;
  months: AssetMonths;
  updatedAt: number;
  sort: Sort;
  order: Order;
  view: View;
  cell: number;
  hiddenView: boolean;
}

function Gallery({ filter, months, updatedAt, sort, order, view, cell, hiddenView }: GalleryProps) {
  const api = useApi();
  const qc = useQueryClient();
  const thumbUrl = useThumbUrl();
  const scroller = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const [sel, setSel] = useState<Sel.Selection>(Sel.EMPTY);
  const [focus, setFocus] = useState(0);
  const [notice, setNotice] = useState<{ tone: "ok" | "danger"; text: string } | null>(null);
  // Detail panel (B13): which photo is open, and whether it fills the gallery area.
  const [openId, setOpenId] = useState<number | null>(null);
  const [full, setFull] = useState(false);

  const loader = useMemo(
    () =>
      new SegmentLoader((seg, cursor, limit) =>
        unwrap(
          api.GET("/v1/assets", {
            params: { query: { ...filter, ...seg.range, sort, order, limit, ...(cursor ? { cursor } : {}) } },
          }),
        ),
      ),
    [api, filter, sort, order],
  );
  useSyncExternalStore(loader.subscribe, loader.getVersion);

  // Counts changed (live event, edit, new photos): reload what is on screen.
  const firstUpdate = useRef(updatedAt);
  useEffect(() => {
    if (updatedAt !== firstUpdate.current) loader.refresh();
  }, [updatedAt, loader]);

  useLayoutEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    setWidth(el.clientWidth);
    return () => ro.disconnect();
  }, []);

  const segments = useMemo(() => segmentsFor(months, sort, order), [months, sort, order]);
  const headers = view === "timeline";
  const cols = view === "list" ? 1 : columnsFor(Math.max(0, width - GAP - 48), cell, GAP);
  const layout = useMemo(() => layoutRows(segments, cols, headers), [segments, cols, headers]);
  const total = layout.firstItem[layout.firstItem.length - 1] ?? 0;
  const rowH = view === "list" ? LIST_ROW_H : cell + GAP;

  const virtual = useVirtualizer({
    count: layout.rows.length,
    getScrollElement: () => scroller.current,
    estimateSize: (i) => (layout.rows[i]?.kind === "header" ? HEADER_H : rowH),
    overscan: 4,
  });
  useEffect(() => virtual.measure(), [rowH, cols, virtual]);

  const itemAt = useCallback(
    (index: number): AssetSummary | undefined => {
      const at = locate(layout.firstItem, index);
      return at ? loader.item(segments[at.seg]!.key, at.offset) : undefined;
    },
    [layout, segments, loader, loader.version], // version: items arrived
  );

  // Ask for what is on screen; drop months long out of view.
  const items = virtual.getVirtualItems();
  useEffect(() => {
    const visible = new Set<string>();
    const upTo = new Map<number, number>();
    for (const v of items) {
      const row = layout.rows[v.index];
      if (!row) continue;
      visible.add(segments[row.seg]!.key);
      if (row.kind === "items") upTo.set(row.seg, Math.max(upTo.get(row.seg) ?? 0, row.end - 1));
    }
    for (const [seg, end] of upTo) loader.want(segments[seg]!, end);
    loader.evict(visible);
  });

  // While the panel is open it follows the focused photo (arrows, clicks, a page that just arrived).
  const focused = itemAt(focus);
  useEffect(() => {
    if (openId !== null && focused && focused.id !== openId) setOpenId(focused.id);
  }, [focused, openId]);

  const open = (id: number) => setOpenId(id);
  const close = useCallback(() => {
    setOpenId(null);
    setFull(false);
    scroller.current?.focus();
  }, []);
  const toggleFull = useCallback(() => setFull((f) => !f), []);

  const selectedIds = [...sel.ids];
  const move = useCallback(
    (to: number) => {
      const next = Math.max(0, Math.min(total - 1, to));
      setFocus(next);
      virtual.scrollToIndex(rowOfItem(layout, segments, next, cols, headers), { align: "auto" });
    },
    [total, virtual, layout, segments, cols, headers],
  );
  const step = useCallback((d: -1 | 1) => move(focus + d), [move, focus]);

  const sendBulk = async (change: Omit<BulkBody, "ids">, what: string, ids = selectedIds) => {
    setNotice(null);
    const out = await runBulk(ids, change, (body) => unwrap(api.POST("/v1/assets:bulk", { body })));
    for (const k of [keys.assets.all, keys.asset.all, keys.stats, keys.tags, keys.collections]) void qc.invalidateQueries({ queryKey: k });
    if (out.error) {
      setNotice({
        tone: "danger",
        text: `${what}: ${num(out.done)} of ${plural(out.total, "photo")} done, then it failed: ${errorText(out.error)}. You can run it again.`,
      });
    } else {
      setNotice({ tone: "ok", text: `${what}: ${plural(out.done, "photo")}.` });
      if (change.hide !== undefined) setSel(Sel.EMPTY); // they left this view
    }
  };

  const onKey = (e: React.KeyboardEvent) => {
    const item = itemAt(focus);
    const k = e.key;
    if (k === "ArrowRight") move(focus + 1);
    else if (k === "ArrowLeft") move(focus - 1);
    else if (k === "ArrowDown" || k === "j") move(focus + cols);
    else if (k === "ArrowUp" || k === "k") move(focus - cols);
    else if (k === "Home") move(0);
    else if (k === "End") move(total - 1);
    else if (k === "Enter" && item) open(item.id);
    else if (k === "Escape") {
      if (openId !== null) close();
      else setSel(Sel.EMPTY);
    }
    else if ((k === "a" || k === "A") && (e.ctrlKey || e.metaKey)) setSel(Sel.all(total, itemAt));
    else if (k === "x" && item) setSel(Sel.toggle(sel, focus, item.id));
    else if (k === "f" && item) {
      const ids = sel.ids.size ? selectedIds : [item.id];
      void sendBulk({ set_favorite: !item.is_favorite }, item.is_favorite ? "Removed from favourites" : "Added to favourites", ids);
    } else if (k === "h" && item) {
      const ids = sel.ids.size ? selectedIds : [item.id];
      void sendBulk({ hide: !hiddenView }, hiddenView ? "Unhidden" : "Hidden", ids);
    } else if ((k === "E" || k === "e") && e.ctrlKey && e.shiftKey && item) {
      void unwrap(api.POST("/v1/assets/{asset_id}/reveal", { params: { path: { asset_id: item.id } } }))
        .then((r) => revealInExplorer(r.path))
        .catch((err) => setNotice({ tone: "danger", text: `Could not show it in Explorer: ${errorText(err)}` }));
    } else return;
    e.preventDefault();
  };

  const marks = headers ? yearMarks(segments, layout) : [];
  const height = virtual.getTotalSize();

  return (
    <>
      {notice ? (
        <Notice tone={notice.tone} onClose={() => setNotice(null)}>
          {notice.text}
        </Notice>
      ) : null}
      {sel.ids.size ? (
        <BulkBar count={sel.ids.size} hiddenView={hiddenView} onSend={sendBulk} onClear={() => setSel(Sel.EMPTY)} />
      ) : null}
      <div className="gallery-wrap">
        <div
          ref={scroller}
          className={`gallery ${view}`}
          tabIndex={0}
          role="grid"
          aria-label="Photos"
          aria-rowcount={layout.rows.length}
          aria-multiselectable
          onKeyDown={onKey}
        >
          <div style={{ height, position: "relative" }}>
            {items.map((v) => {
              const row = layout.rows[v.index]!;
              const seg = segments[row.seg]!;
              const style = { transform: `translateY(${v.start}px)`, height: v.size } as const;
              if (row.kind === "header") {
                return (
                  <div key={v.key} className="g-header" style={style} role="row">
                    <h2>{row.label}</h2>
                    <span className="muted small">{plural(row.count, "photo")}</span>
                  </div>
                );
              }
              const failed = loader.error(seg.key);
              const first = layout.firstItem[row.seg]!;
              return (
                <div key={v.key} className="g-row" style={style} role="row">
                  {failed && !loader.item(seg.key, row.start) ? (
                    <div className="g-error">
                      <span className="text-danger small">Could not load {seg.label ?? "these photos"}: {errorText(failed)}</span>
                      <Button size="sm" onClick={() => loader.retry(seg)}>
                        Try again
                      </Button>
                    </div>
                  ) : (
                    Array.from({ length: row.end - row.start }, (_, i) => {
                      const index = first + row.start + i;
                      const a = loader.item(seg.key, row.start + i);
                      return (
                        <Cell
                          key={a?.id ?? `p${index}`}
                          asset={a}
                          list={view === "list"}
                          size={cell}
                          thumb={a ? thumbUrl(a.thumb_path) : null}
                          selected={!!a && sel.ids.has(a.id)}
                          focused={index === focus}
                          onOpen={() => a && open(a.id)}
                          onClick={(e) => {
                            if (!a) return;
                            setFocus(index);
                            setSel(Sel.click(sel, index, a.id, { ctrl: e.ctrlKey || e.metaKey, shift: e.shiftKey }, itemAt));
                          }}
                        />
                      );
                    })
                  )}
                </div>
              );
            })}
          </div>
        </div>
        {marks.length > 1 ? (
          <nav className="scrubber" aria-label="Jump to year">
            {marks.map((m) => (
              <button
                key={m.year}
                type="button"
                style={{ top: `${(virtual.getOffsetForIndex(m.row, "start")?.[0] ?? 0) / Math.max(1, height) * 100}%` }}
                onClick={() => virtual.scrollToIndex(m.row, { align: "start" })}
              >
                {m.year}
              </button>
            ))}
          </nav>
        ) : null}
        {openId !== null ? (
          <AssetDetail id={openId} full={full} onToggleFull={toggleFull} onClose={close} onStep={step} />
        ) : null}
      </div>
    </>
  );
}

const STATUS_BADGE: Partial<Record<AssetSummary["status"], [string, string]>> = {
  pending: ["⟳", "Waiting to be processed"],
  processing: ["⟳", "Processing"],
  failed: ["⚠", "Processing failed"],
  unsupported: ["?", "Format not supported yet"],
};

function Cell({
  asset,
  list,
  size,
  thumb,
  selected,
  focused,
  onClick,
  onOpen,
}: {
  asset: AssetSummary | undefined;
  list: boolean;
  size: number;
  thumb: string | null;
  selected: boolean;
  focused: boolean;
  onClick: (e: React.MouseEvent) => void;
  onOpen: () => void;
}) {
  if (!asset) {
    return (
      <div className="g-cell" style={list ? undefined : { width: size, height: size }} aria-busy="true">
        <Skeleton height={size} />
      </div>
    );
  }
  const name = asset.title ?? asset.file_name ?? `Photo ${asset.id}`;
  const badge = STATUS_BADGE[asset.status];
  const where =
    asset.availability === "unreachable"
      ? (["⏏", "Its drive is not connected"] as const)
      : asset.availability === "missing"
        ? (["✕", "File not found (kept in the library)"] as const)
        : null;
  return (
    <div
      className={`g-cell${selected ? " selected" : ""}${focused ? " focused" : ""}`}
      style={list ? undefined : { width: size, height: size }}
      role="gridcell"
      aria-selected={selected}
      aria-label={name}
      data-asset={asset.id}
      onClick={onClick}
      onDoubleClick={onOpen}
    >
      <div className="g-thumb" style={list ? { width: 44, height: 44 } : undefined}>
        {thumb ? <img src={thumb} alt="" decoding="async" draggable={false} /> : <span className="g-nothumb">{asset.kind}</span>}
        <span className="g-badges">
          {asset.is_favorite ? <span title="Favourite">★</span> : null}
          {badge ? <span title={badge[1]}>{badge[0]}</span> : null}
          {where ? <span title={where[1]}>{where[0]}</span> : null}
        </span>
      </div>
      {list ? (
        <>
          <span className="g-name ellipsis">{name}</span>
          <span className="muted small">{asset.date ? new Date(asset.date).toLocaleDateString() : "–"}</span>
          <span className="muted small num">{asset.width && asset.height ? `${asset.width}×${asset.height}` : "–"}</span>
          <span className="muted small num">{bytes(asset.byte_size)}</span>
          <span className="muted small">{badge?.[1] ?? where?.[1] ?? ""}</span>
        </>
      ) : null}
    </div>
  );
}

function BulkBar({
  count,
  hiddenView,
  onSend,
  onClear,
}: {
  count: number;
  hiddenView: boolean;
  onSend: (change: Omit<BulkBody, "ids">, what: string) => Promise<void>;
  onClear: () => void;
}) {
  const tags = useTags();
  const collections = useCollections();
  const [tag, setTag] = useState("");
  const [busy, setBusy] = useState(false);
  const go = (change: Omit<BulkBody, "ids">, what: string) => {
    setBusy(true);
    void onSend(change, what).finally(() => setBusy(false));
  };
  const name = tag.trim();
  return (
    <div className="bulk-bar" role="toolbar" aria-label="Selected photos">
      <strong>{plural(count, "photo")} selected</strong>
      <Button size="sm" disabled={busy} onClick={() => go({ set_favorite: true }, "Added to favourites")}>
        ★ Favourite
      </Button>
      <Button size="sm" disabled={busy} onClick={() => go({ set_favorite: false }, "Removed from favourites")}>
        Unfavourite
      </Button>
      <input
        className="inline-input"
        list="iv-tag-names"
        placeholder="Tag…"
        aria-label="Tag name"
        maxLength={100}
        value={tag}
        onChange={(e) => setTag(e.target.value)}
      />
      <datalist id="iv-tag-names">{tags.data?.map((t) => <option key={t.id} value={t.name} />)}</datalist>
      <Button size="sm" disabled={busy || !name} onClick={() => go({ add_tags: [name] }, `Tagged “${name}”`)}>
        Add tag
      </Button>
      <Button size="sm" disabled={busy || !name} onClick={() => go({ remove_tags: [name] }, `Removed tag “${name}”`)}>
        Remove tag
      </Button>
      {collections.data?.length ? (
        <select
          aria-label="Add to collection"
          value=""
          disabled={busy}
          onChange={(e) => {
            const id = Number(e.target.value);
            const c = collections.data.find((x) => x.id === id);
            if (c) go({ add_to_collection: id }, `Added to “${c.name}”`);
          }}
        >
          <option value="">Add to collection…</option>
          {collections.data.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
      ) : null}
      <Button size="sm" disabled={busy} onClick={() => go({ hide: !hiddenView }, hiddenView ? "Unhidden" : "Hidden")}>
        {hiddenView ? "Unhide" : "Hide"}
      </Button>
      <span className="spacer" />
      <Button size="sm" onClick={onClear}>
        Clear
      </Button>
    </div>
  );
}
