// The filter panel beside the gallery (B15). Every option shows how many photos ticking it would
// give; the counts come from GET /v1/assets:facets with the same filters as the gallery.
import { useAssetFacets, useCollections, useSources, useTags } from "@imagevault/api-client";
import type { AssetFacets, AssetFilter } from "@imagevault/shared-types";
import { Button } from "@imagevault/ui";
import { type ReactNode, useEffect, useMemo, useState } from "react";

import { collectionOptions } from "../collections/tree";
import { ErrorView } from "../components/States";
import { num } from "../format";
import {
  activeCount,
  availabilityLabel,
  type Filters,
  formatLabel,
  kindLabel,
  typeLabel,
  MB,
  setFilters,
  statusLabel,
} from "./filters";

function Section({ title, children, active }: { title: string; children: ReactNode; active?: boolean }) {
  return (
    <fieldset className="filter-section">
      <legend>
        {title}
        {active ? <span className="dot" aria-label="active" /> : null}
      </legend>
      {children}
    </fieldset>
  );
}

function Count({ n }: { n: number | undefined }) {
  return <span className="count">{n == null ? "" : num(n)}</span>;
}

const toggle = <T,>(list: T[] | T | null | undefined, v: T): T[] => {
  const l = list == null ? [] : Array.isArray(list) ? list : [list];
  return l.includes(v) ? l.filter((x) => x !== v) : [...l, v];
};

/** A number input that commits on blur or Enter, so typing does not refetch on every key. */
function NumberField({
  label,
  value,
  onCommit,
  placeholder,
  step,
}: {
  label: string;
  value: number | null | undefined;
  onCommit: (v: number | undefined) => void;
  placeholder?: string;
  step?: string;
}) {
  const [draft, setDraft] = useState(value == null ? "" : String(value));
  useEffect(() => setDraft(value == null ? "" : String(value)), [value]);
  const commit = () => {
    const t = draft.trim();
    const n = t === "" ? undefined : Number(t);
    if (n !== undefined && (!Number.isFinite(n) || n < 0)) return setDraft(value == null ? "" : String(value));
    if (n !== (value ?? undefined)) onCommit(n);
  };
  return (
    <label className="num-field">
      <span className="sr-only">{label}</span>
      <input
        type="number"
        min={0}
        step={step ?? "1"}
        inputMode="decimal"
        aria-label={label}
        placeholder={placeholder}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => e.key === "Enter" && commit()}
      />
    </label>
  );
}

function TriState({ label, value, counts, onChange }: {
  label: string;
  value: boolean | null | undefined;
  counts: AssetFacets["favorite"] | undefined;
  onChange: (v: boolean | undefined) => void;
}) {
  const opts: [string, boolean | undefined, number | undefined][] = [
    ["Any", undefined, counts ? counts.yes + counts.no : undefined],
    ["Yes", true, counts?.yes],
    ["No", false, counts?.no],
  ];
  return (
    <div className="segmented tri" role="radiogroup" aria-label={label}>
      {opts.map(([text, v, n]) => (
        <button key={text} type="button" role="radio" aria-checked={(value ?? undefined) === v} onClick={() => onChange(v)}>
          {text} <Count n={n} />
        </button>
      ))}
    </div>
  );
}

export function FilterPanel({
  filter,
  onClose,
  inCollection = false,
}: {
  filter: AssetFilter;
  onClose: () => void;
  /** Viewing one collection: it is fixed, so there is no collection choice. */
  inCollection?: boolean;
}) {
  const f = filter as Filters;
  const [tagQ, setTagQ] = useState("");
  const [debouncedQ, setDebouncedQ] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(tagQ.trim()), 250);
    return () => clearTimeout(t);
  }, [tagQ]);
  const facets = useAssetFacets(filter, debouncedQ);
  const tags = useTags();
  const sources = useSources();
  const collections = useCollections();
  const set = (patch: Filters) => setFilters((old) => ({ ...old, ...patch }));
  const d = facets.data;

  const tagName = useMemo(() => new Map((tags.data ?? []).map((t) => [t.id, t.name])), [tags.data]);
  const selectedTags = (Array.isArray(f.tag) ? f.tag : f.tag != null ? [f.tag] : []) as number[];
  // Ticked tags stay listed even when the search or the top 50 would leave them out.
  const tagRows = useMemo(() => {
    const rows = [...(d?.tags ?? [])];
    for (const id of selectedTags) {
      if (!rows.some((r) => r.id === id) && !d?.ai_tags.some((r) => r.id === id)) rows.unshift({ id, name: tagName.get(id) ?? `#${id}`, count: d?.total ?? 0 });
    }
    return rows;
  }, [d, selectedTags, tagName]);
  const collCount = new Map((d?.collection ?? []).map((c) => [c.id, c.count]));
  const srcCount = new Map((d?.source ?? []).map((s) => [s.id, s.count]));

  const listOf = (
    facet: "kind" | "type" | "format" | "status",
    key: "kind" | "type" | "mime" | "status",
    label: (v: string) => string,
  ) => {
    const selected = (Array.isArray(f[key]) ? f[key] : f[key] != null ? [f[key]] : []) as string[];
    const rows = [...(d?.[facet] ?? [])];
    for (const v of selected) if (!rows.some((r) => r.value === v)) rows.push({ value: v, count: 0 });
    if (!rows.length) return <p className="muted small">None</p>;
    return (
      <ul className="filter-list">
        {rows.map((r) => (
          <li key={r.value}>
            <label className="check">
              <input
                type="checkbox"
                checked={selected.includes(r.value)}
                onChange={() => set({ [key]: toggle(selected, r.value) } as Filters)}
              />
              {label(r.value)}
              <Count n={r.count} />
            </label>
          </li>
        ))}
      </ul>
    );
  };

  return (
    <aside id="filter-panel" className="filter-panel" aria-label="Filters">
      <div className="filter-head">
        <strong>Filters</strong>
        {d ? <span className="muted small" aria-live="polite">{num(d.total)} match</span> : null}
        <span className="spacer" />
        {activeCount(inCollection ? { ...f, collection: undefined } : f) ? (
          <Button size="sm" onClick={() => setFilters({})}>
            Clear all
          </Button>
        ) : null}
        <Button size="sm" aria-label="Close filters" onClick={onClose}>
          ✕
        </Button>
      </div>
      {facets.isError ? (
        <ErrorView what="the filter counts" error={facets.error} onRetry={() => void facets.refetch()} />
      ) : null}
      <div className={`filter-body${facets.isFetching ? " stale" : ""}`}>
        <Section title="Kind" active={f.kind != null}>
          {listOf("kind", "kind", kindLabel)}
        </Section>
        <Section title="Type" active={f.type != null}>
          {listOf("type", "type", typeLabel)}
        </Section>
        <Section title="Date" active={!!(f.from || f.to)}>
          <div className="range">
            <input type="date" aria-label="From date" value={f.from ?? ""} min={d?.date.min ?? undefined} max={d?.date.max ?? undefined} onChange={(e) => set({ from: e.target.value || undefined })} />
            <span>–</span>
            <input type="date" aria-label="To date" value={f.to ?? ""} min={d?.date.min ?? undefined} max={d?.date.max ?? undefined} onChange={(e) => set({ to: e.target.value || undefined })} />
          </div>
        </Section>
        <Section title="Format" active={f.mime != null}>
          {listOf("format", "mime", formatLabel)}
        </Section>
        <Section title="File size (MB)" active={f.min_bytes != null || f.max_bytes != null}>
          <div className="range">
            <NumberField
              label="Smallest file size in MB"
              step="0.1"
              placeholder={d?.size.min != null ? String(+(d.size.min / MB).toPrecision(2)) : "min"}
              value={f.min_bytes != null ? +(f.min_bytes / MB).toFixed(3) : undefined}
              onCommit={(v) => set({ min_bytes: v == null ? undefined : Math.round(v * MB) })}
            />
            <span>–</span>
            <NumberField
              label="Largest file size in MB"
              step="0.1"
              placeholder={d?.size.max != null ? String(+(d.size.max / MB).toPrecision(2)) : "max"}
              value={f.max_bytes != null ? +(f.max_bytes / MB).toFixed(3) : undefined}
              onCommit={(v) => set({ max_bytes: v == null ? undefined : Math.round(v * MB) })}
            />
          </div>
        </Section>
        <Section title="Dimensions (px)" active={[f.min_w, f.min_h, f.max_w, f.max_h].some((v) => v != null)}>
          <div className="range">
            <span className="small muted">At least</span>
            <NumberField label="Minimum width" placeholder="width" value={f.min_w} onCommit={(v) => set({ min_w: v || undefined })} />
            <span>×</span>
            <NumberField label="Minimum height" placeholder="height" value={f.min_h} onCommit={(v) => set({ min_h: v || undefined })} />
          </div>
          <div className="range">
            <span className="small muted">At most</span>
            <NumberField label="Maximum width" placeholder="width" value={f.max_w} onCommit={(v) => set({ max_w: v || undefined })} />
            <span>×</span>
            <NumberField label="Maximum height" placeholder="height" value={f.max_h} onCommit={(v) => set({ max_h: v || undefined })} />
          </div>
        </Section>
        <Section title="Tags" active={selectedTags.length > 0}>
          <input
            className="inline-input"
            type="search"
            aria-label="Search tags"
            placeholder="Search tags"
            value={tagQ}
            onChange={(e) => setTagQ(e.target.value)}
          />
          {tagRows.length ? (
            <TagList rows={tagRows} selected={selectedTags} onToggle={(id) => set({ tag: toggle(selectedTags, id) })} />
          ) : (
            <p className="muted small">{debouncedQ ? "No tags match." : "No tags on these photos."}</p>
          )}
          {selectedTags.length > 1 ? <p className="muted small">Photos must have all ticked tags.</p> : null}
        </Section>
        {d?.ai_tags.length ? (
          <Section title="AI tags" active={d.ai_tags.some((t) => selectedTags.includes(t.id))}>
            <p className="muted small">Suggested by AI, not yet accepted.</p>
            <TagList
              label="AI tags"
              rows={d.ai_tags}
              selected={selectedTags}
              onToggle={(id) => set({ tag: toggle(selectedTags, id) })}
            />
          </Section>
        ) : null}
        {inCollection ? null : (
          <Section title="Collection" active={f.collection != null}>
            <select aria-label="Collection" value={f.collection ?? ""} onChange={(e) => set({ collection: e.target.value ? Number(e.target.value) : undefined })}>
              <option value="">Any</option>
              {collectionOptions(collections.data ?? []).map(({ c, depth }) => (
                <option key={c.id} value={c.id}>
                  {"  ".repeat(depth) + c.name} ({num(collCount.get(c.id) ?? 0)})
                </option>
              ))}
            </select>
          </Section>
        )}
        <Section title="Folder" active={f.source != null}>
          <select aria-label="Folder" value={f.source ?? ""} onChange={(e) => set({ source: e.target.value ? Number(e.target.value) : undefined })}>
            <option value="">Any</option>
            {(sources.data ?? []).map((s) => (
              <option key={s.id} value={s.id}>
                {s.display_name} ({num(srcCount.get(s.id) ?? 0)})
              </option>
            ))}
          </select>
        </Section>
        <Section title="Favourite" active={f.favorite != null}>
          <TriState label="Favourite" value={f.favorite} counts={d?.favorite} onChange={(v) => set({ favorite: v })} />
        </Section>
        <Section title="Reviewed" active={f.reviewed != null}>
          <TriState label="Reviewed" value={f.reviewed} counts={d?.reviewed} onChange={(v) => set({ reviewed: v })} />
        </Section>
        <Section title="Processing status" active={f.status != null}>
          {listOf("status", "status", statusLabel)}
        </Section>
        <Section title="Availability" active={f.availability != null}>
          <ul className="filter-list">
            {[undefined, ...(d?.availability ?? []).map((a) => a.value)].map((v) => (
              <li key={v ?? "any"}>
                <label className="check">
                  <input
                    type="radio"
                    name="availability"
                    checked={(f.availability ?? undefined) === v}
                    onChange={() => set({ availability: v as Filters["availability"] })}
                  />
                  {v ? availabilityLabel(v) : "Any"}
                  <Count
                    n={
                      v
                        ? d?.availability.find((a) => a.value === v)?.count
                        : d?.availability.reduce((n, a) => n + a.count, 0)
                    }
                  />
                </label>
              </li>
            ))}
          </ul>
        </Section>
      </div>
    </aside>
  );
}

function TagList({
  rows,
  selected,
  onToggle,
  label = "Tags",
}: {
  rows: { id: number; name: string; count: number }[];
  selected: number[];
  onToggle: (id: number) => void;
  label?: string;
}) {
  return (
    <ul className="filter-list" aria-label={label}>
      {rows.map((t) => (
        <li key={t.id}>
          <label className="check">
            <input type="checkbox" checked={selected.includes(t.id)} onChange={() => onToggle(t.id)} />
            {t.name}
            <Count n={t.count} />
          </label>
        </li>
      ))}
    </ul>
  );
}
