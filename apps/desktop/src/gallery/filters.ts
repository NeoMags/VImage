// Gallery filters (B15). Kept in memory for the session: they survive switching screens and reset
// when the app restarts (saved filters are D3 Smart Collections).
import type { AssetFilter } from "@imagevault/shared-types";
import { useSyncExternalStore } from "react";

/** Everything the filter panel sets; "hidden" stays a separate toggle on the screen. */
export type Filters = Omit<AssetFilter, "hidden">;

let current: Filters = {};
const listeners = new Set<() => void>();

const subscribe = (fn: () => void) => {
  listeners.add(fn);
  return () => listeners.delete(fn);
};

/** Drops empty values so equal filters always serialise the same (query keys, gallery reset). */
export function clean(f: Filters): Filters {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(f)) {
    if (v === undefined || v === null || v === "" || (Array.isArray(v) && v.length === 0)) continue;
    out[k] = v;
  }
  return out as Filters;
}

export function setFilters(next: Filters | ((old: Filters) => Filters)) {
  current = clean(typeof next === "function" ? next(current) : next);
  for (const fn of listeners) fn();
}

export function useFilters(): Filters {
  return useSyncExternalStore(subscribe, () => current);
}

export const activeCount = (f: Filters) => Object.keys(clean(f)).length;

export const MB = 1_000_000;

const KIND_LABEL: Record<string, string> = {
  image: "Photos",
  screenshot: "Screenshots",
  video: "Videos",
  pdf: "PDFs",
  drawing: "Drawings",
  document: "Documents",
  other: "Other",
};
export const kindLabel = (k: string) => KIND_LABEL[k] ?? k;

/** "image/jpeg" → "JPEG", "image/x-canon-cr2" → "CR2". */
export function formatLabel(mime: string): string {
  const sub = mime.split("/")[1] ?? mime;
  const tail = sub.split(/[-.+]/).filter((p) => p && p !== "x" && p !== "vnd").pop() ?? sub;
  return tail.toUpperCase();
}

const STATUS_LABEL: Record<string, string> = {
  ready: "Ready",
  pending: "Waiting",
  processing: "Processing",
  failed: "Failed",
  unsupported: "Not supported",
};
export const statusLabel = (s: string) => STATUS_LABEL[s] ?? s;

const AVAIL_LABEL: Record<string, string> = { present: "On disk", missing: "Missing", unreachable: "Drive not connected" };
export const availabilityLabel = (a: string) => AVAIL_LABEL[a] ?? a;

export interface Chip {
  key: string;
  label: string;
  remove: (f: Filters) => Filters;
}

interface Names {
  tag: (id: number) => string | undefined;
  source: (id: number) => string | undefined;
  collection: (id: number) => string | undefined;
}

const without = (f: Filters, ...keys: (keyof Filters)[]) => {
  const out = { ...f };
  for (const k of keys) delete out[k];
  return out;
};

const asList = <T,>(v: T | T[] | null | undefined): T[] => (v == null ? [] : Array.isArray(v) ? v : [v]);

/** One removable chip per active filter value, in panel order. */
export function chipsFor(f: Filters, names: Names): Chip[] {
  const chips: Chip[] = [];
  for (const k of asList(f.kind)) {
    chips.push({ key: `kind:${k}`, label: kindLabel(k), remove: (x) => ({ ...x, kind: asList(x.kind).filter((v) => v !== k) }) });
  }
  for (const m of asList(f.mime)) {
    chips.push({ key: `mime:${m}`, label: formatLabel(m), remove: (x) => ({ ...x, mime: asList(x.mime).filter((v) => v !== m) }) });
  }
  if (f.from || f.to) {
    const label = f.from && f.to ? `${f.from} – ${f.to}` : f.from ? `From ${f.from}` : `Until ${f.to}`;
    chips.push({ key: "date", label, remove: (x) => without(x, "from", "to") });
  }
  if (f.min_bytes != null || f.max_bytes != null) {
    const mb = (n: number) => `${+(n / MB).toFixed(1)} MB`;
    const label =
      f.min_bytes != null && f.max_bytes != null
        ? `${mb(f.min_bytes)} – ${mb(f.max_bytes)}`
        : f.min_bytes != null
          ? `≥ ${mb(f.min_bytes)}`
          : `≤ ${mb(f.max_bytes!)}`;
    chips.push({ key: "size", label, remove: (x) => without(x, "min_bytes", "max_bytes") });
  }
  if (f.min_w != null || f.min_h != null) {
    chips.push({ key: "min-dim", label: `At least ${f.min_w ?? "any"} × ${f.min_h ?? "any"} px`, remove: (x) => without(x, "min_w", "min_h") });
  }
  if (f.max_w != null || f.max_h != null) {
    chips.push({ key: "max-dim", label: `At most ${f.max_w ?? "any"} × ${f.max_h ?? "any"} px`, remove: (x) => without(x, "max_w", "max_h") });
  }
  if (f.source != null) {
    chips.push({ key: "source", label: `Folder: ${names.source(f.source) ?? f.source}`, remove: (x) => without(x, "source") });
  }
  for (const t of asList(f.tag)) {
    chips.push({ key: `tag:${t}`, label: `#${names.tag(t) ?? t}`, remove: (x) => ({ ...x, tag: asList(x.tag).filter((v) => v !== t) }) });
  }
  if (f.collection != null) {
    chips.push({
      key: "collection",
      label: `In “${names.collection(f.collection) ?? f.collection}”`,
      remove: (x) => without(x, "collection"),
    });
  }
  if (f.favorite != null) {
    chips.push({ key: "favorite", label: f.favorite ? "Favourites" : "Not favourites", remove: (x) => without(x, "favorite") });
  }
  if (f.reviewed != null) {
    chips.push({ key: "reviewed", label: f.reviewed ? "Reviewed" : "Not reviewed", remove: (x) => without(x, "reviewed") });
  }
  for (const s of asList(f.status)) {
    chips.push({ key: `status:${s}`, label: statusLabel(s), remove: (x) => ({ ...x, status: asList(x.status).filter((v) => v !== s) }) });
  }
  if (f.availability) {
    chips.push({ key: "availability", label: availabilityLabel(f.availability), remove: (x) => without(x, "availability") });
  }
  return chips;
}
