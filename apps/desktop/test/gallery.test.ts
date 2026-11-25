import type { AssetPage, AssetSummary } from "@imagevault/shared-types";

import { MAX_BULK, runBulk } from "../src/gallery/bulk";
import { columnsFor, layoutRows, locate, monthRange, rowOfItem, segmentsFor, yearMarks } from "../src/gallery/layout";
import { EVICT_AFTER_MS, PAGE_SIZE, SegmentLoader } from "../src/gallery/loader";
import * as Sel from "../src/gallery/selection";

const months = {
  total: 7,
  months: [
    { month: "2026-03", count: 3 },
    { month: "2026-01", count: 2 },
    { month: "2024-12", count: 2 },
  ],
};

describe("layout", () => {
  it("makes one segment per month with inclusive day ranges", () => {
    const segs = segmentsFor(months, "date", "desc");
    expect(segs.map((s) => s.key)).toEqual(["2026-03", "2026-01", "2024-12"]);
    expect(segs[0]!.range).toEqual({ from: "2026-03-01", to: "2026-03-31" });
    expect(monthRange("2024-02")).toEqual({ from: "2024-02-01", to: "2024-02-29" }); // leap year
    expect(segmentsFor(months, "date", "asc").map((s) => s.key)).toEqual(["2024-12", "2026-01", "2026-03"]);
    expect(segmentsFor(months, "size", "desc")).toEqual([
      { key: "all", label: null, year: null, count: 7, range: {} },
    ]);
    expect(segmentsFor({ total: 0, months: [] }, "size", "desc")).toEqual([]);
  });

  it("lays out headers and rows, and finds items and rows again", () => {
    const segs = segmentsFor(months, "date", "desc");
    const lay = layoutRows(segs, 2, true);
    // 2026-03: header + 2 rows (2+1); 2026-01: header + 1 row; 2024-12: header + 1 row
    expect(lay.rows.map((r) => r.kind)).toEqual(["header", "items", "items", "header", "items", "header", "items"]);
    expect(lay.firstItem).toEqual([0, 3, 5, 7]);
    expect(locate(lay.firstItem, 4)).toEqual({ seg: 1, offset: 1 });
    expect(locate(lay.firstItem, 7)).toBeNull();
    expect(rowOfItem(lay, segs, 2, 2, true)).toBe(2);
    expect(rowOfItem(lay, segs, 5, 2, true)).toBe(6);
    expect(yearMarks(segs, lay)).toEqual([
      { year: 2026, row: 0 },
      { year: 2024, row: 5 },
    ]);
    expect(layoutRows(segs, 2, false).rows.every((r) => r.kind === "items")).toBe(true);
  });

  it("fits as many columns as the width allows, at least one", () => {
    expect(columnsFor(1000, 180, 6)).toBe(5);
    expect(columnsFor(10, 180, 6)).toBe(1);
  });
});

const asset = (id: number) => ({ id }) as AssetSummary;

function fakeEngine(sizes: Record<string, number>) {
  const calls: { key: string; cursor: string | undefined }[] = [];
  let fail = false;
  const fetchPage = async (seg: { key: string }, cursor: string | undefined, limit: number): Promise<AssetPage> => {
    calls.push({ key: seg.key, cursor });
    if (fail) throw new Error("engine down");
    const start = cursor ? Number(cursor) : 0;
    const n = Math.min(limit, sizes[seg.key]! - start);
    const base = seg.key === "a" ? 0 : 100_000;
    return {
      items: Array.from({ length: n }, (_, i) => asset(base + start + i)),
      next_cursor: start + n < sizes[seg.key]! ? String(start + n) : null,
    };
  };
  return { fetchPage, calls, setFail: (f: boolean) => (fail = f) };
}
const seg = (key: string, count: number) => ({ key, label: key, year: null, count, range: {} });
const settle = () => new Promise((r) => setTimeout(r, 0));

describe("segment loader", () => {
  it("loads only the pages needed, in order, and never twice", async () => {
    const eng = fakeEngine({ a: 450 });
    const l = new SegmentLoader(eng.fetchPage);
    l.want(seg("a", 450), 10);
    l.want(seg("a", 450), 20); // already in flight: no second request
    await settle();
    expect(eng.calls).toHaveLength(1);
    expect(l.item("a", 10)?.id).toBe(10);
    expect(l.item("a", PAGE_SIZE)).toBeUndefined();
    l.want(seg("a", 450), 420);
    await settle();
    await settle();
    expect(eng.calls.map((c) => c.cursor)).toEqual([undefined, "200", "400"]);
    expect(l.item("a", 449)?.id).toBe(449);
    l.want(seg("a", 450), 449);
    await settle();
    expect(eng.calls).toHaveLength(3); // done
  });

  it("keeps showing old items while it reloads after a change", async () => {
    const eng = fakeEngine({ a: 50 });
    const l = new SegmentLoader(eng.fetchPage);
    l.want(seg("a", 50), 5);
    await settle();
    l.refresh();
    expect(l.item("a", 5)?.id).toBe(5); // still visible
    l.want(seg("a", 50), 5);
    await settle();
    expect(eng.calls).toHaveLength(2);
    expect(l.item("a", 5)?.id).toBe(5);
  });

  it("reports a failed month and retries on request", async () => {
    const eng = fakeEngine({ a: 10 });
    const l = new SegmentLoader(eng.fetchPage);
    eng.setFail(true);
    l.want(seg("a", 10), 3);
    await settle();
    expect(String(l.error("a"))).toContain("engine down");
    l.want(seg("a", 10), 3); // no hammering while failed
    await settle();
    expect(eng.calls).toHaveLength(1);
    eng.setFail(false);
    l.retry(seg("a", 10));
    await settle();
    expect(l.error("a")).toBeNull();
    expect(l.item("a", 3)?.id).toBe(3);
  });

  it("forgets months that stayed off screen for a while", async () => {
    let now = 0;
    const eng = fakeEngine({ a: 10, b: 10 });
    const l = new SegmentLoader(eng.fetchPage, () => now);
    l.want(seg("a", 10), 1);
    l.want(seg("b", 10), 1);
    await settle();
    now = EVICT_AFTER_MS - 1;
    expect(l.evict(new Set(["b"]))).toBe(0);
    now = EVICT_AFTER_MS + 1;
    expect(l.evict(new Set(["b"]))).toBe(1);
    expect(l.item("a", 1)).toBeUndefined();
    expect(l.item("b", 1)?.id).toBe(100_001);
  });

  it("ignores pages that arrive after a refresh started", async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    const l = new SegmentLoader(async () => {
      await gate;
      return { items: [asset(999)], next_cursor: null };
    });
    l.want(seg("a", 1), 0);
    l.refresh();
    release();
    await settle();
    expect(l.item("a", 0)).toBeUndefined();
  });
});

describe("selection", () => {
  const itemAt = (i: number) => (i === 3 ? undefined : { id: i + 100 }); // item 3 not loaded yet
  it("click selects one, ctrl toggles, shift selects a range of loaded items", () => {
    let s = Sel.click(Sel.EMPTY, 1, 101, { ctrl: false, shift: false }, itemAt);
    expect([...s.ids]).toEqual([101]);
    s = Sel.click(s, 5, 105, { ctrl: false, shift: true }, itemAt);
    expect([...s.ids].sort()).toEqual([101, 102, 104, 105]);
    s = Sel.click(s, 2, 102, { ctrl: true, shift: false }, itemAt);
    expect(s.ids.has(102)).toBe(false);
    s = Sel.click(s, 0, 100, { ctrl: true, shift: true }, itemAt); // extends from anchor 2, keeps the rest
    expect([...s.ids].sort()).toEqual([100, 101, 102, 104, 105]);
    expect(Sel.all(5, itemAt).ids.size).toBe(4);
  });
});

describe("bulk in chunks", () => {
  it("splits at the engine's limit and reports partial progress on failure", async () => {
    const ids = Array.from({ length: MAX_BULK * 2 + 10 }, (_, i) => i + 1);
    const sent: number[] = [];
    const ok = await runBulk(ids, { hide: true }, async (b) => {
      sent.push(b.ids.length);
      expect(b.hide).toBe(true);
    });
    expect(sent).toEqual([MAX_BULK, MAX_BULK, 10]);
    expect(ok).toEqual({ done: ids.length, total: ids.length, error: null });

    let n = 0;
    const bad = await runBulk(ids, { set_favorite: true }, async () => {
      if (++n === 2) throw new Error("busy");
    });
    expect(bad.done).toBe(MAX_BULK);
    expect(String(bad.error)).toContain("busy");
  });
});
