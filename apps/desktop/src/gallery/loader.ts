// Loads the library one segment (month) at a time, page by page, only for the parts on screen.
// Months scrolled far away are dropped after a while so 100k photos never sit in memory at once.
import type { AssetPage, AssetSummary } from "@imagevault/shared-types";

import type { Segment } from "./layout";

export const PAGE_SIZE = 200;
export const EVICT_AFTER_MS = 5 * 60_000;

export type FetchPage = (segment: Segment, cursor: string | undefined, limit: number) => Promise<AssetPage>;

interface SegState {
  items: AssetSummary[];
  /** What was shown before a refresh; displayed until the fresh copy catches up (no flicker). */
  previous: AssetSummary[] | null;
  cursor: string | undefined;
  done: boolean;
  busy: boolean;
  error: unknown;
  /** Highest offset anyone asked for. */
  wanted: number;
  seenAt: number;
}

export class SegmentLoader {
  private segs = new Map<string, SegState>();
  private listeners = new Set<() => void>();
  private generation = 0;
  private notifyScheduled = false;
  version = 0;

  constructor(
    private readonly fetchPage: FetchPage,
    private readonly now: () => number = Date.now,
  ) {}

  subscribe = (fn: () => void): (() => void) => {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  };

  getVersion = (): number => this.version;

  item(key: string, offset: number): AssetSummary | undefined {
    const s = this.segs.get(key);
    return s ? (s.items[offset] ?? s.previous?.[offset]) : undefined;
  }

  error(key: string): unknown {
    return this.segs.get(key)?.error ?? null;
  }

  /** Asks for a segment up to an offset; fetches the missing pages in order (keyset paging). */
  want(segment: Segment, upTo: number): void {
    const s = this.state(segment.key);
    s.seenAt = this.now();
    s.wanted = Math.max(s.wanted, Math.min(upTo, segment.count - 1));
    if (!s.busy && !s.done && !s.error && s.items.length <= s.wanted) void this.pump(segment, s);
  }

  /** Library changed (live event or edit): reload what is on screen, keeping it visible meanwhile. */
  refresh(): void {
    this.generation++;
    for (const [key, s] of this.segs) {
      this.segs.set(key, {
        ...this.blank(),
        previous: s.items.length ? s.items : s.previous,
        wanted: Math.max(0, s.items.length - 1),
        seenAt: s.seenAt,
      });
    }
    this.changed();
  }

  retry(segment: Segment): void {
    const s = this.state(segment.key);
    s.error = null;
    this.want(segment, s.wanted);
  }

  /** Drops segments not on screen for a while. Returns how many were dropped. */
  evict(visible: Set<string>): number {
    const cutoff = this.now() - EVICT_AFTER_MS;
    let n = 0;
    for (const [key, s] of this.segs) {
      if (!visible.has(key) && !s.busy && s.seenAt < cutoff) {
        this.segs.delete(key);
        n++;
      }
    }
    if (n) this.changed();
    return n;
  }

  /** Every loaded item, in no particular order (for selection by id). */
  loadedCount(): number {
    let n = 0;
    for (const s of this.segs.values()) n += Math.max(s.items.length, s.previous?.length ?? 0);
    return n;
  }

  private async pump(segment: Segment, s: SegState): Promise<void> {
    const gen = this.generation;
    s.busy = true;
    try {
      while (!s.done && s.items.length <= s.wanted) {
        const page = await this.fetchPage(segment, s.cursor, PAGE_SIZE);
        if (gen !== this.generation || this.segs.get(segment.key) !== s) return; // superseded
        s.items = s.items.concat(page.items);
        s.cursor = page.next_cursor ?? undefined;
        s.done = !page.next_cursor;
        if (s.previous && (s.done || s.items.length >= s.previous.length)) s.previous = null;
        this.changed();
      }
    } catch (e) {
      if (gen === this.generation && this.segs.get(segment.key) === s) {
        s.error = e;
        this.changed();
      }
    } finally {
      s.busy = false;
    }
  }

  private state(key: string): SegState {
    let s = this.segs.get(key);
    if (!s) {
      s = this.blank();
      this.segs.set(key, s);
    }
    return s;
  }

  private blank(): SegState {
    return { items: [], previous: null, cursor: undefined, done: false, busy: false, error: null, wanted: 0, seenAt: this.now() };
  }

  /** Batches notifications to one per microtask: a fast scroll can complete many pages at once. */
  private changed(): void {
    this.version++;
    if (this.notifyScheduled) return;
    this.notifyScheduled = true;
    queueMicrotask(() => {
      this.notifyScheduled = false;
      for (const fn of this.listeners) fn();
    });
  }
}
