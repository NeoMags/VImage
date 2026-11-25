// Pure layout for the gallery (B12): which segments the library is loaded in, and which rows the
// virtual list shows. Kept free of React so it can be tested on its own.
import type { AssetMonths } from "@imagevault/shared-types";

export type Sort = "date" | "indexed" | "size";
export type Order = "asc" | "desc";
export type View = "timeline" | "grid" | "list";

/** A slice of the library loaded on its own: one month (date sort) or everything (other sorts). */
export interface Segment {
  key: string;
  /** "March 2026"; null for the single segment of a non-date sort. */
  label: string | null;
  year: number | null;
  count: number;
  /** Extra list filters for this slice (inclusive dates). */
  range: { from?: string; to?: string };
}

const MONTH_NAMES = new Intl.DateTimeFormat(undefined, { month: "long", year: "numeric", timeZone: "UTC" });

export function monthRange(month: string): { from: string; to: string } {
  const [y, m] = month.split("-").map(Number) as [number, number];
  const last = new Date(Date.UTC(y, m, 0)).getUTCDate(); // day 0 of the next month
  return { from: `${month}-01`, to: `${month}-${String(last).padStart(2, "0")}` };
}

export function segmentsFor(months: AssetMonths, sort: Sort, order: Order): Segment[] {
  if (sort !== "date") {
    return months.total ? [{ key: "all", label: null, year: null, count: months.total, range: {} }] : [];
  }
  // The engine lists months newest first. Every asset has a date (taken, else modified, else added),
  // so a null month is not expected; it is skipped because no date range can select it.
  const segs = months.months
    .filter((m): m is { month: string; count: number } => m.month !== null && m.count > 0)
    .map((m) => {
      const [y, mo] = m.month.split("-").map(Number) as [number, number];
      return {
        key: m.month,
        label: MONTH_NAMES.format(new Date(Date.UTC(y, mo - 1, 1))),
        year: y,
        count: m.count,
        range: monthRange(m.month),
      };
    });
  return order === "asc" ? segs.reverse() : segs;
}

export type Row =
  | { kind: "header"; seg: number; label: string; count: number }
  | { kind: "items"; seg: number; start: number; end: number };

export interface Layout {
  rows: Row[];
  /** Global index of each segment's first item (prefix sums), plus the total at the end. */
  firstItem: number[];
  /** Row index of each segment's first row (its header when there is one). */
  firstRow: number[];
}

/** Rows for the virtual list: optional month header, then rows of `cols` items. */
export function layoutRows(segments: Segment[], cols: number, headers: boolean): Layout {
  const rows: Row[] = [];
  const firstItem: number[] = [];
  const firstRow: number[] = [];
  let item = 0;
  segments.forEach((s, seg) => {
    firstItem.push(item);
    firstRow.push(rows.length);
    if (headers && s.label) rows.push({ kind: "header", seg, label: s.label, count: s.count });
    for (let start = 0; start < s.count; start += cols) rows.push({ kind: "items", seg, start, end: Math.min(s.count, start + cols) });
    item += s.count;
  });
  firstItem.push(item);
  return { rows, firstItem, firstRow };
}

/** Segment and offset of a global item index (binary search over the prefix sums). */
export function locate(firstItem: number[], index: number): { seg: number; offset: number } | null {
  const total = firstItem[firstItem.length - 1] ?? 0;
  if (index < 0 || index >= total) return null;
  let lo = 0;
  let hi = firstItem.length - 2;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (firstItem[mid]! <= index) lo = mid;
    else hi = mid - 1;
  }
  return { seg: lo, offset: index - firstItem[lo]! };
}

/** Row that shows a given global item. */
export function rowOfItem(layout: Layout, segments: Segment[], index: number, cols: number, headers: boolean): number {
  const at = locate(layout.firstItem, index);
  if (!at) return 0;
  const header = headers && segments[at.seg]?.label ? 1 : 0;
  return layout.firstRow[at.seg]! + header + Math.floor(at.offset / cols);
}

/** Year markers for the scrubber: the first row of each year, in display order. */
export function yearMarks(segments: Segment[], layout: Layout): { year: number; row: number }[] {
  const out: { year: number; row: number }[] = [];
  segments.forEach((s, i) => {
    if (s.year !== null && out[out.length - 1]?.year !== s.year) out.push({ year: s.year, row: layout.firstRow[i]! });
  });
  return out;
}

export function columnsFor(width: number, cell: number, gap: number): number {
  return Math.max(1, Math.floor((width + gap) / (cell + gap)));
}
